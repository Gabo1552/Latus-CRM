"""Unit tests for the business-hours utilities and scan_lead_no_response.

These are pure-process tests — they do NOT hit the running FastAPI server.
They:
  * import ``business_hours`` directly from ``backend/utils``
  * monkey-patch the ``db`` collections used by ``scan_lead_no_response``
    so we can drive deterministic scenarios without Mongo.

Covers the 6 acceptance scenarios from the task:
  1. business_seconds_between returns correct seconds for an interval fully
     inside business hours.
  2. No alert outside business hours when the business-time threshold is not
     yet reached.
  3. Alert IS generated once business-time threshold is crossed inside
     business hours.
  4. Weekend handling: Friday 17:00 -> Monday 10:00 -> 1h of business time
     (with 09-18 Mon-Fri).
  5. Timezone handling: switching ``business_timezone`` shifts windows.
  6. Idempotency: scan run twice produces exactly one notification.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

# --- Ensure backend/ is on sys.path so we can ``import server`` -----------
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# --- Provide env vars required by server.py at import time ----------------
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_unit_tests")
os.environ.setdefault("CORS_ORIGINS", "*")

from utils.business_hours import (  # noqa: E402
    business_seconds_between,
    is_within_business_hours,
)


# ---------------------------------------------------------------------------
# Settings fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def utc_settings():
    """Mon-Fri 09:00-18:00 in UTC."""
    return {
        "lead_no_response_enabled": True,
        "lead_no_response_threshold_hours": 2,
        "lead_no_response_business_hours_only": True,
        "business_hours_start": "09:00",
        "business_hours_end": "18:00",
        "business_days": [0, 1, 2, 3, 4],
        "business_timezone": "UTC",
    }


@pytest.fixture
def cordoba_settings(utc_settings):
    s = dict(utc_settings)
    s["business_timezone"] = "America/Argentina/Cordoba"
    return s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _tz(tzname, y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tzname)).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# 1) business_seconds_between fully inside one business window
# ---------------------------------------------------------------------------

class TestBusinessSecondsBetween:
    def test_full_inside_single_window(self, utc_settings):
        # Monday 2025-06-02 10:00 -> 13:00 UTC (Mon=0)
        start = _utc(2025, 6, 2, 10, 0)
        end = _utc(2025, 6, 2, 13, 0)
        assert business_seconds_between(start, end, utc_settings) == 3 * 3600

    def test_partial_outside_window_clips(self, utc_settings):
        # 08:30 -> 09:30 only the 30m after 09:00 counts
        start = _utc(2025, 6, 2, 8, 30)
        end = _utc(2025, 6, 2, 9, 30)
        assert business_seconds_between(start, end, utc_settings) == 30 * 60

    def test_zero_when_end_before_start(self, utc_settings):
        a = _utc(2025, 6, 2, 12, 0)
        b = _utc(2025, 6, 2, 11, 0)
        assert business_seconds_between(a, b, utc_settings) == 0


# ---------------------------------------------------------------------------
# 4) Weekend handling: Fri 17:00 -> Mon 10:00 => 1h
# ---------------------------------------------------------------------------

class TestWeekendHandling:
    def test_friday_evening_to_monday_morning(self, utc_settings):
        # Friday 2025-06-06 17:00 UTC -> Monday 2025-06-09 10:00 UTC
        fri = _utc(2025, 6, 6, 17, 0)
        mon = _utc(2025, 6, 9, 10, 0)
        # Fri remaining business window: 17->18 = 1h
        # Sat + Sun: skipped (weekend)
        # Mon: 09->10 = 1h
        # Total: 2h
        assert business_seconds_between(fri, mon, utc_settings) == 2 * 3600

    def test_friday_evening_post_close_to_monday_open(self, utc_settings):
        # Fri 18:00 (close) -> Mon 10:00 -> only Mon 09->10 counts -> 1h
        fri = _utc(2025, 6, 6, 18, 0)
        mon = _utc(2025, 6, 9, 10, 0)
        assert business_seconds_between(fri, mon, utc_settings) == 1 * 3600


# ---------------------------------------------------------------------------
# 5) Timezone handling: switching tz shifts the windows
# ---------------------------------------------------------------------------

class TestTimezone:
    def test_inside_when_local_inside_outside_when_utc_eval(self, cordoba_settings, utc_settings):
        # Cordoba is UTC-3. 12:00 UTC == 09:00 Cordoba (start of business).
        sample = _utc(2025, 6, 2, 12, 0)  # Monday
        # With Cordoba TZ -> exactly at 09:00 local -> inside
        assert is_within_business_hours(sample, cordoba_settings) is True
        # With UTC TZ -> 12:00 -> also inside (09-18 UTC) -> inside
        assert is_within_business_hours(sample, utc_settings) is True

        # 11:00 UTC = 08:00 Cordoba (outside) but inside UTC window
        sample2 = _utc(2025, 6, 2, 11, 0)
        assert is_within_business_hours(sample2, cordoba_settings) is False
        assert is_within_business_hours(sample2, utc_settings) is True

    def test_business_seconds_shift_with_tz(self, cordoba_settings, utc_settings):
        # 11:00 UTC -> 12:00 UTC on Mon 2025-06-02
        a = _utc(2025, 6, 2, 11, 0)
        b = _utc(2025, 6, 2, 12, 0)
        # UTC settings: both inside 09-18 UTC -> 3600s
        assert business_seconds_between(a, b, utc_settings) == 3600
        # Cordoba: 11Z=08:00 local, 12Z=09:00 local -> only 09 included? no, end==09 -> 0s
        assert business_seconds_between(a, b, cordoba_settings) == 0


# ---------------------------------------------------------------------------
# Scan tests (2,3,6) via monkey-patched server.db
# ---------------------------------------------------------------------------

class _Cursor:
    """Mimics motor's chainable find(...)->.sort(...).to_list(N) returning a coroutine."""

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        # newest first by created_at
        self._docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return self

    async def to_list(self, _n):
        return list(self._docs)


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query=None, projection=None):
        q = query or {}
        if "conversation_id" in q:
            cid = q["conversation_id"]
            return _Cursor([d for d in self.docs if d.get("conversation_id") == cid])
        if "role" in q and isinstance(q["role"], dict):
            wanted = set(q["role"].get("$in", []))
            return _Cursor([d for d in self.docs if d.get("role") in wanted])
        return _Cursor(self.docs)

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, *_a, **_k):
        return None

    async def update_many(self, *_a, **_k):
        return None

    async def delete_one(self, *_a, **_k):
        return None

    async def count_documents(self, *_a, **_k):
        return len(self.docs)


class _FakeDB:
    def __init__(self):
        self.conversations = _Collection()
        self.contacts = _Collection()
        self.leads = _Collection()
        self.messages = _Collection()
        self.notifications = _Collection()
        self.users = _Collection()
        self.settings = _Collection()
        self.organizations = _Collection()
        self.memberships = _Collection()
        self.whatsapp_routes = _Collection()


@pytest.fixture
def server_with_fakedb(monkeypatch):
    """Import the real server module but swap its ``db`` with a fake."""
    import server  # type: ignore

    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)

    # Stop the real scheduler from doing anything funny in tests.
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    return server, fake


def _setup_demo_one_unanswered(server, fake_db, *, msg_created_at_utc: datetime,
                               assigned_to: str = "user_admin01",
                               status: str = "open",
                               lead_status: str = "qualified"):
    """Insert: 1 contact, 1 lead, 1 conversation, 1 admin, 1 customer message."""
    asyncio.run(fake_db.users.insert_one({
        "user_id": "user_admin01", "email": "a@a.com", "name": "Admin",
        "role": "admin", "active": True,
    }))
    asyncio.run(fake_db.contacts.insert_one({
        "id": "ct_1", "name": "Ana Pérez",
    }))
    asyncio.run(fake_db.leads.insert_one({
        "id": "ld_1", "contact_id": "ct_1", "status": lead_status,
    }))
    asyncio.run(fake_db.conversations.insert_one({
        "id": "cv_1", "contact_id": "ct_1", "lead_id": "ld_1",
        "status": status, "priority": "medium",
        "bot_enabled": True, "assigned_to": assigned_to,
    }))
    asyncio.run(fake_db.messages.insert_one({
        "id": "msg_1", "conversation_id": "cv_1",
        "sender_type": "contact", "body": "hola, sigue ahí?",
        "created_at": msg_created_at_utc.isoformat(),
    }))


# 2) No alert outside business hours when business-time threshold not reached
class TestNoAlertOutsideBusinessHours:
    def test_threshold_not_reached_outside_hours(self, server_with_fakedb, monkeypatch):
        server, fake = server_with_fakedb

        # Force settings: business-only on, threshold 2h, UTC 09-18 Mon-Fri
        async def fake_settings():
            return {
                "lead_no_response_enabled": True,
                "lead_no_response_threshold_hours": 2,
                "lead_no_response_business_hours_only": True,
                "business_hours_start": "09:00",
                "business_hours_end": "18:00",
                "business_days": [0, 1, 2, 3, 4],
                "business_timezone": "UTC",
            }
        monkeypatch.setattr(server, "get_app_settings", fake_settings)

        # Pretend "now" is Saturday 12:00 UTC (outside business days)
        fake_now = _utc(2025, 6, 7, 12, 0)

        class _FixedDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)
        monkeypatch.setattr(server, "datetime", _FixedDT)

        # Customer message: Saturday 11:00 UTC -> only 0 business seconds elapsed
        _setup_demo_one_unanswered(server, fake, msg_created_at_utc=_utc(2025, 6, 7, 11, 0))

        qualifying = asyncio.run(server.scan_lead_no_response())
        # 0 business seconds < 2h threshold -> no qualifying
        assert qualifying == []
        # And of course no notifications created
        assert fake.notifications.docs == []


# 3) Alert IS generated once business-time threshold is crossed inside business hours
class TestAlertWhenThresholdCrossed:
    def test_alert_generated_inside_business_hours(self, server_with_fakedb, monkeypatch):
        server, fake = server_with_fakedb

        async def fake_settings():
            return {
                "lead_no_response_enabled": True,
                "lead_no_response_threshold_hours": 2,
                "lead_no_response_business_hours_only": True,
                "business_hours_start": "09:00",
                "business_hours_end": "18:00",
                "business_days": [0, 1, 2, 3, 4],
                "business_timezone": "UTC",
            }
        monkeypatch.setattr(server, "get_app_settings", fake_settings)

        # Now: Monday 12:00 UTC (inside window)
        fake_now = _utc(2025, 6, 2, 12, 0)

        class _FixedDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now if tz is None else fake_now.astimezone(tz)
        monkeypatch.setattr(server, "datetime", _FixedDT)

        # Customer message at Monday 09:30 UTC -> 2.5h business elapsed > 2h
        _setup_demo_one_unanswered(server, fake, msg_created_at_utc=_utc(2025, 6, 2, 9, 30))

        qualifying = asyncio.run(server.scan_lead_no_response())
        assert len(qualifying) == 1, qualifying

        # Exactly one notification created
        notifs = fake.notifications.docs
        assert len(notifs) == 1, notifs
        n = notifs[0]
        assert n["type"] == "lead_no_response"
        assert n["assigned_user_id"] == "user_admin01"
        assert n["title"].startswith("Lead sin respuesta:")
        assert n["priority"] == "high"


# 6) Idempotency
class TestIdempotency:
    def test_running_scan_twice_creates_one_notification(self, server_with_fakedb, monkeypatch):
        server, fake = server_with_fakedb

        async def fake_settings():
            return {
                "lead_no_response_enabled": True,
                "lead_no_response_threshold_hours": 2,
                "lead_no_response_business_hours_only": False,  # use wall clock
                "business_hours_start": "09:00",
                "business_hours_end": "18:00",
                "business_days": [0, 1, 2, 3, 4],
                "business_timezone": "UTC",
            }
        monkeypatch.setattr(server, "get_app_settings", fake_settings)

        # Customer message: 3h ago in wall-clock
        msg_dt = datetime.now(timezone.utc) - timedelta(hours=3)
        _setup_demo_one_unanswered(server, fake, msg_created_at_utc=msg_dt)

        # Scan twice
        q1 = asyncio.run(server.scan_lead_no_response())
        q2 = asyncio.run(server.scan_lead_no_response())
        assert len(q1) == 1
        assert len(q2) == 1  # still qualifies, but…
        # …only one notification exists (dedup on unread + type + entity + user)
        assert len(fake.notifications.docs) == 1, fake.notifications.docs
