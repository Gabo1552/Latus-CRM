"""Parse WhatsApp Cloud webhook payloads into simple dataclasses.

This only knows about the small subset Latus CRM cares about: text +
media message shells, statuses, errors. Everything else is preserved in
``raw`` so audit logs keep the full event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_TYPES = {
    "text", "image", "audio", "document", "video", "sticker",
    "button", "interactive",
}


@dataclass
class InboundMessage:
    wa_id: str                # contact phone (Meta wa_id), e.g. "5491155551234"
    profile_name: str          # contact display name from WhatsApp profile (may be "")
    message_id: str            # message external id (wamid....)
    timestamp: str             # ISO-8601 UTC
    message_type: str          # see SUPPORTED_TYPES
    text: str                  # only filled for type=text/button/interactive
    phone_number_id: str       # destination phone_number_id
    raw: dict = field(default_factory=dict)


@dataclass
class StatusUpdate:
    message_id: str            # external id
    status: str                # sent|delivered|read|failed
    timestamp: str             # ISO-8601 UTC
    recipient_id: str = ""
    error_code: int | None = None
    error_message: str = ""
    raw: dict = field(default_factory=dict)


def _ts_to_iso(ts: Any) -> str:
    """WhatsApp gives epoch seconds as string. Convert to ISO-8601 UTC."""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _extract_text(msg: dict) -> str:
    mtype = msg.get("type", "")
    if mtype == "text":
        return (msg.get("text") or {}).get("body", "") or ""
    if mtype == "button":
        return (msg.get("button") or {}).get("text", "") or ""
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        sub = inter.get("type")
        if sub == "button_reply":
            return (inter.get("button_reply") or {}).get("title", "") or ""
        if sub == "list_reply":
            return (inter.get("list_reply") or {}).get("title", "") or ""
    return ""


def parse_inbound_value(value: dict) -> tuple[list[InboundMessage], list[StatusUpdate], list[dict]]:
    """Parse a single ``entry[].changes[].value`` block.

    Returns (messages, statuses, errors). ``errors`` is the raw list as
    received so the caller can persist them for the Admin panel.
    """
    messages: list[InboundMessage] = []
    statuses: list[StatusUpdate] = []
    errors: list[dict] = list(value.get("errors") or [])

    metadata = value.get("metadata") or {}
    phone_number_id = str(metadata.get("phone_number_id") or "")

    # ---- contacts (profile names) keyed by wa_id ------------------------
    profiles: dict[str, str] = {}
    for c in (value.get("contacts") or []):
        wa = str(c.get("wa_id") or "")
        if wa:
            profiles[wa] = (c.get("profile") or {}).get("name", "") or ""

    # ---- messages -------------------------------------------------------
    for msg in (value.get("messages") or []):
        mtype = str(msg.get("type") or "")
        if mtype not in SUPPORTED_TYPES:
            # still ingest as raw event so we don't drop audit trail; skip
            # creating a Message row
            errors.append({"unsupported_type": mtype, "message": msg})
            continue
        wa = str(msg.get("from") or "")
        if not wa:
            continue
        messages.append(InboundMessage(
            wa_id=wa,
            profile_name=profiles.get(wa, ""),
            message_id=str(msg.get("id") or ""),
            timestamp=_ts_to_iso(msg.get("timestamp")),
            message_type=mtype,
            text=_extract_text(msg),
            phone_number_id=phone_number_id,
            raw=msg,
        ))

    # ---- statuses -------------------------------------------------------
    for st in (value.get("statuses") or []):
        err = (st.get("errors") or [None])[0] or {}
        statuses.append(StatusUpdate(
            message_id=str(st.get("id") or ""),
            status=str(st.get("status") or ""),
            timestamp=_ts_to_iso(st.get("timestamp")),
            recipient_id=str(st.get("recipient_id") or ""),
            error_code=err.get("code") if err else None,
            error_message=str((err or {}).get("message") or "") if err else "",
            raw=st,
        ))

    return messages, statuses, errors
