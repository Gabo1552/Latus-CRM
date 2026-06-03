"""Tests for catalog→bot integration (Phase 4)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_catbot_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")
os.environ.setdefault("EMERGENT_LLM_KEY", "test-key")

from test_simulate_inbound import _FakeDB, _run  # type: ignore


# -----------------------------------------------------------------------------
# Unit tests for the catalog_search module
# -----------------------------------------------------------------------------


class TestDetectIntent:
    def test_01_price(self):
        from ai.catalog_search import detect_commercial_intent as d
        r = d("¿cuánto sale la zapatilla Latus?")
        assert r["is_commercial"] is True
        assert r["intent_type"] == "price"

    def test_02_no_match(self):
        from ai.catalog_search import detect_commercial_intent as d
        r = d("hola buenas tardes")
        assert r["is_commercial"] is False

    def test_03_sku(self):
        from ai.catalog_search import detect_commercial_intent as d
        r = d("LATUS-DEMO-1?")
        assert r["is_commercial"] is True
        assert r["intent_type"] == "sku_lookup"

    def test_04_extract_query(self):
        from ai.catalog_search import extract_product_query as e
        q = e("¿cuánto cuesta la zapatilla running?")
        assert "zapatilla" in q.lower()
        assert "running" in q.lower()
        assert "cuesta" not in q.lower()
        assert "cuánto" not in q.lower()

    def test_negotiation_detect(self):
        from ai.catalog_search import detect_negotiation as n
        assert n("¿me hacen un descuento?")
        assert n("tienen financiación en cuotas?")
        assert not n("hola buenas tardes")


@pytest.fixture
def fake():
    f = _FakeDB()
    # Seed 3 products
    _run(f.products.insert_one({
        "product_id": "p1", "name": "Zapatilla Latus Demo",
        "sku": "LATUS-DEMO-1", "category": "Indumentaria",
        "price": 89999, "currency": "ARS", "stock_status": "disponible",
        "tags": ["zapatilla", "running"], "active": True, "deleted_at": None,
        "description": "Modelo running unisex", "promo_price": None,
        "commercial_conditions": None, "image_url": None, "external_link": None,
        "created_at": "2025-01-01T00:00:00+00:00",
    }))
    _run(f.products.insert_one({
        "product_id": "p2", "name": "Remera Latus Negra",
        "sku": "LATUS-RM-N", "category": "Indumentaria",
        "price": 12500, "currency": "ARS", "stock_status": "disponible",
        "tags": ["remera"], "active": True, "deleted_at": None,
        "description": "", "promo_price": None,
        "commercial_conditions": None, "image_url": None, "external_link": None,
        "created_at": "2025-01-01T00:00:00+00:00",
    }))
    # An inactive/deleted product that must NOT appear
    _run(f.products.insert_one({
        "product_id": "p3", "name": "Zapatilla Vieja",
        "sku": "OLD-1", "active": False, "deleted_at": "2024-12-01T00:00:00+00:00",
        "price": 1, "currency": "ARS", "tags": ["zapatilla"],
    }))
    return f


class TestSearch:
    def test_05_sku_exact(self, fake):
        from ai.catalog_search import search_catalog
        rows = _run(search_catalog(fake, "LATUS-DEMO-1"))
        assert len(rows) == 1
        assert rows[0]["sku"] == "LATUS-DEMO-1"
        assert rows[0]["price"] == 89999

    def test_06_name_match(self, fake):
        from ai.catalog_search import search_catalog
        rows = _run(search_catalog(fake, "zapatilla"))
        skus = {r["sku"] for r in rows}
        assert "LATUS-DEMO-1" in skus
        assert "OLD-1" not in skus  # excluded (inactive + deleted)

    def test_07_empty_query_no_match(self, fake):
        from ai.catalog_search import search_catalog
        rows = _run(search_catalog(fake, "asdfqwerty"))
        # fallback returns top items by name
        assert all(r["sku"] != "OLD-1" for r in rows)

    def test_08_excludes_soft_deleted(self, fake):
        from ai.catalog_search import search_catalog
        rows = _run(search_catalog(fake, "vieja"))
        assert all(r.get("sku") != "OLD-1" for r in rows)


class TestFormatForLLM:
    def test_09_with_products(self, fake):
        from ai.catalog_search import search_catalog, format_catalog_for_llm
        rows = _run(search_catalog(fake, "LATUS-DEMO-1"))
        out = format_catalog_for_llm(rows)
        assert "CATÁLOGO DISPONIBLE" in out
        assert "Zapatilla Latus Demo" in out
        assert "89999" in out
        assert "ARS" in out

    def test_10_empty_list(self):
        from ai.catalog_search import format_catalog_for_llm
        out = format_catalog_for_llm([])
        assert "NO inventes" in out
        assert "productos ni precios" in out


# -----------------------------------------------------------------------------
# Pipeline end-to-end (mocked LLM, real catalog injection)
# -----------------------------------------------------------------------------


class TestPipelineCatalogIntegration:
    def _seed_conv(self, db, last_text="Hola"):
        _run(db.contacts.insert_one({"id": "c1", "name": "X", "whatsapp_id": "+1"}))
        _run(db.conversations.insert_one({
            "id": "conv1", "contact_id": "c1", "channel": "whatsapp",
            "status": "open", "bot_enabled": True, "bot_status": "bot_activo",
            "unread": 0, "last_message_at": "2025-01-01T00:00:00+00:00",
        }))
        _run(db.messages.insert_one({
            "id": "m1", "conversation_id": "conv1", "direction": "inbound",
            "sender_type": "contact", "body": last_text,
            "external_message_id": "wamid.1",
            "created_at": "2025-01-01T00:01:00+00:00",
        }))

    def _patch_llm(self, monkeypatch, decision="reply_with_bot", reply="ok"):
        captured = {"system_prompt": None, "called": 0}
        async def fake(*, system_prompt, user_messages_block, model="x", db=None,
                       purpose="bot_pipeline", conversation_id=None,
                       message_id=None, user_id=None, **_kw):
            captured["called"] += 1
            captured["system_prompt"] = system_prompt
            parsed = {
                "intent": "consulta", "confidence": 0.9, "decision": decision,
                "reply": reply, "summary": "x",
                "human_required_reason": None,
                "next_best_action": None,
                "lead_status_suggested": None,
                "bot_status_suggested": None,
                "evidence_for_status_change": "",
            }
            return parsed, ""
        from ai import pipeline as p
        monkeypatch.setattr(p, "call_llm_json", fake)
        return captured

    def test_11_sku_lookup_injects_real_price(self, fake, monkeypatch):
        from ai import pipeline
        cap = self._patch_llm(monkeypatch)
        self._seed_conv(fake, last_text="¿precio de LATUS-DEMO-1?")
        _run(pipeline.process_inbound(fake, "conv1", "wamid.1", wa_send=None))
        assert cap["called"] == 1
        sp = cap["system_prompt"] or ""
        assert "LATUS-DEMO-1" in sp
        assert "89999" in sp
        assert "CATÁLOGO DISPONIBLE" in sp

    def test_12_negotiation_short_circuits(self, fake, monkeypatch):
        from ai import pipeline
        cap = self._patch_llm(monkeypatch)
        self._seed_conv(fake, last_text="quiero un descuento")
        event = _run(pipeline.process_inbound(fake, "conv1", "wamid.1", wa_send=None))
        assert cap["called"] == 0, "LLM must NOT be called when negotiation is detected"
        assert event["decision"] == "require_human"
        assert "Negociación" in event["human_required_reason"]
        conv = fake.conversations.docs[0]
        assert conv["bot_enabled"] is False
        assert conv["bot_status"] == "requiere_humano"

    def test_13_models_query_injects_catalog(self, fake, monkeypatch):
        from ai import pipeline
        cap = self._patch_llm(monkeypatch)
        self._seed_conv(fake, last_text="qué modelos tienen?")
        _run(pipeline.process_inbound(fake, "conv1", "wamid.1", wa_send=None))
        sp = cap["system_prompt"] or ""
        assert "CATÁLOGO DISPONIBLE" in sp
        # at least one product surfaced
        assert "Zapatilla" in sp or "Remera" in sp

    def test_14_greeting_no_catalog_block(self, fake, monkeypatch):
        from ai import pipeline
        cap = self._patch_llm(monkeypatch)
        self._seed_conv(fake, last_text="hola buenas tardes")
        _run(pipeline.process_inbound(fake, "conv1", "wamid.1", wa_send=None))
        sp = cap["system_prompt"] or ""
        assert "CATÁLOGO DISPONIBLE" not in sp
        assert "NO inventes productos" not in sp

    def test_15_bot_event_audit_fields(self, fake, monkeypatch):
        from ai import pipeline
        self._patch_llm(monkeypatch)
        self._seed_conv(fake, last_text="¿precio de LATUS-DEMO-1?")
        _run(pipeline.process_inbound(fake, "conv1", "wamid.1", wa_send=None))
        evs = fake.bot_events.docs
        assert evs
        e = evs[-1]
        assert e.get("catalog_matched") is True
        assert e.get("catalog_intent_type") in ("sku_lookup", "price")
        assert e.get("catalog_products_returned") >= 1
        assert "LATUS-DEMO-1" in (e.get("raw_input_excerpt") or "")
