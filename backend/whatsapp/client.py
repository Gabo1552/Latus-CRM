"""Outbound calls to the WhatsApp Cloud API.

Only text messages are supported in this phase. Media uploads are
intentionally out of scope.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import WAConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {500, 502, 503, 504}
DEFAULT_TIMEOUT = 10.0


class WhatsAppSendError(Exception):
    """Raised when the WhatsApp send call fails after retries."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 error_code: int | None = None, error_message: str = "",
                 payload: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        self.payload = payload or {}


async def send_text_message(cfg: WAConfig, to_wa_id: str, body: str,
                            *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Send a single text message via WhatsApp Cloud API.

    Performs **one** retry on transient errors (timeout or 5xx). On
    persistent failure raises :class:`WhatsAppSendError`. On success
    returns the parsed JSON from Meta (contains ``messages[0].id``).
    """
    if not (cfg.access_token and cfg.phone_number_id):
        raise WhatsAppSendError("WhatsApp no configurado", status_code=503)

    url = f"https://graph.facebook.com/{cfg.api_version}/{cfg.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": body},
    }

    last_exc: Exception | None = None
    for attempt in range(2):  # 1 try + 1 retry
        try:
            async with httpx.AsyncClient(timeout=timeout) as hc:
                resp = await hc.post(url, headers=headers, json=payload)
            if 200 <= resp.status_code < 300:
                return resp.json()
            # Non-2xx -> parse Meta error and decide retry
            data = _safe_json(resp)
            meta_err = (data.get("error") or {}) if isinstance(data, dict) else {}
            logger.warning(
                "WhatsApp send failed status=%s code=%s msg=%s phone_number_id=%s",
                resp.status_code, meta_err.get("code"), meta_err.get("message"), cfg.phone_number_id,
            )
            if resp.status_code in _RETRYABLE_STATUSES and attempt == 0:
                continue  # retry once
            raise WhatsAppSendError(
                "No se pudo enviar el mensaje",
                status_code=resp.status_code,
                error_code=meta_err.get("code"),
                error_message=str(meta_err.get("message") or ""),
                payload=data if isinstance(data, dict) else {},
            )
        except WhatsAppSendError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            logger.warning("WhatsApp send transient error attempt=%s err=%s", attempt, e)
            last_exc = e
            if attempt == 0:
                continue
            raise WhatsAppSendError(
                "No se pudo enviar el mensaje",
                status_code=504,
                error_message=str(e),
            ) from e
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("WhatsApp send unexpected error: %s", e)
            raise WhatsAppSendError(
                "No se pudo enviar el mensaje",
                status_code=500,
                error_message=str(e),
            ) from e

    # Defensive (loop always returns/raises)
    raise WhatsAppSendError("No se pudo enviar el mensaje", status_code=500,
                            error_message=str(last_exc) if last_exc else "")


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:500]}
