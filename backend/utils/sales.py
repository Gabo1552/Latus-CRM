"""Immutable sale snapshots and promotion-unit accounting."""
from __future__ import annotations

from datetime import datetime, timezone

from catalog import promotion_state


class SaleError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def close_sale(db, lead: dict, products: list[dict], *, user_id: str | None) -> dict:
    closed_at = _now_iso()
    snapshot_products = []
    unit_promotions_to_consume: dict[str, dict] = {}

    for sold in products or []:
        quantity = max(1, int(sold.get("quantity") or 1))
        unit_price = float(sold.get("price") or 0)
        product_id = sold.get("id")
        catalog_product = None
        promo_applied = bool(sold.get("promotion_applied"))
        if product_id:
            catalog_product = await db.products.find_one(
                {"product_id": product_id, "deleted_at": None}, {"_id": 0}
            )
        if catalog_product:
            promo = promotion_state(catalog_product)
            promo_matches_price = (
                promo.get("promo_active")
                and catalog_product.get("promo_price") is not None
                and abs(unit_price - float(catalog_product["promo_price"])) < 0.000001
            )
            promo_applied = promo_applied or promo_matches_price
            product_name = sold.get("name") or catalog_product.get("name")
            if promo_applied and not promo.get("promo_active"):
                raise SaleError(f"La promoción de {product_name} ya no está vigente")
            if promo_applied and (catalog_product.get("promo_limit_type") or "none") == "units":
                remaining = int(promo.get("promo_units_remaining") or 0)
                pending = unit_promotions_to_consume.setdefault(product_id, {
                    "quantity": 0,
                    "remaining": remaining,
                    "limit": int(catalog_product.get("promo_unit_limit") or 0),
                    "name": product_name,
                })
                pending["quantity"] += quantity

        list_price = sold.get("list_price")
        if list_price is None and catalog_product:
            list_price = catalog_product.get("price")
        snapshot_products.append({
            "id": product_id,
            "name": sold.get("name"),
            "price": unit_price,
            "unit_price": unit_price,
            "quantity": quantity,
            "line_total": unit_price * quantity,
            "currency": sold.get("currency") or (catalog_product or {}).get("currency") or "ARS",
            "list_price": float(list_price) if list_price is not None else unit_price,
            "promotion_applied": promo_applied,
            "promotion_limit_type": (catalog_product or {}).get("promo_limit_type") if promo_applied else None,
        })

    for promo in unit_promotions_to_consume.values():
        if promo["quantity"] > promo["remaining"]:
            raise SaleError(
                f"Solo quedan {promo['remaining']} unidades promocionales de {promo['name']}"
            )

    consumed = []
    try:
        for product_id, promo in unit_promotions_to_consume.items():
            quantity = promo["quantity"]
            result = await db.products.update_one(
                {
                    "product_id": product_id,
                    "deleted_at": None,
                    "promo_limit_type": "units",
                    "promo_unit_limit": promo["limit"],
                    "$or": [
                        {"promo_units_used": {"$lte": promo["limit"] - quantity}},
                        {"promo_units_used": {"$exists": False}},
                    ],
                },
                {"$inc": {"promo_units_used": quantity}},
            )
            if result is not None and getattr(result, "modified_count", 0) != 1:
                raise SaleError(
                    f"Ya no quedan suficientes unidades promocionales de {promo['name']}"
                )
            consumed.append((product_id, quantity))
    except Exception:
        for product_id, quantity in consumed:
            await db.products.update_one(
                {"product_id": product_id, "deleted_at": None},
                {"$inc": {"promo_units_used": -quantity}},
            )
        raise

    closed_value = (
        sum(item["line_total"] for item in snapshot_products)
        if snapshot_products
        else float(lead.get("value") or 0)
    )
    return {
        "value": closed_value,
        "closed_value": closed_value,
        "closed_at": closed_at,
        "closed_by": user_id,
        "sale_snapshot": {
            "closed_at": closed_at,
            "closed_by": user_id,
            "products": snapshot_products,
            "total": closed_value,
        },
    }


async def reverse_sale(db, snapshot: dict | None, *, user_id: str | None) -> dict:
    updated = dict(snapshot or {})
    for sold in updated.get("products") or []:
        if sold.get("promotion_applied") and sold.get("promotion_limit_type") == "units" and sold.get("id"):
            await db.products.update_one(
                {
                    "product_id": sold["id"],
                    "deleted_at": None,
                    "promo_units_used": {"$gte": max(1, int(sold.get("quantity") or 1))},
                },
                {"$inc": {"promo_units_used": -max(1, int(sold.get("quantity") or 1))}},
            )
    updated["reversed_at"] = _now_iso()
    updated["reversed_by"] = user_id
    return updated
