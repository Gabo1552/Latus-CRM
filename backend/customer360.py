"""Customer 360 read model.

The CRM keeps each operational record in its domain collection.  This module
builds a tenant-scoped, permission-aware projection without duplicating those
records or introducing a second source of truth.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any


def _public(document: dict | None) -> dict | None:
    if not document:
        return document
    return {key: value for key, value in document.items() if key not in {"_id", "organization_id"}}


async def _find(collection, query: dict, *, sort: str, direction: int = -1,
                limit: int = 250) -> list[dict]:
    docs = await collection.find(query, {"_id": 0, "organization_id": 0}) \
        .sort(sort, direction).to_list(limit)
    return [_public(item) for item in docs]


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _timestamp(document: dict, *fields: str) -> str | None:
    for field in fields:
        value = document.get(field)
        if value:
            return str(value)
    return None


def _timeline_event(*, event_id: str, event_type: str, occurred_at: str | None,
                    title: str, description: str | None = None,
                    entity_id: str | None = None, status: str | None = None,
                    channel: str | None = None, actor_name: str | None = None,
                    metadata: dict | None = None) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "occurred_at": occurred_at,
        "title": title,
        "description": description,
        "entity_id": entity_id,
        "status": status,
        "channel": channel,
        "actor_name": actor_name,
        "metadata": metadata or {},
    }


async def build_customer_360(
    db,
    contact_id: str,
    *,
    user_id: str,
    permissions: set[str],
    activity_limit: int = 150,
) -> dict | None:
    """Build the visible Customer 360 projection for the current tenant."""
    contact = _public(await db.contacts.find_one({"id": contact_id}, {"_id": 0}))
    if not contact:
        return None

    crm_all = "crm_admin" in permissions
    inbox_visible = "inbox_view" in permissions
    inbox_all = "inbox_admin" in permissions
    calendar_visible = "calendar_view" in permissions
    calendar_all = "calendar_admin" in permissions

    lead_query: dict[str, Any] = {"contact_id": contact_id}
    sale_query: dict[str, Any] = {"contact_id": contact_id}
    if not crm_all:
        lead_query["assigned_to"] = user_id
        sale_query["created_by"] = user_id

    conversation_query: dict[str, Any] = {"contact_id": contact_id}
    if not inbox_all:
        conversation_query["assigned_to"] = user_id
    appointment_query: dict[str, Any] = {"contact_id": contact_id}
    if not calendar_all:
        appointment_query["assigned_to"] = user_id

    leads, conversations, appointments, sales = await asyncio.gather(
        _find(db.leads, lead_query, sort="updated_at", limit=100),
        _find(db.conversations, conversation_query, sort="last_message_at", limit=100)
        if inbox_visible else asyncio.sleep(0, result=[]),
        _find(db.appointments, appointment_query, sort="start_time", direction=-1, limit=250)
        if calendar_visible else asyncio.sleep(0, result=[]),
        _find(db.sales, sale_query, sort="created_at", limit=250),
    )
    lead_ids = [item["id"] for item in leads if item.get("id")]

    tasks: list[dict] = []
    notes: list[dict] = []
    if lead_ids:
        task_query: dict[str, Any] = {"lead_id": {"$in": lead_ids}}
        if not crm_all:
            task_query["assigned_to"] = user_id
        tasks, notes = await asyncio.gather(
            _find(db.tasks, task_query, sort="created_at", limit=250),
            _find(db.notes, {"lead_id": {"$in": lead_ids}}, sort="created_at", limit=250),
        )

    conversation_ids = [item["id"] for item in conversations if item.get("id")]
    messages: list[dict] = []
    bot_events: list[dict] = []
    if conversation_ids:
        messages, bot_events = await asyncio.gather(
            _find(
                db.messages,
                {"conversation_id": {"$in": conversation_ids}},
                sort="created_at",
                limit=max(activity_limit, 250),
            ),
            _find(
                db.bot_events,
                {"conversation_id": {"$in": conversation_ids}},
                sort="created_at",
                limit=250,
            ),
        )

    confirmed_sales = [item for item in sales if item.get("status") == "confirmed"]
    payments = [
        {**_public(payment), "sale_id": sale.get("sale_id"), "currency": sale.get("currency", "ARS")}
        for sale in sales
        for payment in (sale.get("payments") or [])
    ]

    product_map: dict[str, dict] = {}
    for sale in confirmed_sales:
        for line in sale.get("lines") or []:
            key = str(line.get("product_id") or line.get("name") or "producto")
            product = product_map.setdefault(key, {
                "product_id": line.get("product_id"),
                "name": line.get("name") or "Producto",
                "sku": line.get("sku"),
                "quantity": 0,
                "total_spent": 0.0,
                "currency": line.get("currency") or sale.get("currency") or "ARS",
                "last_purchase_at": None,
            })
            product["quantity"] += int(line.get("quantity") or 0)
            product["total_spent"] = _money(product["total_spent"] + _money(line.get("line_total")))
            purchased_at = _timestamp(sale, "confirmed_at", "created_at")
            if purchased_at and (not product["last_purchase_at"] or purchased_at > product["last_purchase_at"]):
                product["last_purchase_at"] = purchased_at

    timeline: list[dict] = []
    conversation_channels = {
        item.get("id"): item.get("channel") or "whatsapp" for item in conversations
    }
    for message in messages:
        sender = message.get("sender_type") or "contact"
        sender_label = {"contact": "Cliente", "bot": "Bot", "agent": "Agente"}.get(sender, sender)
        timeline.append(_timeline_event(
            event_id=f"message:{message.get('id')}", event_type="message",
            occurred_at=_timestamp(message, "created_at"),
            title=f"Mensaje de {message.get('sender_name') or sender_label}",
            description=(message.get("body") or "")[:500],
            entity_id=message.get("conversation_id"),
            status=message.get("delivery_status"),
            channel=message.get("channel") or conversation_channels.get(message.get("conversation_id")),
            actor_name=message.get("sender_name"),
            metadata={"sender_type": sender, "message_type": message.get("message_type", "text")},
        ))
    for appointment in appointments:
        timeline.append(_timeline_event(
            event_id=f"appointment:{appointment.get('id')}", event_type="appointment",
            occurred_at=_timestamp(appointment, "start_time", "created_at"),
            title=appointment.get("title") or "Turno",
            description=appointment.get("service_name") or appointment.get("description"),
            entity_id=appointment.get("id"), status=appointment.get("status"),
            channel="calendar",
            metadata={"end_time": appointment.get("end_time"), "location": appointment.get("location")},
        ))
    for sale in sales:
        timeline.append(_timeline_event(
            event_id=f"sale:{sale.get('sale_id')}", event_type="sale",
            occurred_at=_timestamp(sale, "confirmed_at", "cancelled_at", "created_at"),
            title=f"Venta por {sale.get('currency', 'ARS')} {_money(sale.get('total')):,.2f}",
            description=f"{len(sale.get('lines') or [])} producto(s)",
            entity_id=sale.get("sale_id"), status=sale.get("status"), channel="crm",
            metadata={"payment_status": sale.get("payment_status"), "balance_due": sale.get("balance_due")},
        ))
    for payment in payments:
        timeline.append(_timeline_event(
            event_id=f"payment:{payment.get('payment_id')}", event_type="payment",
            occurred_at=_timestamp(payment, "received_at", "created_at"),
            title=f"Pago recibido por {payment.get('currency', 'ARS')} {_money(payment.get('amount')):,.2f}",
            description=payment.get("method"), entity_id=payment.get("sale_id"),
            status="received", channel="crm",
        ))
    for note in notes:
        timeline.append(_timeline_event(
            event_id=f"note:{note.get('id')}", event_type="note",
            occurred_at=_timestamp(note, "created_at"), title="Nota interna",
            description=(note.get("body") or "")[:500], entity_id=note.get("lead_id"),
            channel="crm", actor_name=note.get("author_name"),
        ))
    for task in tasks:
        timeline.append(_timeline_event(
            event_id=f"task:{task.get('id')}", event_type="task",
            occurred_at=_timestamp(task, "created_at", "due_date"),
            title=task.get("title") or "Tarea",
            description=task.get("description"), entity_id=task.get("id"),
            status=task.get("status"), channel="crm",
            metadata={"due_date": task.get("due_date"), "priority": task.get("priority")},
        ))
    for event in bot_events:
        timeline.append(_timeline_event(
            event_id=f"bot:{event.get('id') or event.get('triggered_by_message_id')}",
            event_type="bot_event", occurred_at=_timestamp(event, "created_at"),
            title={
                "human_handoff": "Derivación a una persona",
                "bot_enabled": "Bot reactivado",
            }.get(event.get("type"), "Actividad del bot"),
            description=event.get("reason") or event.get("decision") or event.get("type"),
            entity_id=event.get("conversation_id"), status=event.get("status"),
            channel="ai", actor_name=event.get("actor"),
            metadata={"confidence": event.get("confidence"), "intent": event.get("intent")},
        ))

    timeline.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)
    timeline = timeline[:max(1, min(activity_limit, 500))]

    now = datetime.now(timezone.utc).isoformat()
    scheduled = [
        item for item in appointments
        if item.get("status") == "scheduled" and str(item.get("start_time") or "") >= now
    ]
    scheduled.sort(key=lambda item: item.get("start_time") or "")
    latest_activity_dates = [
        value for value in [
            *[item.get("created_at") for item in messages],
            *[item.get("last_message_at") for item in conversations],
        ] if value
    ]

    return {
        "contact": contact,
        "summary": {
            "lifetime_value": _money(sum(_money(item.get("total")) for item in confirmed_sales)),
            "amount_paid": _money(sum(_money(item.get("amount_paid")) for item in confirmed_sales)),
            "balance_due": _money(sum(_money(item.get("balance_due")) for item in confirmed_sales)),
            "sales_count": len(confirmed_sales),
            "conversations_count": len(conversations),
            "unread_messages": sum(int(item.get("unread") or 0) for item in conversations),
            "appointments_count": len(appointments),
            "completed_appointments": sum(1 for item in appointments if item.get("status") == "completed"),
            "pending_tasks": sum(1 for item in tasks if item.get("status") not in {"done", "completed"}),
            "bot_messages": sum(1 for item in messages if item.get("sender_type") == "bot"),
            "handoffs": sum(1 for item in bot_events if item.get("type") == "human_handoff"),
            "last_contact_at": max(latest_activity_dates) if latest_activity_dates else None,
            "last_purchase_at": max(
                (_timestamp(item, "confirmed_at", "created_at") or "" for item in confirmed_sales),
                default="",
            ) or None,
            "next_appointment": scheduled[0] if scheduled else None,
        },
        "section_access": {
            "crm": True,
            "inbox": inbox_visible,
            "calendar": calendar_visible,
        },
        "leads": leads,
        "conversations": conversations,
        "appointments": appointments,
        "sales": sales,
        "payments": payments,
        "products": sorted(product_map.values(), key=lambda item: item.get("last_purchase_at") or "", reverse=True),
        "notes": notes,
        "tasks": tasks,
        "bot_events": bot_events,
        "timeline": timeline,
    }
