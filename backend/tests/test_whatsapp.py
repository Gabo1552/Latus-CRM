"""Unit tests for the WhatsApp Cloud API integration.

These are in-process tests:
  * import server.py directly with a fake DB
  * use fastapi.testclient to exercise the webhook + send endpoints
  * mock httpx.AsyncClient.post so we never hit Meta in tests

Covers:
  * GET webhook verify_token ok/ko
  * POST webhook bad signature
  * POST webhook text inbound -> contact + conversation + message created,
    dedup on repeat, notification fan-out (assigned vs admin fallback)
  * POST webhook statuses (delivered/read/failed)
  * POST /api/conversations/{id}/send-whatsapp success/error/not-configured
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# ---- sys.path / env so server imports cleanly -----------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_wa_tests")
os.environ.setdefault("CORS_ORIGINS", "*")


# ---- Fake Mongo collection -----------------------------------------------

class _Cursor:
    def __init__(self, docs, sort_key=None, reverse=False):
        self._docs = list(docs)
        if sort_key:
            self._docs.sort(key=lambda d: d.get(sort_key, ""), reverse=reverse)

    def sort(self, key_or_list, direction=None):
        # support both .sort("field", -1) and .sort([("field", -1)])
        if isinstance(key_or_list, str):
            self._docs.sort(key=lambda d: d.get(key_or_list, ""), reverse=(direction == -1))
        else:
            for k, d in reversed(key_or_list):
                self._docs.sort(key=lambda doc, k=k: doc.get(k, ""), reverse=(d == -1))
        return self

    async def to_list(self, n=None):
        return list(self._docs if n is None else self._docs[:n])


def _matches(doc: dict, query: dict) -> bool:
    for k, v in query.items():
        if isinstance(v, dict):
            if "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            else:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Collection:
    def __init__(self):
        self.docs: list[dict] = []
        self._unique_indexes: list[str] = []

    # --- crud ---
    def find(self, query=None, projection=None):
        query = query or {}
        return _Cursor([d for d in self.docs if _matches(d, query)])

    async def find_one(self, query=None, projection=None, sort=None):
        items = [d for d in self.docs if _matches(d, query or {})]
        if sort:
            for k, d in reversed(sort):
                items.sort(key=lambda doc, k=k: doc.get(k, ""), reverse=(d == -1))
        return dict(items[0]) if items else None

    async def insert_one(self, doc):
        d = dict(doc)
        for k in self._unique_indexes:
            if d.get(k) is not None:
                for existing in self.docs:
                    if existing.get(k) == d[k]:
                        raise Exception("duplicate key: " + k)
        self.docs.append(d)
        return d

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                if "$set" in update:
                    d.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = (d.get(k) or 0) + v
                return MagicMock(matched_count=1)
        if upsert:
            new = dict(query)
            if "$set" in update:
                new.update(update["$set"])
            self.docs.append(new)
        return MagicMock(matched_count=0)

    async def update_many(self, *_a, **_k):
        return MagicMock()

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _matches(d, query))

    async def delete_one(self, *_a, **_k):
        return MagicMock()

    async def create_index(self, key, unique=False, sparse=False, name=None):  # noqa: D401
        if unique:
            if isinstance(key, str):
                self._unique_indexes.append(key)
        return name or "idx"


class _FakeDB:
    def __init__(self):
        self.users = _Collection()
        self.contacts = _Collection()
        self.leads = _Collection()
        self.conversations = _Collection()
        self.messages = _Collection()
        self.notifications = _Collection()
        self.settings = _Collection()
        self.user_sessions = _Collection()
        self.wa_status = _Collection()
        self.whatsapp_events = _Collection()
        self.bot_events = _Collection()
        self.notes = _Collection()
        self.tasks = _Collection()


def _run(coro):
    """Run a coroutine in a fresh event loop (compat with pytest reordering)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- fixtures -------------------------------------------------------------

@pytest.fixture
def app_env(monkeypatch):
    """Set WhatsApp env vars for tests + return the verify_token used."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "EAA-test")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setenv("WHATSAPP_API_VERSION", "v21.0")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "WABA-1")
    return {
        "verify_token": "test-verify",
        "app_secret": "test-app-secret",
        "phone_number_id": "1234567890",
    }


@pytest.fixture
def server_and_client(app_env, monkeypatch):
    # Fresh import to ensure clean module-level state.
    import importlib
    if "server" in sys.modules:
        del sys.modules["server"]
    import server  # type: ignore

    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    # Disable the scheduler in tests
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

    # Seed an admin user + session
    _run(fake.users.insert_one({
        "user_id": "user_admin_test", "email": "admin@test", "name": "Admin Test",
        "role": "admin", "active": True, "created_at": "2025-01-01T00:00:00+00:00",
    }))
    _run(fake.user_sessions.insert_one({
        "user_id": "user_admin_test", "session_token": "T-ADMIN",
        "expires_at": "2099-01-01T00:00:00+00:00", "created_at": "2025-01-01T00:00:00+00:00",
    }))

    client = TestClient(server.app)
    return server, fake, client


# ---- helpers --------------------------------------------------------------

def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _inbound_payload(*, msg_id: str = "wamid.TEST1", text: str = "hola",
                     wa_id: str = "5491155551234", phone_number_id: str = "1234567890",
                     ts: int = 1700000000) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA-1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "+1", "phone_number_id": phone_number_id},
                    "contacts": [{"profile": {"name": "Ana Test"}, "wa_id": wa_id}],
                    "messages": [{
                        "from": wa_id,
                        "id": msg_id,
                        "timestamp": str(ts),
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def _status_payload(*, msg_id: str, status: str = "delivered",
                    error_code: int | None = None,
                    error_msg: str = "") -> dict:
    st: dict = {"id": msg_id, "status": status, "timestamp": "1700000050",
                "recipient_id": "5491155551234"}
    if status == "failed":
        st["errors"] = [{"code": error_code or 131000, "message": error_msg or "error"}]
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "1234567890"},
            "statuses": [st],
        }}]}],
    }


# ====================================================================
# GET verify
# ====================================================================
class TestWebhookVerify:
    def test_ok(self, server_and_client):
        _, _, client = server_and_client
        r = client.get("/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify",
            "hub.challenge": "ECHO-123",
        })
        assert r.status_code == 200
        assert r.text == "ECHO-123"
        assert "text/plain" in r.headers.get("content-type", "")

    def test_wrong_token(self, server_and_client):
        _, _, client = server_and_client
        r = client.get("/api/webhooks/whatsapp", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG",
            "hub.challenge": "X",
        })
        assert r.status_code == 403


# ====================================================================
# POST signature
# ====================================================================
class TestWebhookSignature:
    def test_invalid_signature_returns_403(self, server_and_client):
        _, _, client = server_and_client
        body = json.dumps(_inbound_payload()).encode()
        r = client.post(
            "/api/webhooks/whatsapp", content=body,
            headers={"Content-Type": "application/json",
                     "X-Hub-Signature-256": "sha256=00deadbeef"},
        )
        assert r.status_code == 403

    def test_valid_signature_accepted(self, server_and_client):
        _, _, client = server_and_client
        body = json.dumps(_inbound_payload()).encode()
        sig = _sign("test-app-secret", body)
        r = client.post(
            "/api/webhooks/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert r.status_code == 200


# ====================================================================
# Inbound text message
# ====================================================================
class TestInboundText:
    def _post(self, client, payload):
        body = json.dumps(payload).encode()
        sig = _sign("test-app-secret", body)
        return client.post(
            "/api/webhooks/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )

    def test_creates_contact_conv_message_and_notification(self, server_and_client):
        server, fake, client = server_and_client
        # No agent assigned -> notification falls back to admin/supervisor
        r = self._post(client, _inbound_payload(msg_id="wamid.A1"))
        assert r.status_code == 200

        assert len(fake.contacts.docs) == 1
        c = fake.contacts.docs[0]
        assert c["whatsapp_id"] == "5491155551234"
        assert c["name"] == "Ana Test"

        assert len(fake.conversations.docs) == 1
        conv = fake.conversations.docs[0]
        assert conv["channel"] == "whatsapp"
        assert conv["channel_external_id"].startswith("1234567890:")

        msgs = fake.messages.docs
        assert len(msgs) == 1
        m = msgs[0]
        assert m["external_message_id"] == "wamid.A1"
        assert m["sender_type"] == "contact"
        assert m["direction"] == "inbound"
        assert m["body"] == "hola"

        # admin fallback notification (no agent assigned)
        notifs = [n for n in fake.notifications.docs if n["type"] == "new_message"]
        assert len(notifs) == 1
        assert notifs[0]["assigned_user_id"] == "user_admin_test"

        # wa_status_doc gets a last_webhook_at
        assert fake.wa_status.docs and fake.wa_status.docs[0].get("last_webhook_at")

    def test_idempotent_same_message_id(self, server_and_client):
        server, fake, client = server_and_client
        r1 = self._post(client, _inbound_payload(msg_id="wamid.DUP", text="hola"))
        r2 = self._post(client, _inbound_payload(msg_id="wamid.DUP", text="hola"))
        assert r1.status_code == 200 and r2.status_code == 200
        assert len(fake.messages.docs) == 1
        assert len(fake.contacts.docs) == 1
        # only one notification (existing logic dedups via _make_notification too)
        assert len([n for n in fake.notifications.docs if n["type"] == "new_message"]) == 1

    def test_assigned_agent_gets_notification(self, server_and_client):
        server, fake, client = server_and_client
        # Pre-create an agent + a contact + conv assigned to them
        agent = {"user_id": "user_agent_A", "email": "a@x", "name": "Agente A",
                 "role": "sales_agent", "active": True,
                 "created_at": "2025-01-01T00:00:00+00:00"}
        _run(fake.users.insert_one(agent))

        contact = {"id": "ct_X", "name": "Old Name", "phone": "+5491155551234",
                   "whatsapp_id": "5491155551234", "created_at": "2025-01-01T00:00:00+00:00"}
        _run(fake.contacts.insert_one(contact))
        conv = {"id": "cv_X", "contact_id": "ct_X", "lead_id": None,
                "status": "open", "priority": "medium", "bot_enabled": True,
                "assigned_to": "user_agent_A", "last_message": "",
                "last_message_at": "2025-01-01T00:00:00+00:00", "unread": 0,
                "created_at": "2025-01-01T00:00:00+00:00",
                "channel": "whatsapp",
                "channel_external_id": "1234567890:5491155551234"}
        _run(fake.conversations.insert_one(conv))

        r = self._post(client, _inbound_payload(msg_id="wamid.A2"))
        assert r.status_code == 200
        notifs = [n for n in fake.notifications.docs if n["type"] == "new_message"]
        assert any(n["assigned_user_id"] == "user_agent_A" for n in notifs)


# ====================================================================
# Status updates
# ====================================================================
class TestStatusUpdates:
    def _post(self, client, payload):
        body = json.dumps(payload).encode()
        sig = _sign("test-app-secret", body)
        return client.post(
            "/api/webhooks/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )

    def _seed_outbound_message(self, fake, msg_id: str = "wamid.OUT"):
        # outbound message we sent earlier
        _run(fake.messages.insert_one({
            "id": "msg_out_1", "conversation_id": "cv_Y",
            "sender_type": "agent", "sender_name": "Agente A",
            "body": "hola desde Latus",
            "created_at": "2025-01-01T00:00:00+00:00",
            "direction": "outbound", "delivery_status": "sent",
            "external_message_id": msg_id, "message_type": "text",
        }))

    def test_delivered_updates_status(self, server_and_client):
        server, fake, client = server_and_client
        self._seed_outbound_message(fake)
        r = self._post(client, _status_payload(msg_id="wamid.OUT", status="delivered"))
        assert r.status_code == 200
        assert fake.messages.docs[0]["delivery_status"] == "delivered"

    def test_failed_sets_error_fields(self, server_and_client):
        server, fake, client = server_and_client
        self._seed_outbound_message(fake)
        r = self._post(client, _status_payload(
            msg_id="wamid.OUT", status="failed", error_code=131051,
            error_msg="Message type is currently not supported.",
        ))
        assert r.status_code == 200
        m = fake.messages.docs[0]
        assert m["delivery_status"] == "failed"
        assert m["whatsapp_error_code"] == 131051
        assert m["whatsapp_error_message"] == "Message type is currently not supported."

    def test_orphan_status_logged(self, server_and_client):
        server, fake, client = server_and_client
        r = self._post(client, _status_payload(msg_id="wamid.UNKNOWN", status="read"))
        assert r.status_code == 200
        assert any(e["kind"] == "orphan_status" for e in fake.whatsapp_events.docs)


# ====================================================================
# send-whatsapp endpoint
# ====================================================================
class TestSendWhatsApp:
    def _seed_conv(self, fake, *, whatsapp_id="5491155551234"):
        _run(fake.contacts.insert_one({
            "id": "ct_S", "name": "Ana", "phone": f"+{whatsapp_id}",
            "whatsapp_id": whatsapp_id,
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.conversations.insert_one({
            "id": "cv_S", "contact_id": "ct_S", "lead_id": None,
            "status": "open", "priority": "medium", "bot_enabled": True,
            "assigned_to": None, "last_message": "",
            "last_message_at": "2025-01-01T00:00:00+00:00", "unread": 0,
            "created_at": "2025-01-01T00:00:00+00:00",
            "channel": "whatsapp",
            "channel_external_id": "1234567890:5491155551234",
        }))

    def test_success(self, server_and_client):
        server, fake, client = server_and_client
        self._seed_conv(fake)

        async def fake_post(self, url, headers=None, json=None, **kwargs):
            return httpx.Response(
                200,
                json={"messaging_product": "whatsapp",
                      "contacts": [{"input": "5491155551234", "wa_id": "5491155551234"}],
                      "messages": [{"id": "wamid.SENT.1"}]},
                request=httpx.Request("POST", url),
            )

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            r = client.post(
                "/api/conversations/cv_S/send-whatsapp",
                json={"text": "Hola desde Latus"},
                headers={"Authorization": "Bearer T-ADMIN"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["direction"] == "outbound"
        assert body["delivery_status"] == "sent"
        assert body["external_message_id"] == "wamid.SENT.1"
        # message persisted with channel=whatsapp
        out_msgs = [m for m in fake.messages.docs if m.get("external_message_id") == "wamid.SENT.1"]
        assert len(out_msgs) == 1

    def test_meta_500_returns_502_and_no_ghost(self, server_and_client):
        server, fake, client = server_and_client
        self._seed_conv(fake)

        call_counter = {"n": 0}

        async def fake_post(self, url, headers=None, json=None, **kwargs):
            call_counter["n"] += 1
            return httpx.Response(
                500, json={"error": {"code": 1, "message": "internal"}},
                request=httpx.Request("POST", url),
            )

        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            r = client.post(
                "/api/conversations/cv_S/send-whatsapp",
                json={"text": "x"},
                headers={"Authorization": "Bearer T-ADMIN"},
            )
        assert r.status_code == 502, r.text
        assert r.json()["detail"] == "No se pudo enviar el mensaje"
        # Retried once (5xx is retryable)
        assert call_counter["n"] == 2
        # No outbound ghost message created
        assert all(m.get("direction") != "outbound" for m in fake.messages.docs)
        # last_error persisted for the admin panel
        assert fake.wa_status.docs and fake.wa_status.docs[0].get("last_error")

    def test_not_configured_returns_503(self, server_and_client, monkeypatch):
        server, fake, client = server_and_client
        self._seed_conv(fake)
        # Wipe access token -> not configured
        monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "")
        r = client.post(
            "/api/conversations/cv_S/send-whatsapp",
            json={"text": "hi"},
            headers={"Authorization": "Bearer T-ADMIN"},
        )
        assert r.status_code == 503
        assert r.json()["detail"] == "WhatsApp no configurado"


# ====================================================================
# Admin status endpoint
# ====================================================================
class TestAdminStatus:
    def test_admin_sees_checklist_no_secrets(self, server_and_client):
        server, fake, client = server_and_client
        r = client.get("/api/admin/whatsapp/status",
                       headers={"Authorization": "Bearer T-ADMIN"})
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["checklist"] == {
            "phone_number_id": True, "access_token": True, "verify_token": True,
            "app_secret": True, "business_account_id": True,
        }
        # Mask present but no raw secret
        assert body["phone_number_id_masked"].endswith("7890")
        for forbidden in ("EAA-test", "test-app-secret"):
            assert forbidden not in r.text
