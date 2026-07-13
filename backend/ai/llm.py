"""Backwards-compatible LLM JSON helper.

Routes through the configurable multi-provider client in :mod:`providers`
with usage logging via :mod:`usage`. The public API (``call_llm_json``,
``LLMUnavailable``) is kept so the existing pipeline doesn't need to change.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .providers import LLMUnavailable, get_provider  # re-exported
from .usage import call_with_logging

logger = logging.getLogger(__name__)

__all__ = ["LLMUnavailable", "call_llm_json"]


async def call_llm_json(
    *,
    db,
    system_prompt: str,
    user_messages_block: str,
    provider: str | None = None,
    model: str | None = None,
    purpose: str = "bot_pipeline",
    conversation_id: str | None = None,
    message_id: str | None = None,
    user_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Call the configured LLM, log usage, and return ``(parsed_json, raw_text)``."""
    provider_obj = await get_provider(db, override_provider=provider, override_model=model, for_bot=(purpose == "bot_pipeline"))
    res = await call_with_logging(
        db, provider_obj,
        system_prompt=system_prompt,
        user_block=user_messages_block,
        purpose=purpose,
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user_id,
    )
    return _parse_json_response(res.content), (res.content or "")[:8000]


def _parse_json_response(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception as e:
        logger.warning("LLM JSON parse failed: %s | raw=%r", e, raw[:200])
        return {}
