"""AI usage logging + pricing helpers (Phase 2).

Pricing seeds use USD per 1,000,000 tokens. Admins can override via the
``pricing_config`` document. Unknown models are logged defensively, while the
platform settings reject activating them until a price has been configured.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from utils.tenancy import get_organization_id

logger = logging.getLogger(__name__)


DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # OpenAI GPT-5 family
    "gpt-5.5-pro":                 {"input": 2.000,  "output": 8.000},
    "gpt-5.5-instant":             {"input": 0.150,  "output": 0.600},
    "gpt-5.4":                     {"input": 1.000,  "output": 4.000},
    "gpt-5.4-mini":                {"input": 0.100,  "output": 0.400},
    "gpt-4o-mini":                 {"input": 0.150,  "output": 0.600},
    "gpt-4o":                      {"input": 2.500,  "output": 10.000},
    # Anthropic Claude 4/3.5 family
    "claude-opus-4-8":             {"input": 15.000, "output": 75.000},
    "claude-opus-4-7":             {"input": 15.000, "output": 75.000},
    "claude-sonnet-4-6":           {"input": 3.000,  "output": 15.000},
    "claude-haiku-4-5":            {"input": 0.500,  "output": 2.500},
    "claude-3-5-sonnet-20241022":  {"input": 3.000,  "output": 15.000},
    "claude-3-5-haiku-20241022":   {"input": 0.800,  "output": 4.000},
    # Google Gemini family
    "gemini-3.5-flash":            {"input": 0.075,  "output": 0.300},
    "gemini-3.1-pro":              {"input": 1.250,  "output": 5.000},
    "gemini-3.1-flash-lite":       {"input": 0.035,  "output": 0.140},
    "gemini-1.5-pro":              {"input": 1.250,  "output": 5.000},
    "gemini-1.5-flash":            {"input": 0.075,  "output": 0.300},
}

VALID_PURPOSES = ("bot_pipeline", "summary_regen", "suggest_reply", "connection_test")
DEFAULT_AI_FEE_PERCENT = 20.0
MAX_AI_FEE_PERCENT = 500.0
PLAN_MONTHLY_AI_TOKENS = {
    "base": 500_000,
    "starter": 250_000,
    "growth": 1_500_000,
    "scale": 5_000_000,
}

# Models we already warned about, to avoid log spam.
_warned_models: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def load_pricing(db) -> dict[str, dict[str, float]]:
    """Return the effective pricing table = seeds overlaid with DB overrides."""
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    overrides = doc.get("models") or {}
    merged = {m: dict(p) for m, p in DEFAULT_PRICING.items()}
    for m, p in overrides.items():
        merged[m] = {"input": float(p.get("input") or 0.0),
                     "output": float(p.get("output") or 0.0)}
    return merged


async def load_pricing_metadata(db) -> dict[str, dict[str, Any]]:
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    return dict(doc.get("model_metadata") or {})


async def save_pricing(db, model: str, input_per_million: float,
                       output_per_million: float, user_id: str | None,
                       fee_percent: float | None = None) -> dict:
    if input_per_million < 0 or output_per_million < 0:
        raise ValueError("Los precios no pueden ser negativos")
    if not model or len(model) > 200:
        raise ValueError("Nombre de modelo inválido")
    if input_per_million == 0 and output_per_million == 0:
        raise ValueError(
            "Indicá al menos un precio mayor a cero; un modelo gratuito solo puede verificarse desde el proveedor"
        )
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    models = dict(doc.get("models") or {})
    metadata = dict(doc.get("model_metadata") or {})
    models[model] = {
        "input": float(input_per_million),
        "output": float(output_per_million),
    }
    meta_entry = dict(metadata.get(model) or {})
    meta_entry.update({
        "source": "manual",
        "verified_free": False,
        "verified_at": _now_iso(),
    })
    if fee_percent is not None:
        meta_entry["fee_percent"] = validate_fee_percent(fee_percent)
    metadata[model] = meta_entry
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {"models": models, "model_metadata": metadata,
                  "updated_at": _now_iso(),
                  "updated_by": user_id}},
        upsert=True,
    )
    return await load_pricing(db)


async def save_provider_pricing(db, model: str, input_per_million: float,
                                output_per_million: float, user_id: str | None,
                                *, verified_free: bool = False) -> dict:
    """Persist rates supplied by a provider catalog, including verified-free models."""
    if input_per_million < 0 or output_per_million < 0 or not model or len(model) > 200:
        raise ValueError("Precio de proveedor inválido")
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    models = dict(doc.get("models") or {})
    metadata = dict(doc.get("model_metadata") or {})
    models[model] = {"input": float(input_per_million), "output": float(output_per_million)}
    metadata[model] = {
        "source": "provider_api", "verified_free": bool(verified_free),
        "verified_at": _now_iso(),
    }
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {"models": models, "model_metadata": metadata,
                  "updated_at": _now_iso(), "updated_by": user_id}},
        upsert=True,
    )
    return await load_pricing(db)


async def pricing_is_configured(db, model: str) -> bool:
    return bool(model and model in await load_pricing(db))


async def monthly_token_usage(db, organization_id: str) -> int:
    now = datetime.now(timezone.utc)
    month_start = datetime.combine(now.date().replace(day=1), datetime.min.time(),
                                   tzinfo=timezone.utc).isoformat()
    collection = db.ai_usage_logs
    if hasattr(collection, "aggregate"):
        rows = await collection.aggregate([
            {"$match": {"organization_id": organization_id,
                        "created_at": {"$gte": month_start}}},
            {"$group": {"_id": None, "tokens": {"$sum": "$total_tokens"}}},
        ]).to_list(1)
        return int(rows[0].get("tokens") or 0) if rows else 0
    # Lightweight in-memory test databases do not implement aggregation.
    logs = await collection.find(
        {"organization_id": organization_id, "created_at": {"$gte": month_start}},
        {"_id": 0, "total_tokens": 1}).to_list(100_000)
    return sum(int(item.get("total_tokens") or 0) for item in logs)


async def ensure_monthly_token_quota(db, *, projected_tokens: int = 0) -> dict:
    """Prevent tenant AI calls from exceeding the included plan allowance."""
    organization_id = get_organization_id()
    if not organization_id:
        return {"allowed": True, "used": 0, "limit": None}
    organization = await db.organizations.find_one(
        {"organization_id": organization_id}, {"_id": 0, "plan_code": 1}
    ) or {}
    plan_code = organization.get("plan_code") or "base"
    limit = PLAN_MONTHLY_AI_TOKENS.get(plan_code, PLAN_MONTHLY_AI_TOKENS["base"])
    used = await monthly_token_usage(db, organization_id)
    allowed = used < limit and used + max(0, int(projected_tokens or 0)) <= limit
    return {"allowed": allowed, "used": used, "limit": limit, "plan_code": plan_code}


async def reset_pricing(db, user_id: str | None) -> dict:
    """Restore seeded rates without deleting custom/provider-discovered models."""
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    models = {model: rates for model, rates in (doc.get("models") or {}).items()
              if model not in DEFAULT_PRICING}
    metadata = {model: meta for model, meta in (doc.get("model_metadata") or {}).items()
                if model in models}
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {"models": models, "model_metadata": metadata, "updated_at": _now_iso(),
                  "updated_by": user_id}},
        upsert=True,
    )
    return await load_pricing(db)


def validate_fee_percent(value: float) -> float:
    fee = float(value)
    if not math.isfinite(fee) or fee < 0 or fee > MAX_AI_FEE_PERCENT:
        raise ValueError(f"El fee debe estar entre 0 y {int(MAX_AI_FEE_PERCENT)}%")
    return round(fee, 4)


async def load_billing_policy(db) -> dict:
    """Return the platform-wide AI billing policy."""
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    stored_fee = doc.get("default_ai_fee_percent")
    return {
        "default_fee_percent": validate_fee_percent(
            DEFAULT_AI_FEE_PERCENT if stored_fee is None else stored_fee
        ),
        "updated_at": doc.get("ai_fee_updated_at"),
        "updated_by": doc.get("ai_fee_updated_by"),
    }


async def save_billing_policy(db, fee_percent: float, user_id: str | None) -> dict:
    fee = validate_fee_percent(fee_percent)
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {
            "default_ai_fee_percent": fee,
            "ai_fee_updated_at": _now_iso(),
            "ai_fee_updated_by": user_id,
        }},
        upsert=True,
    )
    return await load_billing_policy(db)


async def effective_fee_percent(db, organization_id: str | None = None) -> float:
    """Resolve a tenant override, falling back to the global platform fee."""
    policy = await load_billing_policy(db)
    organization_id = organization_id or get_organization_id()
    if organization_id:
        organization = await db.organizations.find_one(
            {"organization_id": organization_id}, {"_id": 0, "ai_fee_percent": 1}
        ) or {}
        override = organization.get("ai_fee_percent")
        if override is not None:
            return validate_fee_percent(override)
    return policy["default_fee_percent"]


def billing_breakdown(log: dict) -> dict[str, float | str]:
    """Return the immutable provider/fee/customer amounts for one usage log.

    Logs created before AI billing existed are kept at 0% fee so historical
    amounts never change retroactively when the platform policy changes.
    """
    has_provider_cost = log.get("provider_cost_usd") is not None
    base_cost = float(
        log.get("provider_cost_usd") if has_provider_cost
        else log.get("estimated_cost_usd") or 0.0
    )
    fee_percent = float(log.get("ai_fee_percent") or 0.0)
    fee_usd = float(
        log.get("ai_fee_usd")
        if log.get("ai_fee_usd") is not None
        else round(base_cost * fee_percent / 100.0, 8)
    )
    billable = float(
        log.get("billable_cost_usd")
        if log.get("billable_cost_usd") is not None
        else round(base_cost + fee_usd, 8)
    )
    return {
        "base_cost_usd": round(base_cost, 8),
        "ai_fee_percent": fee_percent,
        "ai_fee_usd": round(fee_usd, 8),
        "billable_cost_usd": round(billable, 8),
        "billing_cost_source": "provider_response" if has_provider_cost else "estimated",
    }


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  pricing: dict[str, dict[str, float]] | None = None) -> float:
    """USD cost given a model and token counts. ``pricing`` defaults to
    :data:`DEFAULT_PRICING` (use ``load_pricing(db)`` to get the effective
    table when DB-backed overrides matter)."""
    table = pricing if pricing is not None else DEFAULT_PRICING
    p = table.get(model)
    if not p:
        if model and model not in _warned_models:
            _warned_models.add(model)
            logger.warning("ai_usage: no pricing entry for model=%r — cost will be 0",
                           model)
        return 0.0
    cost = (prompt_tokens or 0) * (p["input"]  or 0.0) / 1_000_000.0 \
         + (completion_tokens or 0) * (p["output"] or 0.0) / 1_000_000.0
    return round(cost, 6)


async def log_usage(db, *, status: str, provider: str, model: str,
                    prompt_tokens: int, completion_tokens: int,
                    latency_ms: int, purpose: str,
                    conversation_id: str | None = None,
                    message_id: str | None = None,
                    user_id: str | None = None,
                    error_message: str | None = None,
                    provider_cost_usd: float | None = None,
                    provider_request_id: str | None = None,
                    fee_percent: float | None = None,
                    pricing: dict | None = None) -> None:
    """Persist a single usage log entry. Best-effort — never raises."""
    try:
        total = (prompt_tokens or 0) + (completion_tokens or 0)
        cost = estimate_cost(model, prompt_tokens, completion_tokens, pricing) \
            if status == "success" else 0.0
        effective_fee = validate_fee_percent(
            fee_percent if fee_percent is not None else await effective_fee_percent(db)
        )
        base_cost = float(provider_cost_usd) if provider_cost_usd is not None else float(cost)
        fee_usd = round(base_cost * effective_fee / 100.0, 8)
        billable_cost = round(base_cost + fee_usd, 8)
        await db.ai_usage_logs.insert_one({
            "log_id": uuid.uuid4().hex,
            "created_at": _now_iso(),
            "provider": provider,
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(total),
            "estimated_cost_usd": float(cost),
            "provider_cost_usd": float(provider_cost_usd) if provider_cost_usd is not None else None,
            "cost_source": "provider_response" if provider_cost_usd is not None else "estimated",
            "base_cost_usd": round(base_cost, 8),
            "ai_fee_percent": effective_fee,
            "ai_fee_usd": fee_usd,
            "billable_cost_usd": billable_cost,
            "token_source": "provider_response" if status == "success" and total > 0 else "unavailable",
            "provider_request_id": provider_request_id,
            "latency_ms": int(latency_ms or 0),
            "status": status,
            "error_message": (error_message or None) if status == "error" else None,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "user_id": user_id,
            "purpose": purpose if purpose in VALID_PURPOSES else "bot_pipeline",
        })
    except Exception:
        logger.exception("ai_usage: failed to persist log (non-fatal)")


async def call_with_logging(db, provider, *,
                            system_prompt: str, user_block: str,
                            json_mode: bool = True,
                            purpose: str = "bot_pipeline",
                            conversation_id: str | None = None,
                            message_id: str | None = None,
                            user_id: str | None = None):
    """Call ``provider.chat`` and persist a usage log (success or error)."""
    projected_tokens = math.ceil((len(system_prompt or "") + len(user_block or "")) / 4) \
        + int(getattr(provider, "max_tokens", 0) or 0)
    quota = await ensure_monthly_token_quota(db, projected_tokens=projected_tokens)
    if not quota["allowed"]:
        from ai.providers import LLMUnavailable
        raise LLMUnavailable(
            f"Se alcanzó el cupo mensual de IA del plan ({quota['used']:,} de {quota['limit']:,} tokens)"
        )
    pricing = await load_pricing(db)
    fee_percent = await effective_fee_percent(db)
    t0 = time.perf_counter()
    try:
        res = await provider.chat(system_prompt=system_prompt,
                                  user_block=user_block, json_mode=json_mode)
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        msg = str(e)
        # Scrub potential query-string secrets (defensive; providers already do this)
        if "key=" in msg:
            msg = msg.split("key=")[0] + "key=•••"
        await log_usage(db, status="error", provider=provider.name,
                        model=provider.model, prompt_tokens=0,
                        completion_tokens=0, latency_ms=elapsed,
                        purpose=purpose, conversation_id=conversation_id,
                        message_id=message_id, user_id=user_id,
                        error_message=msg[:500], fee_percent=fee_percent,
                        pricing=pricing)
        raise
    await log_usage(db, status="success", provider=res.provider,
                    model=res.model, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens,
                    latency_ms=res.latency_ms, purpose=purpose,
                    conversation_id=conversation_id, message_id=message_id,
                    user_id=user_id,
                    provider_cost_usd=getattr(res, "provider_cost_usd", None),
                    provider_request_id=getattr(res, "provider_request_id", None),
                    fee_percent=fee_percent,
                    pricing=pricing)
    return res
