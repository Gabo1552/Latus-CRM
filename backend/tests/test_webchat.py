from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_webchat_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Coll:
    def __init__(self, name):
        self.name = name
        self.docs = []

    def _matches(self, doc, query):
        if not query:
            return True
        for k, v in query.items():
            if k == "$or":
                if not any(self._matches(doc, cond) for cond in v):
                    return False
            elif isinstance(v, dict):
                if "$in" in v and doc.get(k) not in v["$in"]:
                    return False
            elif v is None and doc.get(k) is not None:
                return False
            elif v is not None and doc.get(k) != v:
                return False
        return True

    async def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if self._matches(d, filter_):
                res = dict(d)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                return res
        return None

    def find(self, filter_=None, projection=None):
        matched = [dict(d) for d in self.docs if self._matches(d, filter_)]
        class _Cur:
            def __init__(self, items): self.items = items
            def sort(self, *a, **k): return self
            async def to_list(self, n): return self.items[:n]
        return _Cur(matched)

    async def insert_one(self, doc):
        d = dict(doc)
        if "_id" not in d: d["_id"] = f"oid_{len(self.docs)+1}"
        self.docs.append(d)
        doc["_id"] = d["_id"]
        class _Res: inserted_id = d["_id"]
        return _Res()

    async def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if self._matches(d, filter_):
                if "$set" in update:
                    d.update(update["$set"])
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = d.get(k, 0) + v
                if "$unset" in update:
                    for k in update["$unset"]:
                        d.pop(k, None)
                class _Res: modified_count = 1
                return _Res()
        if upsert:
            new_doc = dict(filter_)
            if "$set" in update: new_doc.update(update["$set"])
            await self.insert_one(new_doc)
            class _Res: modified_count = 1
            return _Res()
        class _Res: modified_count = 0
        return _Res()


class _FakeDB:
    def __init__(self):
        for name in ("users", "user_sessions", "contacts", "leads",
                     "conversations", "messages", "notifications", "settings",
                     "wa_status", "whatsapp_events", "app_secrets", "platform_secrets", "tasks",
                     "appointments", "notes", "bot_events", "bot_settings", "ai_usage_logs",
                     "pricing_config", "products", "work_areas", "work_area_members", "billing_requests",
                     "billing_events", "ai_billing_statements", "organizations", "memberships", "whatsapp_routes"):
            setattr(self, name, _Coll(name))


@pytest.fixture
def srv(monkeypatch):
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith("whatsapp") or mod.startswith("utils") or mod.startswith("ai"):
            sys.modules.pop(mod, None)
    import server
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    server._WEBCHAT_RATE_BUCKETS.clear()
    _run(fake.organizations.insert_one({
        "organization_id": "default", "name": "Estética Demo", "status": "active",
        "subscription_status": "active", "license_status": "active",
        "webchat_public_key": "wpk_test_default",
    }))
    
    # Mock bot pipeline process_inbound
    from ai import pipeline as bot_pipeline
    async def fake_process_inbound(db, conv_id, msg_id, wa_send=None):
        return {"decision": "reply", "bot_reply": "Respuesta simulada del bot"}
    monkeypatch.setattr(bot_pipeline, "process_inbound", fake_process_inbound)

    return server, fake, TestClient(server.app)


def test_webchat_session_creation(srv):
    server, fake, client = srv
    res = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default",
        "name": "Cliente Test Web",
        "phone": "+5493519998877"
    })
    assert res.status_code == 200
    data = res.json()
    assert "session_token" in data
    assert data["session_token"].startswith("cw_")
    assert "conversation_id" in data
    assert data["contact_name"] == "Cliente Test Web"
    assert "webchat_title" in data


def test_webchat_send_message(srv):
    server, fake, client = srv
    # 1. Create session
    session_res = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default",
        "name": "Juan Perez",
        "phone": "+5493511112233"
    })
    assert session_res.status_code == 200
    token = session_res.json()["session_token"]

    # 2. Send message
    send_res = client.post(f"/api/public/webchat/{token}/messages", json={
        "body": "Hola, cuáles son los precios de los servicios?",
        "sender_name": "Juan Perez"
    })
    assert send_res.status_code == 200
    data = send_res.json()
    assert data["status"] == "ok"
    assert "messages" in data
    assert len(data["messages"]) >= 1
    user_msg = data["messages"][-1]
    assert user_msg["body"] == "Hola, cuáles son los precios de los servicios?"


def test_webchat_get_messages(srv):
    server, fake, client = srv
    # 1. Create session
    session_res = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default",
        "name": "Maria Lopez"
    })
    token = session_res.json()["session_token"]

    # 2. Get messages
    msgs_res = client.get(f"/api/public/webchat/{token}/messages")
    assert msgs_res.status_code == 200
    assert "messages" in msgs_res.json()


def test_webchat_bot_replies_to_message(monkeypatch):
    """The visitor message is acknowledged before the bot reply is returned."""
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith("whatsapp") or mod.startswith("utils") or mod.startswith("ai"):
            sys.modules.pop(mod, None)
    import server
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    server._WEBCHAT_RATE_BUCKETS.clear()
    _run(fake.organizations.insert_one({
        "organization_id": "default", "name": "Estética Demo", "status": "active",
        "subscription_status": "active", "license_status": "active",
        "webchat_public_key": "wpk_test_default",
    }))

    # Use a real-looking pipeline that persists a bot message to DB for webchat
    from ai import pipeline as bot_pipeline
    async def fake_process_inbound_with_reply(db, conv_id, msg_id, wa_send=None):
        # Simulate webchat bot reply branch: persist message directly
        conv = await db.conversations.find_one({"id": conv_id})
        if conv and conv.get("channel") == "webchat":
            await db.messages.insert_one({
                "id": f"msg_bot_{msg_id}",
                "conversation_id": conv_id,
                "sender_type": "bot",
                "sender_name": "Bot",
                "body": "¡Hola! Soy el asistente virtual. ¿En qué puedo ayudarte?",
                "direction": "outbound",
                "delivery_status": "sent",
                "channel": "webchat",
                "created_at": "2026-01-01T00:00:00+00:00",
            })
        return {"decision": "reply_with_bot"}
    monkeypatch.setattr(bot_pipeline, "process_inbound", fake_process_inbound_with_reply)

    client = TestClient(server.app)

    # 1. Create webchat session
    session_res = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Tester Bot",
    })
    assert session_res.status_code == 200
    token = session_res.json()["session_token"]

    # 2. Send a message
    send_res = client.post(f"/api/public/webchat/{token}/messages", json={
        "body": "Hola, quiero información",
        "sender_name": "Tester Bot"
    })
    assert send_res.status_code == 200
    data = send_res.json()
    assert data["status"] == "ok"

    # 3. The immediate response only contains the acknowledged visitor message.
    messages = data["messages"]
    sender_types = [m["sender_type"] for m in messages]
    assert "contact" in sender_types, "User message should be in messages"
    assert "bot" not in sender_types
    assert data["processing"] is True

    # The background task has completed by the time TestClient returns, so the
    # next poll exposes the reply without delaying the send acknowledgement.
    poll = client.get(f"/api/public/webchat/{token}/messages")
    assert poll.status_code == 200
    assert any(m["sender_type"] == "bot" for m in poll.json()["messages"])


def test_public_chat_hides_internal_handoff_description(srv):
    _, fake, client = srv
    created = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Derivación",
    }).json()
    _run(fake.messages.insert_one({
        "id": "msg_internal_handoff",
        "conversation_id": created["conversation_id"],
        "sender_type": "system",
        "body": "Control humano activado - Confianza baja y reglas internas",
        "created_at": "2026-01-01T00:00:00+00:00",
    }))
    _run(fake.messages.insert_one({
        "id": "msg_agent_public",
        "conversation_id": created["conversation_id"],
        "sender_type": "agent", "sender_name": "María",
        "body": "Hola, continúo yo con tu consulta.",
        "created_at": "2026-01-01T00:00:01+00:00",
    }))

    poll = client.get(f"/api/public/webchat/{created['session_token']}/messages")
    assert poll.status_code == 200
    messages = poll.json()["messages"]
    assert all(item["sender_type"] != "system" for item in messages)
    assert any(item["sender_type"] == "agent" for item in messages)


def test_unknown_token_does_not_create_session(srv):
    _, fake, client = srv
    before = len(fake.conversations.docs)
    response = client.post("/api/public/webchat/session", json={"session_token": "cw_missing_token_123456789"})
    assert response.status_code == 404
    assert len(fake.conversations.docs) == before


def test_phone_does_not_resume_an_existing_conversation(srv):
    _, _, client = srv
    first = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Primera", "phone": "+5493511112233",
    }).json()
    second = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Segunda", "phone": "+5493511112233",
    }).json()
    assert first["conversation_id"] != second["conversation_id"]
    assert first["session_token"] != second["session_token"]


def test_finish_is_idempotent_and_new_message_reopens(srv):
    _, fake, client = srv
    created = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Cierre",
    }).json()
    token = created["session_token"]
    first = client.post(f"/api/public/webchat/{token}/finish")
    second = client.post(f"/api/public/webchat/{token}/finish")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["finished"] is True
    sent = client.post(f"/api/public/webchat/{token}/messages", json={"body": "Necesito retomar"})
    assert sent.status_code == 200
    conv = _run(fake.conversations.find_one({"id": created["conversation_id"]}))
    assert conv["status"] == "abierta"
    assert conv["bot_status"] == "bot_activo"


def test_disabled_webchat_rejects_public_access(srv):
    _, fake, client = srv
    _run(fake.bot_settings.insert_one({"_id": "default", "webchat_enabled": False}))
    response = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "No disponible",
    })
    assert response.status_code == 404


def test_whatsapp_invite_opens_a_linked_webchat_without_changing_original_channel(srv):
    _, fake, client = srv
    _run(fake.contacts.insert_one({
        "id": "contact_wa", "organization_id": "default", "name": "Cliente WA",
        "phone": "+5493512223344",
    }))
    token = "cw_whatsapp_invite_12345678901234567890"
    _run(fake.conversations.insert_one({
        "id": "conv_wa", "organization_id": "default", "contact_id": "contact_wa",
        "lead_id": "lead_wa", "channel": "whatsapp", "status": "abierta",
        "webchat_session_token": token,
    }))
    response = client.post("/api/public/webchat/session", json={"session_token": token})
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["conversation_id"] != "conv_wa"
    linked = _run(fake.conversations.find_one({"id": created["conversation_id"]}))
    original = _run(fake.conversations.find_one({"id": "conv_wa"}))
    assert linked["channel"] == "webchat"
    assert linked["source_conversation_id"] == "conv_wa"
    assert linked["contact_id"] == "contact_wa"
    assert original["channel"] == "whatsapp"
    assert "webchat_session_token" not in original


def test_retried_client_message_is_idempotent(srv):
    _, fake, client = srv
    created = client.post("/api/public/webchat/session", json={
        "organization_key": "wpk_test_default", "name": "Reintento",
    }).json()
    token = created["session_token"]
    payload = {"body": "El mismo mensaje", "client_message_id": "browser-message-1"}
    first = client.post(f"/api/public/webchat/{token}/messages", json=payload)
    second = client.post(f"/api/public/webchat/{token}/messages", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    inbound = [
        item for item in fake.messages.docs
        if item.get("conversation_id") == created["conversation_id"]
        and item.get("sender_type") == "contact"
    ]
    assert len(inbound) == 1
