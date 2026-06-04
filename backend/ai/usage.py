"""AI usage logging + pricing helpers (Phase 2).

Pricing seeds use USD per 1,000,000 tokens. Admins can override via the
``pricing_config`` document. Unknown models default to zero cost (logged
once per process).
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

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


async def save_pricing(db, model: str, input_per_million: float,
                       output_per_million: float, user_id: str | None) -> dict:
    if input_per_million < 0 or output_per_million < 0:
        raise ValueError("Los precios no pueden ser negativos")
    if not model or len(model) > 200:
        raise ValueError("Nombre de modelo inválido")
    doc = await db.pricing_config.find_one({"_id": "default"}, {"_id": 0}) or {}
    models = dict(doc.get("models") or {})
    models[model] = {
        "input": float(input_per_million),
        "output": float(output_per_million),
    }
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {"models": models, "updated_at": _now_iso(),
                  "updated_by": user_id}},
        upsert=True,
    )
    return await load_pricing(db)


async def reset_pricing(db, user_id: str | None) -> dict:
    """Restore default values by clearing the overrides map."""
    await db.pricing_config.update_one(
        {"_id": "default"},
        {"$set": {"models": {}, "updated_at": _now_iso(), "updated_by": user_id}},
        upsert=True,
    )
    return await load_pricing(db)


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
                    pricing: dict | None = None) -> None:
    """Persist a single usage log entry. Best-effort — never raises."""
    try:
        total = (prompt_tokens or 0) + (completion_tokens or 0)
        cost = estimate_cost(model, prompt_tokens, completion_tokens, pricing) \
            if status == "success" else 0.0
        await db.ai_usage_logs.insert_one({
            "log_id": uuid.uuid4().hex,
            "created_at": _now_iso(),
            "provider": provider,
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(total),
            "estimated_cost_usd": float(cost),
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
    pricing = await load_pricing(db)
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
                        error_message=msg[:500], pricing=pricing)
        raise
    await log_usage(db, status="success", provider=res.provider,
                    model=res.model, prompt_tokens=res.prompt_tokens,
                    completion_tokens=res.completion_tokens,
                    latency_ms=res.latency_ms, purpose=purpose,
                    conversation_id=conversation_id, message_id=message_id,
                    user_id=user_id, pricing=pricing)
    return res
