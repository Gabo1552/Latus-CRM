"""Regression tests for POST /api/conversations/{id}/simulate-inbound and
POST /api/conversations/{id}/messages.

These used to return 500 because Motor's ``insert_one`` mutates the input dict
adding an ``_id: ObjectId(...)`` field which FastAPI's ``jsonable_encoder``
cannot serialize. The fake DB in this module reproduces that behaviour so the
regression cannot reappear silently.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_sim_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- FakeDB that mutates input dicts on insert (like real Motor) -----------


class _OidProxy:
    """Stand-in for ``bson.ObjectId``: not iterable, no ``__dict__`` — exactly
    the surface that breaks FastAPI's encoder when leaked into a response."""

    __slots__ = ("hex",)

    def __init__(self, hex_: str):
        object.__setattr__(self, "hex", hex_)

    def __repr__(self):
        return f"ObjectId('{self.hex}')"

    def __setattr__(self, *_a, **_k):  # mimic immutability
        raise AttributeError("immutable")


_OID_COUNTER = {"n": 0}


def _next_oid():
    _OID_COUNTER["n"] += 1
    return _OidProxy(f"{_OID_COUNTER['n']:024x}")


def _matches(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
            elif "$exists" in v:
                exists = k in doc and doc.get(k) is not None
                if v["$exists"] != exists:
                    return False
            elif "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            elif "$nin" in v and doc.get(k) in v["$nin"]:
                return False
            elif "$regex" in v:
                rx = v["$regex"]
                fv = doc.get(k)
                if isinstance(fv, list):
                    if not any(rx.search(str(x)) for x in fv):
                        return False
                elif fv is None or not rx.search(str(fv)):
                    return False
            elif "$gte" in v or "$lte" in v or "$gt" in v or "$lt" in v:
                fv = doc.get(k)
                if fv is None:
                    return False
                if "$gte" in v and fv < v["$gte"]: return False
                if "$lte" in v and fv > v["$lte"]: return False
                if "$gt" in v and fv <= v["$gt"]: return False
                if "$lt" in v and fv >= v["$lt"]: return False
        elif v is None and doc.get(k) is not None:
            return False
        elif v is not None and doc.get(k) != v:
            return False
    return True


def _query_matches(doc, query):
    if "$or" in query:
        if not any(_matches(doc, sub) for sub in query["$or"]):
            return False
        rest = {k: v for k, v in query.items() if k != "$or"}
        return _matches(doc, rest)
    return _matches(doc, query)


class _Cursor:
    def __init__(self, docs): self._docs = list(docs)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            self._docs.sort(key=lambda d: d.get(key_or_list, "") or "",
                            reverse=(direction == -1))
        return self

    async def to_list(self, n=None):
        return list(self._docs if n is None else self._docs[:n])


class _Coll:
    def __init__(self, name=""):
        self.docs = []
        self.name = name
        self.unique_indexes: list[str] = []

    def find(self, query=None, projection=None):
        docs = [d for d in self.docs if _query_matches(d, query or {})]
        if projection and projection.get("_id") == 0:
            docs = [{k: v for k, v in d.items() if k != "_id"} for d in docs]
        return _Cursor(docs)

    async def find_one(self, query, projection=None, sort=None):
        for d in self.docs:
            if _query_matches(d, query):
                out = dict(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def insert_one(self, doc):
        # Enforce sparse unique indexes
        for idx_field in self.unique_indexes:
            v = doc.get(idx_field)
            if v is None:
                continue
            if any(d.get(idx_field) == v for d in self.docs):
                raise RuntimeError(f"E11000 duplicate key on {idx_field}")
        # Like Motor: mutate caller's dict adding an _id ObjectId
        if "_id" not in doc:
            doc["_id"] = _next_oid()
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _query_matches(d, query):
                if "$set" in update: d.update(update["$set"])
                if "$unset" in update:
                    for k in update["$unset"]: d.pop(k, None)
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = (d.get(k) or 0) + v
                return
        if upsert:
            new = {k: v for k, v in (query or {}).items() if not isinstance(v, dict)}
            if "$set" in update: new.update(update["$set"])
            self.docs.append(new)

    async def update_many(self, *_a, **_k): pass
    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _query_matches(d, query):
                self.docs.pop(i); return
    async def delete_many(self, query):
        self.docs[:] = [d for d in self.docs if not _query_matches(d, query)]
    async def count_documents(self, query):
        return sum(1 for d in self.docs if _query_matches(d, query))
    async def distinct(self, key, query=None):
        seen = set()
        out = []
        for d in self.docs:
            if not _query_matches(d, query or {}):
                continue
            v = d.get(key)
            if v is None: continue
            vs = v if isinstance(v, list) else [v]
            for x in vs:
                if x not in seen:
                    seen.add(x); out.append(x)
        return out
    async def create_index(self, key, **kw):
        if kw.get("unique"):
            self.unique_indexes.append(key if isinstance(key, str) else key[0][0])
        return "idx"


class _FakeDB:
    def __init__(self):
        for name in ("users", "user_sessions", "contacts", "leads",
                     "conversations", "messages", "notifications", "settings",
                     "wa_status", "whatsapp_events", "app_secrets", "platform_secrets", "tasks",
                     "appointments", "notes", "bot_events", "bot_settings", "ai_usage_logs",
                     "pricing_config", "products", "work_areas", "billing_requests",
                     "billing_events"):
            setattr(self, name, _Coll(name))
        for name in ("organizations", "memberships", "whatsapp_routes"):
            setattr(self, name, _Coll(name))


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def srv(monkeypatch):
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith("whatsapp") or mod.startswith("utils") \
                or mod.startswith("ai"):
            sys.modules.pop(mod, None)
    import server  # type: ignore
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

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
    # contact + conversation (bot enabled by default)
    _run(fake.contacts.insert_one({
        "id": "c_1", "name": "Marcus Demo", "whatsapp_id": "+1234",
        "channel": "demo",
    }))
    _run(fake.conversations.insert_one({
        "id": "conv_1", "contact_id": "c_1", "channel": "demo",
        "status": "open", "priority": "medium", "unread": 0,
        "bot_enabled": True, "bot_status": "bot_activo",
        "last_message": "",
        "last_message_at": "2025-01-01T00:00:00+00:00",
    }))
    return server, fake, TestClient(server.app)


def _h(token="T-ADMIN"):
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Tests
# ============================================================================

class TestSimulateInbound:
    def test_returns_200_and_json_serializable(self, srv):
        _, fake, client = srv
        r = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        assert r.status_code == 200, r.text
        body = r.json()
        # round-trip serialization is guaranteed by .json(), but assert anyway:
        json.dumps(body)
        # no ObjectId leaked
        assert "_id" not in body
        assert body["direction"] == "inbound"
        assert body["sender_type"] == "contact"
        assert body["external_message_id"].startswith("sim_")
        assert body["conversation_id"] == "conv_1"

    def test_message_persisted_with_sim_external_id(self, srv):
        _, fake, client = srv
        r = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        ext_id = r.json()["external_message_id"]
        # Stored in db.messages
        stored = next((m for m in fake.messages.docs
                       if m.get("external_message_id") == ext_id), None)
        assert stored is not None
        assert stored["direction"] == "inbound"
        assert stored["sender_type"] == "contact"
        assert stored["conversation_id"] == "conv_1"
        # internal _id remains in storage (real Mongo behaviour), but is not
        # exposed via the API
        assert "_id" in stored

    def test_conversation_last_message_at_updated(self, srv):
        _, fake, client = srv
        before = fake.conversations.docs[0]["last_message_at"]
        r = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        assert r.status_code == 200
        after = fake.conversations.docs[0]["last_message_at"]
        assert after != before, "last_message_at should be advanced"
        assert fake.conversations.docs[0]["last_message"] == r.json()["body"]
        assert fake.conversations.docs[0]["unread"] == 1

    def test_notification_created_for_new_message(self, srv):
        _, fake, client = srv
        r = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        assert r.status_code == 200
        notifs = [n for n in fake.notifications.docs if n["type"] == "new_message"]
        # broadcast (no asignado) → assigned_user_id is None
        assert len(notifs) >= 1
        n = notifs[0]
        assert n["related_entity_type"] == "conversation"
        assert n["related_entity_id"] == "conv_1"

    def test_two_calls_produce_two_distinct_messages(self, srv):
        _, fake, client = srv
        r1 = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        r2 = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        assert r1.status_code == 200 and r2.status_code == 200
        e1 = r1.json()["external_message_id"]
        e2 = r2.json()["external_message_id"]
        assert e1 != e2
        msgs = [m for m in fake.messages.docs
                if m.get("conversation_id") == "conv_1" and m.get("direction") == "inbound"]
        assert len(msgs) == 2

    def test_bot_disabled_no_bot_event(self, srv):
        _, fake, client = srv
        # disable bot
        _run(fake.conversations.update_one(
            {"id": "conv_1"},
            {"$set": {"bot_enabled": False, "bot_status": "en_atencion_humana"}},
        ))
        r = client.post("/api/conversations/conv_1/simulate-inbound", headers=_h())
        assert r.status_code == 200
        # No bot_event should be created for this trigger because the pipeline
        # task is gated on conversation_bot_should_run().
        ext_id = r.json()["external_message_id"]
        assert not any(e.get("triggered_by_message_id") == ext_id
                       for e in fake.bot_events.docs)


class TestPostMessages:
    def test_post_messages_agent_returns_clean_doc(self, srv):
        _, _, client = srv
        r = client.post("/api/conversations/conv_1/messages", headers=_h(),
                        json={"sender_type": "agent", "body": "hola"})
        assert r.status_code == 200, r.text
        body = r.json()
        json.dumps(body)
        assert "_id" not in body
        assert body["direction"] == "outbound"
        assert body["delivery_status"] == "sent"
        assert body["sender_type"] == "agent"

    def test_post_messages_contact_returns_clean_doc(self, srv):
        _, fake, client = srv
        r = client.post("/api/conversations/conv_1/messages", headers=_h(),
                        json={"sender_type": "contact", "body": "tengo una duda"})
        assert r.status_code == 200, r.text
        body = r.json()
        json.dumps(body)
        assert "_id" not in body
        assert body["direction"] == "inbound"
        assert fake.conversations.docs[0]["unread"] == 1
