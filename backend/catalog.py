"""Catalog (products) module — CRUD, CSV import/export, validation.

Keeps SKU uniqueness via a sparse unique index, name text index for search,
soft delete via ``deleted_at``. CSV uses stdlib ``csv`` only.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


ALLOWED_CURRENCIES = {"ARS", "USD", "EUR", "BRL", "CLP", "UYU", "MXN"}
ALLOWED_STOCK_STATUS = {"disponible", "sin_stock", "consultar"}

CSV_HEADERS = [
    "name", "sku", "category", "description", "price", "currency",
    "stock_status", "active", "tags", "image_url", "promo_price",
    "commercial_conditions", "external_link",
]

MAX_CSV_BYTES = 5 * 1024 * 1024  # 5MB
TRUTHY = {"true", "1", "si", "sí", "yes", "y", "x"}
FALSY = {"false", "0", "no", "n", ""}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_product_id() -> str:
    return f"prod_{uuid.uuid4().hex[:12]}"


def _is_url(s: str) -> bool:
    return bool(s) and (s.startswith("http://") or s.startswith("https://"))


def _coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in TRUTHY:
        return True
    if s in FALSY:
        return False
    return None


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        # accept "1.234,50" (es-AR) and "1,234.50" (en-US)
        s = str(v).strip()
        if s.count(",") and s.count("."):
            s = s.replace(".", "").replace(",", ".")
        elif s.count(",") == 1 and s.count(".") == 0:
            s = s.replace(",", ".")
        return float(s)
    except Exception as e:
        raise ValueError(f"valor numérico inválido: {v!r}") from e


def _split_tags(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    s = str(raw)
    # autodetect ; vs , (prefer ; if present)
    sep = ";" if ";" in s else ","
    return [t.strip() for t in s.split(sep) if t.strip()]


def validate_product(payload: dict, *, partial: bool = False) -> dict:
    """Validate and normalize a product payload. Returns the cleaned dict.

    Raises ``ValueError`` with a Spanish, user-facing message.
    """
    out: dict[str, Any] = {}

    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("El nombre es requerido")
        if len(name) > 240:
            raise ValueError("El nombre supera los 240 caracteres")
        out["name"] = name
    elif not partial:
        raise ValueError("El nombre es requerido")

    if "sku" in payload:
        sku = (payload.get("sku") or "").strip()
        out["sku"] = sku or None
    if "category" in payload:
        out["category"] = (payload.get("category") or "").strip() or None
    if "description" in payload:
        d = payload.get("description") or ""
        if len(d) > 4000:
            raise ValueError("La descripción supera los 4000 caracteres")
        out["description"] = d
    if "price" in payload:
        v = _coerce_float(payload["price"])
        if v is not None and v < 0:
            raise ValueError("El precio no puede ser negativo")
        out["price"] = v
    if "promo_price" in payload:
        v = _coerce_float(payload["promo_price"])
        if v is not None and v < 0:
            raise ValueError("El precio promocional no puede ser negativo")
        out["promo_price"] = v
    if "currency" in payload:
        c = (payload.get("currency") or "ARS").strip().upper()
        if c not in ALLOWED_CURRENCIES:
            raise ValueError("Moneda no soportada")
        out["currency"] = c
    elif not partial:
        out["currency"] = "ARS"
    if "stock_status" in payload:
        s = (payload.get("stock_status") or "consultar").strip().lower()
        if s not in ALLOWED_STOCK_STATUS:
            raise ValueError("Estado de stock inválido")
        out["stock_status"] = s
    elif not partial:
        out["stock_status"] = "consultar"
    if "active" in payload:
        b = _coerce_bool(payload["active"])
        if b is None:
            raise ValueError("Estado 'activo' inválido")
        out["active"] = b
    elif not partial:
        out["active"] = True
    if "tags" in payload:
        out["tags"] = _split_tags(payload["tags"])
    if "image_url" in payload:
        u = (payload.get("image_url") or "").strip()
        if u and not _is_url(u):
            raise ValueError("La URL de imagen debe empezar con http:// o https://")
        out["image_url"] = u or None
    if "commercial_conditions" in payload:
        out["commercial_conditions"] = (payload.get("commercial_conditions") or "") or None
    if "external_link" in payload:
        u = (payload.get("external_link") or "").strip()
        if u and not _is_url(u):
            raise ValueError("El enlace externo debe empezar con http:// o https://")
        out["external_link"] = u or None

    # promo < price cross check
    pp = out.get("promo_price")
    p = out.get("price")
    if pp is not None and p is not None and pp >= p:
        raise ValueError("El precio promocional debe ser menor que el precio")

    return out


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------


def _strip(doc: dict | None) -> dict | None:
    if not isinstance(doc, dict):
        return doc
    return {k: v for k, v in doc.items() if k not in ("_id", "deleted_at")}


async def create_product(db, payload: dict, *, user_id: str | None) -> dict:
    clean = validate_product(payload, partial=False)
    if clean.get("sku"):
        dupe = await db.products.find_one(
            {"sku": clean["sku"], "deleted_at": None}, {"_id": 0, "product_id": 1})
        if dupe:
            raise ValueError("Ya existe un producto con ese SKU")
    doc = {
        "product_id": new_product_id(),
        "created_at": _now_iso(), "created_by": user_id,
        "updated_at": _now_iso(), "updated_by": user_id,
        "deleted_at": None,
        # defaults so a record always has the fields
        "name": clean.get("name"),
        "sku": clean.get("sku"),
        "category": clean.get("category"),
        "description": clean.get("description") or "",
        "price": clean.get("price"),
        "currency": clean.get("currency", "ARS"),
        "stock_status": clean.get("stock_status", "consultar"),
        "active": clean.get("active", True),
        "tags": clean.get("tags") or [],
        "image_url": clean.get("image_url"),
        "promo_price": clean.get("promo_price"),
        "commercial_conditions": clean.get("commercial_conditions"),
        "external_link": clean.get("external_link"),
    }
    await db.products.insert_one(doc)
    return _strip(doc)


async def update_product(db, product_id: str, payload: dict, *,
                         user_id: str | None) -> dict | None:
    clean = validate_product(payload, partial=True)
    if clean.get("sku"):
        dupe = await db.products.find_one(
            {"sku": clean["sku"], "deleted_at": None,
             "product_id": {"$ne": product_id}}, {"_id": 0, "product_id": 1})
        if dupe:
            raise ValueError("Ya existe un producto con ese SKU")
    clean["updated_at"] = _now_iso()
    clean["updated_by"] = user_id
    await db.products.update_one(
        {"product_id": product_id, "deleted_at": None}, {"$set": clean})
    return await db.products.find_one({"product_id": product_id}, {"_id": 0})


def build_listing_query(filters: dict) -> dict:
    q: dict[str, Any] = {}
    if not filters.get("include_inactive"):
        q["deleted_at"] = None
    if filters.get("active") is not None and not filters.get("include_inactive"):
        q["active"] = bool(filters["active"])
    if filters.get("category"):
        q["category"] = filters["category"]
    if filters.get("stock_status"):
        q["stock_status"] = filters["stock_status"]
    if filters.get("q"):
        rx = re.compile(re.escape(filters["q"]), re.IGNORECASE)
        q["$or"] = [
            {"name": {"$regex": rx}},
            {"description": {"$regex": rx}},
            {"sku": {"$regex": rx}},
            {"tags": {"$regex": rx}},
        ]
    return q


# ----------------------------------------------------------------------------
# CSV import / export
# ----------------------------------------------------------------------------


def _parse_csv(content: bytes) -> tuple[list[dict], list[dict]]:
    """Return (rows, errors). Strips BOM, normalizes headers to lowercase."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames:
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    rows: list[dict] = []
    errors: list[dict] = []
    for i, raw in enumerate(reader, start=2):  # row 1 is the header
        rows.append({"_row": i, **{k: (v if v is None else v.strip()) for k, v in raw.items()}})
    return rows, errors


async def import_csv(db, content: bytes, *, update_existing: bool,
                     user_id: str | None) -> dict:
    if len(content) > MAX_CSV_BYTES:
        raise ValueError("El archivo supera el tamaño máximo (5MB)")
    rows, errors = _parse_csv(content)
    created = updated = skipped = 0
    out_errors: list[dict] = list(errors)
    for r in rows:
        row_num = r.pop("_row")
        # Only keep known headers
        payload = {k: v for k, v in r.items() if k in CSV_HEADERS}
        sku = (payload.get("sku") or "").strip()
        try:
            existing = await db.products.find_one(
                {"sku": sku, "deleted_at": None}, {"_id": 0, "product_id": 1}) \
                if sku else None
            if existing:
                if not update_existing:
                    skipped += 1
                    continue
                await update_product(db, existing["product_id"], payload, user_id=user_id)
                updated += 1
            else:
                await create_product(db, payload, user_id=user_id)
                created += 1
        except ValueError as e:
            out_errors.append({"row": row_num, "sku": sku, "field": "", "message": str(e)})
        except Exception as e:  # pragma: no cover
            logger.exception("CSV import failed row=%s: %s", row_num, e)
            out_errors.append({"row": row_num, "sku": sku, "field": "",
                               "message": "Error al procesar la fila"})
    return {
        "total_rows": len(rows),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": out_errors,
    }


def export_csv(items: list[dict]) -> bytes:
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM for Excel friendliness
    w = csv.DictWriter(buf, fieldnames=CSV_HEADERS, extrasaction="ignore")
    w.writeheader()
    for p in items:
        row = dict(p)
        # serialize tags list → "a;b;c"
        if isinstance(row.get("tags"), list):
            row["tags"] = ";".join(row["tags"])
        # booleans → "true"/"false"
        if isinstance(row.get("active"), bool):
            row["active"] = "true" if row["active"] else "false"
        w.writerow(row)
    return buf.getvalue().encode("utf-8")
