"""Backwards-compatible LLM JSON helper.

Routes through the configurable multi-provider client in :mod:`providers`. The
public API (``call_llm_json``, ``LLMUnavailable``) is kept identical so the
existing pipeline and tests don't need to change.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .providers import LLMUnavailable, get_provider  # re-exported

logger = logging.getLogger(__name__)

__all__ = ["LLMUnavailable", "call_llm_json"]


async def call_llm_json(
    *,
    db,
    system_prompt: str,
    user_messages_block: str,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Call the configured LLM and return ``(parsed_json, raw_text)``.

    ``model`` overrides the configured one (used when bot_settings.model is
    pinned per-conversation).
    """
    provider = await get_provider(db, override_model=model)
    res = await provider.chat(system_prompt=system_prompt,
                              user_block=user_messages_block)
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
