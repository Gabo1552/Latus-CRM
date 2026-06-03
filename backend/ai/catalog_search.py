"""Pre-LLM catalog search hook for the bot pipeline.

When a customer message looks like a commercial query (price, stock, model,
SKU), we look up the real catalog and inject the results into the system
prompt so the LLM never hallucinates prices. Catalog access is direct against
the ``db.products`` collection (no HTTP) for latency.
"""
from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_KW_PRICE = re.compile(
    r"\b(precio|cuesta|cu[áa]nto\s+sale|cu[áa]nto\s+vale|valor|valor\s+de|sale)\b",
    re.IGNORECASE,
)
_KW_STOCK = re.compile(
    r"\b(stock|disponible|tienen|ten[ée]s|hay\s+(?:de|en)?|en\s+stock)\b",
    re.IGNORECASE,
)
_KW_CATALOG = re.compile(
    r"\b(cat[áa]logo|modelos|qu[ée]\s+modelos|qu[ée]\s+productos|qu[ée]\s+tienen)\b",
    re.IGNORECASE,
)
_KW_BUY = re.compile(r"\b(comprar|venden|vende|vende[nm]?)\b", re.IGNORECASE)
_SKU_RX = re.compile(r"\b([A-Z]{2,}-[A-Z0-9][A-Z0-9-]+)\b")

# Hard handoff triggers — pre-LLM negotiation guard.
_NEGOTIATION_RX = re.compile(
    r"\b(descuento|rebaja|promo|promoci[óo]n|financiaci[óo]n|cuotas|"
    r"m[áa]s\s+barato|precio\s+especial|me\s+hacen|negocia(?:r|mos)?)\b",
    re.IGNORECASE,
)


def detect_commercial_intent(text: str) -> dict:
    """Classify whether ``text`` is a commercial product query.

    Returns ``{"is_commercial", "intent_type", "matched_terms"}`` where
    ``intent_type`` ∈ {price, stock, catalog, sku_lookup, general_commercial}.
    """
    t = text or ""
    matched: list[str] = []
    intent_type = None

    sku_hit = _SKU_RX.search(t)
    if sku_hit:
        matched.append(sku_hit.group(1))
        intent_type = "sku_lookup"
    if _KW_PRICE.search(t):
        matched.append("precio")
        intent_type = intent_type or "price"
    if _KW_STOCK.search(t):
        matched.append("stock")
        intent_type = intent_type or "stock"
    if _KW_CATALOG.search(t):
        matched.append("catalogo")
        intent_type = intent_type or "catalog"
    if _KW_BUY.search(t):
        matched.append("compra")
        intent_type = intent_type or "general_commercial"

    return {
        "is_commercial": bool(matched),
        "intent_type": intent_type,
        "matched_terms": matched,
    }


def detect_negotiation(text: str) -> bool:
    """Pre-LLM hard rule: customer is asking for a discount / financing."""
    return bool(_NEGOTIATION_RX.search(text or ""))


# ---------------------------------------------------------------------------
# Query extraction
# ---------------------------------------------------------------------------


STOPWORDS_ES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "al", "a", "en", "por", "para", "con", "sin",
    "que", "qué", "cuál", "cual", "cuáles", "cuanto", "cuánto",
    "cuesta", "vale", "precio", "stock", "tienen", "tenés", "tienes",
    "hay", "venden", "vende", "modelos", "modelo", "catalogo", "catálogo",
    "es", "son", "y", "o", "u", "ni", "no", "sí", "si",
    "me", "te", "le", "se", "nos",
    "hola", "buenas", "buenos", "tardes", "días", "noches",
    "por", "favor", "gracias", "ok", "okay",
    "este", "esta", "esto", "ese", "esa", "eso", "aquel", "aquella",
    "qué", "como", "cómo", "donde", "dónde",
}


def extract_product_query(text: str) -> str:
    """Return a cleaned product query string from a raw inbound message."""
    if not text:
        return ""
    sku = _SKU_RX.search(text)
    if sku:
        return sku.group(1)
    # Strip punctuation, tokenize, drop stopwords/short tokens.
    clean = re.sub(r"[¿?¡!.,;:()\"']+", " ", text)
    tokens = [t for t in clean.split() if t]
    keep = [t for t in tokens if len(t) >= 3 and t.lower() not in STOPWORDS_ES]
    return " ".join(keep[:8]).strip()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_FIELDS = ("name", "sku", "category", "price", "currency", "promo_price",
           "stock_status", "description", "commercial_conditions",
           "image_url", "external_link", "tags")


def _trim(p: dict) -> dict:
    out = {k: p.get(k) for k in _FIELDS}
    if out.get("description"):
        out["description"] = (out["description"] or "")[:200]
    return out


async def search_catalog(db, query: str, limit: int = 5) -> list[dict]:
    """Look up active, non-deleted products matching ``query``.

    Strategy: SKU exact (case-insensitive) → regex on name/tags/description → top by
    category-popularity fallback when query is empty.
    """
    base = {"deleted_at": None, "active": True}
    q = (query or "").strip()

    if q:
        sku_doc = await db.products.find_one(
            {**base, "sku": q.upper()}, {"_id": 0})
        if sku_doc:
            return [_trim(sku_doc)]
        rx = re.compile(re.escape(q.split()[0]) if " " in q else re.escape(q),
                        re.IGNORECASE)
        cursor = db.products.find(
            {**base, "$or": [
                {"name": {"$regex": rx}},
                {"tags": {"$regex": rx}},
                {"description": {"$regex": rx}},
            ]}, {"_id": 0}).sort("name", 1)
        rows = await cursor.to_list(limit)
        if rows:
            return [_trim(r) for r in rows]

    # Fallback — top by name (alphabetical) so the LLM has *something* to show.
    rows = await db.products.find(base, {"_id": 0}).sort("name", 1).to_list(limit)
    return [_trim(r) for r in rows]


# ---------------------------------------------------------------------------
# Render for LLM
# ---------------------------------------------------------------------------


def _fmt_price(p: dict) -> str:
    v = p.get("price")
    cur = p.get("currency") or "ARS"
    promo = p.get("promo_price")
    if v is None:
        return "Consultar"
    base = f"{cur} {v}"
    if promo is not None and promo < v:
        return f"{base} (promo: {cur} {promo})"
    return base


def format_catalog_for_llm(products: list[dict]) -> str:
    if not products:
        return (
            "=== CATÁLOGO ===\n"
            "No se encontraron productos que coincidan con la consulta. NO inventes\n"
            "productos ni precios. Si el cliente preguntó por algo específico, decí\n"
            "que no tenemos esa información y ofrecé derivar a un asesor.\n"
        )
    lines = [
        "=== CATÁLOGO DISPONIBLE (datos reales — usar solo esto, no inventar) ==="
    ]
    for i, p in enumerate(products, start=1):
        lines.append(f"{i}. {p.get('name')}"
                     + (f" (SKU: {p.get('sku')})" if p.get("sku") else ""))
        if p.get("category"):
            lines.append(f"   Categoría: {p['category']}")
        lines.append(f"   Precio: {_fmt_price(p)}")
        if p.get("stock_status"):
            lines.append(f"   Stock: {p['stock_status']}")
        if p.get("tags"):
            lines.append(f"   Tags: {', '.join(p['tags'])}")
        if p.get("commercial_conditions"):
            lines.append(f"   Condiciones: {p['commercial_conditions'][:160]}")
        if p.get("description"):
            lines.append(f"   Detalle: {p['description']}")
    lines.append("")
    lines.append(
        "Reglas extra mientras este bloque esté presente:\n"
        "- Si el cliente pregunta por algo que NO está acá, NO inventes — decí que\n"
        "  no tenés información y derivá a un asesor.\n"
        "- Si el producto está sin_stock, ofrecé alternativas del catálogo si las\n"
        "  hay; si no, derivá a un asesor humano.\n"
    )
    return "\n".join(lines)


__all__ = [
    "detect_commercial_intent",
    "detect_negotiation",
    "extract_product_query",
    "search_catalog",
    "format_catalog_for_llm",
]
