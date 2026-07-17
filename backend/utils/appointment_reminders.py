"""Pure helpers for appointment reminder scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def reminder_fields(
    appointment: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    reset_status: bool,
) -> dict:
    """Return the reminder snapshot copied onto an appointment."""
    is_appointment = appointment.get("event_type", "appointment") == "appointment"
    is_scheduled = appointment.get("status", "scheduled") == "scheduled"
    configured_enabled = bool(settings.get("appointment_reminders_enabled"))
    explicit_enabled = appointment.get("reminder_enabled")
    enabled = configured_enabled if explicit_enabled is None else bool(explicit_enabled)
    enabled = enabled and is_appointment and is_scheduled and bool(appointment.get("contact_id"))
    try:
        minutes = int(
            appointment.get("reminder_minutes_before")
            if appointment.get("reminder_minutes_before") is not None
            else settings.get("appointment_reminder_minutes_before") or 1440
        )
    except (TypeError, ValueError):
        minutes = 1440
    minutes = min(43200, max(5, minutes))
    template_id = (
        appointment.get("reminder_template_id")
        or settings.get("appointment_reminder_template_id")
        or None
    )
    start = _as_utc(appointment.get("start_time"))
    due_at = (start - timedelta(minutes=minutes)).isoformat() if start and enabled else None
    fields = {
        "reminder_enabled": enabled,
        "reminder_minutes_before": minutes,
        "reminder_template_id": template_id,
        "reminder_due_at": due_at,
    }
    if reset_status:
        fields.update({
            "reminder_status": "pending" if enabled else "disabled",
            "reminder_sent_at": None,
            "reminder_error": None,
            "reminder_attempts": 0,
            "confirmation_status": "pending" if enabled else appointment.get("confirmation_status"),
        })
    return fields
