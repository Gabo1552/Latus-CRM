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
                  "user_sessions", "wa_status", "whatsapp_events", "app_secrets",
                  "platform_secrets",
                  "work_areas", "appointments", "products"):
            setattr(self, n, _Coll())
        for n in ("organizations", "memberships", "whatsapp_routes"):
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
        _run(db.platform_secrets.insert_one({"_id": "ai_provider", "whatsapp_auto_reply_enabled": False}))
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

    def test_01c_webchat_replies_even_when_whatsapp_auto_reply_is_disabled(self, pipeline_mod, monkeypatch):
        db = _DB()
        _run(db.platform_secrets.insert_one({"_id": "ai_provider", "whatsapp_auto_reply_enabled": False}))
        cv = _seed_conv(db, channel="webchat", last_text="Hola desde la web")
        captured = {}

        async def fake_llm(**kwargs):
            captured.update(kwargs)
            return await _llm_factory(decision="reply_with_bot", confidence=0.9)(**kwargs)

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        event = _run(pipeline_mod.process_inbound(db, cv, "webmsg.1", wa_send=None))
        assert event["decision"] == "reply_with_bot"
        bot_messages = [m for m in db.messages.docs if m.get("sender_type") == "bot"]
        assert len(bot_messages) == 1
        assert bot_messages[0]["channel"] == "webchat"
        assert "chat web" in captured["system_prompt"]
        assert captured["max_output_tokens"] == 500

    def test_01d_webchat_phone_is_saved_from_structured_extraction(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, channel="webchat", last_text="Mi número es 351 555 7788")
        _run(db.contacts.update_one({"id": "ct1"}, {"$set": {"phone": None}}))

        async def fake_llm(**kwargs):
            parsed, raw = await _llm_factory(decision="reply_with_bot", confidence=0.9)(**kwargs)
            parsed["contact_phone"] = "351 555 7788"
            return parsed, raw

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        event = _run(pipeline_mod.process_inbound(db, cv, "webmsg.2", wa_send=None))
        contact = _run(db.contacts.find_one({"id": "ct1"}))
        assert event["contact_phone_updated"] is True
        assert contact["phone"] == "+543515557788"

    def test_01e_delivery_failure_is_visible_and_handed_to_human(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db)
        monkeypatch.setattr(
            pipeline_mod, "call_llm_json",
            _llm_factory(decision="reply_with_bot", confidence=0.9),
        )

        async def failed_send(conv, text):
            raise RuntimeError("canal temporalmente no disponible")

        event = _run(pipeline_mod.process_inbound(db, cv, "wamid.SENDFAIL", wa_send=failed_send))
        conversation = _run(db.conversations.find_one({"id": cv}))
        assert event["status"] == "error"
        assert event["decision"] == "require_human"
        assert conversation["bot_enabled"] is False
        assert conversation["bot_status"] == "requiere_humano"
        assert any(n.get("type") == "handoff_required" for n in db.notifications.docs)

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

    def test_10_pipeline_ignores_legacy_tenant_provider(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db)
        _run(db.bot_settings.insert_one({
            "_id": "default", "provider": "anthropic", "model": "tenant-model",
        }))
        _run(db.platform_secrets.insert_one({
            "_id": "ai_provider", "provider": "built_in", "model": "gpt-4o-mini",
        }))
        seen = {}

        async def fake_llm(**kwargs):
            seen.update(kwargs)
            return ({"decision": "no_action", "confidence": 1, "reply": ""}, "{}")

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=None))
        assert seen["provider"] == "built_in"
        assert seen["model"] == "gpt-4o-mini"

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


class TestBotInactivityAndTransitions:
    def test_auto_close_inactivity(self, monkeypatch):
        from server import close_inactive_conversations
        from datetime import datetime, timezone, timedelta
        
        # 1. Clean collections
        db = _DB()
        monkeypatch.setattr("server.db", db)
        
        # 2. Configure 48 hours inactivity threshold
        _run(db.bot_settings.insert_one({"_id": "default", "bot_inactive_close_hours": 48}))
        
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=50)).isoformat()
        new_time = (now - timedelta(hours=10)).isoformat()
        
        # 3. Insert inactive conversation
        _run(db.conversations.insert_one({
            "id": "conv_old",
            "status": "open",
            "bot_status": "bot_activo",
            "bot_enabled": False,
            "last_message_at": old_time,
        }))
        
        # 4. Insert active/recent conversation
        _run(db.conversations.insert_one({
            "id": "conv_new",
            "status": "open",
            "bot_status": "bot_activo",
            "bot_enabled": False,
            "last_message_at": new_time,
        }))

        # Already closed chats must still re-arm if the bot was left disabled.
        _run(db.conversations.insert_one({
            "id": "conv_resolved_disabled",
            "status": "resolved",
            "bot_status": "cerrada",
            "bot_enabled": False,
            "last_message_at": old_time,
        }))
        
        # 5. Run scanner
        _run(close_inactive_conversations(db))
        
        # 6. Verify inactive conversation closed and bot re-armed
        c_old = _run(db.conversations.find_one({"id": "conv_old"}))
        assert c_old["status"] == "resolved"
        assert c_old["bot_status"] == "cerrada"
        assert c_old["bot_enabled"] is True
        
        # 7. Verify recent conversation remains open
        c_new = _run(db.conversations.find_one({"id": "conv_new"}))
        assert c_new["status"] == "open"
        assert c_new["bot_enabled"] is False

        c_resolved = _run(db.conversations.find_one({"id": "conv_resolved_disabled"}))
        assert c_resolved["status"] == "resolved"
        assert c_resolved["bot_enabled"] is True
        assert c_resolved["bot_reactivated_reason"] == "inactivity_timeout"
        
        # 8. Verify system transition messages logged
        msgs = _run(db.messages.find({"conversation_id": "conv_old"}).to_list(10))
        assert len(msgs) == 2
        assert msgs[0]["sender_type"] == "system"
        assert "cerrada automáticamente" in msgs[0]["body"]
        assert msgs[1]["sender_type"] == "system"
        assert "Bot reactivado" in msgs[1]["body"]

        resolved_msgs = _run(db.messages.find({"conversation_id": "conv_resolved_disabled"}).to_list(10))
        assert len(resolved_msgs) == 1
        assert "Bot reactivado" in resolved_msgs[0]["body"]

    def test_reopen_re_enables_bot(self, monkeypatch):
        from server import _handle_inbound_message
        
        # 1. Clean collections
        db = _DB()
        monkeypatch.setattr("server.db", db)
        
        # 2. Insert resolved conversation with bot disabled
        _run(db.conversations.insert_one({
            "id": "conv_res",
            "contact_id": "c1",
            "status": "resolved",
            "bot_status": "cerrada",
            "bot_enabled": False,
        }))
        
        # 3. Simulate inbound message (reopen)
        _run(_handle_inbound_message({
            "id": "conv_res",
            "contact_id": "c1",
            "status": "resolved",
            "bot_status": "cerrada",
            "bot_enabled": False,
        }, "Hola de nuevo"))
        
        # 4. Verify conversation is open, bot re-armed
        c = _run(db.conversations.find_one({"id": "conv_res"}))
        assert c["status"] == "open"
        assert c["bot_enabled"] is True
        assert c["bot_status"] == "bot_activo"
        
        # 5. Verify system reopen message logged
        msgs = _run(db.messages.find({"conversation_id": "conv_res", "sender_type": "system"}).to_list(10))
        assert len(msgs) == 1
        assert "Reapertura de chat" in msgs[0]["body"]

    def test_work_area_routing_and_notification(self, monkeypatch):
        import ai.pipeline as pipeline_mod
        
        # 1. Clean and seed mock database
        db = _DB()
        monkeypatch.setattr("server.db", db)
        
        cv = _seed_conv(db)
        
        # Seed work area "finanzas"
        _run(db.work_areas.insert_one({
            "id": "finanzas",
            "name": "Finanzas",
            "description": "Temas de cobranza y finanzas",
            "routing_rules": "Derivar consultas de pagos",
        }))
        
        # Seed user belonging to finanzas
        _run(db.users.insert_one({
            "user_id": "agent_finanzas",
            "email": "finanzas@latus.test",
            "name": "Agente Finanzas",
            "role": "agent",
            "active": True,
            "work_areas": ["finanzas"]
        }))
        
        # 2. Mock LLM call to return decision="require_human", target_work_area="finanzas"
        async def fake_llm(**kw):
            # Verify work areas block was injected in system_prompt
            sp = kw.get("system_prompt", "")
            assert "finanzas: Finanzas" in sp
            assert "Derivar consultas de pagos" in sp
            return ({
                "intent": "precios", "confidence": 0.95, "decision": "require_human",
                "reply": "", "summary": "Cliente pregunta por formas de pago",
                "human_required_reason": "Consulta financiera", "next_best_action": None,
                "lead_status_suggested": None, "bot_status_suggested": None,
                "evidence_for_status_change": "",
                "target_work_area": "finanzas"
            }, "")
            
        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        
        # 3. Run pipeline
        _run(pipeline_mod.process_inbound(db, cv, "wamid.IN1", wa_send=AsyncMock()))
        
        # 4. Verify conversation work area set, assigned_to is cleared/None
        c = _run(db.conversations.find_one({"id": cv}))
        assert c["assigned_work_area"] == "finanzas"
        assert c["assigned_to"] is None
        
        # 5. Verify notification created specifically for agent_finanzas
        notifs = [n for n in db.notifications.docs if n["type"] == "handoff_required"]
        assert len(notifs) == 1
        assert notifs[0]["assigned_user_id"] == "agent_finanzas"

    def test_bot_uses_person_availability_and_rejects_duplicate_slot(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="Quiero una reunión el lunes a las 10")
        weekly = {str(day): ([{"start": "09:00", "end": "18:00"}] if day < 5 else []) for day in range(7)}
        _run(db.users.insert_one({
            "user_id": "agent_agenda",
            "email": "agenda@latus.test",
            "name": "Agente Agenda",
            "role": "agent",
            "active": True,
            "calendar_settings": {
                "enabled": True,
                "timezone": "America/Argentina/Buenos_Aires",
                "default_duration_minutes": 30,
                "buffer_minutes": 0,
                "weekly_schedule": weekly,
            },
        }))
        _run(db.bot_settings.insert_one({
            "_id": "default",
            "appointment_scheduling_enabled": True,
            "appointment_mode": "people",
            "appointment_timezone": "America/Argentina/Buenos_Aires",
        }))

        async def fake_llm(**kwargs):
            assert "agent_agenda" in kwargs.get("system_prompt", "")
            return ({
                "intent": "agendar", "confidence": 0.98,
                "decision": "schedule_appointment",
                "reply": "Listo, tu reunión quedó agendada.",
                "summary": "Cliente agendó una reunión.",
                "human_required_reason": None,
                "next_best_action": None,
                "lead_status_suggested": None,
                "bot_status_suggested": None,
                "evidence_for_status_change": "Horario confirmado por el cliente",
                "appointment_start_time": "2026-07-20T10:00:00-03:00",
                "appointment_assigned_to": "agent_agenda",
                "appointment_service_id": None,
            }, "{}")

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        first = _run(pipeline_mod.process_inbound(db, cv, "wamid.APPT1", wa_send=AsyncMock(return_value={})))
        assert first["appointment_created"]
        assert len(db.appointments.docs) == 1
        assert db.appointments.docs[0]["assigned_to"] == "agent_agenda"
        assert db.appointments.docs[0]["end_time"] == "2026-07-20T13:30:00+00:00"

        second = _run(pipeline_mod.process_inbound(db, cv, "wamid.APPT2", wa_send=AsyncMock(return_value={})))
        assert len(db.appointments.docs) == 1
        assert "schedule failed" in second.get("error_message", "")

    def test_bot_confirms_existing_appointment_without_creating_another(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="Sí, confirmo el turno")
        _run(db.appointments.insert_one({
            "id": "appt_confirm", "contact_id": "ct1", "conversation_id": cv,
            "title": "Consulta", "event_type": "appointment", "status": "scheduled",
            "start_time": "2026-07-20T13:00:00+00:00",
            "end_time": "2026-07-20T13:30:00+00:00",
            "confirmation_status": "awaiting_response",
        }))

        async def fake_llm(**_kwargs):
            return ({
                "intent": "confirmar_turno", "confidence": 0.99,
                "decision": "confirm_appointment", "reply": "Perfecto, tu turno quedó confirmado.",
                "summary": "Cliente confirmó el turno.", "human_required_reason": None,
                "next_best_action": None, "lead_status_suggested": None,
                "bot_status_suggested": None, "evidence_for_status_change": "Confirmación expresa",
                "appointment_id": "appt_confirm", "appointment_start_time": None,
                "appointment_assigned_to": None, "appointment_service_id": None,
            }, "{}")

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        event = _run(pipeline_mod.process_inbound(db, cv, "wamid.CONFIRM", wa_send=AsyncMock(return_value={})))
        appointment = _run(db.appointments.find_one({"id": "appt_confirm"}))
        assert event["appointment_confirmed"] == "appt_confirm"
        assert appointment["confirmation_status"] == "confirmed"
        assert len(db.appointments.docs) == 1

    def test_bot_reschedules_existing_appointment_without_duplication(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="¿Podemos moverlo a las 11?")
        weekly = {str(day): ([{"start": "09:00", "end": "18:00"}] if day < 5 else []) for day in range(7)}
        _run(db.users.insert_one({
            "user_id": "agent_agenda", "name": "Agente Agenda", "active": True,
            "calendar_settings": {
                "enabled": True, "timezone": "America/Argentina/Buenos_Aires",
                "default_duration_minutes": 30, "buffer_minutes": 0,
                "weekly_schedule": weekly,
            },
        }))
        _run(db.bot_settings.insert_one({
            "_id": "default", "appointment_scheduling_enabled": True,
            "appointment_rescheduling_enabled": True, "appointment_mode": "people",
            "appointment_timezone": "America/Argentina/Buenos_Aires",
        }))
        _run(db.appointments.insert_one({
            "id": "appt_move", "contact_id": "ct1", "conversation_id": cv,
            "title": "Consulta", "event_type": "appointment", "status": "scheduled",
            "start_time": "2026-07-20T13:00:00+00:00",
            "end_time": "2026-07-20T13:30:00+00:00",
            "assigned_to": "agent_agenda", "scheduling_mode": "people",
        }))

        async def fake_llm(**kwargs):
            assert "appt_move" in kwargs.get("system_prompt", "")
            return ({
                "intent": "reprogramar_turno", "confidence": 0.99,
                "decision": "reschedule_appointment", "reply": "Listo, movimos tu turno a las 11.",
                "summary": "Cliente reprogramó el turno.", "human_required_reason": None,
                "next_best_action": None, "lead_status_suggested": None,
                "bot_status_suggested": None, "evidence_for_status_change": "Pedido expreso",
                "appointment_id": "appt_move",
                "appointment_start_time": "2026-07-20T11:00:00-03:00",
                "appointment_assigned_to": None, "appointment_service_id": None,
            }, "{}")

        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)
        event = _run(pipeline_mod.process_inbound(db, cv, "wamid.MOVE", wa_send=AsyncMock(return_value={})))
        appointment = _run(db.appointments.find_one({"id": "appt_move"}))
        assert event["appointment_rescheduled"] == "appt_move"
        assert appointment["start_time"] == "2026-07-20T14:00:00+00:00"
        assert appointment["end_time"] == "2026-07-20T14:30:00+00:00"
        assert len(db.appointments.docs) == 1

    def test_bot_won_status_requires_human_review(self, pipeline_mod, monkeypatch):
        db = _DB()
        cv = _seed_conv(db, last_text="Confirmo la compra")
        _run(db.products.insert_one({
            "product_id": "prod_bot_sale", "name": "Plan bot", "price": 120,
            "promo_price": None, "currency": "ARS", "active": True,
            "deleted_at": None,
        }))
        _run(db.leads.update_one({"id": "ld1"}, {"$set": {
            "products": [{
                "id": "prod_bot_sale", "name": "Plan bot", "price": 120,
                "quantity": 1, "currency": "ARS",
            }],
            "value": 120,
        }}))
        monkeypatch.setattr(
            pipeline_mod,
            "call_llm_json",
            _llm_factory(
                decision="update_status_only",
                lead_status="ganado",
                evidence="El cliente confirmó la compra",
                intent="compra_confirmada",
            ),
        )
        event = _run(pipeline_mod.process_inbound(
            db, cv, "wamid.SALE1", wa_send=AsyncMock(return_value={})
        ))
        lead = _run(db.leads.find_one({"id": "ld1"}))
        assert event.get("sale_status_requires_human") is True
        assert event.get("lead_status_suggested") == "ganado"
        assert lead["status"] == "nuevo"
        assert not lead.get("sale_snapshot")
