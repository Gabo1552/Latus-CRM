from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dealership_demo_data import build_dealership_demo_dataset  # noqa: E402
from scripts.reset_production_for_sales_demo import (  # noqa: E402
    DEFAULT_ADMIN_EMAIL,
    DEMO_ORGANIZATION_ID,
    LATUS_ORGANIZATION_ID,
    PRESERVED_COLLECTIONS,
    RESET_COLLECTIONS,
    build_seed_documents,
)


FIXED_NOW = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)


def _ids(rows: list[dict], key: str) -> set[str]:
    values = [row[key] for row in rows]
    assert len(values) == len(set(values))
    return set(values)


def test_dealership_dataset_is_complete_and_relationally_consistent():
    data = build_dealership_demo_dataset(FIXED_NOW)

    assert len(data["users"]) == 4
    assert len(data["contacts"]) == 10
    assert len(data["conversations"]) == 10
    assert len(data["messages"]) >= 30
    assert len(data["products"]) >= 9
    assert len(data["appointments"]) >= 6
    assert len(data["sales"]) >= 2

    user_ids = _ids(data["users"], "user_id")
    work_area_ids = _ids(data["work_areas"], "id")
    product_ids = _ids(data["products"], "product_id")
    contact_ids = _ids(data["contacts"], "id")
    lead_ids = _ids(data["leads"], "id")
    conversation_ids = _ids(data["conversations"], "id")

    assert all(set(user["work_areas"]) <= work_area_ids for user in data["users"])
    assert all(lead["contact_id"] in contact_ids for lead in data["leads"])
    assert all(not lead.get("assigned_to") or lead["assigned_to"] in user_ids for lead in data["leads"])
    assert all(conversation["contact_id"] in contact_ids for conversation in data["conversations"])
    assert all(conversation["lead_id"] in lead_ids for conversation in data["conversations"])
    assert all(message["conversation_id"] in conversation_ids for message in data["messages"])
    assert all(appointment["contact_id"] in contact_ids for appointment in data["appointments"])
    assert all(appointment["lead_id"] in lead_ids for appointment in data["appointments"])
    assert all(sale["contact_id"] in contact_ids for sale in data["sales"])

    for sale in data["sales"]:
        for line in sale["lines"]:
            assert line["product_id"] in product_ids
            assert line["unit_price"] > 0
            assert line["line_total"] == line["unit_price"] * line["quantity"]


def test_promotions_have_bounded_duration_or_units_and_sales_keep_snapshots():
    data = build_dealership_demo_dataset(FIXED_NOW)
    products = {row["product_id"]: row for row in data["products"]}

    promotional = [row for row in products.values() if row.get("promo_price")]
    assert promotional
    for product in promotional:
        assert product["promo_price"] < product["price"]
        assert product.get("promo_end_at") or product.get("promo_unit_limit")

    for sale in data["sales"]:
        for line in sale["lines"]:
            product = products[line["product_id"]]
            assert line["name"]
            assert line["sku"] == product["sku"]
            assert "unit_price" in line and "list_price" in line


def test_reset_builds_empty_latus_tenant_and_isolated_dealership_tenant():
    admin = {
        "user_id": "user_platform_admin",
        "email": DEFAULT_ADMIN_EMAIL,
        "name": "Administrador",
        "password_hash": "hash-existente",
        "auth_provider": "local",
        "created_at": FIXED_NOW.isoformat(),
    }
    documents = build_seed_documents(admin)

    assert {row["organization_id"] for row in documents["organizations"]} == {
        LATUS_ORGANIZATION_ID,
        DEMO_ORGANIZATION_ID,
    }
    stored_admin = next(row for row in documents["users"] if row["email"] == DEFAULT_ADMIN_EMAIL)
    assert stored_admin["password_hash"] == "hash-existente"
    assert stored_admin["default_organization_id"] == LATUS_ORGANIZATION_ID

    admin_memberships = {
        row["organization_id"]
        for row in documents["memberships"]
        if row["user_id"] == stored_admin["user_id"]
    }
    assert admin_memberships == {LATUS_ORGANIZATION_ID, DEMO_ORGANIZATION_ID}

    for collection_name in (
        "contacts", "leads", "conversations", "messages", "appointments",
        "products", "sales", "tasks", "notes", "ai_usage_logs",
    ):
        assert documents[collection_name]
        assert {row["organization_id"] for row in documents[collection_name]} == {
            DEMO_ORGANIZATION_ID,
        }

    assert documents["bot_settings"][0]["_id"] == f"{DEMO_ORGANIZATION_ID}:default"
    organizations = {row["organization_id"]: row for row in documents["organizations"]}
    assert organizations[LATUS_ORGANIZATION_ID]["organization_kind"] == "internal"
    assert organizations[LATUS_ORGANIZATION_ID]["billing_exempt"] is True
    assert organizations[DEMO_ORGANIZATION_ID]["organization_kind"] == "demo"
    assert organizations[DEMO_ORGANIZATION_ID]["billing_exempt"] is True
    assert organizations[DEMO_ORGANIZATION_ID]["automation_enabled"] is False
    assert any(item["title"] == "Entrega Toyota Yaris" for item in documents["appointments"])
    assert not any(
        row.get("organization_id") == LATUS_ORGANIZATION_ID
        for collection_name in ("contacts", "leads", "sales", "appointments")
        for row in documents[collection_name]
    )


def test_reset_requires_existing_local_admin_password():
    with pytest.raises(ValueError, match="password_hash"):
        build_seed_documents({"user_id": "user_platform_admin"})


def test_preserved_and_reset_collections_never_overlap():
    assert PRESERVED_COLLECTIONS
    assert RESET_COLLECTIONS
    assert PRESERVED_COLLECTIONS.isdisjoint(RESET_COLLECTIONS)
    assert "system_migrations" in PRESERVED_COLLECTIONS
    assert "platform_secrets" in PRESERVED_COLLECTIONS
    assert "system_ai_credentials" in PRESERVED_COLLECTIONS
    assert "organizations" in RESET_COLLECTIONS
    assert "user_sessions" in RESET_COLLECTIONS
