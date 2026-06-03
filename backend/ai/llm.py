"""Thin wrapper around Emergent's universal LLM key via emergentintegrations."""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


async def call_llm_json(
    *,
    system_prompt: str,
    user_messages_block: str,
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Call the LLM and return its parsed JSON object.

    Uses ``emergentintegrations`` (Emergent universal key) so a single env var
    ``EMERGENT_LLM_KEY`` covers OpenAI/Anthropic/Google models.
    """
    key = (os.environ.get("EMERGENT_LLM_KEY") or "").strip()
    if not key:
        raise LLMUnavailable("EMERGENT_LLM_KEY no configurado")
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception as e:  # pragma: no cover
        raise LLMUnavailable(f"emergentintegrations no disponible: {e}") from e

    provider = "openai" if model.lower().startswith("gpt") else "anthropic"
    try:
        chat = (LlmChat(api_key=key, session_id=f"latus-bot-{uuid.uuid4().hex[:8]}",
                        system_message=system_prompt)
                .with_model(provider, model)
                .with_params(max_tokens=900, temperature=0.2))
        resp = await chat.send_message(UserMessage(text=user_messages_block))
    except LLMUnavailable:
        raise
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        raise LLMUnavailable(f"llamada al LLM falló: {e}") from e
    raw = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
    return _parse_json_response(raw), (raw or "")[:8000]


def _parse_json_response(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    # strip code fences if any
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # find the first { and the last }
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception as e:
        logger.warning("LLM JSON parse failed: %s | raw=%r", e, raw[:200])
        return {}
