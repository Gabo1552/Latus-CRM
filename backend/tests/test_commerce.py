"""Sales and inventory integration tests through the public API."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_commerce_tests")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENCRYPTION_KEY", "T9VemN99LrWMmb3im576htR6oNUwsyQdIhvFO9QuTI0=")

from test_simulate_inbound import _FakeDB, _run  # type: ignore


@pytest.fixture
def srv(monkeypatch):
    for module in list(sys.modules):
        if module == "server" or module.startswith(("commerce", "catalog", "whatsapp", "utils", "ai")):
            sys.modules.pop(module, None)
    import server  # type: ignore

    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    for role, token in (("admin", "T-ADMIN"), ("agent", "T-AGENT"), ("viewer", "T-VIEW")):
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
    _run(fake.contacts.insert_one({
        "id": "contact_1", "name": "Carolina Méndez", "phone": "+5491111111111",
        "created_at": "2025-01-01T00:00:00+00:00",
    }))
    return server, fake, TestClient(server.app)


def _h(token="T-ADMIN"):
    return {"Authorization": f"Bearer {token}"}


def _product(client, *, stock=5, promo=False):
    payload = {
        "name": "Sérum facial", "sku": f"SER-{stock}-{int(promo)}", "price": 20_000,
        "currency": "ARS", "track_stock": True, "stock_quantity": stock,
        "low_stock_threshold": 1,
    }
    if promo:
        payload.update({
            "promo_price": 15_000, "promo_limit_type": "units", "promo_unit_limit": 1,
        })
    response = client.post("/api/catalog/products", headers=_h(), json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _draft(client, product_id, *, quantity=1, token="T-ADMIN"):
    response = client.post("/api/sales", headers=_h(token), json={
        "contact_id": "contact_1",
        "lines": [{"product_id": product_id, "quantity": quantity}],
        "notes": "Venta de mostrador",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_product_supports_real_stock(srv):
    _, _, client = srv
    product = _product(client, stock=4)
    assert product["track_stock"] is True
    assert product["stock_quantity"] == 4
    assert product["stock_status"] == "disponible"
    assert product["low_stock"] is False


def test_draft_freezes_price_and_confirmation_decrements_stock(srv):
    _, _, client = srv
    product = _product(client, stock=5)
    sale = _draft(client, product["product_id"], quantity=2)
    assert sale["status"] == "draft"
    assert sale["total"] == 40_000

    client.put(
        f"/api/catalog/products/{product['product_id']}", headers=_h(), json={"price": 99_000}
    )
    confirmed = client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "confirmed"
    assert body["lines"][0]["unit_price"] == 20_000
    updated = client.get(f"/api/catalog/products/{product['product_id']}", headers=_h()).json()
    assert updated["stock_quantity"] == 3
    movements = client.get("/api/inventory/movements", headers=_h()).json()["items"]
    assert movements[0]["quantity_delta"] == -2
    assert movements[0]["sale_id"] == sale["sale_id"]


def test_insufficient_stock_keeps_sale_as_draft(srv):
    _, _, client = srv
    product = _product(client, stock=1)
    sale = _draft(client, product["product_id"], quantity=2)
    response = client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    assert response.status_code == 409
    assert "stock" in response.text.lower()
    stored = client.get(f"/api/sales/{sale['sale_id']}", headers=_h()).json()
    assert stored["status"] == "draft"
    updated = client.get(f"/api/catalog/products/{product['product_id']}", headers=_h()).json()
    assert updated["stock_quantity"] == 1


def test_payments_and_overpayment_control(srv):
    _, _, client = srv
    product = _product(client, stock=3)
    sale = _draft(client, product["product_id"], quantity=1)
    client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    partial = client.post(f"/api/sales/{sale['sale_id']}/payments", headers=_h(), json={
        "amount": 5_000, "method": "transfer", "reference": "TRX-100",
    })
    assert partial.status_code == 200, partial.text
    assert partial.json()["payment_status"] == "partial"
    assert partial.json()["balance_due"] == 15_000
    over = client.post(f"/api/sales/{sale['sale_id']}/payments", headers=_h(), json={
        "amount": 20_000, "method": "cash",
    })
    assert over.status_code == 409


def test_cancel_restores_stock_and_marks_refund_pending(srv):
    _, _, client = srv
    product = _product(client, stock=3)
    sale = _draft(client, product["product_id"], quantity=2)
    client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    client.post(f"/api/sales/{sale['sale_id']}/payments", headers=_h(), json={
        "amount": 10_000, "method": "card",
    })
    cancelled = client.post(f"/api/sales/{sale['sale_id']}/cancel", headers=_h(), json={
        "reason": "La clienta desistió de la compra",
    })
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["payment_status"] == "refund_pending"
    updated = client.get(f"/api/catalog/products/{product['product_id']}", headers=_h()).json()
    assert updated["stock_quantity"] == 3


def test_stale_promotion_is_not_consumed_twice(srv):
    _, _, client = srv
    product = _product(client, stock=3, promo=True)
    first = _draft(client, product["product_id"])
    second = _draft(client, product["product_id"])
    assert first["lines"][0]["promotion_applied"] is True
    assert second["lines"][0]["promotion_applied"] is True
    assert client.post(f"/api/sales/{first['sale_id']}/confirm", headers=_h()).status_code == 200
    rejected = client.post(f"/api/sales/{second['sale_id']}/confirm", headers=_h())
    assert rejected.status_code == 409
    assert "promoción" in rejected.text.lower()


def test_inventory_adjustment_and_permissions(srv):
    _, _, client = srv
    product = _product(client, stock=2)
    denied = client.post("/api/inventory/adjustments", headers=_h("T-AGENT"), json={
        "product_id": product["product_id"], "quantity_delta": 3,
        "reason": "purchase", "notes": "Compra proveedor",
    })
    assert denied.status_code == 403
    added = client.post("/api/inventory/adjustments", headers=_h(), json={
        "product_id": product["product_id"], "quantity_delta": 3,
        "reason": "purchase", "notes": "Compra proveedor",
    })
    assert added.status_code == 200, added.text
    assert added.json()["stock_after"] == 5
    negative = client.post("/api/inventory/adjustments", headers=_h(), json={
        "product_id": product["product_id"], "quantity_delta": -6,
        "reason": "adjustment", "notes": "Conteo",
    })
    assert negative.status_code == 409


def test_agent_only_sees_own_sales(srv):
    _, _, client = srv
    product = _product(client, stock=5)
    _draft(client, product["product_id"], token="T-ADMIN")
    own = _draft(client, product["product_id"], token="T-AGENT")
    listed = client.get("/api/sales", headers=_h("T-AGENT"))
    assert listed.status_code == 200
    assert [item["sale_id"] for item in listed.json()["items"]] == [own["sale_id"]]
    all_sales = client.get("/api/sales", headers=_h()).json()
    assert all_sales["total"] == 2


def test_confirm_and_cancel_are_idempotent(srv):
    _, _, client = srv
    product = _product(client, stock=4)
    sale = _draft(client, product["product_id"], quantity=2)
    first = client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    second = client.post(f"/api/sales/{sale['sale_id']}/confirm", headers=_h())
    assert first.status_code == second.status_code == 200
    after_confirm = client.get(f"/api/catalog/products/{product['product_id']}", headers=_h()).json()
    assert after_confirm["stock_quantity"] == 2

    payload = {"reason": "Prueba de cancelación idempotente"}
    cancel_first = client.post(f"/api/sales/{sale['sale_id']}/cancel", headers=_h(), json=payload)
    cancel_second = client.post(f"/api/sales/{sale['sale_id']}/cancel", headers=_h(), json=payload)
    assert cancel_first.status_code == cancel_second.status_code == 200
    after_cancel = client.get(f"/api/catalog/products/{product['product_id']}", headers=_h()).json()
    assert after_cancel["stock_quantity"] == 4


def test_agent_cannot_override_catalog_price(srv):
    _, _, client = srv
    product = _product(client, stock=2)
    response = client.post("/api/sales", headers=_h("T-AGENT"), json={
        "contact_id": "contact_1",
        "lines": [{"product_id": product["product_id"], "quantity": 1, "unit_price": 1}],
    })
    assert response.status_code == 403


def test_partial_confirmation_rolls_back_previous_products(srv):
    _, _, client = srv
    available = _product(client, stock=2)
    unavailable = _product(client, stock=0)
    response = client.post("/api/sales", headers=_h(), json={
        "contact_id": "contact_1",
        "lines": [
            {"product_id": available["product_id"], "quantity": 1},
            {"product_id": unavailable["product_id"], "quantity": 1},
        ],
    })
    assert response.status_code == 200
    sale_id = response.json()["sale_id"]
    confirmed = client.post(f"/api/sales/{sale_id}/confirm", headers=_h())
    assert confirmed.status_code == 409
    restored = client.get(f"/api/catalog/products/{available['product_id']}", headers=_h()).json()
    assert restored["stock_quantity"] == 2
    assert restored["stock_status"] == "disponible"
