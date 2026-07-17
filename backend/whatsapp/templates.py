"""Validation and rendering helpers for approved WhatsApp templates."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ALLOWED_TEMPLATE_PARAMETERS = {
    "client_name",
    "client_phone",
    "appointment_date",
    "appointment_time",
    "appointment_title",
    "appointment_location",
    "service_name",
    "agent_name",
}

_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[_-][A-Za-z]{2})?$")


def normalize_templates(value: Any, *, purpose: str) -> list[dict]:
    """Normalize template settings while keeping Meta's variable order explicit."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("Las plantillas deben enviarse como una lista")
    if len(value) > 30:
        raise ValueError("Podés configurar hasta 30 plantillas por tipo")

    normalized = []
    seen_ids = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError("Cada plantilla debe ser un objeto")
        name = str(raw.get("name") or "").strip()
        if not name or not _TEMPLATE_NAME_RE.fullmatch(name):
            raise ValueError(
                "El nombre de Meta sólo puede contener minúsculas, números y guiones bajos"
            )
        template_id = str(raw.get("id") or f"{purpose}_{name}").strip()
        if not template_id or template_id in seen_ids:
            raise ValueError("Cada plantilla debe tener un identificador único")
        seen_ids.add(template_id)
        language = str(raw.get("language") or "es_AR").strip()
        if not _LANGUAGE_RE.fullmatch(language):
            raise ValueError(f"El idioma de la plantilla {name} no es válido")
        label = str(raw.get("label") or name.replace("_", " ").title()).strip()[:120]
        preview = str(raw.get("body_preview") or "").strip()[:2000]
        keys = raw.get("parameter_keys") or []
        if isinstance(keys, str):
            keys = [part.strip() for part in keys.split(",") if part.strip()]
        if not isinstance(keys, list) or len(keys) > 10:
            raise ValueError(f"Los parámetros de {name} no son válidos")
        clean_keys = [str(key).strip() for key in keys if str(key).strip()]
        unknown = [key for key in clean_keys if key not in ALLOWED_TEMPLATE_PARAMETERS]
        if unknown:
            raise ValueError(f"Parámetros no soportados en {name}: {', '.join(unknown)}")
        normalized.append({
            "id": template_id,
            "purpose": purpose,
            "label": label,
            "name": name,
            "language": language,
            "body_preview": preview,
            "parameter_keys": clean_keys,
            "active": raw.get("active") is not False,
            "sort_order": int(raw.get("sort_order") or index),
        })
    return sorted(normalized, key=lambda template: (template["sort_order"], template["label"].lower()))


def find_template(settings: Mapping[str, Any], template_id: str, *, purpose: str | None = None) -> dict | None:
    groups = (
        ("recontact", settings.get("whatsapp_recontact_templates") or []),
        ("appointment_reminder", settings.get("appointment_reminder_templates") or []),
    )
    for group_purpose, templates in groups:
        if purpose and group_purpose != purpose:
            continue
        for template in templates:
            if template.get("id") == template_id and template.get("active") is not False:
                return {**template, "purpose": group_purpose}
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def build_template_context(
    *,
    contact: Mapping[str, Any] | None,
    appointment: Mapping[str, Any] | None = None,
    assigned_user: Mapping[str, Any] | None = None,
    timezone_name: str = "America/Argentina/Buenos_Aires",
) -> dict[str, str]:
    contact = contact or {}
    appointment = appointment or {}
    assigned_user = assigned_user or {}
    try:
        local_tz = ZoneInfo(timezone_name)
    except Exception:
        local_tz = timezone.utc
    start = _parse_datetime(appointment.get("start_time"))
    local_start = start.astimezone(local_tz) if start else None
    return {
        "client_name": str(contact.get("name") or "cliente"),
        "client_phone": str(contact.get("phone") or contact.get("whatsapp_id") or ""),
        "appointment_date": local_start.strftime("%d/%m/%Y") if local_start else "",
        "appointment_time": local_start.strftime("%H:%M") if local_start else "",
        "appointment_title": str(appointment.get("title") or appointment.get("service_name") or "turno"),
        "appointment_location": str(appointment.get("location") or ""),
        "service_name": str(appointment.get("service_name") or appointment.get("title") or "turno"),
        "agent_name": str(assigned_user.get("name") or appointment.get("created_by_name") or "equipo"),
    }


def template_parameter_values(template: Mapping[str, Any], context: Mapping[str, str]) -> list[str]:
    return [str(context.get(key) or "-")[:1024] for key in template.get("parameter_keys") or []]


def render_template_preview(template: Mapping[str, Any], context: Mapping[str, str]) -> str:
    preview = str(template.get("body_preview") or template.get("label") or template.get("name") or "Plantilla")
    for key in ALLOWED_TEMPLATE_PARAMETERS:
        preview = preview.replace("{{" + key + "}}", str(context.get(key) or "-"))
    return preview
