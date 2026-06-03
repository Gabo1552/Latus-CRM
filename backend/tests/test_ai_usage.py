"""Tests for AI usage logging + pricing (Phase 2)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_aiusage_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")
os.environ.setdefault("EMERGENT_LLM_KEY", "test-key")

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


class TestUsageLogging:
    def test_estimate_cost_exact(self):
        from ai.usage import estimate_cost
        # 1M input @ 0.150 = 0.150 + 0.5M output @ 0.600 = 0.300 → 0.450
        assert estimate_cost("gpt-4o-mini", 1_000_000, 500_000) == 0.450

    def test_success_call_creates_log_with_cost(self, srv, monkeypatch):
        _, fake, client = srv
        from ai import providers, usage

        class _Res:
            def __init__(self):
                self.content = '{"ok":true}'
                self.model = "gpt-4o-mini"
                self.prompt_tokens = 1000
                self.completion_tokens = 200
                self.latency_ms = 123
                self.provider = "openai"

        async def fake_chat(self, *, system_prompt, user_block, json_mode=True):
            return _Res()
        monkeypatch.setattr(providers.OpenAIProvider, "chat", fake_chat)

        prov = providers.OpenAIProvider(model="gpt-4o-mini", api_key="x")
        _run(usage.call_with_logging(fake, prov, system_prompt="s",
                                     user_block="u", purpose="bot_pipeline",
                                     conversation_id="conv1"))
        logs = fake.ai_usage_logs.docs
        assert len(logs) == 1
        l = logs[0]
        assert l["status"] == "success"
        assert l["model"] == "gpt-4o-mini"
        assert l["prompt_tokens"] == 1000
        assert l["completion_tokens"] == 200
        assert l["total_tokens"] == 1200
        assert l["conversation_id"] == "conv1"
        # 1000/1M * 0.150 + 200/1M * 0.600 = 0.00015 + 0.00012 = 0.00027
        assert l["estimated_cost_usd"] == pytest.approx(0.00027, abs=1e-6)
        assert l["purpose"] == "bot_pipeline"

    def test_unknown_model_zero_cost(self, srv, monkeypatch):
        _, fake, client = srv
        from ai import providers, usage

        class _Res:
            content = "{}"; model = "mystery-1.0"; prompt_tokens = 100
            completion_tokens = 50; latency_ms = 10; provider = "custom_openai"

        async def fake_chat(self, **k): return _Res()
        monkeypatch.setattr(providers.CustomOpenAIProvider, "chat", fake_chat)
        prov = providers.CustomOpenAIProvider(model="mystery-1.0", api_key="x",
                                              base_url="https://x/v1")
        _run(usage.call_with_logging(fake, prov, system_prompt="s", user_block="u",
                                     purpose="bot_pipeline"))
        log = fake.ai_usage_logs.docs[-1]
        assert log["estimated_cost_usd"] == 0.0
        assert log["status"] == "success"

    def test_error_call_creates_error_log(self, srv, monkeypatch):
        _, fake, client = srv
        from ai import providers, usage

        async def boom(self, **k):
            raise providers.LLMUnavailable("Invalid API key")
        monkeypatch.setattr(providers.OpenAIProvider, "chat", boom)
        prov = providers.OpenAIProvider(model="gpt-4o-mini", api_key="sk-leaky")
        with pytest.raises(providers.LLMUnavailable):
            _run(usage.call_with_logging(fake, prov, system_prompt="s",
                                          user_block="u", purpose="bot_pipeline"))
        log = fake.ai_usage_logs.docs[-1]
        assert log["status"] == "error"
        assert "Invalid API key" in log["error_message"]
        assert "sk-leaky" not in log["error_message"]  # no api_key leak
        assert log["estimated_cost_usd"] == 0.0
        assert log["latency_ms"] >= 0


class TestUsageEndpoints:
    def _seed_log(self, fake, **overrides):
        from datetime import datetime, timezone
        base = {
            "log_id": f"log_{len(fake.ai_usage_logs.docs)}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": "openai", "model": "gpt-4o-mini",
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            "estimated_cost_usd": 0.000045, "latency_ms": 200,
            "status": "success", "error_message": None,
            "conversation_id": "conv1", "message_id": "m1",
            "user_id": "u_admin", "purpose": "bot_pipeline",
        }
        base.update(overrides)
        _run(fake.ai_usage_logs.insert_one(base))

    def test_summary_filters_and_aggregations(self, srv):
        _, fake, client = srv
        for _ in range(3): self._seed_log(fake)
        self._seed_log(fake, model="gpt-4o", estimated_cost_usd=0.001,
                       conversation_id="conv2")
        r = client.get("/api/admin/ai-usage/summary", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        d = r.json()
        assert d["total_calls"] == 4
        assert d["success_calls"] == 4
        assert d["total_cost_usd"] == pytest.approx(0.001135, abs=1e-6)
        models = {b["model"] for b in d["by_model"]}
        assert models == {"gpt-4o-mini", "gpt-4o"}
        # Filtered by model
        r2 = client.get("/api/admin/ai-usage/summary?model=gpt-4o",
                        headers=_h("T-ADMIN"))
        assert r2.json()["total_calls"] == 1
        assert r2.json()["total_cost_usd"] == 0.001

    def test_logs_pagination(self, srv):
        _, fake, client = srv
        for _ in range(25): self._seed_log(fake)
        r = client.get("/api/admin/ai-usage/logs?limit=10", headers=_h("T-ADMIN"))
        d = r.json()
        assert r.status_code == 200
        assert d["total"] == 25 and d["limit"] == 10
        assert len(d["items"]) == 10

    def test_quick(self, srv):
        _, fake, client = srv
        self._seed_log(fake)
        self._seed_log(fake, model="gpt-4o")
        self._seed_log(fake, model="gpt-4o")
        r = client.get("/api/admin/ai-usage/quick", headers=_h("T-ADMIN"))
        d = r.json()
        for k in ("today", "this_month", "all_time", "top_model"):
            assert k in d
        for sec in ("today", "this_month", "all_time"):
            for k in ("calls", "tokens", "cost_usd"):
                assert k in d[sec]
        assert d["top_model"]["model"] == "gpt-4o"
        assert d["top_model"]["share_pct"] == pytest.approx(66.7, abs=0.1)

    def test_pricing_get_and_put(self, srv):
        _, _, client = srv
        r = client.get("/api/admin/ai-pricing", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        d = r.json()
        assert d["models"]["gpt-4o-mini"]["input"] == 0.150

        r2 = client.put("/api/admin/ai-pricing", headers=_h("T-ADMIN"),
                        json={"model": "gpt-4o-mini",
                              "input_per_million": 0.999,
                              "output_per_million": 1.234})
        assert r2.status_code == 200
        assert r2.json()["models"]["gpt-4o-mini"]["input"] == 0.999

        # estimate_cost should now use the new price
        from ai.usage import load_pricing, estimate_cost
        from server import db as fake_db
        pricing = _run(load_pricing(fake_db))
        # 1M input * 0.999 + 0 output = 0.999
        assert estimate_cost("gpt-4o-mini", 1_000_000, 0, pricing) == pytest.approx(0.999)

    def test_pricing_put_negative_400(self, srv):
        _, _, client = srv
        r = client.put("/api/admin/ai-pricing", headers=_h("T-ADMIN"),
                       json={"model": "x", "input_per_million": -1,
                             "output_per_million": 0})
        assert r.status_code == 400
        assert "negativos" in r.text

    def test_rbac_403(self, srv):
        _, _, client = srv
        assert client.get("/api/admin/ai-usage/summary",
                          headers=_h("T-AGENT")).status_code == 403
        assert client.get("/api/admin/ai-usage/logs",
                          headers=_h("T-VIEWER")).status_code == 403
        assert client.put("/api/admin/ai-pricing", headers=_h("T-AGENT"),
                          json={"model": "x", "input_per_million": 0,
                                "output_per_million": 0}).status_code == 403

    def test_invalid_date_range_400(self, srv):
        _, _, client = srv
        r = client.get("/api/admin/ai-usage/summary?from=2025-02-01&to=2025-01-01",
                       headers=_h("T-ADMIN"))
        assert r.status_code == 400
        assert "no puede ser mayor" in r.text


class TestPipelineEndToEnd:
    def test_pipeline_creates_log(self, srv, monkeypatch):
        _, fake, client = srv
        _run(fake.contacts.insert_one({"id": "c1", "name": "X", "whatsapp_id": "+1"}))
        _run(fake.conversations.insert_one({
            "id": "conv9", "contact_id": "c1", "channel": "whatsapp",
            "status": "open", "bot_enabled": True, "bot_status": "bot_activo",
            "unread": 0,
        }))
        _run(fake.messages.insert_one({
            "id": "m1", "conversation_id": "conv9", "direction": "inbound",
            "sender_type": "contact", "body": "Hola precios?",
            "external_message_id": "wamid.9",
            "created_at": "2025-01-01T00:00:00+00:00",
        }))

        from ai import pipeline as pmod, providers
        class _Res:
            content = '{"intent":"precios","confidence":0.9,"decision":"no_action","reply":"","summary":"x"}'
            model = "gpt-4o-mini"; prompt_tokens = 50; completion_tokens = 30
            latency_ms = 80; provider = "emergent"
        async def fake_chat(self, **k): return _Res()
        monkeypatch.setattr(providers.EmergentProvider, "chat", fake_chat)

        _run(pmod.process_inbound(fake, "conv9", "wamid.9", wa_send=None))
        logs = [l for l in fake.ai_usage_logs.docs if l["purpose"] == "bot_pipeline"]
        assert len(logs) == 1
        assert logs[0]["conversation_id"] == "conv9"
        assert logs[0]["message_id"] == "wamid.9"
        assert logs[0]["status"] == "success"
