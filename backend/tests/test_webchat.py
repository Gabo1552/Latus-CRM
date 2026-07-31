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
    
    # Mock bot pipeline process_inbound
    from ai import pipeline as bot_pipeline
    async def fake_process_inbound(db, conv_id, msg_id, wa_send=None):
        return {"decision": "reply", "bot_reply": "Respuesta simulada del bot"}
    monkeypatch.setattr(bot_pipeline, "process_inbound", fake_process_inbound)

    return server, fake, TestClient(server.app)


def test_webchat_session_creation(srv):
    server, fake, client = srv
    res = client.post("/api/public/webchat/session", json={
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
        "name": "Maria Lopez"
    })
    token = session_res.json()["session_token"]

    # 2. Get messages
    msgs_res = client.get(f"/api/public/webchat/{token}/messages")
    assert msgs_res.status_code == 200
    assert "messages" in msgs_res.json()
