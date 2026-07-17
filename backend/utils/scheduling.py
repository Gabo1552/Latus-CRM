"""Scheduling rules for person calendars and capacity-based business services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/Argentina/Buenos_Aires"
DEFAULT_WINDOWS = {
    "0": [{"start": "09:00", "end": "18:00"}],
    "1": [{"start": "09:00", "end": "18:00"}],
    "2": [{"start": "09:00", "end": "18:00"}],
    "3": [{"start": "09:00", "end": "18:00"}],
    "4": [{"start": "09:00", "end": "18:00"}],
    "5": [],
    "6": [],
}


class SchedulingError(ValueError):
    """A user-facing scheduling validation error."""


def validate_hhmm(value: str) -> str:
    try:
        hours, minutes = str(value).split(":", 1)
        h, m = int(hours), int(minutes)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        return f"{h:02d}:{m:02d}"
    except Exception as exc:
        raise SchedulingError(f"Horario inválido: {value!r}") from exc


def validate_timezone(value: str | None) -> str:
    candidate = (value or DEFAULT_TIMEZONE).strip()
    try:
        ZoneInfo(candidate)
    except Exception as exc:
        raise SchedulingError(f"Zona horaria inválida: {candidate}") from exc
    return candidate


def normalize_weekly_schedule(value: Mapping | None) -> dict[str, list[dict[str, str]]]:
    schedule: dict[str, list[dict[str, str]]] = {str(day): [] for day in range(7)}
    source = value if isinstance(value, Mapping) else {}
    for day in range(7):
        raw_windows = source.get(str(day), source.get(day, [])) or []
        if not isinstance(raw_windows, list):
            raise SchedulingError(f"Las franjas del día {day} deben ser una lista")
        windows = []
        for raw in raw_windows[:6]:
            if not isinstance(raw, Mapping):
                raise SchedulingError(f"Franja inválida para el día {day}")
            start = validate_hhmm(raw.get("start", ""))
            end = validate_hhmm(raw.get("end", ""))
            if end <= start:
                raise SchedulingError("El fin de una franja debe ser posterior al inicio")
            windows.append({"start": start, "end": end})
        windows.sort(key=lambda item: item["start"])
        for previous, current in zip(windows, windows[1:]):
            if current["start"] < previous["end"]:
                raise SchedulingError("Las franjas horarias de un mismo día no pueden superponerse")
        schedule[str(day)] = windows
    return schedule


def legacy_weekly_schedule(settings: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    raw_hours = str(settings.get("appointment_business_hours") or "09:00-18:00")
    try:
        start, end = raw_hours.split("-", 1)
        window = {"start": validate_hhmm(start.strip()), "end": validate_hhmm(end.strip())}
    except Exception:
        window = {"start": "09:00", "end": "18:00"}
    schedule = {str(day): [] for day in range(7)}
    for legacy_day in settings.get("appointment_available_days") or [1, 2, 3, 4, 5]:
        # Legacy convention: 1=Monday ... 6=Saturday, 0=Sunday.
        py_day = 6 if int(legacy_day) == 0 else int(legacy_day) - 1
        if 0 <= py_day <= 6:
            schedule[str(py_day)] = [dict(window)]
    return schedule


def normalize_person_availability(
    value: Mapping | None,
    bot_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = dict(value or {})
    defaults = dict(bot_settings or {})
    weekly = source.get("weekly_schedule")
    if weekly is None:
        weekly = legacy_weekly_schedule(defaults) if defaults else DEFAULT_WINDOWS
    return {
        "enabled": bool(source.get("enabled", True)),
        "timezone": validate_timezone(source.get("timezone") or defaults.get("appointment_timezone")),
        "default_duration_minutes": max(5, min(480, int(source.get("default_duration_minutes") or defaults.get("appointment_duration_minutes") or 30))),
        "buffer_minutes": max(0, min(120, int(source.get("buffer_minutes") or 0))),
        "weekly_schedule": normalize_weekly_schedule(weekly),
    }


def _service_id(value: str | None, name: str) -> str:
    raw = (value or "").strip().lower()
    if raw:
        return raw[:80]
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    return "_".join(part for part in slug.split("_") if part)[:70] or "servicio"


def normalize_services(values: list[dict] | None, timezone_name: str | None = None) -> list[dict[str, Any]]:
    timezone_value = validate_timezone(timezone_name)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(values or []):
        if not isinstance(raw, Mapping):
            raise SchedulingError("Cada servicio debe ser un objeto")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise SchedulingError("Cada servicio debe tener un nombre")
        base_id = _service_id(raw.get("id"), name)
        service_id = base_id
        suffix = 2
        while service_id in seen:
            service_id = f"{base_id}_{suffix}"
            suffix += 1
        seen.add(service_id)
        normalized.append({
            "id": service_id,
            "name": name[:120],
            "description": str(raw.get("description") or "").strip()[:500],
            "active": bool(raw.get("active", True)),
            "duration_minutes": max(5, min(480, int(raw.get("duration_minutes") or 30))),
            "max_concurrent": max(1, min(100, int(raw.get("max_concurrent") or 1))),
            "timezone": validate_timezone(raw.get("timezone") or timezone_value),
            "weekly_schedule": normalize_weekly_schedule(raw.get("weekly_schedule") or DEFAULT_WINDOWS),
            "sort_order": int(raw.get("sort_order", index)),
        })
    return sorted(normalized, key=lambda item: (item["sort_order"], item["name"].lower()))


def parse_datetime(value: str | datetime, timezone_name: str) -> datetime:
    tz = ZoneInfo(validate_timezone(timezone_name))
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception as exc:
            raise SchedulingError("La fecha y hora no tiene un formato ISO-8601 válido") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def _within_weekly_schedule(
    start_utc: datetime,
    end_utc: datetime,
    schedule: Mapping[str, list],
    timezone_name: str,
) -> bool:
    tz = ZoneInfo(timezone_name)
    local_start = start_utc.astimezone(tz)
    local_end = end_utc.astimezone(tz)
    if local_start.date() != local_end.date():
        return False
    start_hhmm = local_start.strftime("%H:%M")
    end_hhmm = local_end.strftime("%H:%M")
    return any(
        start_hhmm >= window["start"] and end_hhmm <= window["end"]
        for window in schedule.get(str(local_start.weekday()), [])
    )


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and end > other_start


async def get_person_availability(db, user_id: str, settings: Mapping[str, Any]) -> tuple[dict, dict]:
    user_doc = await db.users.find_one({"user_id": user_id, "active": {"$ne": False}}, {"_id": 0})
    if not user_doc:
        raise SchedulingError("La persona seleccionada no existe o está inactiva")
    availability = normalize_person_availability(user_doc.get("calendar_settings"), settings)
    if not availability["enabled"]:
        raise SchedulingError(f"{user_doc.get('name') or 'La persona'} no tiene habilitada la agenda")
    return user_doc, availability


def get_business_service(settings: Mapping[str, Any], service_id: str | None) -> dict:
    services = normalize_services(
        settings.get("appointment_services") or [],
        settings.get("appointment_timezone"),
    )
    service = next((item for item in services if item["id"] == service_id and item["active"]), None)
    if not service:
        raise SchedulingError("Seleccioná un servicio activo del local")
    return service


async def appointment_duration_minutes(
    db,
    settings: Mapping[str, Any],
    *,
    mode: str,
    assigned_to: str | None = None,
    service_id: str | None = None,
) -> int:
    if mode == "business":
        return int(get_business_service(settings, service_id)["duration_minutes"])
    if not assigned_to:
        raise SchedulingError("La cita debe estar asignada a una persona")
    _, availability = await get_person_availability(db, assigned_to, settings)
    return int(availability["default_duration_minutes"])


async def validate_appointment_slot(
    db,
    settings: Mapping[str, Any],
    *,
    start_time: str | datetime,
    end_time: str | datetime,
    mode: str,
    assigned_to: str | None = None,
    service_id: str | None = None,
    exclude_appointment_id: str | None = None,
) -> dict[str, Any]:
    if mode not in ("people", "business"):
        raise SchedulingError("La modalidad de agenda debe ser people o business")

    if mode == "people":
        if not assigned_to:
            raise SchedulingError("La cita debe estar asignada a una persona")
        person, resource = await get_person_availability(db, assigned_to, settings)
        timezone_name = resource["timezone"]
        query = {"assigned_to": assigned_to, "status": {"$ne": "cancelled"}}
        capacity = 1
        resource_name = person.get("name") or person.get("email") or "Persona"
    else:
        resource = get_business_service(settings, service_id)
        timezone_name = resource["timezone"]
        query = {"service_id": resource["id"], "status": {"$ne": "cancelled"}}
        capacity = int(resource["max_concurrent"])
        resource_name = resource["name"]

    start_utc = parse_datetime(start_time, timezone_name)
    end_utc = parse_datetime(end_time, timezone_name)
    if end_utc <= start_utc:
        raise SchedulingError("La hora de fin debe ser posterior a la hora de inicio")
    if not _within_weekly_schedule(start_utc, end_utc, resource["weekly_schedule"], timezone_name):
        raise SchedulingError(f"El horario está fuera de la disponibilidad configurada para {resource_name}")

    existing_docs = await db.appointments.find(query, {"_id": 0}).to_list(2000)
    intervals: list[tuple[datetime, datetime]] = []
    buffer_minutes = int(resource.get("buffer_minutes") or 0) if mode == "people" else 0
    for appointment in existing_docs:
        if appointment.get("id") == exclude_appointment_id:
            continue
        try:
            existing_start = parse_datetime(appointment["start_time"], timezone_name)
            existing_end = parse_datetime(appointment["end_time"], timezone_name)
        except (KeyError, SchedulingError):
            continue
        if buffer_minutes:
            existing_start -= timedelta(minutes=buffer_minutes)
            existing_end += timedelta(minutes=buffer_minutes)
        if _overlaps(start_utc, end_utc, existing_start, existing_end):
            intervals.append((existing_start, existing_end))

    if mode == "people" and intervals:
        raise SchedulingError(f"{resource_name} ya tiene otra cita en ese horario")

    if mode == "business":
        points = [(start_utc, 1), (end_utc, -1)]
        for existing_start, existing_end in intervals:
            points.extend([(existing_start, 1), (existing_end, -1)])
        active = peak = 0
        for _, delta in sorted(points, key=lambda point: (point[0], point[1])):
            active += delta
            peak = max(peak, active)
        if peak > capacity:
            raise SchedulingError(
                f"{resource_name} alcanzó el máximo de {capacity} turnos simultáneos en ese horario"
            )

    return {
        "mode": mode,
        "timezone": timezone_name,
        "resource_id": assigned_to if mode == "people" else resource["id"],
        "resource_name": resource_name,
        "duration_minutes": int(resource.get("default_duration_minutes") or resource.get("duration_minutes") or 30),
        "capacity": capacity,
        "start_time": start_utc.isoformat(),
        "end_time": end_utc.isoformat(),
    }


def _format_schedule(schedule: Mapping[str, list]) -> str:
    day_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    parts = []
    for day in range(7):
        windows = schedule.get(str(day), [])
        if windows:
            parts.append(f"{day_names[day]} " + ", ".join(f"{w['start']}-{w['end']}" for w in windows))
    return "; ".join(parts) or "sin horarios"


async def build_appointment_context(db, conv: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    mode = settings.get("appointment_mode") or "people"
    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(days=14)
    instructions = [
        "[Módulo de Agendamiento Activo]",
        f"Modalidad configurada: {'personas' if mode == 'people' else 'servicios del local'}.",
        f"Fecha y hora actual UTC: {now_utc.isoformat()}",
        "Nunca confirmes un horario fuera de estas disponibilidades.",
    ]

    if settings.get("appointment_rescheduling_enabled", True) and conv.get("contact_id"):
        existing = await db.appointments.find({
            "contact_id": conv.get("contact_id"),
            "event_type": "appointment",
            "status": "scheduled",
            "start_time": {"$gte": now_utc.isoformat()},
        }, {"_id": 0}).sort("start_time", 1).to_list(20)
        if existing:
            instructions.append("Turnos próximos ya agendados para este cliente:")
            for appointment in existing:
                instructions.append(
                    f"- {appointment.get('id')}: {appointment.get('title')}; "
                    f"desde {appointment.get('start_time')} hasta {appointment.get('end_time')}; "
                    f"persona {appointment.get('assigned_to') or '-'}; servicio {appointment.get('service_id') or '-'}"
                )
            instructions.extend([
                "Si el cliente pide cambiar, mover o reprogramar uno de esos turnos, usá decision=reschedule_appointment.",
                "En appointment_id colocá el ID exacto del turno existente y en appointment_start_time el nuevo horario.",
                "No uses schedule_appointment para una reprogramación porque duplicaría la reserva.",
                "Si el cliente confirma expresamente uno de esos turnos, usá decision=confirm_appointment y colocá su ID exacto en appointment_id.",
            ])

    if mode == "people":
        user_query: dict[str, Any] = {"active": {"$ne": False}}
        if conv.get("assigned_to"):
            user_query = {"user_id": conv.get("assigned_to"), "active": {"$ne": False}}
        people = await db.users.find(user_query, {"_id": 0}).to_list(100)
        instructions.append("Personas disponibles (usá el ID exacto en appointment_assigned_to):")
        for person in people:
            availability = normalize_person_availability(person.get("calendar_settings"), settings)
            if not availability["enabled"]:
                continue
            bookings = await db.appointments.find({
                "assigned_to": person["user_id"],
                "status": {"$ne": "cancelled"},
            }, {"_id": 0}).to_list(500)
            booked = []
            for appointment in bookings:
                try:
                    start = parse_datetime(appointment["start_time"], availability["timezone"])
                except (KeyError, SchedulingError):
                    continue
                if now_utc <= start <= horizon:
                    booked.append(f"{appointment.get('start_time')} a {appointment.get('end_time')}")
            instructions.append(
                f"- {person['user_id']}: {person.get('name')}. Zona {availability['timezone']}. "
                f"Duración {availability['default_duration_minutes']} min. "
                f"Horarios: {_format_schedule(availability['weekly_schedule'])}. "
                f"Ocupado: {', '.join(booked) if booked else 'sin reservas próximas'}."
            )
        instructions.append("appointment_service_id debe ser null.")
    else:
        services = normalize_services(settings.get("appointment_services") or [], settings.get("appointment_timezone"))
        instructions.append("Servicios disponibles (usá el ID exacto en appointment_service_id):")
        for service in services:
            if not service["active"]:
                continue
            bookings = await db.appointments.find({
                "service_id": service["id"],
                "status": {"$ne": "cancelled"},
            }, {"_id": 0}).to_list(1000)
            booked = []
            for appointment in bookings:
                try:
                    start = parse_datetime(appointment["start_time"], service["timezone"])
                except (KeyError, SchedulingError):
                    continue
                if now_utc <= start <= horizon:
                    booked.append(f"{appointment.get('start_time')} a {appointment.get('end_time')}")
            instructions.append(
                f"- {service['id']}: {service['name']}. {service['description']} "
                f"Duración {service['duration_minutes']} min. Capacidad simultánea {service['max_concurrent']}. "
                f"Zona {service['timezone']}. Horarios: {_format_schedule(service['weekly_schedule'])}. "
                f"Reservas: {', '.join(booked) if booked else 'sin reservas próximas'}."
            )
        instructions.append("appointment_assigned_to puede ser null; appointment_service_id es obligatorio.")

    instructions.extend([
        "Antes de usar schedule_appointment, verificá horario, duración, reservas y capacidad.",
        "Si no hay disponibilidad, ofrecé alternativas y no uses schedule_appointment.",
    ])
    return "\n".join(instructions)
