from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from customer360 import build_customer_360


def run(coro):
    return asyncio.run(coro)


def matches(document, query):
    for key, expected in (query or {}).items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Cursor:
    def __init__(self, documents):
        self.documents = list(documents)

    def sort(self, field, direction):
        self.documents.sort(key=lambda item: item.get(field) or "", reverse=direction < 0)
        return self

    async def to_list(self, limit):
        return [dict(item) for item in self.documents[:limit]]


class Collection:
    def __init__(self, documents=()):
        self.documents = [dict(item) for item in documents]

    async def find_one(self, query, projection=None):
        return next((dict(item) for item in self.documents if matches(item, query)), None)

    def find(self, query, projection=None):
        return Cursor(item for item in self.documents if matches(item, query))


class Database:
    def __init__(self):
        self.contacts = Collection([{
            "id": "contact_1", "name": "Ana López", "phone": "+54 351 555 0101",
            "created_at": "2026-07-01T10:00:00+00:00",
        }])
        self.leads = Collection([{
            "id": "lead_1", "contact_id": "contact_1", "title": "Tratamiento facial",
            "status": "qualified", "assigned_to": "user_1",
            "updated_at": "2026-07-02T10:00:00+00:00",
        }])
        self.conversations = Collection([{
            "id": "conv_1", "contact_id": "contact_1", "channel": "whatsapp",
            "assigned_to": "user_1", "status": "open", "unread": 1,
            "last_message_at": "2026-07-04T10:00:00+00:00",
        }])
        self.messages = Collection([
            {"id": "msg_1", "conversation_id": "conv_1", "sender_type": "contact",
             "sender_name": "Ana", "body": "Quiero reservar", "created_at": "2026-07-04T09:00:00+00:00"},
            {"id": "msg_2", "conversation_id": "conv_1", "sender_type": "bot",
             "sender_name": "Aura", "body": "¿Qué día preferís?", "created_at": "2026-07-04T10:00:00+00:00"},
        ])
        self.appointments = Collection([{
            "id": "appt_1", "contact_id": "contact_1", "assigned_to": "user_1",
            "title": "Limpieza facial", "status": "completed", "service_name": "Limpieza profunda",
            "start_time": "2026-07-05T14:00:00+00:00",
        }])
        self.sales = Collection([{
            "sale_id": "sale_1", "contact_id": "contact_1", "created_by": "user_1",
            "status": "confirmed", "currency": "ARS", "total": 50000, "amount_paid": 30000,
            "balance_due": 20000, "confirmed_at": "2026-07-05T15:00:00+00:00",
            "created_at": "2026-07-05T14:30:00+00:00",
            "lines": [{"product_id": "prod_1", "name": "Sérum", "quantity": 2,
                       "line_total": 50000, "currency": "ARS"}],
            "payments": [{"payment_id": "pay_1", "amount": 30000, "method": "transfer",
                          "received_at": "2026-07-05T15:10:00+00:00"}],
        }])
        self.tasks = Collection([{
            "id": "task_1", "lead_id": "lead_1", "assigned_to": "user_1",
            "title": "Confirmar evolución", "status": "todo",
            "created_at": "2026-07-06T10:00:00+00:00",
        }])
        self.notes = Collection([{
            "id": "note_1", "lead_id": "lead_1", "body": "Prefiere horario tarde",
            "author_name": "Sofía", "created_at": "2026-07-03T10:00:00+00:00",
        }])
        self.bot_events = Collection([{
            "id": "event_1", "conversation_id": "conv_1", "type": "human_handoff",
            "created_at": "2026-07-04T10:05:00+00:00",
        }])


def test_customer_360_unifies_commercial_and_service_history():
    result = run(build_customer_360(
        Database(), "contact_1", user_id="admin_1",
        permissions={"crm_admin", "crm_view", "inbox_admin", "inbox_view", "calendar_admin", "calendar_view"},
    ))

    assert result["contact"]["name"] == "Ana López"
    assert result["summary"]["lifetime_value"] == 50000
    assert result["summary"]["amount_paid"] == 30000
    assert result["summary"]["balance_due"] == 20000
    assert result["summary"]["bot_messages"] == 1
    assert result["summary"]["handoffs"] == 1
    assert result["products"] == [{
        "product_id": "prod_1", "name": "Sérum", "sku": None, "quantity": 2,
        "total_spent": 50000.0, "currency": "ARS",
        "last_purchase_at": "2026-07-05T15:00:00+00:00",
    }]
    assert {item["type"] for item in result["timeline"]} == {
        "message", "appointment", "sale", "payment", "note", "task", "bot_event",
    }


def test_customer_360_hides_sections_and_records_outside_assignment():
    db = Database()
    db.leads.documents[0]["assigned_to"] = "user_2"
    db.sales.documents[0]["created_by"] = "user_2"

    result = run(build_customer_360(
        db, "contact_1", user_id="user_1", permissions={"crm_view"},
    ))

    assert result["section_access"] == {"crm": True, "inbox": False, "calendar": False}
    assert result["leads"] == []
    assert result["sales"] == []
    assert result["conversations"] == []
    assert result["appointments"] == []
    assert result["timeline"] == []


def test_customer_360_returns_none_for_unknown_contact():
    assert run(build_customer_360(
        Database(), "missing", user_id="user_1", permissions={"crm_view"},
    )) is None
