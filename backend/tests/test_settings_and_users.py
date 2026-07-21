"""Tests for Configuración: users CRUD, local auth, viewer guard, WhatsApp config."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_settings_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")


# ---- run helper -----------------------------------------------------------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- fake DB --------------------------------------------------------------

def _matches(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            elif "$exists" in v:
                exists = k in doc and doc.get(k) is not None
                if v["$exists"] != exists:
                    return False
            elif "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
            elif "$regex" in v:
                import re
                rx = re.compile(v["$regex"], re.IGNORECASE if v.get("$options") == "i" else 0)
                if not rx.search(str(doc.get(k, ""))):
                    return False
            else:
                return False
        elif doc.get(k) != v:
            return False
    return True


def _query_matches(doc, query):
    if "$or" in query:
        return any(_matches(doc, sub) for sub in query["$or"])
    return _matches(doc, query)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            self._docs.sort(key=lambda d: d.get(key_or_list, "") or "", reverse=(direction == -1))
        return self

    async def to_list(self, n=None):
        return list(self._docs if n is None else self._docs[:n])


class _Coll:
    def __init__(self):
        self.docs = []

    def find(self, query=None, projection=None):
        q = query or {}
        return _Cursor([d for d in self.docs if _query_matches(d, q)])

    async def find_one(self, query, projection=None, sort=None):
        for d in self.docs:
            if _query_matches(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _query_matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                if "$unset" in update:
                    for k in update["$unset"]:
                        d.pop(k, None)
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = (d.get(k) or 0) + v
                return
        if upsert:
            new = {k: v for k, v in (query or {}).items() if not isinstance(v, dict)}
            if "$set" in update:
                new.update(update["$set"])
            self.docs.append(new)

    async def update_many(self, *_a, **_k):
        pass

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _query_matches(d, query):
                self.docs.pop(i)
                return

    async def delete_many(self, query):
        self.docs[:] = [d for d in self.docs if not _query_matches(d, query)]

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _query_matches(d, query))

    async def create_index(self, *_a, **_k):
        return "idx"


class _FakeDB:
    def __init__(self):
        for name in ("users", "user_sessions", "contacts", "leads", "conversations",
                     "messages", "notifications", "settings", "wa_status",
                     "whatsapp_events", "app_secrets", "tasks", "notes", "bot_events",
                     "password_reset_tokens"):
            setattr(self, name, _Coll())
        for name in ("organizations", "memberships", "whatsapp_routes"):
            setattr(self, name, _Coll())
        for name in ("billing_requests", "billing_events"):
            setattr(self, name, _Coll())


# ---- fixtures -------------------------------------------------------------

@pytest.fixture
def srv(monkeypatch):
    # force re-import so module-level state is fresh
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith("whatsapp") or mod.startswith("utils"):
            sys.modules.pop(mod, None)
    import server  # type: ignore
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

    # seed an admin + session
    _run(fake.users.insert_one({
        "user_id": "u_admin", "email": "admin@latus.test", "name": "Admin",
        "role": "admin", "active": True, "auth_provider": "google",
        "created_at": "2025-01-01T00:00:00+00:00",
    }))
    _run(fake.user_sessions.insert_one({
        "user_id": "u_admin", "session_token": "T-ADMIN",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "created_at": "2025-01-01T00:00:00+00:00",
    }))

    client = TestClient(server.app)
    return server, fake, client


def _h(token="T-ADMIN"):
    return {"Authorization": f"Bearer {token}"}


# ====================================================================
# Users CRUD
# ====================================================================
class TestUsersCRUD:
    def test_create_local_then_login(self, srv):
        server, fake, client = srv
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "agente@latus.test", "name": "Agente Uno",
            "role": "agent", "auth_provider": "local", "password": "Hola1234",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == "agente@latus.test"
        assert body["role"] == "agent"
        assert body["has_password"] is True
        assert "password_hash" not in body

        # Login locally
        r = client.post("/api/auth/login", json={
            "email": "agente@latus.test", "password": "Hola1234",
        })
        assert r.status_code == 200, r.text
        # Wrong password -> 401
        r2 = client.post("/api/auth/login", json={
            "email": "agente@latus.test", "password": "Wrong123",
        })
        assert r2.status_code == 401

    def test_create_google_preapproves_email(self, srv):
        server, fake, client = srv
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "google@latus.test", "name": "Google User",
            "role": "supervisor", "auth_provider": "google",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["auth_provider"] == "google"
        assert body["has_password"] is False

    def test_duplicate_email_409(self, srv):
        server, fake, client = srv
        client.post("/api/admin/users", headers=_h(), json={
            "email": "dup@latus.test", "name": "Dup", "role": "agent",
            "auth_provider": "local", "password": "Hola1234",
        })
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "dup@latus.test", "name": "Dup2", "role": "agent",
            "auth_provider": "local", "password": "Hola1234",
        })
        assert r.status_code == 409

    def test_invalid_password_rejected(self, srv):
        server, fake, client = srv
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "weak@latus.test", "name": "Weak", "role": "agent",
            "auth_provider": "local", "password": "short",
        })
        assert r.status_code == 400

    def test_delete_self_400(self, srv):
        server, fake, client = srv
        r = client.delete("/api/admin/users/u_admin", headers=_h())
        assert r.status_code == 400

    def test_delete_last_admin_400(self, srv):
        server, fake, client = srv
        # Add a viewer + try to delete the only admin
        client.post("/api/admin/users", headers=_h(), json={
            "email": "v@latus.test", "name": "V", "role": "viewer",
            "auth_provider": "google",
        })
        # Make the viewer logged-in as admin actor "u_admin" not allowed to remove self.
        # The actual test for "last admin": create another admin then delete original
        client.post("/api/admin/users", headers=_h(), json={
            "email": "a2@latus.test", "name": "Admin 2", "role": "admin",
            "auth_provider": "google",
        })
        # Now we have 2 admins, deactivate u_admin via API impossible (deleting self) —
        # so simulate: deactivate the OTHER admin first then try to delete it -> still
        # one admin alive -> deletion of the other admin should be 400
        # Find admin2 id
        others = [u for u in fake.users.docs if u["email"] == "a2@latus.test"]
        a2_id = others[0]["user_id"]
        # Deactivate admin2
        r = client.post(f"/api/admin/users/{a2_id}/deactivate", headers=_h())
        assert r.status_code == 200
        # Now u_admin is the last active admin. Try to delete a2 (inactive admin) — that's fine.
        # But trying to deactivate u_admin (self) -> 400
        r = client.post("/api/admin/users/u_admin/deactivate", headers=_h())
        assert r.status_code == 400

    def test_soft_delete_hidden_by_default(self, srv):
        server, fake, client = srv
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "byebye@latus.test", "name": "BB", "role": "agent",
            "auth_provider": "google",
        })
        uid = r.json()["user_id"]
        client.delete(f"/api/admin/users/{uid}", headers=_h())
        r = client.get("/api/admin/users", headers=_h())
        emails = {u["email"] for u in r.json()}
        assert "byebye@latus.test" not in emails
        r = client.get("/api/admin/users?include_inactive=true", headers=_h())
        emails = {u["email"] for u in r.json()}
        assert "byebye@latus.test" in emails

    def test_revived_user_with_deleted_at_null_appears_in_listing(self, srv):
        """Regression: a doc revived in Mongo with ``deleted_at: null`` (instead of
        unsetting the field) must show up in GET /api/admin/users."""
        server, fake, client = srv
        # Create + soft-delete
        r = client.post("/api/admin/users", headers=_h(), json={
            "email": "revived@latus.test", "name": "Rev",
            "role": "agent", "auth_provider": "google",
        })
        uid = r.json()["user_id"]
        client.delete(f"/api/admin/users/{uid}", headers=_h())
        # Simulate manual revive: deleted_at literally set to null + active=true
        for d in fake.users.docs:
            if d["user_id"] == uid:
                d["deleted_at"] = None
                d["active"] = True
        for membership in fake.memberships.docs:
            if membership.get("user_id") == uid:
                membership["status"] = "active"
        r = client.get("/api/admin/users", headers=_h())
        assert r.status_code == 200
        emails = {u["email"] for u in r.json()}
        assert "revived@latus.test" in emails, (
            f"revived user not visible. query results: {emails}. doc: "
            f"{[d for d in fake.users.docs if d['user_id']==uid]}"
        )

    def test_reset_password_returns_temp_once(self, srv):
        server, fake, client = srv
        client.post("/api/admin/users", headers=_h(), json={
            "email": "reset@latus.test", "name": "R", "role": "agent",
            "auth_provider": "local", "password": "Hola1234",
        })
        uid = [u for u in fake.users.docs if u["email"] == "reset@latus.test"][0]["user_id"]
        r = client.post(f"/api/admin/users/{uid}/reset-password", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert "temporary_password" in body
        assert len(body["temporary_password"]) == 12
        # Login with the new temp password
        r2 = client.post("/api/auth/login", json={
            "email": "reset@latus.test", "password": body["temporary_password"],
        })
        assert r2.status_code == 200


# ====================================================================
# Billing and platform licenses
# ====================================================================
class TestBillingFoundation:
    def test_subscription_summary_and_plan_request(self, srv):
        server, fake, client = srv
        summary = client.get("/api/billing/subscription", headers=_h())
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["access"]["allowed"] is True
        assert body["plan"]["code"] in {"base", "starter"}

        plans = client.get("/api/billing/plans", headers=_h())
        assert plans.status_code == 200
        assert {plan["code"] for plan in plans.json()} >= {"starter", "growth", "scale"}

        requested = client.post(
            "/api/billing/plan-requests", headers=_h(),
            json={"plan_code": "growth", "notes": "Necesitamos más usuarios"},
        )
        assert requested.status_code == 200, requested.text
        assert requested.json()["status"] == "pending"
        assert fake.billing_requests.docs[0]["organization_id"] == body["organization"]["organization_id"]

    def test_platform_admin_can_suspend_and_access_is_enforced(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "admin@latus.test")

        me = client.get("/api/auth/me", headers=_h())
        assert me.status_code == 200
        assert me.json()["is_platform_admin"] is True

        current = client.get("/api/organizations/current", headers=_h()).json()
        updated = client.patch(
            f"/api/platform/organizations/{current['organization_id']}/subscription",
            headers=_h(),
            json={
                "subscription_status": "suspended", "license_status": "suspended",
                "internal_notes": "Dato visible solo para plataforma",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["access"]["allowed"] is False
        assert fake.billing_events.docs

        monkeypatch.delenv("PLATFORM_ADMIN_EMAILS")
        blocked = client.get("/api/dashboard/metrics", headers=_h())
        assert blocked.status_code == 402
        assert blocked.json()["detail"]["code"] == "subscription_required"

        billing_still_available = client.get("/api/billing/subscription", headers=_h())
        assert billing_still_available.status_code == 200
        assert "internal_notes" not in billing_still_available.json()["organization"]
        assert "internal_notes" not in client.get("/api/organizations/current", headers=_h()).json()

    def test_mercadopago_checkout_creates_pending_preapproval(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "TEST-secret")
        monkeypatch.setattr(server, "APP_BASE_URL", "https://crm.example.com")
        calls = []

        async def provider_request(method, path, *, payload=None):
            calls.append((method, path, payload))
            return {
                "id": "mp-sub-1",
                "status": "pending",
                "init_point": "https://mercadopago.example/checkout/mp-sub-1",
            }

        monkeypatch.setattr(server, "_mercadopago_request", provider_request)
        response = client.post(
            "/api/billing/checkout",
            headers=_h(),
            json={"plan_code": "growth", "billing_email": "pagos@empresa.com"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["checkout_url"].startswith("https://mercadopago.example/")
        assert calls[0][0:2] == ("POST", "/preapproval")
        assert calls[0][2]["status"] == "pending"
        assert calls[0][2]["auto_recurring"]["currency_id"] == "ARS"
        organization = fake.organizations.docs[0]
        assert organization["provider_preapproval_id"] == "mp-sub-1"
        assert organization["provider_plan_code"] == "growth"

    def test_pending_checkout_changes_plan_without_canceling_preapproval(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "TEST-secret")
        monkeypatch.setattr(server, "APP_BASE_URL", "https://crm.example.com")
        current = client.get("/api/organizations/current", headers=_h()).json()
        _run(fake.organizations.update_one(
            {"organization_id": current["organization_id"]},
            {"$set": {
                "provider_preapproval_id": "mp-pending-1",
                "provider_plan_code": "scale",
                "provider_status": "pending",
            }},
        ))
        calls = []

        async def provider_request(method, path, *, payload=None):
            calls.append((method, path, payload))
            return {
                "id": "mp-pending-1",
                "status": "pending",
                "init_point": "https://mercadopago.example/checkout/mp-pending-1",
            }

        monkeypatch.setattr(server, "_mercadopago_request", provider_request)
        response = client.post(
            "/api/billing/checkout", headers=_h(),
            json={"plan_code": "starter", "billing_email": "pagos@empresa.com"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["checkout_url"].endswith("mp-pending-1")
        assert response.json()["plan_updated"] is True
        assert [call[0:2] for call in calls] == [
            ("GET", "/preapproval/mp-pending-1"),
            ("PUT", "/preapproval/mp-pending-1"),
        ]
        assert calls[1][2]["status"] == "pending"
        assert calls[1][2]["auto_recurring"]["transaction_amount"] == 45000
        organization = fake.organizations.docs[0]
        assert organization["provider_plan_code"] == "starter"
        assert organization["provider_status"] == "pending"

    def test_mercadopago_webhook_validates_and_activates_license(self, srv, monkeypatch):
        server, fake, client = srv
        secret = "webhook-secret"
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", secret)

        current = client.get("/api/organizations/current", headers=_h()).json()
        _run(fake.organizations.update_one(
            {"organization_id": current["organization_id"]},
            {"$set": {
                "provider_preapproval_id": "mp-sub-1",
                "provider_plan_code": "growth",
                "provider_status": "pending",
            }},
        ))
        provider_calls = []

        async def provider_request(method, path, *, payload=None):
            provider_calls.append((method, path))
            return {
                "id": "mp-sub-1",
                "status": "authorized",
                "external_reference": f"latus:{current['organization_id']}:growth",
                "next_payment_date": "2099-02-01T00:00:00Z",
            }

        monkeypatch.setattr(server, "_mercadopago_request", provider_request)
        request_id = "request-1"
        timestamp = "1704908010"
        manifest = f"id:mp-sub-1;request-id:{request_id};ts:{timestamp};"
        digest = hmac.new(
            secret.encode(), manifest.encode(), hashlib.sha256
        ).hexdigest()
        headers = {
            "x-request-id": request_id,
            "x-signature": f"ts={timestamp},v1={digest}",
        }
        url = "/api/webhooks/mercadopago?type=subscription_preapproval&data.id=mp-sub-1"
        first = client.post(url, headers=headers, json={
            "type": "subscription_preapproval", "data": {"id": "mp-sub-1"},
        })
        assert first.status_code == 200, first.text
        organization = fake.organizations.docs[0]
        assert organization["subscription_status"] == "active"
        assert organization["license_status"] == "active"
        assert organization["plan_code"] == "growth"

        duplicate = client.post(url, headers=headers, json={
            "type": "subscription_preapproval", "data": {"id": "mp-sub-1"},
        })
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True
        assert provider_calls == [("GET", "/preapproval/mp-sub-1")]

    def test_active_subscription_changes_plan_without_duplicate_checkout(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "TEST-secret")
        monkeypatch.setattr(server, "APP_BASE_URL", "https://crm.example.com")
        current = client.get("/api/organizations/current", headers=_h()).json()
        _run(fake.organizations.update_one(
            {"organization_id": current["organization_id"]},
            {"$set": {
                "provider_preapproval_id": "mp-active-1",
                "provider_plan_code": "starter",
                "provider_status": "authorized",
                "subscription_status": "active",
                "license_status": "active",
            }},
        ))
        calls = []

        async def provider_request(method, path, *, payload=None):
            calls.append((method, path, payload))
            return {"id": "mp-active-1", "status": "authorized"}

        monkeypatch.setattr(server, "_mercadopago_request", provider_request)
        response = client.post(
            "/api/billing/checkout", headers=_h(),
            json={"plan_code": "scale", "billing_email": "pagos@empresa.com"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["checkout_url"] is None
        assert response.json()["plan_updated"] is True
        assert len(calls) == 1
        assert calls[0][0:2] == ("PUT", "/preapproval/mp-active-1")
        assert calls[0][2]["auto_recurring"]["transaction_amount"] == 185000
        assert fake.organizations.docs[0]["plan_code"] == "scale"

    def test_mercadopago_webhook_rejects_invalid_signature(self, srv, monkeypatch):
        server, _, client = srv
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "webhook-secret")
        response = client.post(
            "/api/webhooks/mercadopago?type=subscription_preapproval&data.id=mp-sub-1",
            headers={"x-request-id": "bad", "x-signature": "ts=1,v1=invalid"},
            json={"type": "subscription_preapproval", "data": {"id": "mp-sub-1"}},
        )
        assert response.status_code == 401

    def test_customer_can_cancel_automatic_renewal(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("MERCADOPAGO_ACCESS_TOKEN", "TEST-token")
        current = client.get("/api/organizations/current", headers=_h()).json()
        _run(fake.organizations.update_one(
            {"organization_id": current["organization_id"]},
            {"$set": {
                "provider_preapproval_id": "mp-active-1",
                "provider_plan_code": "starter",
                "provider_status": "authorized",
                "subscription_status": "active",
                "license_status": "active",
                "current_period_end": "2099-02-01T00:00:00+00:00",
            }},
        ))

        async def provider_request(method, path, *, payload=None):
            assert (method, path, payload) == (
                "PUT", "/preapproval/mp-active-1", {"status": "canceled"}
            )
            return {"id": "mp-active-1", "status": "canceled"}

        monkeypatch.setattr(server, "_mercadopago_request", provider_request)
        response = client.post("/api/billing/cancel", headers=_h())
        assert response.status_code == 200, response.text
        assert response.json()["organization"]["subscription_status"] == "canceled"
        assert response.json()["access"]["allowed"] is True


# ====================================================================
# Viewer write-guard
# ====================================================================
class TestViewerGuard:
    def _make_viewer(self, fake):
        _run(fake.users.insert_one({
            "user_id": "u_view", "email": "view@latus.test", "name": "V",
            "role": "viewer", "active": True, "auth_provider": "google",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.user_sessions.insert_one({
            "user_id": "u_view", "session_token": "T-VIEW",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))

    def test_viewer_cannot_create_contact(self, srv):
        _, fake, client = srv
        self._make_viewer(fake)
        r = client.post("/api/contacts", headers=_h("T-VIEW"),
                        json={"name": "X", "phone": "+1"})
        assert r.status_code == 403
        assert r.json()["detail"] == "Sin permisos"

    def test_viewer_cannot_simulate_inbound(self, srv):
        _, fake, client = srv
        self._make_viewer(fake)
        _run(fake.conversations.insert_one({
            "id": "cv_1", "contact_id": "ct_1", "status": "open",
            "priority": "medium", "bot_enabled": True,
            "last_message_at": "2025-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00", "unread": 0,
        }))
        r = client.post("/api/conversations/cv_1/simulate-inbound",
                        headers=_h("T-VIEW"))
        assert r.status_code == 403

    def test_viewer_cannot_patch_settings(self, srv):
        _, fake, client = srv
        self._make_viewer(fake)
        r = client.patch("/api/settings", headers=_h("T-VIEW"),
                         json={"lead_no_response_enabled": False})
        assert r.status_code == 403

    def test_viewer_can_read(self, srv):
        _, fake, client = srv
        self._make_viewer(fake)
        r = client.get("/api/contacts", headers=_h("T-VIEW"))
        assert r.status_code == 200

    def test_agent_cannot_access_admin_users(self, srv):
        _, fake, client = srv
        _run(fake.users.insert_one({
            "user_id": "u_ag", "email": "ag@latus.test", "name": "Ag",
            "role": "agent", "active": True, "auth_provider": "google",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.user_sessions.insert_one({
            "user_id": "u_ag", "session_token": "T-AG",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        r = client.get("/api/admin/users", headers=_h("T-AG"))
        assert r.status_code == 403


# ====================================================================
# WhatsApp config (DB+env, encrypted)
# ====================================================================
class TestWhatsAppConfig:
    def test_put_encrypts_get_returns_masked(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "")
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "")
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "")
        r = client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "verify_token": "my-verify-XYZ",
            "access_token": "EAA-supersecret-1234",
            "phone_number_id": "1234567890",
            "api_version": "v21.0",
        })
        assert r.status_code == 200, r.text
        # DB doc must store *_enc fields, never plain
        secrets = fake.app_secrets.docs[0]
        assert "verify_token_enc" in secrets
        assert "verify_token" not in secrets
        # plain values must NOT be on the wire
        full = client.get("/api/admin/whatsapp/config", headers=_h())
        text = full.text
        for plain in ("my-verify-XYZ", "EAA-supersecret-1234"):
            assert plain not in text
        body = full.json()
        assert body["fields"]["verify_token"]["source"] == "db"
        assert body["fields"]["verify_token"]["masked"].endswith("-XYZ")
        assert body["configured"] is True

    def test_put_with_none_clears_back_to_env(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "ENV-TOKEN-ABCD")
        # First set via DB
        client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "access_token": "DB-TOKEN-ZZZZ",
        })
        body = client.get("/api/admin/whatsapp/config", headers=_h()).json()
        assert body["fields"]["access_token"]["source"] == "db"
        # Now clear with explicit null
        client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "access_token": None,
        })
        body = client.get("/api/admin/whatsapp/config", headers=_h()).json()
        assert body["fields"]["access_token"]["source"] == "env"
        assert body["fields"]["access_token"]["masked"].endswith("ABCD")

    def test_rotate_verify_token(self, srv):
        server, fake, client = srv
        r = client.post("/api/admin/whatsapp/rotate-verify-token", headers=_h())
        assert r.status_code == 200
        new = r.json()["verify_token"]
        assert len(new) >= 24
        body = client.get("/api/admin/whatsapp/config", headers=_h()).json()
        assert body["fields"]["verify_token"]["source"] == "db"
        # New value should round-trip through GET verify endpoint
        r2 = client.get("/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe",
            "hub.verify_token": new,
            "hub.challenge": "OK",
        })
        assert r2.status_code == 200
        assert r2.text == "OK"

    def test_test_connection_success(self, srv, monkeypatch):
        server, fake, client = srv
        client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "access_token": "TOK",
            "phone_number_id": "PNI-1234",
            "verify_token": "v",
        })

        async def fake_get(self, url, headers=None, **kwargs):
            return httpx.Response(
                200,
                json={"display_phone_number": "+54 11 5555-7777", "verified_name": "Latus Demo"},
                request=httpx.Request("GET", url),
            )

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            r = client.post("/api/admin/whatsapp/test-connection", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["display_phone_number"] == "+54 11 5555-7777"
        assert body["verified_name"] == "Latus Demo"

    def test_test_connection_meta_401(self, srv):
        server, fake, client = srv
        client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "access_token": "TOK", "phone_number_id": "PNI", "verify_token": "v",
        })

        async def fake_get(self, url, headers=None, **kwargs):
            return httpx.Response(
                401, json={"error": {"code": 190, "message": "Invalid OAuth"}},
                request=httpx.Request("GET", url),
            )

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            r = client.post("/api/admin/whatsapp/test-connection", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error_code"] == 190
        assert "Invalid OAuth" in body["error_message"]

    def test_test_connection_503_when_unconfigured(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "")
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "")
        r = client.post("/api/admin/whatsapp/test-connection", headers=_h())
        assert r.status_code == 503

    def test_put_503_when_encryption_key_missing(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("APP_ENCRYPTION_KEY", "")
        # invalidate cached fernet
        from utils import crypto as cryptomod
        cryptomod._cached_fernet = None
        cryptomod._cached_key = None
        r = client.put("/api/admin/whatsapp/config", headers=_h(), json={
            "verify_token": "x",
        })
        assert r.status_code == 503
        assert "APP_ENCRYPTION_KEY" in r.json()["detail"]


# ====================================================================
# webhook_url derivation
# ====================================================================
class TestWebhookUrl:
    def test_public_base_url_wins_over_headers(self, srv, monkeypatch):
        _, _, client = srv
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://prod.example.com")
        r = client.get(
            "/api/admin/whatsapp/config",
            headers={
                **_h(),
                "host": "internal.cluster-8.preview.latuscf.cloud",
                "x-forwarded-host": "internal.cluster-8.preview.latuscf.cloud",
                "x-forwarded-proto": "http",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["webhook_url"].startswith(
            "https://prod.example.com/api/webhooks/whatsapp?organization_id="
        )
        assert "webhook_url_warning" not in body

    def test_internal_cluster_host_rejected_with_warning(self, srv, monkeypatch):
        _, _, client = srv
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        r = client.get(
            "/api/admin/whatsapp/config",
            headers={
                **_h(),
                "host": "lead-scan-scheduler.cluster-8.preview.latuscf.cloud",
                "x-forwarded-host": "lead-scan-scheduler.cluster-8.preview.latuscf.cloud",
                "x-forwarded-proto": "http",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["webhook_url"] == ""
        assert "PUBLIC_BASE_URL" in body.get("webhook_url_warning", "")

    def test_https_forced_when_header_says_http(self, srv, monkeypatch):
        _, _, client = srv
        monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        r = client.get(
            "/api/admin/whatsapp/config",
            headers={
                **_h(),
                "host": "lead-scan-scheduler.preview.latusagent.com",
                "x-forwarded-host": "lead-scan-scheduler.preview.latusagent.com",
                "x-forwarded-proto": "http",  # lying upstream
            },
        )
        body = r.json()
        assert body["webhook_url"].startswith("https://lead-scan-scheduler.preview.latusagent.com/")


# ====================================================================
# test-webhook-verify
# ====================================================================
class TestWebhookSelfTest:
    def _seed_verify_token(self, fake, value: str = "my-verify-XYZ"):
        from utils.crypto import encrypt
        _run(fake.app_secrets.update_one(
            {"_id": "whatsapp"},
            {"$set": {"_id": "whatsapp", "verify_token_enc": encrypt(value)}},
            upsert=True,
        ))

    def test_success_when_verify_token_matches(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://prod.example.com")
        self._seed_verify_token(fake, "TOKEN-OK")

        captured = {}

        async def fake_get(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params or {}
            # Simulate our own webhook GET handler succeeding: echo challenge
            return httpx.Response(
                200, text=params["hub.challenge"],
                request=httpx.Request("GET", url),
            )

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            r = client.post("/api/admin/whatsapp/test-webhook-verify", headers=_h())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["status"] == 200
        assert body["webhook_url"].startswith(
            "https://prod.example.com/api/webhooks/whatsapp?organization_id="
        )
        assert body["echoed_challenge"].startswith("ping-")
        # Sanity: real verify token must not leak into the response
        assert "TOKEN-OK" not in r.text
        # And the actual GET targeted the right URL with the configured token
        assert captured["url"].startswith(
            "https://prod.example.com/api/webhooks/whatsapp?organization_id="
        )
        assert captured["params"]["hub.verify_token"] == "TOKEN-OK"

    def test_failure_returns_verify_token_mismatch(self, srv, monkeypatch):
        server, fake, client = srv
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://prod.example.com")
        self._seed_verify_token(fake, "TOKEN-MISMATCH")

        async def fake_get(self, url, params=None, **kwargs):
            return httpx.Response(403, text="forbidden",
                                  request=httpx.Request("GET", url))

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            r = client.post("/api/admin/whatsapp/test-webhook-verify", headers=_h())
        body = r.json()
        assert body["ok"] is False
        assert body["status"] == 403
        assert body["detail"] == "verify_token mismatch"
        # masked token shown, never the raw one
        assert "TOKEN-MISMATCH" not in r.text
        assert body["configured_verify_token_masked"].endswith("ATCH")


# ====================================================================
# CRM Colors configuration (Task Statuses & Catalog Categories)
# ====================================================================
class TestCRMColorsConfig:
    def test_get_and_patch_crm_colors(self, srv):
        server, fake, client = srv
        
        # 1. Fetch default settings
        r = client.get("/api/settings", headers=_h())
        assert r.status_code == 200
        body = r.json()
        assert "task_statuses" in body
        assert "catalog_categories" in body
        assert "catalog_category_colors" in body
        assert body["catalog_category_colors"] == {}
        
        # Check task statuses defaults do not have colors initially
        for status in body["task_statuses"]:
            assert "color" not in status

        # 2. Patch custom colors
        custom_statuses = [
            {"key": "todo", "label": "Pendiente", "is_done": False, "color": "#FF4500"},
            {"key": "done", "label": "Completada", "is_done": True, "color": "#064E3B", "bg": "#ECFDF5"}
        ]
        custom_categories = ["Electrónica", "Ropa"]
        custom_category_colors = {
            "Electrónica": "#FF5733",
            "Ropa": "#33FF57"
        }
        
        r_patch = client.patch("/api/admin/settings", headers=_h(), json={
            "task_statuses": custom_statuses,
            "catalog_categories": custom_categories,
            "catalog_category_colors": custom_category_colors
        })
        assert r_patch.status_code == 200, r_patch.text
        
        # 3. Verify changes persist
        r_verify = client.get("/api/settings", headers=_h())
        assert r_verify.status_code == 200
        body_verify = r_verify.json()
        
        assert body_verify["catalog_category_colors"] == custom_category_colors
        assert body_verify["catalog_categories"] == ["Electrónica", "Ropa"]
        
        statuses = body_verify["task_statuses"]
        assert len(statuses) == 2
        assert statuses[0]["key"] == "todo"
        assert statuses[0]["color"] == "#FF4500"
        assert "bg" not in statuses[0] # not sent
        
        assert statuses[1]["key"] == "done"
        assert statuses[1]["color"] == "#064E3B"
        assert statuses[1]["bg"] == "#ECFDF5"


# ====================================================================
# Dashboard Date Filters & Contact Update
# ====================================================================
class TestDashboardDateFilters:
    def test_contact_lead_source_patch_and_dashboard_metrics(self, srv):
        server, fake, client = srv
        
        # 1. Create a contact via API (which also creates a linked lead)
        r_create = client.post("/api/contacts", headers=_h(), json={
            "name": "Test Contact",
            "phone": "+1234567890",
            "email": "test@contact.com"
        })
        assert r_create.status_code == 200, r_create.text
        contact_id = r_create.json()["id"]
        
        # 2. Patch contact lead source
        r_patch = client.patch(f"/api/contacts/{contact_id}", headers=_h(), json={
            "lead_source": "Meta Ads"
        })
        assert r_patch.status_code == 200, r_patch.text
        assert r_patch.json()["lead_source"] == "Meta Ads"
        
        # 3. Get contact to verify persistence
        r_get = client.get(f"/api/contacts/{contact_id}", headers=_h())
        assert r_get.status_code == 200
        assert r_get.json()["lead_source"] == "Meta Ads"
        
        # 4. Fetch dashboard metrics with query params (current & comparison)
        r_dash = client.get("/api/dashboard/metrics", headers=_h(), params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-15",
            "compare_start_date": "2026-05-15",
            "compare_end_date": "2026-05-31"
        })
        assert r_dash.status_code == 200, r_dash.text
        body = r_dash.json()
        
        assert "total_leads" in body
        assert "leads_trend" in body
        assert "leads_by_source" in body
        assert "comparison" in body
        
        comp = body["comparison"]
        assert comp is not None
        assert "total_leads" in comp
        assert "leads_trend" in comp
        assert "leads_by_source" in comp
