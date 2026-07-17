from datetime import datetime, timezone

from demo_data import build_demo_dataset


FIXED_NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)


def test_demo_dataset_covers_the_crm_and_uses_argentine_values():
    data = build_demo_dataset(FIXED_NOW)

    expected_collections = {
        "users", "work_areas", "tags", "products", "contacts", "leads",
        "conversations", "messages", "notes", "tasks", "appointments",
        "ai_usage_logs",
    }
    assert expected_collections.issubset(data)
    assert len(data["contacts"]) >= 10
    assert len(data["products"]) >= 10
    assert len(data["appointments"]) >= 6
    assert len(data["ai_usage_logs"]) >= 20
    assert all(product["currency"] == "ARS" for product in data["products"])
    assert all(contact["phone"].startswith("+54") for contact in data["contacts"])
    assert data["bot_settings"]["appointment_timezone"] == "America/Argentina/Buenos_Aires"
    assert data["bot_settings"]["appointment_scheduling_enabled"] is True
    assert data["bot_settings"]["appointment_reminders_enabled"] is True


def test_demo_dataset_relations_and_sale_snapshots_are_consistent():
    data = build_demo_dataset(FIXED_NOW)
    contact_ids = {item["id"] for item in data["contacts"]}
    lead_ids = {item["id"] for item in data["leads"]}
    conversation_ids = {item["id"] for item in data["conversations"]}
    product_ids = {item["product_id"] for item in data["products"]}

    assert all(lead["contact_id"] in contact_ids for lead in data["leads"])
    assert all(conv["contact_id"] in contact_ids and conv["lead_id"] in lead_ids
               for conv in data["conversations"])
    assert all(message["conversation_id"] in conversation_ids for message in data["messages"])
    assert all(product["id"] in product_ids for lead in data["leads"]
               for product in lead.get("products", []))

    won = [lead for lead in data["leads"] if lead["status"] == "won"]
    assert won
    assert all(lead.get("sale_snapshot", {}).get("products") for lead in won)
    assert all(line["currency"] == "ARS" for lead in won
               for line in lead["sale_snapshot"]["products"])


def test_demo_has_active_date_and_unit_limited_promotions():
    products = build_demo_dataset(FIXED_NOW)["products"]
    by_limit = {item["promo_limit_type"]: item for item in products
                if item.get("promo_price") is not None}

    assert by_limit["date"]["promo_end_at"]
    assert by_limit["units"]["promo_unit_limit"] > by_limit["units"]["promo_units_used"]
