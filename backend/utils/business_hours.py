"""Business-hours utilities for Latus CRM.

Pure helpers — no DB access, no FastAPI dependencies — so they can be unit
tested in isolation.

Settings shape (a plain ``dict`` is accepted; the same keys live in
``DEFAULT_SETTINGS`` in ``server.py``):

    business_hours_start    "HH:MM"     e.g. "09:00"
    business_hours_end      "HH:MM"     e.g. "18:00"
    business_days           List[int]   0=Mon ... 6=Sun, e.g. [0,1,2,3,4]
    business_timezone       IANA str    e.g. "America/Argentina/Cordoba"

All public functions accept timezone-aware UTC datetimes (or naive — which
are coerced to UTC) and do *all* day-by-day math inside the configured
``business_timezone`` via :mod:`zoneinfo`.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Argentina/Cordoba"
DEFAULT_START = "09:00"
DEFAULT_END = "18:00"
DEFAULT_DAYS = [0, 1, 2, 3, 4]  # Mon-Fri


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(value: str, default: str) -> time:
    """Parse an ``HH:MM`` string to a :class:`datetime.time`.

    Falls back to ``default`` if ``value`` is missing or malformed.
    """
    raw = (value or default or "00:00").strip()
    try:
        hh, mm = raw.split(":")
        return time(hour=int(hh), minute=int(mm))
    except (ValueError, AttributeError):
        hh, mm = default.split(":")
        return time(hour=int(hh), minute=int(mm))


def _normalize_days(days: Iterable[int] | None) -> set[int]:
    """Coerce a settings ``business_days`` value to a clamped set of ints."""
    if not days:
        return set(DEFAULT_DAYS)
    out: set[int] = set()
    for d in days:
        try:
            n = int(d)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 6:
            out.add(n)
    return out or set(DEFAULT_DAYS)


def _tz(settings: Mapping | None) -> ZoneInfo:
    name = (settings or {}).get("business_timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _to_utc_aware(dt: datetime) -> datetime:
    """Ensure ``dt`` is timezone-aware in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _window_for_local_date(local_date: datetime, settings: Mapping) -> tuple[datetime, datetime] | None:
    """Return the (start, end) datetimes of the business window for a given
    local date, or ``None`` if that date is not a business day.

    The returned datetimes are timezone-aware in the *business* timezone.
    """
    days = _normalize_days(settings.get("business_days"))
    if local_date.weekday() not in days:
        return None

    tz = _tz(settings)
    start_t = _parse_hhmm(settings.get("business_hours_start"), DEFAULT_START)
    end_t = _parse_hhmm(settings.get("business_hours_end"), DEFAULT_END)

    start = datetime(
        local_date.year, local_date.month, local_date.day,
        start_t.hour, start_t.minute, 0, tzinfo=tz,
    )
    end = datetime(
        local_date.year, local_date.month, local_date.day,
        end_t.hour, end_t.minute, 0, tzinfo=tz,
    )
    if end <= start:
        return None
    return start, end


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_within_business_hours(dt: datetime, settings: Mapping) -> bool:
    """``True`` iff ``dt`` falls inside a business window (in the configured TZ).

    Accepts naive (assumed UTC) or aware datetimes.
    """
    utc_dt = _to_utc_aware(dt)
    local = utc_dt.astimezone(_tz(settings))
    win = _window_for_local_date(local, settings)
    if not win:
        return False
    start, end = win
    return start <= local < end


def business_seconds_between(start_dt: datetime, end_dt: datetime, settings: Mapping) -> int:
    """Number of *business* seconds between ``start_dt`` and ``end_dt``.

    Only seconds inside configured business windows count. Weekends / non
    business days are skipped. All math is done in the configured business
    timezone so DST transitions are respected.

    If ``end_dt <= start_dt`` returns ``0``.
    """
    start_utc = _to_utc_aware(start_dt)
    end_utc = _to_utc_aware(end_dt)
    if end_utc <= start_utc:
        return 0

    tz = _tz(settings)
    start_local = start_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    total = 0
    # Iterate day-by-day in local time so we correctly handle DST shifts.
    cursor_date = start_local.date()
    last_date = end_local.date()
    while cursor_date <= last_date:
        day_anchor = datetime(
            cursor_date.year, cursor_date.month, cursor_date.day,
            12, 0, 0, tzinfo=tz,  # noon avoids DST edge ambiguity for window calc
        )
        win = _window_for_local_date(day_anchor, settings)
        if win is not None:
            day_start, day_end = win
            seg_start = max(day_start, start_local)
            seg_end = min(day_end, end_local)
            if seg_end > seg_start:
                total += int((seg_end - seg_start).total_seconds())
        cursor_date = cursor_date + timedelta(days=1)
    return total


def next_business_moment(dt: datetime, settings: Mapping) -> datetime:
    """Return the first datetime >= ``dt`` that is inside business hours.

    Useful for deferring "would have alerted" decisions until the next tick.
    Returned datetime is UTC-aware. Looks at most 14 days ahead.
    """
    utc_dt = _to_utc_aware(dt)
    tz = _tz(settings)
    local = utc_dt.astimezone(tz)
    for offset in range(0, 14):
        day_anchor = datetime(
            local.year, local.month, local.day, 12, 0, 0, tzinfo=tz,
        ) + timedelta(days=offset)
        win = _window_for_local_date(day_anchor, settings)
        if win is None:
            continue
        day_start, day_end = win
        if offset == 0:
            if local < day_start:
                return day_start.astimezone(timezone.utc)
            if day_start <= local < day_end:
                return local.astimezone(timezone.utc)
            # past today's window -> next iteration
            continue
        return day_start.astimezone(timezone.utc)
    # fallback (shouldn't happen): return original
    return utc_dt
