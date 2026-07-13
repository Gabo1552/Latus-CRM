"""Tests for the multi-provider AI configuration (Phase 1).

Covers:
  - GET/PUT /api/admin/ai-provider (admin only; masking; validation; clear)
  - POST /api/admin/ai-provider/test (httpx mocked)
  - Pipeline integration: ai_enabled=false → no_action;
    whatsapp_auto_reply_enabled=false → reply_with_bot degrades to update_status_only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_aiprov_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")
os.environ.setdefault("LATUS_LLM_KEY", "test-key-not-used")


# Reuse the FakeDB from the simulate-inbound suite to avoid duplication
from test_simulate_inbound import _FakeDB, _run  # type: ignore


@pytest.fixture
def srv(monkeypatch):
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith(("whatsapp", "utils", "ai")):
            sys.modules.pop(mod, None)
    import server  # type: ignore
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

    # Seed admin + agent + viewer sessions
    for role, token in (("admin", "T-ADMIN"), ("agent", "T-AGENT"), ("viewer", "T-VIEWER")):
        _run(fake.users.insert_one({
            "user_id": f"u_{role}", "email": f"{role}@latus.test", "name": role.title(),
            "role": role, "active": True, "auth_provider": "google",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.user_sessions.insert_one({
            "user_id": f"u_{role}", "session_token": token,
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))
    return server, fake, TestClient(server.app)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# REST endpoints
# ============================================================================


class TestAIProviderConfig:
    def test_get_default_for_fresh_db(self, srv):
        _, _, client = srv
        r = client.get("/api/admin/ai-provider", headers=_h("T-ADMIN"))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "built_in"
        assert d["api_key_configured"] is False
        assert d["api_key_masked"] == ""
        # never expose api_key
        assert "api_key" not in d
        assert "api_key_enc" not in d
        assert "ai_enabled" in d and d["ai_enabled"] is True
        assert "model_suggestions" in d and "openai" in d["model_suggestions"]
        assert "openai" in d["supported_providers"]

    def test_put_openai_with_key_then_get_masked(self, srv):
        _, fake, client = srv
        r = client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                       json={"provider": "openai", "model": "gpt-4o-mini",
                             "api_key": "sk-very-secret-1234"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "openai"
        assert d["api_key_configured"] is True
        # masked, never plain
        assert d["api_key_masked"] == "••••1234"
        assert "api_key" not in d
        # The plain key is NOT stored — only the encrypted blob
        doc = next((x for x in fake.app_secrets.docs if x.get("_id") == "ai_provider"), None)
        assert doc and "api_key_enc" in doc
        assert "sk-very-secret-1234" not in json.dumps(doc)

    def test_put_clear_key(self, srv):
        _, _, client = srv
        # set then clear (and switch back to built_in since clear is disallowed for openai)
        client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                   json={"provider": "openai", "api_key": "sk-x"})
        r = client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                       json={"provider": "built_in", "api_key": None})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["api_key_configured"] is False
        assert d["api_key_masked"] == ""

    def test_put_openai_without_key_rejected(self, srv):
        _, _, client = srv
        r = client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                       json={"provider": "openai"})
        assert r.status_code == 400
        assert "API Key" in r.text

    def test_validation_ranges(self, srv):
        _, _, client = srv
        assert client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                          json={"temperature": 3}).status_code == 400
        assert client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                          json={"max_tokens": 10000}).status_code == 400
        assert client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                          json={"min_confidence_for_auto_reply": 1.5}).status_code == 400

    def test_custom_openai_requires_base_url(self, srv):
        _, _, client = srv
        r = client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                       json={"provider": "custom_openai", "api_key": "sk-x"})
        assert r.status_code == 400
        assert "URL base" in r.text
        # with base_url it should pass
        r2 = client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                        json={"provider": "custom_openai", "api_key": "sk-x",
                              "base_url": "https://api.together.xyz/v1"})
        assert r2.status_code == 200, r2.text

    def test_rbac_agent_403(self, srv):
        _, _, client = srv
        assert client.put("/api/admin/ai-provider", headers=_h("T-AGENT"),
                          json={"provider": "built_in"}).status_code == 403
        assert client.get("/api/admin/ai-provider",
                          headers=_h("T-VIEWER")).status_code == 403


# ============================================================================
# Provider test endpoint (httpx mocked)
# ============================================================================


class TestProviderConnectivity:
    def test_test_ok_with_openai_mocked(self, srv, monkeypatch):
        _, _, client = srv
        client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                   json={"provider": "openai", "api_key": "sk-test",
                         "model": "gpt-4o-mini"})

        class _FakeResp:
            status_code = 200
            def json(self):
                return {"model": "gpt-4o-mini",
                        "choices": [{"message": {"content":
                                                  '{"ok": true, "echo": "latus"}'}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 9}}

        class _FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, *a, **k): return _FakeResp()

        from ai import providers
        monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeClient)
        r = client.post("/api/admin/ai-provider/test", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert isinstance(d["latency_ms"], int)
        assert d["latency_ms"] >= 0
        assert d["model"] == "gpt-4o-mini"

    def test_test_401_does_not_leak_key(self, srv, monkeypatch):
        _, _, client = srv
        client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                   json={"provider": "openai", "api_key": "sk-secret-XXYY",
                         "model": "gpt-4o-mini"})

        class _FakeResp:
            status_code = 401
            def json(self):
                return {"error": {"message": "Invalid API key", "code": "invalid_api_key"}}

        class _FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, *a, **k): return _FakeResp()

        from ai import providers
        monkeypatch.setattr(providers.httpx, "AsyncClient", _FakeClient)
        r = client.post("/api/admin/ai-provider/test", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert "Invalid API key" in d["error"]
        # never leak the api key
        assert "sk-secret-XXYY" not in r.text


# ============================================================================
# Pipeline global flags
# ============================================================================


class TestPipelineGlobalFlags:
    def _seed_conv(self, fake):
        _run(fake.contacts.insert_one({"id": "c1", "name": "Cliente", "whatsapp_id": "+1"}))
        _run(fake.conversations.insert_one({
            "id": "conv1", "contact_id": "c1", "channel": "whatsapp",
            "status": "open", "bot_enabled": True, "bot_status": "bot_activo",
            "unread": 0, "last_message_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(fake.messages.insert_one({
            "id": "m1", "conversation_id": "conv1", "direction": "inbound",
            "sender_type": "contact", "body": "Hola, ¿precios?",
            "external_message_id": "wamid.1", "created_at": "2025-01-01T00:01:00+00:00",
        }))

    def test_ai_disabled_short_circuits(self, srv, monkeypatch):
        server, fake, client = srv
        self._seed_conv(fake)
        # Disable AI globally
        client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                   json={"ai_enabled": False})

        from ai import pipeline as pipeline_mod
        called = {"n": 0}

        async def fake_llm(**kw):
            called["n"] += 1
            return ({}, "")
        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)

        event = _run(pipeline_mod.process_inbound(
            fake, "conv1", "wamid.1", wa_send=None))
        assert event["decision"] == "no_action"
        assert event["human_required_reason"] == "ia_desactivada"
        assert called["n"] == 0, "LLM must NOT be called when ai_enabled=false"

    def test_auto_reply_off_degrades_to_status_only(self, srv, monkeypatch):
        server, fake, client = srv
        self._seed_conv(fake)
        client.put("/api/admin/ai-provider", headers=_h("T-ADMIN"),
                   json={"whatsapp_auto_reply_enabled": False})

        from ai import pipeline as pipeline_mod
        sent = {"n": 0}

        async def fake_llm(**kw):
            return ({
                "intent": "precios", "confidence": 0.9, "decision": "reply_with_bot",
                "reply": "¡Hola!", "summary": "Cliente preguntó por precios",
                "human_required_reason": None, "next_best_action": "enviar lista",
                "lead_status_suggested": None, "bot_status_suggested": None,
                "evidence_for_status_change": "",
            }, "")
        monkeypatch.setattr(pipeline_mod, "call_llm_json", fake_llm)

        async def fake_wa(conv, text):
            sent["n"] += 1
        event = _run(pipeline_mod.process_inbound(
            fake, "conv1", "wamid.1", wa_send=fake_wa))
        assert event["decision"] == "update_status_only"
        assert event.get("auto_reply_suppressed") is True
        assert sent["n"] == 0, "wa_send must NOT be called when auto_reply is off"
        # No bot outbound message persisted
        bot_msgs = [m for m in fake.messages.docs
                    if m.get("conversation_id") == "conv1" and m.get("sender_type") == "bot"]
        assert bot_msgs == []
        # But intent/summary still updated on conversation
        conv = fake.conversations.docs[0]
        assert conv.get("detected_intent") == "precios"
        assert conv.get("summary") == "Cliente preguntó por precios"

    def test_provider_api_key_separation(self, srv):
        server, fake, client = srv
        from ai import providers as ai_providers
        from utils import crypto
        
        # 1. Configure assistant key
        _run(fake.app_secrets.update_one(
            {"_id": "ai_provider"},
            {"$set": {"api_key_openai_enc": crypto.encrypt("assistant_key")}},
            upsert=True
        ))
        
        # 2. Get provider for assistant (for_bot=False) -> should resolve to assistant_key
        prov_asst = _run(ai_providers.get_provider(fake, override_provider="openai", for_bot=False))
        assert prov_asst.api_key == "assistant_key"
        
        # 3. Get provider for bot (for_bot=True) with fallback -> should resolve to assistant_key since no bot key exists
        prov_bot_fallback = _run(ai_providers.get_provider(fake, override_provider="openai", for_bot=True))
        assert prov_bot_fallback.api_key == "assistant_key"
        
        # 4. Configure bot-specific key
        _run(fake.app_secrets.update_one(
            {"_id": "bot_provider"},
            {"$set": {"api_key_openai_enc": crypto.encrypt("bot_key")}},
            upsert=True
        ))
        
        # 5. Get provider for bot (for_bot=True) -> should resolve to bot_key
        prov_bot = _run(ai_providers.get_provider(fake, override_provider="openai", for_bot=True))
        assert prov_bot.api_key == "bot_key"
        
        # 6. Get provider for assistant (for_bot=False) -> should still resolve to assistant_key
        prov_asst_sep = _run(ai_providers.get_provider(fake, override_provider="openai", for_bot=False))
        assert prov_asst_sep.api_key == "assistant_key"
