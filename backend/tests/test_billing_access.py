from datetime import datetime, timedelta, timezone

from server import subscription_access_state


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_existing_unconfigured_organizations_remain_enabled():
    state = subscription_access_state({
        "status": "active",
        "subscription_status": "not_configured",
        "license_status": "not_configured",
    }, at=NOW)
    assert state["allowed"] is True


def test_active_trial_and_grace_period_are_enabled():
    trial = subscription_access_state({
        "status": "active", "subscription_status": "trialing", "license_status": "active",
        "trial_ends_at": (NOW + timedelta(days=2)).isoformat(),
    }, at=NOW)
    grace = subscription_access_state({
        "status": "active", "subscription_status": "past_due", "license_status": "grace_period",
        "grace_ends_at": (NOW + timedelta(days=1)).isoformat(),
    }, at=NOW)
    assert trial["allowed"] is True and trial["mode"] == "trial"
    assert grace["allowed"] is True and grace["mode"] == "grace"


def test_expired_or_suspended_access_is_blocked():
    expired_trial = subscription_access_state({
        "status": "active", "subscription_status": "trialing", "license_status": "active",
        "trial_ends_at": (NOW - timedelta(seconds=1)).isoformat(),
    }, at=NOW)
    suspended = subscription_access_state({
        "status": "active", "subscription_status": "active", "license_status": "suspended",
    }, at=NOW)
    expired_grace = subscription_access_state({
        "status": "active", "subscription_status": "active", "license_status": "grace_period",
        "grace_ends_at": (NOW - timedelta(days=1)).isoformat(),
    }, at=NOW)
    assert expired_trial == {"allowed": False, "mode": "blocked", "reason": "trial_expired", "expires_at": (NOW - timedelta(seconds=1)).isoformat()}
    assert suspended["allowed"] is False
    assert expired_grace["allowed"] is False
