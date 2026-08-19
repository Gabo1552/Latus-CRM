"""Reliable orchestration for Latus CRM scheduled automation.

The web process and the worker can share the same domain functions, but only
one process may claim a scheduled cycle.  A Mongo lease prevents duplicate
execution when Railway restarts or scales the worker horizontally.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


logger = logging.getLogger(__name__)

AUTOMATION_LEASE_ID = "scheduled_automations"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def worker_identity() -> str:
    configured = (os.environ.get("AUTOMATION_WORKER_ID") or "").strip()
    return configured or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def summarize_cycle(result: dict) -> dict:
    """Keep the operational lease compact even with thousands of tenants."""
    failed: list[dict] = []
    jobs_total = jobs_ok = 0
    for organization_id, jobs in (result.get("organizations") or {}).items():
        for name, job in jobs.items():
            jobs_total += 1
            if job.get("ok"):
                jobs_ok += 1
            elif len(failed) < 50:
                failed.append({
                    "organization_id": organization_id,
                    "job": name,
                    "error": job.get("error"),
                })
    platform_jobs = result.get("platform") or {}
    for name, job in platform_jobs.items():
        jobs_total += 1
        if job.get("ok"):
            jobs_ok += 1
        elif len(failed) < 50:
            failed.append({"organization_id": None, "job": name, "error": job.get("error")})
    return {
        "ok": bool(result.get("ok")),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "organizations_count": len(result.get("organizations") or {}),
        "jobs_total": jobs_total,
        "jobs_ok": jobs_ok,
        "jobs_failed": jobs_total - jobs_ok,
        "failures": failed,
    }


async def ensure_worker_indexes(runtime) -> None:
    leases = runtime._raw_collection("automation_leases")
    await leases.create_index("next_run_at", name="ix_automation_leases_next_run")
    await leases.create_index("locked_until", name="ix_automation_leases_locked_until")


async def claim_scheduled_cycle(
    collection,
    *,
    owner: str,
    interval_seconds: int = 300,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> dict | None:
    """Atomically claim the next due cycle or return ``None``.

    ``next_run_at`` prevents a second replica from running immediately after a
    successful release. ``locked_until`` permits recovery after a crash.
    """
    current = now or _utcnow()
    query = {
        "_id": AUTOMATION_LEASE_ID,
        "$and": [
            {"$or": [
                {"next_run_at": {"$lte": current}},
                {"next_run_at": {"$exists": False}},
            ]},
            {"$or": [
                {"locked_until": {"$lte": current}},
                {"locked_until": {"$exists": False}},
            ]},
        ],
    }
    update = {
        "$set": {
            "owner": owner,
            "status": "running",
            "started_at": current,
            "locked_until": current + timedelta(seconds=max(30, lease_seconds)),
            "next_run_at": current + timedelta(seconds=max(10, interval_seconds)),
            "updated_at": current,
        },
        "$inc": {"attempts": 1},
    }
    try:
        return await collection.find_one_and_update(
            query,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # The document exists but another worker owns it or it is not due.
        return None


async def finish_scheduled_cycle(
    collection,
    *,
    owner: str,
    result: dict,
    now: datetime | None = None,
) -> None:
    current = now or _utcnow()
    summary = summarize_cycle(result)
    status_fields = (
        {"status": "idle", "last_succeeded_at": current, "last_error": None}
        if summary["ok"] else {
            "status": "degraded",
            "last_failed_at": current,
            "last_error": f"{summary['jobs_failed']} automatización(es) fallaron",
        }
    )
    await collection.update_one(
        {"_id": AUTOMATION_LEASE_ID, "owner": owner, "status": "running"},
        {"$set": {
            **status_fields,
            "last_completed_at": current,
            "last_result": summary,
            "locked_until": current,
            "updated_at": current,
        }},
    )


async def renew_scheduled_cycle(
    collection,
    *,
    owner: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> bool:
    """Extend an owned lease while a potentially long cycle is still running."""
    current = now or _utcnow()
    result = await collection.update_one(
        {"_id": AUTOMATION_LEASE_ID, "owner": owner, "status": "running"},
        {"$set": {
            "locked_until": current + timedelta(seconds=max(30, lease_seconds)),
            "updated_at": current,
        }},
    )
    return getattr(result, "modified_count", 1) == 1


async def _keep_lease_alive(collection, *, owner: str, lease_seconds: int) -> None:
    heartbeat_seconds = max(5, min(60, max(30, lease_seconds) // 3))
    while True:
        await asyncio.sleep(heartbeat_seconds)
        if not await renew_scheduled_cycle(
            collection, owner=owner, lease_seconds=lease_seconds,
        ):
            logger.warning("automation lease heartbeat lost owner=%s", owner)
            return


async def fail_scheduled_cycle(
    collection,
    *,
    owner: str,
    error: Exception,
    retry_seconds: int = 60,
    now: datetime | None = None,
) -> None:
    current = now or _utcnow()
    await collection.update_one(
        {"_id": AUTOMATION_LEASE_ID, "owner": owner, "status": "running"},
        {"$set": {
            "status": "error",
            "last_failed_at": current,
            "last_error": str(error)[:1000],
            "locked_until": current,
            "next_run_at": current + timedelta(seconds=max(10, retry_seconds)),
            "updated_at": current,
        }},
    )


async def _run_org_job(runtime, organization_id: str, name: str, operation) -> dict:
    try:
        value = await operation()
        return {"ok": True, "result": value}
    except Exception as exc:  # pragma: no cover - individual failures are logged and isolated
        logger.exception("automation job failed org=%s job=%s", organization_id, name)
        return {"ok": False, "error": str(exc)[:500]}


async def run_automation_cycle(runtime=None) -> dict:
    """Run every tenant automation once, isolating failures by job and tenant."""
    if runtime is None:
        import server as runtime  # lazy import avoids circular imports

    organizations = await runtime._raw_collection("organizations").find(
        {
            "status": "active",
            "is_demo": {"$ne": True},
            "automation_enabled": {"$ne": False},
        },
        {"organization_id": 1, "_id": 0},
    ).to_list(10_000)
    output: dict[str, Any] = {
        "started_at": _utcnow().isoformat(),
        "organizations": {},
        "platform": {},
    }

    for organization in organizations:
        organization_id = organization.get("organization_id")
        if not organization_id:
            continue
        token = runtime.set_organization_id(organization_id)
        try:
            jobs = {
                "lead_no_response": runtime.scan_lead_no_response,
                "task_notifications": runtime.scan_task_notifications,
                "inactive_conversations": lambda: runtime.close_inactive_conversations(runtime.db),
                "scheduled_reports": runtime.check_and_send_scheduled_reports,
                "appointment_reminders": runtime.send_due_appointment_reminders,
            }
            results = {}
            for name, operation in jobs.items():
                results[name] = await _run_org_job(runtime, organization_id, name, operation)
            output["organizations"][organization_id] = results
        finally:
            runtime.reset_organization_id(token)

    try:
        output["platform"]["ai_settlements"] = {
            "ok": True,
            "result": await runtime.process_due_ai_settlements(),
        }
    except Exception as exc:  # pragma: no cover - logged for operational recovery
        logger.exception("scheduled AI billing settlement scan failed")
        output["platform"]["ai_settlements"] = {"ok": False, "error": str(exc)[:500]}

    output["finished_at"] = _utcnow().isoformat()
    output["ok"] = all(
        job.get("ok")
        for organization in output["organizations"].values()
        for job in organization.values()
    ) and output["platform"]["ai_settlements"].get("ok", False)
    return output


async def run_scheduled_cycle(
    runtime=None,
    *,
    owner: str | None = None,
    interval_seconds: int = 300,
    lease_seconds: int = 900,
) -> dict:
    """Claim and execute a cycle. Safe to call frequently from many replicas."""
    if runtime is None:
        import server as runtime
    leases = runtime._raw_collection("automation_leases")
    worker = owner or worker_identity()
    claim = await claim_scheduled_cycle(
        leases,
        owner=worker,
        interval_seconds=interval_seconds,
        lease_seconds=lease_seconds,
    )
    if not claim:
        return {"claimed": False, "reason": "not_due_or_claimed"}
    heartbeat = asyncio.create_task(
        _keep_lease_alive(leases, owner=worker, lease_seconds=lease_seconds)
    )
    try:
        result = await run_automation_cycle(runtime)
        await finish_scheduled_cycle(leases, owner=worker, result=result)
        return {"claimed": True, **result}
    except Exception as exc:
        await fail_scheduled_cycle(leases, owner=worker, error=exc)
        raise
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
