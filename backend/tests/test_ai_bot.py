"""Unit tests for the AI bot pipeline. Mocks the LLM and the WhatsApp send adapter."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_bot_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- Tiny FakeDB tailored to pipeline.process_inbound ---------------------

def _matches(d, q):
    for k, v in (q or {}).items():
        if isinstance(v, dict):
            if "$in" in v and d.get(k) not in v["$in"]: return False
            elif "$ne" in v and d.get(k) == v["$ne"]: return False
            elif "$exists" in v:
                ex = (k in d) and (d.get(k) is not None)
                if v["$exists"] != ex: return False
            elif "$nin" in v and d.get(k) in v["$nin"]: return False
        elif v is None and d.get(k) is not None: return False
        elif v is not None and d.get(k) != v: return False
    return True


class _Cur:
    def __init__(self, docs): self._d = list(docs)
    def sort(self, *a, **k):
        if a:
            key = a[0] if isinstance(a[0], str) else a[0][0][0]
            direction = a[1] if len(a) > 1 and isinstance(a[1], int) else -1
            self._d.sort(key=lambda x: x.get(key) or "", reverse=(direction == -1))
        return self
    async def to_list(self, n=None): return list(self._d if n is None else self._d[:n])


class _Coll:
    def __init__(self): self.docs = []
    def find(self, q=None, p=None): return _Cur([d for d in self.docs if _matches(d, q or {})])
    async def find_one(self, q, p=None, **kw):
        for d in self.docs:
            if _matches(d, q): return dict(d)
        return None
    async def insert_one(self, doc):
        # simulate sparse unique index on triggered_by_message_id for bot_events
        if "triggered_by_message_id" in doc and doc.get("triggered_by_message_id"):
            for x in self.docs:
                if x.get("triggered_by_message_id") == doc["triggered_by_message_id"]:
                    raise Exception("duplicate triggered_by_message_id")
        self.docs.append(dict(doc))
    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if _matches(d, q):
                if "$set" in upd: d.update(upd["$set"])
                if "$inc" in upd:
                    for k, v in upd["$inc"].items(): d[k] = (d.get(k) or 0) + v
                if "$unset" in upd:
                    for k in upd["$unset"]: d.pop(k, None)
                return
        if upsert:
            new = {k: v for k, v in (q or {}).items() if not isinstance(v, dict)}
            if "$set" in upd: new.update(upd["$set"])
            self.docs.append(new)
    async def count_documents(self, q): return sum(1 for d in self.docs if _matches(d, q))
    async def create_index(self, *a, **k): return "idx"


class _DB:
    def __init__(self):
        for n in ("users", "contacts", "leads", "conversations", "messages",
                  "notifications", "bot_events", "bot_settings", "settings",
                  "user_sessions", "wa_status", "whatsapp_events", "app_secrets"):
            setattr(self, n, _Coll())


# ---- Helpers --------------------------------------------------------------

def _seed_conv(db, *, bot_enabled=True, bot_status="bot_activo", channel="whatsapp",
               last_text="hola, ¿precios?"):
    _run(db.contacts.insert_one({
        "id": "ct1", "name": "Ana", "phone": "+5491155551234",
        "whatsapp_id": "5491155551234",
    }))
    _run(db.leads.insert_one({
        "id": "ld1", "contact_id": "ct1", "status": "nuevo",
    }))
    _run(db.conversations.insert_one({
        "id": "cv1", "contact_id": "ct1", "lead_id": "ld1",
        "status": "open", "priority": "medium",
        "bot_enabled": bot_enabled, "bot_status": bot_status,
        "assigned_to": None, "channel": channel,
        "last_message_at": "2025-01-01T00:00:00+00:00",
    }))
    _run(db.messages.insert_one({
        "id": "msg_in_1", "conversation_id": "cv1", "sender_type": "contact",
        "sender_name": "Ana", "body": last_text,
        "external_message_id": "wamid.IN1",
        "created_at": "2025-01-01T00:00:00+00:00",
    }))
    return "cv1"


def _llm_factory(*, decision="reply_with_bot", confidence=0.85, reply="¡Hola Ana!",
                 summary="Cliente preguntó por precios.", intent="precios",
                 lead_status=None, bot_status=None, evidence="",
                 human_reason=None):
    """Returns an async fake for call_llm_json."""
    async def fake(*, system_prompt, user_messages_block, model="gpt-4o-mini",
                   db=None, **_kw):
        parsed = {
            "intent": intent, "confidence": confidence, "decision": decision,
            "reply": reply, "human_required_reason": human_reason,
            "next_best_action": "enviar info adicional",
            "summary": summary, "lead_status_suggested": lead_status,
            "bot_status_suggested": bot_status,
            "evidence_for_status_change": evidence,
        }
        return parsed, '{"raw":"mock"}'
    return fake


# ===========================================================================
# TESTS
# ===========================================================================

@pytest.fixture
def pipeline_mod():
    from ai import pipeline as p
    return p


class TestPipeline:
    def test_01_reply_with_bot_happy_path(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db)
        sent = []
        async def wa_send(conv, text): sent.append(text); return {"ok": True}
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.9))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=wa_send))
        # Now it should reply directly since whatsapp_auto_reply_enabled defaults to True
        assert ev["decision"] == "reply_with_bot"
        assert sent == ["¡Hola Ana!"]
        bots = [m for m in db.messages.docs if m["sender_type"] == "bot"]
        assert len(bots) == 1
        assert bots[0]["body"] == "¡Hola Ana!"
        conv = _run(db.conversations.find_one({"id": cv}))
        assert conv["bot_status"] == "esperando_cliente"
        assert conv["summary"] == "Cliente preguntó por precios."

    def test_01b_reply_with_bot_degraded_when_auto_reply_disabled(self, pipeline_mod, monkeypatch):
        db = _DB()
        _run(db.app_secrets.insert_one({"_id": "ai_provider", "whatsapp_auto_reply_enabled": False}))
        cv = _seed_conv(db)
        sent = []
        async def wa_send(conv, text): sent.append(text); return {"ok": True}
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.9))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=wa_send))
        # With auto reply disabled, it should degrade to update_status_only
        assert ev["decision"] == "update_status_only"
        assert sent == []
        bots = [m for m in db.messages.docs if m["sender_type"] == "bot"]
        assert len(bots) == 0

    def test_02_bot_disabled_no_llm_call(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, bot_enabled=False)
        called = {"n": 0}
        async def fake(**kw): called["n"] += 1; return ({}, "")
        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake)
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                                wa_send=AsyncMock(return_value={})))
        assert called["n"] == 0
        assert ev["decision"] == "no_action"

    def test_03_low_confidence_triggers_handoff(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db)
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.4))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                                wa_send=AsyncMock(return_value={})))
        assert ev["decision"] == "require_human"
        conv = _run(db.conversations.find_one({"id": cv}))
        assert conv["bot_status"] == "requiere_humano"
        assert conv["bot_enabled"] is False
        assert conv.get("human_required_reason")
        notifs = [n for n in db.notifications.docs if n["type"] == "handoff_required"]
        assert len(notifs) >= 1

    def test_04_explicit_human_request_skips_llm_response(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="quiero hablar con un asesor por favor")
        # LLM still gets called for summary but its reply is forced ignored.
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.95,
                                          reply="esto NO debería enviarse"))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                                wa_send=AsyncMock(return_value={})))
        assert ev["decision"] == "require_human"
        assert "humano" in ev["human_required_reason"].lower() or \
               "humano" in ev["human_required_reason"]
        bots = [m for m in db.messages.docs if m["sender_type"] == "bot"]
        assert bots == []

    def test_05_summary_and_timestamp_updated(self, pipeline_mod, monkeypatch):
        db = _DB(); cv = _seed_conv(db)
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(summary="Lead preguntó por precios; enviar lista."))
        _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                           wa_send=AsyncMock(return_value={})))
        conv = _run(db.conversations.find_one({"id": cv}))
        assert conv["summary"] == "Lead preguntó por precios; enviar lista."
        assert conv["last_summary_at"]

    def test_06_lead_status_requires_evidence(self, pipeline_mod, monkeypatch):
        db = _DB(); cv = _seed_conv(db)
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(lead_status="calificado", evidence=""))
        _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                           wa_send=AsyncMock(return_value={})))
        lead = _run(db.leads.find_one({"id": "ld1"}))
        assert lead["status"] == "nuevo"  # NOT changed without evidence

    def test_07_idempotent_same_message_id(self, pipeline_mod, monkeypatch):
        db = _DB(); cv = _seed_conv(db)
        sent = []
        async def wa_send(conv, text): sent.append(text); return {"ok": True}
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.9))
        _run(pipeline_mod.process_inbound(db, cv, "wamid.DUP", wa_send=wa_send))
        _run(pipeline_mod.process_inbound(db, cv, "wamid.DUP", wa_send=wa_send))
        assert len([e for e in db.bot_events.docs
                    if e.get("triggered_by_message_id") == "wamid.DUP"]) == 1
        bots = [m for m in db.messages.docs if m["sender_type"] == "bot"]
        assert len(bots) == 1

    def test_08_handoff_routes_to_default_handoff_user_id(self, pipeline_mod, monkeypatch):
        db = _DB(); cv = _seed_conv(db)
        # Update bot settings to include default_handoff_user_id
        _run(db.bot_settings.insert_one({"_id": "default", "default_handoff_user_id": "operator1"}))
        
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="require_human", confidence=0.9, human_reason="Handoff needed"))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=AsyncMock()))
        
        # Verify conversation and lead are updated with the assigned operator
        conv = _run(db.conversations.find_one({"id": cv}))
        assert conv["assigned_to"] == "operator1"
        assert conv["bot_status"] == "requiere_humano"
        
        lead = _run(db.leads.find_one({"id": "ld1"}))
        assert lead["assigned_to"] == "operator1"

    def test_09_dni_pattern_forces_handoff_without_replying(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="hola, mi DNI es 30123456 para registrarme")
        monkeypatch.setattr(pipeline_mod, "call_llm_json",
                            _llm_factory(decision="reply_with_bot", confidence=0.95,
                                          reply="no debería enviarse"))
        ev = _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1",
                                                wa_send=AsyncMock(return_value={})))
        assert ev["decision"] == "require_human"
        assert "sensible" in (ev.get("human_required_reason") or "").lower() or \
               "DNI" in (ev.get("human_required_reason") or "")
        bots = [m for m in db.messages.docs if m["sender_type"] == "bot"]
        assert bots == []  # bot did NOT reply

    def test_10_settings_validation_via_pipeline_constants(self, pipeline_mod):
        # Validation happens in the FastAPI endpoint (server.py). Here we
        # assert the allowed-models guard / threshold range are enforced by
        # the model constants the endpoint imports.
        from server import _ALLOWED_BOT_MODELS
        assert "gpt-4o-mini" in _ALLOWED_BOT_MODELS
        assert "gpt-3.5-turbo" not in _ALLOWED_BOT_MODELS

    def test_11_catalog_reading_enabled(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="Hola, tienen stock de Cursos?")
        # Seed catalog setting as False
        _run(db.bot_settings.insert_one({"_id": "default", "catalog_reading_enabled": False}))
        
        called_llm_args = {}
        async def fake_llm(**kw):
            nonlocal called_llm_args
            called_llm_args = kw
            return ({
                "intent": "stock", "confidence": 0.9, "decision": "update_status_only",
                "reply": "", "summary": "Cliente preguntó por stock.",
                "human_required_reason": None, "next_best_action": None,
                "lead_status_suggested": None, "bot_status_suggested": None,
                "evidence_for_status_change": "",
            }, "")
        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        
        _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=AsyncMock()))
        
        # Verify that catalog block is NOT injected in system_prompt
        sp = called_llm_args.get("system_prompt", "")
        assert "CATÁLOGO" not in sp
