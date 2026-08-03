"""Sales and inventory domain services.

The module keeps commercial documents immutable after confirmation, records an
inventory ledger, and uses atomic conditional product updates so concurrent
sales cannot drive tracked stock or promotional quotas below zero.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from catalog import promotion_state


SALE_STATUSES = {"draft", "confirmed", "cancelled"}
PAYMENT_METHODS = {"cash", "transfer", "card", "mercadopago", "other"}
INVENTORY_REASONS = {"purchase", "adjustment", "damage", "return", "initial"}


class CommerceError(ValueError):
    """Business rule violation safe to expose to an authenticated user."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _money(value: Any) -> float:
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommerceError("El importe no es válido") from exc
    return float(amount)


def _modified(result: Any) -> bool:
    """Fake adapters return None; Motor returns UpdateResult."""
    return result is None or getattr(result, "modified_count", 0) == 1


def _public(document: dict | None) -> dict | None:
    if not document:
        return document
    return {key: value for key, value in document.items() if key != "_id"}


async def _resolve_customer(
    db, *, contact_id: str | None, lead_id: str | None,
) -> tuple[str | None, dict | None, dict | None]:
    lead = None
    contact = None
    if lead_id:
        lead = await db.leads.find_one({"id": lead_id}, {"_id": 0})
        if not lead:
            raise CommerceError("El lead seleccionado no existe")
        contact_id = contact_id or lead.get("contact_id")
    if contact_id:
        contact = await db.contacts.find_one({"id": contact_id}, {"_id": 0})
        if not contact:
            raise CommerceError("El cliente seleccionado no existe")
    return contact_id, lead, contact


async def create_sale_draft(db, payload: dict, *, user_id: str) -> dict:
    contact_id, lead, contact = await _resolve_customer(
        db, contact_id=payload.get("contact_id"), lead_id=payload.get("lead_id")
    )
    requested_lines = payload.get("lines") or []
    if not requested_lines:
        raise CommerceError("Agregá al menos un producto a la venta")

    merged: dict[str, dict] = {}
    for requested in requested_lines:
        product_id = str(requested.get("product_id") or "").strip()
        if not product_id:
            raise CommerceError("Todos los renglones deben tener un producto")
        quantity = int(requested.get("quantity") or 0)
        if quantity < 1:
            raise CommerceError("La cantidad debe ser mayor a cero")
        if product_id in merged:
            merged[product_id]["quantity"] += quantity
            if requested.get("unit_price") is not None:
                merged[product_id]["unit_price"] = requested.get("unit_price")
        else:
            merged[product_id] = {"quantity": quantity, "unit_price": requested.get("unit_price")}

    lines: list[dict] = []
    currency: str | None = None
    for product_id, requested in merged.items():
        product = await db.products.find_one(
            {"product_id": product_id, "deleted_at": None, "active": True}, {"_id": 0}
        )
        if not product:
            raise CommerceError("Uno de los productos ya no está disponible")
        promo = promotion_state(product)
        catalog_price = promo.get("effective_price")
        if catalog_price is None:
            raise CommerceError(f"{product.get('name') or 'El producto'} no tiene precio configurado")
        unit_price = _money(
            requested.get("unit_price") if requested.get("unit_price") is not None else catalog_price
        )
        if unit_price < 0:
            raise CommerceError("El precio no puede ser negativo")
        line_currency = str(product.get("currency") or "ARS").upper()
        if currency and currency != line_currency:
            raise CommerceError("Una venta no puede mezclar productos de distintas monedas")
        currency = line_currency
        quantity = requested["quantity"]
        lines.append({
            "product_id": product_id,
            "name": product.get("name") or "Producto",
            "sku": product.get("sku"),
            "quantity": quantity,
            "unit_price": unit_price,
            "list_price": _money(product.get("price")),
            "line_total": _money(unit_price * quantity),
            "currency": line_currency,
            "promotion_applied": bool(
                promo.get("promo_active") and _money(product.get("promo_price")) == unit_price
            ),
            "stock_tracked": bool(product.get("track_stock")),
        })

    total = _money(sum(line["line_total"] for line in lines))
    now = _now_iso()
    sale = {
        "sale_id": _id("sale"),
        "status": "draft",
        "payment_status": "pending",
        "contact_id": contact_id,
        "lead_id": payload.get("lead_id"),
        "customer_name": payload.get("customer_name") or (contact or {}).get("name"),
        "currency": currency or "ARS",
        "lines": lines,
        "subtotal": total,
        "discount_total": 0.0,
        "total": total,
        "payments": [],
        "amount_paid": 0.0,
        "balance_due": total,
        "notes": str(payload.get("notes") or "").strip() or None,
        "created_at": now,
        "created_by": user_id,
        "updated_at": now,
        "updated_by": user_id,
    }
    await db.sales.insert_one(sale)
    return _public(sale)


async def get_sale(db, sale_id: str) -> dict | None:
    return _public(await db.sales.find_one({"sale_id": sale_id}, {"_id": 0}))


async def list_sales(
    db, *, status: str | None = None, contact_id: str | None = None,
    created_by: str | None = None, limit: int = 50, offset: int = 0,
) -> dict:
    query: dict[str, Any] = {}
    if status:
        if status not in SALE_STATUSES:
            raise CommerceError("Estado de venta inválido")
        query["status"] = status
    if contact_id:
        query["contact_id"] = contact_id
    if created_by:
        query["created_by"] = created_by
    total = await db.sales.count_documents(query)
    items = await db.sales.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit + offset)
    items = items[offset:offset + limit]
    return {"items": [_public(item) for item in items], "total": total, "limit": limit, "offset": offset}


async def sales_summary(db, *, created_by: str | None = None) -> dict:
    owner_filter = {"created_by": created_by} if created_by else {}
    confirmed = await db.sales.find(
        {**owner_filter, "status": "confirmed"}, {"_id": 0}
    ).to_list(10_000)
    drafts = await db.sales.count_documents({**owner_filter, "status": "draft"})
    cancelled = await db.sales.count_documents({**owner_filter, "status": "cancelled"})
    return {
        "confirmed_count": len(confirmed),
        "draft_count": drafts,
        "cancelled_count": cancelled,
        "confirmed_total": _money(sum(float(item.get("total") or 0) for item in confirmed)),
        "pending_collection": _money(sum(float(item.get("balance_due") or 0) for item in confirmed)),
    }


async def _record_movement(
    db, *, product: dict, quantity_delta: int, movement_type: str,
    user_id: str, sale_id: str | None = None, reason: str | None = None,
    stock_before: int | None = None, stock_after: int | None = None,
) -> dict:
    movement = {
        "movement_id": _id("mov"),
        "product_id": product["product_id"],
        "product_name": product.get("name"),
        "sku": product.get("sku"),
        "quantity_delta": int(quantity_delta),
        "movement_type": movement_type,
        "sale_id": sale_id,
        "reason": reason,
        "stock_before": stock_before,
        "stock_after": stock_after,
        "created_at": _now_iso(),
        "created_by": user_id,
    }
    await db.inventory_movements.insert_one(movement)
    return movement


async def confirm_sale(db, sale_id: str, *, user_id: str) -> dict:
    sale = await db.sales.find_one({"sale_id": sale_id}, {"_id": 0})
    if not sale:
        raise CommerceError("La venta no existe")
    if sale.get("status") == "confirmed":
        return _public(sale)
    if sale.get("status") != "draft":
        raise CommerceError("La venta ya no puede confirmarse")

    claim = await db.sales.update_one(
        {"sale_id": sale_id, "status": "draft"},
        {"$set": {"status": "confirming", "updated_at": _now_iso(), "updated_by": user_id}},
    )
    if not _modified(claim):
        raise CommerceError("La venta está siendo procesada por otro usuario")

    consumed: list[tuple[dict, int, int]] = []
    try:
        for line in sale.get("lines") or []:
            product = await db.products.find_one(
                {"product_id": line["product_id"], "deleted_at": None, "active": True}, {"_id": 0}
            )
            if not product:
                raise CommerceError(f"{line.get('name') or 'Un producto'} ya no está disponible")
            quantity = int(line.get("quantity") or 0)
            promo_increment = 0
            query: dict[str, Any] = {"product_id": product["product_id"], "deleted_at": None}
            increments: dict[str, int] = {}
            stock_before = int(product.get("stock_quantity") or 0)
            if line.get("stock_tracked"):
                query["track_stock"] = True
                query["stock_quantity"] = {"$gte": quantity}
                increments["stock_quantity"] = -quantity
            if line.get("promotion_applied"):
                promo = promotion_state(product)
                if not promo.get("promo_active") or _money(product.get("promo_price")) != _money(line.get("unit_price")):
                    raise CommerceError(f"La promoción de {line.get('name')} ya no está vigente")
                if (product.get("promo_limit_type") or "none") == "units":
                    limit = int(product.get("promo_unit_limit") or 0)
                    query["promo_unit_limit"] = limit
                    query["promo_units_used"] = {"$lte": limit - quantity}
                    increments["promo_units_used"] = quantity
                    promo_increment = quantity
            if increments:
                result = await db.products.update_one(query, {"$inc": increments})
                if not _modified(result):
                    raise CommerceError(f"No hay stock o cupo promocional suficiente de {line.get('name')}")
                consumed.append((product, quantity if line.get("stock_tracked") else 0, promo_increment))
            if line.get("stock_tracked"):
                stock_after = stock_before - quantity
                await db.products.update_one(
                    {"product_id": product["product_id"]},
                    {"$set": {"stock_status": "disponible" if stock_after > 0 else "sin_stock", "updated_at": _now_iso()}},
                )
                await _record_movement(
                    db, product=product, quantity_delta=-quantity, movement_type="sale",
                    sale_id=sale_id, user_id=user_id, stock_before=stock_before, stock_after=stock_after,
                )

        confirmed_at = _now_iso()
        await db.sales.update_one(
            {"sale_id": sale_id, "status": "confirming"},
            {"$set": {
                "status": "confirmed", "confirmed_at": confirmed_at,
                "confirmed_by": user_id, "updated_at": confirmed_at, "updated_by": user_id,
            }},
        )
        if sale.get("lead_id"):
            await db.leads.update_one(
                {"id": sale["lead_id"]},
                {"$set": {"latest_sale_id": sale_id, "updated_at": confirmed_at}},
            )
    except Exception as exc:
        for product, stock_quantity, promo_quantity in reversed(consumed):
            increments = {}
            if stock_quantity:
                increments["stock_quantity"] = stock_quantity
            if promo_quantity:
                increments["promo_units_used"] = -promo_quantity
            if increments:
                await db.products.update_one({"product_id": product["product_id"]}, {"$inc": increments})
            if stock_quantity:
                restored = await db.products.find_one(
                    {"product_id": product["product_id"]}, {"_id": 0, "stock_quantity": 1}
                ) or {}
                restored_quantity = int(restored.get("stock_quantity") or 0)
                await db.products.update_one(
                    {"product_id": product["product_id"]},
                    {"$set": {
                        "stock_status": "disponible" if restored_quantity > 0 else "sin_stock",
                        "updated_at": _now_iso(),
                    }},
                )
                await _record_movement(
                    db, product=product, quantity_delta=stock_quantity,
                    movement_type="confirmation_rollback", sale_id=sale_id,
                    reason="Confirmación revertida por error", user_id=user_id,
                    stock_after=restored_quantity,
                )
        await db.sales.update_one(
            {"sale_id": sale_id, "status": "confirming"},
            {"$set": {"status": "draft", "last_error": str(exc)[:500], "updated_at": _now_iso()}},
        )
        raise
    return await get_sale(db, sale_id)


async def add_payment(
    db, sale_id: str, *, amount: float, method: str,
    reference: str | None, user_id: str,
) -> dict:
    if method not in PAYMENT_METHODS:
        raise CommerceError("Medio de pago inválido")
    amount = _money(amount)
    if amount <= 0:
        raise CommerceError("El pago debe ser mayor a cero")
    sale = await db.sales.find_one({"sale_id": sale_id}, {"_id": 0})
    if not sale or sale.get("status") != "confirmed":
        raise CommerceError("La venta debe estar confirmada para registrar pagos")
    balance = _money(sale.get("balance_due"))
    if amount > balance:
        raise CommerceError("El pago supera el saldo pendiente")
    paid = _money(float(sale.get("amount_paid") or 0) + amount)
    next_balance = _money(float(sale.get("total") or 0) - paid)
    payment = {
        "payment_id": _id("pay"), "amount": amount, "method": method,
        "reference": str(reference or "").strip() or None,
        "received_at": _now_iso(), "created_by": user_id,
    }
    payments = [*(sale.get("payments") or []), payment]
    result = await db.sales.update_one(
        {"sale_id": sale_id, "status": "confirmed", "amount_paid": sale.get("amount_paid", 0.0)},
        {"$set": {
            "payments": payments, "amount_paid": paid, "balance_due": next_balance,
            "payment_status": "paid" if next_balance <= 0 else "partial",
            "updated_at": _now_iso(), "updated_by": user_id,
        }},
    )
    if not _modified(result):
        raise CommerceError("La venta cambió mientras se registraba el pago. Intentá nuevamente")
    return await get_sale(db, sale_id)


async def cancel_sale(db, sale_id: str, *, reason: str, user_id: str) -> dict:
    sale = await db.sales.find_one({"sale_id": sale_id}, {"_id": 0})
    if not sale:
        raise CommerceError("La venta no existe")
    if sale.get("status") == "cancelled":
        return _public(sale)
    if sale.get("status") == "draft":
        await db.sales.update_one(
            {"sale_id": sale_id, "status": "draft"},
            {"$set": {"status": "cancelled", "cancelled_at": _now_iso(),
                      "cancelled_by": user_id, "cancellation_reason": reason}},
        )
        return await get_sale(db, sale_id)
    if sale.get("status") != "confirmed":
        raise CommerceError("La venta está siendo procesada y no puede cancelarse")
    claim = await db.sales.update_one(
        {"sale_id": sale_id, "status": "confirmed"}, {"$set": {"status": "cancelling"}}
    )
    if not _modified(claim):
        raise CommerceError("La venta está siendo procesada por otro usuario")
    try:
        for line in sale.get("lines") or []:
            product = await db.products.find_one({"product_id": line["product_id"]}, {"_id": 0})
            if not product:
                continue
            quantity = int(line.get("quantity") or 0)
            increments: dict[str, int] = {}
            stock_before = int(product.get("stock_quantity") or 0)
            if line.get("stock_tracked"):
                increments["stock_quantity"] = quantity
            if line.get("promotion_applied") and (product.get("promo_limit_type") or "none") == "units":
                increments["promo_units_used"] = -min(quantity, int(product.get("promo_units_used") or 0))
            if increments:
                await db.products.update_one({"product_id": product["product_id"]}, {"$inc": increments})
            if line.get("stock_tracked"):
                stock_after = stock_before + quantity
                await db.products.update_one(
                    {"product_id": product["product_id"]},
                    {"$set": {"stock_status": "disponible", "updated_at": _now_iso()}},
                )
                await _record_movement(
                    db, product=product, quantity_delta=quantity, movement_type="sale_cancelled",
                    sale_id=sale_id, reason=reason, user_id=user_id,
                    stock_before=stock_before, stock_after=stock_after,
                )
        cancelled_at = _now_iso()
        await db.sales.update_one(
            {"sale_id": sale_id, "status": "cancelling"},
            {"$set": {
                "status": "cancelled", "cancelled_at": cancelled_at,
                "cancelled_by": user_id, "cancellation_reason": reason,
                "payment_status": "refund_pending" if float(sale.get("amount_paid") or 0) > 0 else "cancelled",
                "updated_at": cancelled_at, "updated_by": user_id,
            }},
        )
    except Exception:
        await db.sales.update_one({"sale_id": sale_id, "status": "cancelling"}, {"$set": {"status": "confirmed"}})
        raise
    return await get_sale(db, sale_id)


async def adjust_inventory(
    db, *, product_id: str, quantity_delta: int, reason: str,
    notes: str | None, user_id: str,
) -> dict:
    if reason not in INVENTORY_REASONS:
        raise CommerceError("Motivo de movimiento inválido")
    if quantity_delta == 0:
        raise CommerceError("La variación de stock no puede ser cero")
    product = await db.products.find_one(
        {"product_id": product_id, "deleted_at": None}, {"_id": 0}
    )
    if not product:
        raise CommerceError("El producto no existe")
    if not product.get("track_stock"):
        raise CommerceError("Activá el control de stock para este producto")
    stock_before = int(product.get("stock_quantity") or 0)
    query: dict[str, Any] = {"product_id": product_id, "track_stock": True, "deleted_at": None}
    if quantity_delta < 0:
        query["stock_quantity"] = {"$gte": abs(quantity_delta)}
    result = await db.products.update_one(query, {"$inc": {"stock_quantity": quantity_delta}})
    if not _modified(result):
        raise CommerceError("El movimiento dejaría el stock en negativo")
    stock_after = stock_before + quantity_delta
    await db.products.update_one(
        {"product_id": product_id},
        {"$set": {"stock_status": "disponible" if stock_after > 0 else "sin_stock", "updated_at": _now_iso()}},
    )
    movement = await _record_movement(
        db, product=product, quantity_delta=quantity_delta, movement_type=reason,
        reason=str(notes or "").strip() or None, user_id=user_id,
        stock_before=stock_before, stock_after=stock_after,
    )
    return _public(movement)


async def record_initial_inventory(db, product: dict, *, user_id: str) -> dict | None:
    quantity = int(product.get("stock_quantity") or 0)
    if not product.get("track_stock") or quantity <= 0:
        return None
    return _public(await _record_movement(
        db, product=product, quantity_delta=quantity, movement_type="initial",
        reason="Stock inicial del producto", user_id=user_id,
        stock_before=0, stock_after=quantity,
    ))


async def list_inventory_movements(
    db, *, product_id: str | None = None, limit: int = 100, offset: int = 0,
) -> dict:
    query = {"product_id": product_id} if product_id else {}
    total = await db.inventory_movements.count_documents(query)
    items = await db.inventory_movements.find(query, {"_id": 0}).sort("created_at", -1).to_list(limit + offset)
    items = items[offset:offset + limit]
    return {"items": [_public(item) for item in items], "total": total, "limit": limit, "offset": offset}
