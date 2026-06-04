"""Lead-no-response automation + settings + Spanish notifications tests.

Covers the new Latus CRM features:
- GET/PATCH /api/settings (defaults + admin-only PATCH; non-admin -> 403)
- POST /api/automations/lead-no-response/scan
- scan_lead_no_response rules (status, lead status, last sender, threshold, dedup,
  agent reply suppresses, bot reply suppresses, enabled flag, threshold change)
- Dashboard requires_attention.no_response list
- Spanish notification titles/bodies
- Regression: bell, mark-as-read, mark-all-read, handoff toggle, simulate-inbound
"""

import os
import time
import pytest
import requests

BASE_URL = os.environ.get(
    'REACT_APP_BACKEND_URL',
    'https://lead-scan-scheduler.preview.latusagent.com'
).rstrip('/')

ADMIN_TOKEN = "test_session_admin_persist"
AGENT_TOKEN = "test_session_agent_persist"
ADMIN_H = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
AGENT_H = {"Authorization": f"Bearer {AGENT_TOKEN}", "Content-Type": "application/json"}


# ---------------- helpers ----------------
def _get(url, headers=ADMIN_H, **kw):
    return requests.get(f"{BASE_URL}{url}", headers=headers, timeout=30, **kw)


def _post(url, headers=ADMIN_H, **kw):
    return requests.post(f"{BASE_URL}{url}", headers=headers, timeout=30, **kw)


def _patch(url, headers=ADMIN_H, **kw):
    return requests.patch(f"{BASE_URL}{url}", headers=headers, timeout=30, **kw)


def _list_notifs(unread_only=False, headers=ADMIN_H):
    suffix = "?unread_only=true" if unread_only else ""
    r = _get(f"/api/notifications{suffix}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _reseed():
    """Restore demo state (admin only)."""
    r = _post("/api/seed", headers=ADMIN_H)
    assert r.status_code == 200, r.text


def _conversations():
    r = _get("/api/conversations")
    assert r.status_code == 200, r.text
    return r.json()


def _unread_lnr_for(conv_id, headers=ADMIN_H):
    return [n for n in _list_notifs(unread_only=True, headers=headers)
            if n["type"] == "lead_no_response" and n["related_entity_id"] == conv_id]


def _set_settings(**kw):
    r = _patch("/api/settings", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def fresh_demo():
    """Reseed once at start of module so message timestamps (~3h old) qualify."""
    _reseed()
    yield
    # Final reseed to leave state clean for next iteration
    _reseed()


@pytest.fixture
def restore_settings():
    """Capture settings before test and restore after."""
    snap = _get("/api/settings").json()
    yield
    _set_settings(**{k: v for k, v in snap.items() if k in (
        "lead_no_response_enabled", "lead_no_response_threshold_hours",
        "lead_no_response_business_hours_only"
    )})


# =====================================================================
# Settings
# =====================================================================
class TestSettings:
    def test_get_settings_defaults(self, restore_settings):
        # Reset to defaults first
        _set_settings(
            lead_no_response_enabled=True,
            lead_no_response_threshold_hours=2,
            lead_no_response_business_hours_only=False,
        )
        r = _get("/api/settings")
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["lead_no_response_enabled"] is True
        assert s["lead_no_response_threshold_hours"] == 2
        assert s["lead_no_response_business_hours_only"] is False

    def test_patch_settings_admin(self, restore_settings):
        r = _patch("/api/settings", json={
            "lead_no_response_threshold_hours": 5,
            "lead_no_response_enabled": False,
        })
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["lead_no_response_threshold_hours"] == 5
        assert s["lead_no_response_enabled"] is False
        # verify persistence via GET
        s2 = _get("/api/settings").json()
        assert s2["lead_no_response_threshold_hours"] == 5
        assert s2["lead_no_response_enabled"] is False

    def test_patch_settings_threshold_min_clamp(self, restore_settings):
        # threshold should clamp to >=1
        r = _patch("/api/settings", json={"lead_no_response_threshold_hours": 0})
        assert r.status_code == 200, r.text
        assert r.json()["lead_no_response_threshold_hours"] == 1

    def test_patch_settings_non_admin_forbidden(self):
        r = _patch("/api/settings", headers=AGENT_H,
                   json={"lead_no_response_threshold_hours": 99})
        assert r.status_code == 403, r.text


# =====================================================================
# scan_lead_no_response rules
# =====================================================================
class TestLeadNoResponseScan:
    def test_scan_creates_notif_for_unanswered_customer(self, restore_settings):
        # Ensure default-ish state
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        # Wipe unread to start clean
        _post("/api/notifications/read-all")

        r = _post("/api/automations/lead-no-response/scan")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "created_for" in body
        assert isinstance(body["created_for"], int)
        assert body["created_for"] >= 1, "Seeded backdated customer messages should qualify"

        # admin must see at least one lead_no_response notification
        notifs = _list_notifs(unread_only=True)
        lnr = [n for n in notifs if n["type"] == "lead_no_response"]
        assert len(lnr) >= 1, f"expected lead_no_response notif. Got: {[n['type'] for n in notifs]}"
        # Spanish title check
        sample = lnr[0]
        assert sample["title"].startswith("Lead sin respuesta:"), sample["title"]
        assert "más de" in sample.get("body", "") and " h " in sample.get("body", "")
        assert sample["priority"] == "high"
        assert sample["related_entity_type"] == "conversation"

    def test_scan_is_idempotent(self, restore_settings):
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        _post("/api/notifications/read-all")
        # First scan creates
        r1 = _post("/api/automations/lead-no-response/scan").json()
        first_lnr = [n for n in _list_notifs(unread_only=True) if n["type"] == "lead_no_response"]
        # Second + third scans must not duplicate (dedup on unread + type + entity + user)
        _post("/api/automations/lead-no-response/scan")
        _post("/api/automations/lead-no-response/scan")
        second_lnr = [n for n in _list_notifs(unread_only=True) if n["type"] == "lead_no_response"]
        assert len(second_lnr) == len(first_lnr), (
            f"dedup failed: first={len(first_lnr)} second={len(second_lnr)}")

    def test_agent_reply_suppresses_lnr(self, restore_settings):
        """Posting an agent message AFTER customer suppresses lead_no_response on next scan."""
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        # find a conversation that's currently qualifying (admin can see -> unassigned)
        convs = _conversations()
        # Pick an unassigned conv (Elena/Marcus) to make admin own the notification
        target = next((c for c in convs if not c.get("assigned_to") and c.get("status") != "resolved"), None)
        assert target, "need at least one unassigned conv"

        # Wipe unread + initial scan to confirm it WOULD qualify (admin will get notif)
        _post("/api/notifications/read-all")
        _post("/api/automations/lead-no-response/scan")
        had_lnr_before = len(_unread_lnr_for(target["id"])) >= 1

        # Now agent replies -> latest message is sender_type=agent
        r = _post(f"/api/conversations/{target['id']}/messages",
                  json={"body": "TEST agent reply", "sender_type": "agent"})
        assert r.status_code == 200, r.text

        # Mark read so dedup doesn't mask creation
        _post("/api/notifications/read-all")
        _post("/api/automations/lead-no-response/scan")
        after = _unread_lnr_for(target["id"])
        assert len(after) == 0, f"agent reply must suppress lnr. had_before={had_lnr_before}, after={after}"

    def test_bot_reply_suppresses_lnr(self, restore_settings):
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        convs = _conversations()
        # pick a different unassigned conv if possible; else any non-resolved
        unassigned = [c for c in convs if not c.get("assigned_to") and c.get("status") != "resolved"]
        # the prior test posted an agent msg to unassigned[0]; pick another
        target = unassigned[1] if len(unassigned) > 1 else unassigned[0]

        _post("/api/notifications/read-all")
        # bot replies after customer
        r = _post(f"/api/conversations/{target['id']}/messages",
                  json={"body": "TEST bot reply", "sender_type": "bot"})
        assert r.status_code == 200, r.text

        _post("/api/notifications/read-all")
        _post("/api/automations/lead-no-response/scan")
        after = _unread_lnr_for(target["id"])
        assert len(after) == 0, f"bot reply must suppress lnr. after={after}"

    def test_won_lost_lead_suppresses_lnr(self, restore_settings):
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        # Find a conversation with a related lead_id
        convs = _conversations()
        target = next((c for c in convs if c.get("lead_id")), None)
        assert target, "need at least one conv with lead_id"
        lead_id = target["lead_id"]

        # First scan should normally include it (if customer is latest). Mark read.
        _post("/api/notifications/read-all")
        # Set lead status -> won
        r = _patch(f"/api/leads/{lead_id}", json={"status": "won"})
        assert r.status_code == 200, r.text

        _post("/api/notifications/read-all")
        _post("/api/automations/lead-no-response/scan")
        lnr = _unread_lnr_for(target["id"])
        assert len(lnr) == 0, f"won lead must suppress lnr. got={lnr}"

        # revert
        _patch(f"/api/leads/{lead_id}", json={"status": "qualified"})

    def test_fallback_to_admins_when_unassigned(self, restore_settings):
        # Prior tests in this class posted agent/bot replies to unassigned convs,
        # which by-design suppresses lnr. Reseed to restore demo state.
        _reseed()
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        convs = _conversations()
        unassigned = [c for c in convs if not c.get("assigned_to") and c.get("status") != "resolved"]
        assert unassigned, "need unassigned conv (e.g. Marcus Webb)"

        _post("/api/notifications/read-all")
        _post("/api/automations/lead-no-response/scan")
        # Admin must receive at least one lead_no_response across unassigned convs
        notifs = _list_notifs(unread_only=True)
        lnr = [n for n in notifs if n["type"] == "lead_no_response"]
        target_ids = {c["id"] for c in unassigned}
        admin_got = [n for n in lnr if n["related_entity_id"] in target_ids]
        assert len(admin_got) >= 1, (
            f"admin should fallback-receive lnr for unassigned convs. lnr={[n['related_entity_id'] for n in lnr]} unassigned={target_ids}"
        )
        # assigned_user_id must equal admin's user id
        admin_id = _get("/api/auth/me").json()["user_id"]
        assert all(n["assigned_user_id"] == admin_id for n in admin_got)

    def test_disabled_setting_creates_nothing(self, restore_settings):
        # Wipe unread
        _post("/api/notifications/read-all")
        _set_settings(lead_no_response_enabled=False)
        r = _post("/api/automations/lead-no-response/scan")
        assert r.status_code == 200, r.text
        assert r.json()["created_for"] == 0
        lnr = [n for n in _list_notifs(unread_only=True) if n["type"] == "lead_no_response"]
        assert lnr == [], f"disabled flag must prevent creation. got={lnr}"

    def test_threshold_change_affects_qualification(self, restore_settings):
        # Raise threshold high -> no qualifying convs
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=999)
        _post("/api/notifications/read-all")
        r = _post("/api/automations/lead-no-response/scan")
        assert r.json()["created_for"] == 0

        # Lower threshold to 1 -> demo backdated (3h) messages qualify again
        _set_settings(lead_no_response_threshold_hours=1)
        _post("/api/notifications/read-all")
        r2 = _post("/api/automations/lead-no-response/scan")
        assert r2.json()["created_for"] >= 1


# =====================================================================
# Dashboard requires_attention.no_response
# =====================================================================
class TestDashboardNoResponse:
    def test_metrics_includes_no_response_list(self, restore_settings):
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        _post("/api/notifications/read-all")
        r = _get("/api/dashboard/metrics")
        assert r.status_code == 200, r.text
        ra = r.json().get("requires_attention", {})
        assert "no_response" in ra, f"missing no_response in requires_attention: {list(ra.keys())}"
        assert isinstance(ra["no_response"], list)
        assert len(ra["no_response"]) >= 1
        # brief shape: must have id and contact name-ish field
        item = ra["no_response"][0]
        assert "id" in item

    def test_metrics_no_response_empty_when_disabled(self, restore_settings):
        _set_settings(lead_no_response_enabled=False)
        r = _get("/api/dashboard/metrics")
        assert r.status_code == 200, r.text
        ra = r.json().get("requires_attention", {})
        assert ra.get("no_response") == []


# =====================================================================
# Spanish notification strings (regression on existing types)
# =====================================================================
class TestSpanishStrings:
    def test_spanish_titles(self, restore_settings):
        _set_settings(lead_no_response_enabled=True, lead_no_response_threshold_hours=2)
        # trigger overdue_task & lnr via dashboard, handoff via toggle, new_message via simulate-inbound
        convs = _conversations()
        # handoff: pick a bot_enabled conv and disable bot
        h_target = next((c for c in convs if c.get("bot_enabled")), convs[0])
        _post("/api/notifications/read-all")
        _patch(f"/api/conversations/{h_target['id']}", json={"bot_enabled": False})

        # simulate-inbound on unassigned -> new_message
        unassigned = [c for c in convs if not c.get("assigned_to")]
        if unassigned:
            _post(f"/api/conversations/{unassigned[0]['id']}/simulate-inbound")

        # dashboard triggers overdue_task + scan_lnr
        _get("/api/dashboard/metrics")

        notifs = _list_notifs(unread_only=True)
        # collect first of each type
        by_type = {}
        for n in notifs:
            by_type.setdefault(n["type"], n)
        # Spanish assertions on what's available
        if "lead_no_response" in by_type:
            assert by_type["lead_no_response"]["title"].startswith("Lead sin respuesta:")
        if "new_message" in by_type:
            assert by_type["new_message"]["title"].startswith("Nuevo mensaje de ")
        if "handoff_required" in by_type:
            assert by_type["handoff_required"]["title"].startswith("Requiere atención humana:")
        if "overdue_task" in by_type:
            assert by_type["overdue_task"]["title"].startswith("Tarea vencida:")
        # at least one Spanish-typed notif must exist
        assert any(k in by_type for k in (
            "lead_no_response", "new_message", "handoff_required", "overdue_task")), \
            f"expected at least one Spanish notif. got types={list(by_type.keys())}"


# =====================================================================
# Regression: bell core actions still work
# =====================================================================
class TestNotificationBellRegression:
    def test_unread_count_and_mark_all_read(self):
        # ensure some unread exist
        _get("/api/dashboard/metrics")
        # mark all read
        r = _post("/api/notifications/read-all")
        assert r.status_code == 200, r.text
        c = _get("/api/notifications/unread-count").json()["count"]
        assert c == 0

    def test_mark_single_read(self):
        # generate at least one notif
        _post("/api/automations/lead-no-response/scan")
        unread = _list_notifs(unread_only=True)
        if not unread:
            pytest.skip("no unread to mark")
        target = unread[0]
        r = _patch(f"/api/notifications/{target['id']}/read")
        assert r.status_code == 200, r.text
        # verify
        all_n = _list_notifs()
        upd = next(n for n in all_n if n["id"] == target["id"])
        assert upd["is_read"] is True
        assert upd["read_at"] is not None
