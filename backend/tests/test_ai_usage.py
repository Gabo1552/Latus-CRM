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
os.environ.setdefault("LATUS_LLM_KEY", "test-key")

from test_simulate_inbound import _FakeDB, _run  # type: ignore


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "admin@latus.test")
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
        assert l["base_cost_usd"] == pytest.approx(0.00027, abs=1e-8)
        assert l["ai_fee_percent"] == 20.0
        assert l["ai_fee_usd"] == pytest.approx(0.000054, abs=1e-8)
        assert l["billable_cost_usd"] == pytest.approx(0.000324, abs=1e-8)
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

    def test_provider_reported_cost_is_kept_separate(self, srv, monkeypatch):
        _, fake, _ = srv
        from ai import providers, usage

        class _Res:
            content = "{}"; model = "openai/gpt-4o-mini"
            prompt_tokens = 100; completion_tokens = 50; latency_ms = 20
            provider = "openrouter"; provider_cost_usd = 0.0042
            provider_request_id = "gen-123"

        async def fake_chat(self, **kwargs): return _Res()
        monkeypatch.setattr(providers.OpenRouterProvider, "chat", fake_chat)
        prov = providers.OpenRouterProvider(model="openai/gpt-4o-mini", api_key="x")
        _run(usage.call_with_logging(fake, prov, system_prompt="s", user_block="u"))
        log = fake.ai_usage_logs.docs[-1]
        assert log["provider_cost_usd"] == pytest.approx(0.0042)
        assert log["base_cost_usd"] == pytest.approx(0.0042)
        assert log["ai_fee_usd"] == pytest.approx(0.00084)
        assert log["billable_cost_usd"] == pytest.approx(0.00504)
        assert log["cost_source"] == "provider_response"
        assert log["token_source"] == "provider_response"
        assert log["provider_request_id"] == "gen-123"

    def test_usage_freezes_organization_fee_override(self, srv, monkeypatch):
        _, fake, _ = srv
        from ai import providers, usage
        from utils.tenancy import reset_organization_id, set_organization_id

        _run(fake.organizations.insert_one({
            "organization_id": "org_fee", "name": "Empresa Fee",
            "ai_fee_percent": 12.5,
        }))

        class _Res:
            content = "{}"; model = "gpt-4o-mini"
            prompt_tokens = 1000; completion_tokens = 0; latency_ms = 10
            provider = "openai"

        async def fake_chat(self, **kwargs): return _Res()
        monkeypatch.setattr(providers.OpenAIProvider, "chat", fake_chat)
        token = set_organization_id("org_fee")
        try:
            provider = providers.OpenAIProvider(model="gpt-4o-mini", api_key="x")
            _run(usage.call_with_logging(fake, provider, system_prompt="s", user_block="u"))
        finally:
            reset_organization_id(token)
        log = fake.ai_usage_logs.docs[-1]
        assert log["ai_fee_percent"] == 12.5
        assert log["ai_fee_usd"] == pytest.approx(0.00001875)
        assert log["billable_cost_usd"] == pytest.approx(0.00016875)

    def test_plan_token_quota_blocks_provider_call(self, srv, monkeypatch):
        _, fake, _ = srv
        from ai import providers, usage
        from utils.tenancy import reset_organization_id, set_organization_id
        from datetime import datetime, timezone

        _run(fake.organizations.insert_one({
            "organization_id": "org_quota", "name": "Empresa Cupo", "plan_code": "starter",
        }))
        _run(fake.ai_usage_logs.insert_one({
            "organization_id": "org_quota", "created_at": datetime.now(timezone.utc).isoformat(),
            "total_tokens": 250_000,
        }))
        calls = {"count": 0}
        async def fake_chat(self, **kwargs):
            calls["count"] += 1
            raise AssertionError("No debe llamar al proveedor")
        monkeypatch.setattr(providers.OpenAIProvider, "chat", fake_chat)

        context_token = set_organization_id("org_quota")
        try:
            provider = providers.OpenAIProvider(model="gpt-4o-mini", api_key="x")
            with pytest.raises(providers.LLMUnavailable, match="cupo mensual"):
                _run(usage.call_with_logging(fake, provider, system_prompt="s", user_block="u"))
        finally:
            reset_organization_id(context_token)
        assert calls["count"] == 0

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


class TestAIVariableSettlement:
    def test_amount_calculation_freezes_rate_and_buffer(self):
        from billing.ai_settlement import calculate_amounts
        amounts = calculate_amounts(
            plan_amount_ars=45_000, billable_cost_usd=2.5,
            usd_to_ars_rate=1_500, fx_buffer_percent=10,
        )
        assert amounts["ai_cost_converted_ars"] == 3_750
        assert amounts["ai_amount_ars"] == 4_125
        assert amounts["total_amount_ars"] == 49_125

    def test_policy_requires_exchange_rate_before_enabling(self, srv):
        _, _, client = srv
        default = client.get("/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"))
        assert default.status_code == 200
        assert default.json()["enabled"] is False
        rejected = client.put(
            "/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"),
            json={"enabled": True},
        )
        assert rejected.status_code == 400
        assert "cotización" in rejected.text.lower()
        configured = client.put(
            "/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"),
            json={"usd_to_ars_rate": 1500, "fx_buffer_percent": 8,
                  "settlement_lead_hours": 24, "max_rate_age_hours": 72,
                  "enabled": True},
        )
        assert configured.status_code == 200
        assert configured.json()["enabled"] is True
        assert client.get(
            "/api/platform/ai-settlement-policy", headers=_h("T-AGENT")
        ).status_code == 403

    def test_tenant_billing_configuration_is_safe_validated_and_platform_only(self, srv):
        _, fake, client = srv
        organization = client.get("/api/organizations/current", headers=_h("T-ADMIN")).json()
        organization_id = organization["organization_id"]

        listed = client.get("/api/platform/organizations", headers=_h("T-ADMIN"))
        assert listed.status_code == 200
        row = next(item for item in listed.json() if item["organization_id"] == organization_id)
        assert row["ai_variable_billing"]["state"] == "disabled"

        assert client.patch(
            f"/api/platform/organizations/{organization_id}/ai-variable-billing",
            headers=_h("T-AGENT"), json={"state": "active"},
        ).status_code == 403
        invalid = client.patch(
            f"/api/platform/organizations/{organization_id}/ai-variable-billing",
            headers=_h("T-ADMIN"), json={"fx_buffer_percent": 101},
        )
        assert invalid.status_code == 400

        updated = client.patch(
            f"/api/platform/organizations/{organization_id}/ai-variable-billing",
            headers=_h("T-ADMIN"),
            json={"state": "active", "ai_fee_percent": 17.5,
                  "fx_buffer_percent": 12},
        )
        assert updated.status_code == 200, updated.text
        config = updated.json()["ai_variable_billing"]
        assert config["state"] == "active"
        assert config["billing_start_date"]
        assert config["ai_fee_percent"] == 17.5
        stored = _run(fake.organizations.find_one({"organization_id": organization_id}))
        assert stored["ai_fee_percent"] == 17.5
        assert stored["ai_variable_billing"]["fx_buffer_percent"] == 12

    def test_simulation_and_pilot_modes_never_enter_automatic_run(self, srv, monkeypatch):
        server, fake, client = srv
        from datetime import datetime, timedelta, timezone

        organization = client.get("/api/organizations/current", headers=_h("T-ADMIN")).json()
        organization_id = organization["organization_id"]
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=29)).isoformat()
        _run(fake.organizations.update_one(
            {"organization_id": organization_id},
            {"$set": {
                "plan_code": "starter", "subscription_status": "active",
                "provider_status": "authorized", "provider_preapproval_id": "pre_pilot",
                "current_period_end": (now + timedelta(hours=2)).isoformat(),
                "provider_last_payment_at": start,
                "ai_variable_billing": {"state": "simulation", "billing_start_date": start},
            }},
        ))
        _run(fake.ai_usage_logs.insert_one({
            "organization_id": organization_id,
            "created_at": (now - timedelta(days=1)).isoformat(),
            "status": "success", "total_tokens": 1000,
            "provider_cost_usd": 1.0, "ai_fee_percent": 20,
            "ai_fee_usd": 0.2, "billable_cost_usd": 1.2,
        }))
        assert client.put(
            "/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"),
            json={"usd_to_ars_rate": 1000, "fx_buffer_percent": 10,
                  "settlement_lead_hours": 24, "max_rate_age_hours": 72,
                  "enabled": True},
        ).status_code == 200
        calls = []

        async def fake_mp(method, path, *, payload=None):
            calls.append((method, path, payload))
            return {"id": "pre_pilot", "status": "authorized"}

        monkeypatch.setattr(server, "_mercadopago_request", fake_mp)
        simulation_run = client.post(
            f"/api/platform/ai-settlements/run?organization_id={organization_id}",
            headers=_h("T-ADMIN"),
        )
        assert simulation_run.status_code == 200
        assert simulation_run.json()["items"][0]["reason"] == "organization_simulation_only"
        assert calls == []

        pilot = client.patch(
            f"/api/platform/organizations/{organization_id}/ai-variable-billing",
            headers=_h("T-ADMIN"),
            json={"state": "pilot", "billing_start_date": start,
                  "fx_buffer_percent": 25},
        )
        assert pilot.status_code == 200, pilot.text
        automatic = client.post("/api/platform/ai-settlements/run", headers=_h("T-ADMIN"))
        assert automatic.status_code == 200
        assert automatic.json()["processed"] == 0
        assert calls == []

        manual = client.post(
            f"/api/platform/ai-settlements/run?organization_id={organization_id}",
            headers=_h("T-ADMIN"),
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["applied"] == 1
        assert calls[-1][2]["auto_recurring"]["transaction_amount"] == 46_500

    def test_due_settlement_updates_subscription_once_and_payment_closes_it(self, srv, monkeypatch):
        server, fake, client = srv
        from datetime import datetime, timedelta, timezone

        organization = client.get("/api/organizations/current", headers=_h("T-ADMIN")).json()
        organization_id = organization["organization_id"]
        now = datetime.now(timezone.utc)
        _run(fake.organizations.update_one(
            {"organization_id": organization_id},
            {"$set": {
                "plan_code": "starter", "subscription_status": "active",
                "provider_status": "authorized", "provider_preapproval_id": "pre_123",
                "current_period_end": (now + timedelta(hours=2)).isoformat(),
                "provider_last_payment_at": (now - timedelta(days=29)).isoformat(),
                "ai_variable_billing": {
                    "state": "active",
                    "billing_start_date": (now - timedelta(days=29)).isoformat(),
                },
            }},
        ))
        _run(fake.ai_usage_logs.insert_one({
            "organization_id": organization_id,
            "created_at": (now - timedelta(days=1)).isoformat(),
            "status": "success", "total_tokens": 1000,
            "provider_cost_usd": 1.0, "ai_fee_percent": 20,
            "ai_fee_usd": 0.2, "billable_cost_usd": 1.2,
        }))
        configured = client.put(
            "/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"),
            json={"usd_to_ars_rate": 1000, "fx_buffer_percent": 10,
                  "settlement_lead_hours": 24, "max_rate_age_hours": 72,
                  "enabled": True},
        )
        assert configured.status_code == 200
        calls = []
        async def fake_mp(method, path, *, payload=None):
            calls.append((method, path, payload))
            return {"id": "pre_123", "status": "authorized"}
        monkeypatch.setattr(server, "_mercadopago_request", fake_mp)

        first = client.post(
            "/api/platform/ai-settlements/run", headers=_h("T-ADMIN"),
        )
        assert first.status_code == 200, first.text
        assert first.json()["applied"] == 1
        assert len(calls) == 1
        assert calls[0][2]["auto_recurring"]["transaction_amount"] == 46_320
        statement = fake.ai_billing_statements.docs[-1]
        assert statement["billable_cost_usd"] == pytest.approx(1.2)
        assert statement["status"] == "applied"

        second = client.post(
            "/api/platform/ai-settlements/run", headers=_h("T-ADMIN"),
        )
        assert second.status_code == 200
        assert len(calls) == 1, "Idempotency must not update Mercado Pago twice"

        _run(server._apply_mercadopago_payment({
            "id": "pay_123", "preapproval_id": "pre_123", "status": "approved",
            "transaction_amount": 46_320, "date_approved": now.isoformat(),
        }))
        assert fake.ai_billing_statements.docs[-1]["status"] == "paid"
        assert fake.ai_billing_statements.docs[-1]["provider_payment_id"] == "pay_123"
        assert fake.ai_billing_statements.docs[-1]["base_amount_restored_ars"] == 45_000
        assert len(calls) == 2
        assert calls[-1][2]["auto_recurring"]["transaction_amount"] == 45_000

        _run(server._apply_mercadopago_payment({
            "id": "pay_123", "preapproval_id": "pre_123", "status": "approved",
            "transaction_amount": 46_320, "date_approved": now.isoformat(),
        }))
        assert len(calls) == 2, "A repeated payment webhook must not restore the base amount twice"

    def test_bcra_rate_refresh_parses_official_response(self, srv, monkeypatch):
        _, _, client = srv
        from billing import ai_settlement

        class _Response:
            status_code = 200
            def json(self):
                return {"results": [{"fecha": "2026-07-21", "detalle": [{
                    "codigoMoneda": "USD", "tipoCotizacion": 1477.5,
                }]}]}
        class _Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, *args, **kwargs): return _Response()
        monkeypatch.setattr(ai_settlement.httpx, "AsyncClient", _Client)
        response = client.post(
            "/api/platform/ai-settlement-policy/refresh-rate", headers=_h("T-ADMIN")
        )
        assert response.status_code == 200
        assert response.json()["usd_to_ars_rate"] == 1477.5
        assert response.json()["exchange_rate_source"] == "bcra"

    def test_cancellation_waits_for_final_variable_settlement(self, srv, monkeypatch):
        server, fake, client = srv
        from datetime import datetime, timedelta, timezone
        organization = client.get("/api/organizations/current", headers=_h("T-ADMIN")).json()
        organization_id = organization["organization_id"]
        now = datetime.now(timezone.utc)
        _run(fake.organizations.update_one(
            {"organization_id": organization_id},
            {"$set": {"subscription_status": "active", "provider_status": "authorized",
                      "provider_preapproval_id": "pre_cancel",
                      "current_period_end": (now + timedelta(hours=2)).isoformat(),
                      "provider_last_payment_at": (now - timedelta(days=29)).isoformat(),
                      "ai_variable_billing": {
                          "state": "active",
                          "billing_start_date": (now - timedelta(days=29)).isoformat(),
                      }}},
        ))
        _run(fake.ai_usage_logs.insert_one({
            "organization_id": organization_id,
            "created_at": (now - timedelta(days=1)).isoformat(), "status": "success",
            "total_tokens": 1000, "provider_cost_usd": 1.0, "ai_fee_percent": 20,
            "ai_fee_usd": 0.2, "billable_cost_usd": 1.2,
        }))
        enabled = client.put(
            "/api/platform/ai-settlement-policy", headers=_h("T-ADMIN"),
            json={"usd_to_ars_rate": 1500, "enabled": True},
        )
        assert enabled.status_code == 200
        calls = []
        async def fake_mp(*args, **kwargs):
            calls.append((args, kwargs))
            return {"id": "pre_cancel", "status": "authorized"}
        monkeypatch.setattr(server, "_mercadopago_request", fake_mp)
        response = client.post("/api/billing/cancel", headers=_h("T-ADMIN"))
        assert response.status_code == 200
        assert response.json()["organization"]["cancel_at_period_end"] is True
        assert calls == [], "Mercado Pago must remain active until the final settlement is paid"
        settlement = client.post(
            f"/api/platform/ai-settlements/run?organization_id={organization_id}",
            headers=_h("T-ADMIN"),
        )
        assert settlement.status_code == 200
        assert len(calls) == 1
        charged = calls[0][1]["payload"]["auto_recurring"]["transaction_amount"]
        assert charged == 1980, "Final settlement must charge AI only, not another plan month"


class TestProviderUsageParsing:
    def test_openai_report_aggregation(self, srv):
        from ai.provider_usage import _openai_cost, _openai_usage
        pages = [{"data": [{
            "start_time": 1730419200,
            "results": [
                {"model": "gpt-4o-mini", "input_tokens": 1000, "output_tokens": 500, "num_model_requests": 5},
                {"model": "gpt-4o", "input_tokens": 200, "output_tokens": 100, "num_model_requests": 1},
            ],
        }]}]
        tokens, requests, by_model, by_day = _openai_usage(pages)
        assert tokens == 1800 and requests == 6
        assert by_model[0]["model"] == "gpt-4o-mini"
        assert by_day[0]["requests"] == 6
        assert _openai_cost([{"data": [{"results": [{"amount": {"currency": "usd", "value": 1.25}}]}]}]) == 1.25

    def test_anthropic_cost_is_converted_from_cents(self, srv):
        from ai.provider_usage import _anthropic_cost, _anthropic_tokens
        result = {"uncached_input_tokens": 100, "cache_read_input_tokens": 30, "output_tokens": 20}
        assert _anthropic_tokens(result) == 150
        pages = [{"data": [{"results": [{"amount": "125.5"}, {"amount": "24.5"}]}]}]
        assert _anthropic_cost(pages) == 1.5


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
        assert d["estimated_cost_usd"] == d["total_cost_usd"]
        assert d["measurement"]["cost"] == "estimated"
        models = {b["model"] for b in d["by_model"]}
        assert models == {"gpt-4o-mini", "gpt-4o"}
        # Filtered by model
        r2 = client.get("/api/admin/ai-usage/summary?model=gpt-4o",
                        headers=_h("T-ADMIN"))
        assert r2.json()["total_calls"] == 1
        assert r2.json()["total_cost_usd"] == 0.001

        # Filtered by provider
        self._seed_log(fake, provider="anthropic", model="claude-test")
        r3 = client.get("/api/admin/ai-usage/summary?provider=anthropic", headers=_h("T-ADMIN"))
        assert r3.json()["total_calls"] == 1
        assert r3.json()["by_model"][0]["model"] == "claude-test"

    def test_logs_pagination(self, srv):
        _, fake, client = srv
        for _ in range(25): self._seed_log(fake)
        r = client.get("/api/admin/ai-usage/logs?limit=10", headers=_h("T-ADMIN"))
        d = r.json()
        assert r.status_code == 200
        assert d["total"] == 25 and d["limit"] == 10
        assert len(d["items"]) == 10

    def test_fee_breakdown_is_aggregated_and_exposed(self, srv):
        _, fake, client = srv
        self._seed_log(
            fake, estimated_cost_usd=0.001, base_cost_usd=0.001,
            ai_fee_percent=25, ai_fee_usd=0.00025,
            billable_cost_usd=0.00125,
        )
        summary = client.get(
            "/api/admin/ai-usage/summary", headers=_h("T-ADMIN")
        ).json()
        assert summary["base_cost_usd"] == pytest.approx(0.001)
        assert summary["ai_fee_usd"] == pytest.approx(0.00025)
        assert summary["billable_cost_usd"] == pytest.approx(0.00125)
        assert summary["by_model"][0]["billable_cost_usd"] == pytest.approx(0.00125)

        detail = client.get(
            "/api/admin/ai-usage/logs", headers=_h("T-ADMIN")
        ).json()["items"][0]
        assert detail["ai_fee_percent"] == 25
        assert detail["billing_cost_source"] == "estimated"
        assert detail["billable_cost_usd"] == pytest.approx(0.00125)

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

    def test_manual_zero_pricing_is_rejected(self, srv):
        _, _, client = srv
        r = client.put("/api/admin/ai-pricing", headers=_h("T-ADMIN"),
                       json={"model": "modelo-sin-costo", "input_per_million": 0,
                             "output_per_million": 0})
        assert r.status_code == 400
        assert "mayor a cero" in r.text

    def test_reset_keeps_custom_model_pricing(self, srv):
        _, _, client = srv
        saved = client.put("/api/admin/ai-pricing", headers=_h("T-ADMIN"),
                           json={"model": "modelo-personalizado", "input_per_million": 2,
                                 "output_per_million": 8})
        assert saved.status_code == 200
        reset = client.post("/api/admin/ai-pricing/reset", headers=_h("T-ADMIN"))
        assert reset.status_code == 200
        assert reset.json()["models"]["modelo-personalizado"] == {"input": 2.0, "output": 8.0}

    def test_platform_fee_policy_and_organization_override(self, srv):
        server, fake, client = srv
        current = client.get("/api/organizations/current", headers=_h("T-ADMIN")).json()
        policy = client.get("/api/platform/ai-billing", headers=_h("T-ADMIN"))
        assert policy.status_code == 200
        assert policy.json()["default_fee_percent"] == 20.0

        updated = client.put(
            "/api/platform/ai-billing", headers=_h("T-ADMIN"),
            json={"default_fee_percent": 35},
        )
        assert updated.status_code == 200
        assert updated.json()["default_fee_percent"] == 35.0
        assert client.put(
            "/api/platform/ai-billing", headers=_h("T-AGENT"),
            json={"default_fee_percent": 10},
        ).status_code == 403

        override = client.patch(
            f"/api/platform/organizations/{current['organization_id']}/subscription",
            headers=_h("T-ADMIN"), json={"ai_fee_percent": 12.5},
        )
        assert override.status_code == 200
        assert override.json()["ai_billing"]["fee_percent"] == 12.5
        assert override.json()["ai_billing"]["has_custom_fee"] is True
        assert "this_month" in override.json()["ai_billing"]
        assert client.patch(
            f"/api/platform/organizations/{current['organization_id']}/subscription",
            headers=_h("T-ADMIN"), json={"ai_fee_percent": 501},
        ).status_code == 400

        from ai import usage
        assert _run(usage.effective_fee_percent(fake, current["organization_id"])) == 12.5

    def test_rbac_403(self, srv):
        _, _, client = srv
        # Agente y visualizador tienen ai_view por jerarquía de permisos.
        assert client.get("/api/admin/ai-usage/summary",
                          headers=_h("T-AGENT")).status_code == 200
        assert client.get("/api/admin/ai-usage/logs",
                          headers=_h("T-VIEWER")).status_code == 200
        assert client.put("/api/admin/ai-pricing", headers=_h("T-AGENT"),
                          json={"model": "x", "input_per_million": 0,
                                "output_per_million": 0}).status_code == 403

    def test_invalid_date_range_400(self, srv):
        _, _, client = srv
        r = client.get("/api/admin/ai-usage/summary?from=2025-02-01&to=2025-01-01",
                       headers=_h("T-ADMIN"))
        assert r.status_code == 400
        assert "no puede ser mayor" in r.text

    def test_reporting_status_and_admin_key_storage(self, srv):
        _, fake, client = srv
        r = client.get("/api/admin/ai-usage/provider-reporting", headers=_h("T-ADMIN"))
        assert r.status_code == 200
        providers = {item["provider"]: item for item in r.json()["providers"]}
        assert providers["openai"]["requires_separate_key"] is True
        assert providers["gemini"]["reporting_supported"] is False

        saved = client.put(
            "/api/admin/ai-usage/provider-reporting/openai",
            headers=_h("T-ADMIN"), json={"key": "sk-admin-secret1234"},
        )
        assert saved.status_code == 200
        item = next(x for x in saved.json()["providers"] if x["provider"] == "openai")
        assert item["configured"] is True
        assert item["masked"].endswith("1234")
        raw_doc = fake.platform_secrets.docs[-1]
        assert "sk-admin-secret1234" not in str(raw_doc)

    def test_reporting_key_rejects_unsupported_provider(self, srv):
        _, _, client = srv
        r = client.put(
            "/api/admin/ai-usage/provider-reporting/gemini",
            headers=_h("T-ADMIN"), json={"key": "secret"},
        )
        assert r.status_code == 400


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
            latency_ms = 80; provider = "built_in"
        async def fake_chat(self, **k): return _Res()
        monkeypatch.setattr(providers.BuiltInProvider, "chat", fake_chat)

        _run(pmod.process_inbound(fake, "conv9", "wamid.9", wa_send=None))
        logs = [l for l in fake.ai_usage_logs.docs if l["purpose"] == "bot_pipeline"]
        assert len(logs) == 1
        assert logs[0]["conversation_id"] == "conv9"
        assert logs[0]["message_id"] == "wamid.9"
        assert logs[0]["status"] == "success"
