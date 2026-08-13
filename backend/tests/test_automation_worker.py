from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation_worker import (
    claim_scheduled_cycle,
    fail_scheduled_cycle,
    finish_scheduled_cycle,
    renew_scheduled_cycle,
    run_automation_cycle,
)


def run(coro):
    return asyncio.run(coro)


class LeaseCollection:
    def __init__(self):
        self.document = None

    async def find_one_and_update(self, query, update, **kwargs):
        now = update["$set"]["started_at"]
        if self.document is not None:
            next_run = self.document.get("next_run_at")
            locked = self.document.get("locked_until")
            if (next_run and next_run > now) or (locked and locked > now):
                from pymongo.errors import DuplicateKeyError
                raise DuplicateKeyError("already claimed")
        self.document = dict(self.document or {"_id": query["_id"], "attempts": 0})
        self.document.update(update["$set"])
        self.document["attempts"] += update["$inc"]["attempts"]
        return dict(self.document)

    async def update_one(self, query, update):
        if self.document and all(self.document.get(key) == value for key, value in query.items()):
            self.document.update(update["$set"])


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    async def to_list(self, _limit):
        return list(self.documents)


class Organizations:
    def find(self, query, projection):
        return Cursor([{"organization_id": "org_a"}, {"organization_id": "org_b"}])


def test_cycle_lease_prevents_duplicate_execution_until_next_interval():
    collection = LeaseCollection()
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    first = run(claim_scheduled_cycle(
        collection, owner="worker-a", interval_seconds=300, lease_seconds=900, now=now,
    ))
    second = run(claim_scheduled_cycle(
        collection, owner="worker-b", interval_seconds=300, lease_seconds=900, now=now,
    ))
    run(finish_scheduled_cycle(collection, owner="worker-a", result={"ok": True}, now=now))
    third = run(claim_scheduled_cycle(
        collection, owner="worker-b", interval_seconds=300, lease_seconds=900,
        now=now + timedelta(seconds=299),
    ))
    fourth = run(claim_scheduled_cycle(
        collection, owner="worker-b", interval_seconds=300, lease_seconds=900,
        now=now + timedelta(seconds=301),
    ))

    assert first["owner"] == "worker-a"
    assert second is None
    assert third is None
    assert fourth["owner"] == "worker-b"
    assert fourth["attempts"] == 2


def test_failed_cycle_is_released_and_scheduled_for_retry():
    collection = LeaseCollection()
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    run(claim_scheduled_cycle(collection, owner="worker-a", now=now))
    run(fail_scheduled_cycle(
        collection, owner="worker-a", error=RuntimeError("fallo controlado"),
        retry_seconds=60, now=now,
    ))

    assert collection.document["status"] == "error"
    assert collection.document["last_error"] == "fallo controlado"
    assert collection.document["next_run_at"] == now + timedelta(seconds=60)
    assert collection.document["locked_until"] == now


def test_running_cycle_renews_its_lease():
    collection = LeaseCollection()
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    run(claim_scheduled_cycle(collection, owner="worker-a", lease_seconds=90, now=now))

    renewed = run(renew_scheduled_cycle(
        collection,
        owner="worker-a",
        lease_seconds=90,
        now=now + timedelta(seconds=60),
    ))

    assert renewed is True
    assert collection.document["locked_until"] == now + timedelta(seconds=150)


def test_cycle_runs_every_job_for_each_tenant_and_restores_context():
    calls = []
    active_org = {"value": None}

    def set_org(organization_id):
        previous = active_org["value"]
        active_org["value"] = organization_id
        return previous

    def reset_org(previous):
        active_org["value"] = previous

    async def operation(name, result=None):
        calls.append((active_org["value"], name))
        return result if result is not None else {"processed": 1}

    runtime = SimpleNamespace(
        _raw_collection=lambda name: Organizations(),
        set_organization_id=set_org,
        reset_organization_id=reset_org,
        db=object(),
        scan_lead_no_response=lambda: operation("lead_no_response"),
        scan_task_notifications=lambda: operation("task_notifications"),
        close_inactive_conversations=lambda db: operation("inactive_conversations"),
        check_and_send_scheduled_reports=lambda: operation("scheduled_reports"),
        send_due_appointment_reminders=lambda: operation("appointment_reminders"),
        process_due_ai_settlements=lambda: operation("ai_settlements"),
    )

    result = run(run_automation_cycle(runtime))

    tenant_jobs = [name for org, name in calls if org in {"org_a", "org_b"}]
    assert tenant_jobs.count("lead_no_response") == 2
    assert tenant_jobs.count("task_notifications") == 2
    assert tenant_jobs.count("inactive_conversations") == 2
    assert tenant_jobs.count("scheduled_reports") == 2
    assert tenant_jobs.count("appointment_reminders") == 2
    assert calls[-1] == (None, "ai_settlements")
    assert active_org["value"] is None
    assert result["ok"] is True


def test_worker_status_hides_cross_tenant_diagnostics_from_company_admin(monkeypatch):
    import os
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "latus_worker_tests")
    os.environ.setdefault("CORS_ORIGINS", "*")
    import server

    class LeaseLookup:
        async def find_one(self, query, projection):
            return {
                "status": "degraded",
                "owner": "worker-secret",
                "last_error": "org_b failed",
                "last_result": {"failures": [{"organization_id": "org_b"}]},
                "last_completed_at": "2026-08-13T12:00:00+00:00",
            }

    monkeypatch.setattr(server, "_raw_collection", lambda name: LeaseLookup())
    company_admin = server.User(
        user_id="admin_a", email="admin@empresa.test", name="Admin",
        role="admin", is_platform_admin=False,
    )
    platform_admin = company_admin.model_copy(update={"is_platform_admin": True})

    company_view = run(server.automation_worker_status(company_admin))
    platform_view = run(server.automation_worker_status(platform_admin))

    assert company_view["status"] == "degraded"
    assert "owner" not in company_view
    assert "last_error" not in company_view
    assert "last_result" not in company_view
    assert platform_view["owner"] == "worker-secret"
    assert platform_view["last_result"]["failures"][0]["organization_id"] == "org_b"


def test_task_scan_creates_deduplicated_due_notifications(monkeypatch):
    import server

    now = datetime.now(timezone.utc)
    tasks = [
        {
            "id": "task_overdue",
            "title": "Confirmar turno",
            "status": "todo",
            "assigned_to": "user_a",
            "due_date": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "id": "task_soon",
            "title": "Llamar a la clienta",
            "status": "todo",
            "assigned_to": "user_b",
            "due_date": (now + timedelta(hours=2)).isoformat(),
        },
        {
            "id": "task_done",
            "title": "Tarea terminada",
            "status": "done",
            "assigned_to": "user_a",
            "due_date": (now - timedelta(days=1)).isoformat(),
        },
    ]
    notifications = []

    class Tasks:
        def find(self, query, projection):
            return Cursor(tasks)

    async def notify(*args, **kwargs):
        notifications.append((args, kwargs))

    async def done_statuses():
        return {"done"}

    monkeypatch.setattr(server, "db", SimpleNamespace(tasks=Tasks()))
    monkeypatch.setattr(server, "_notify_target", notify)
    monkeypatch.setattr(server, "get_task_done_statuses", done_statuses)

    result = run(server.scan_task_notifications())

    assert result == {"overdue": 1, "due_soon": 1}
    assert [call[0][1] for call in notifications] == ["overdue_task", "task_due_soon"]
    assert notifications[0][1]["dedupe_key"].startswith("overdue_task:task_overdue:")
    assert notifications[1][1]["dedupe_key"].startswith("task_due_soon:task_soon:")
