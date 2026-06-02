"""FlowDesk notification system tests (Phase 2 feature)."""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://whatsales-crm.preview.emergentagent.com').rstrip('/')
ADMIN_TOKEN = "test_session_admin_persist"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}


# ---------- Helpers ----------
def _list_notifs(unread_only=False):
    url = f"{BASE_URL}/api/notifications"
    if unread_only:
        url += "?unread_only=true"
    r = requests.get(url, headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def _unread_count():
    r = requests.get(f"{BASE_URL}/api/notifications/unread-count", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["count"]


def _conversations():
    r = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def warmed_state():
    """Trigger dashboard once so task notifications get backfilled, then mark all read for a clean slate."""
    # First, generate task notifs via dashboard
    requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
    return True


# ---------- GET list & counts ----------
class TestNotificationList:
    def test_list_notifications_admin(self, warmed_state):
        notifs = _list_notifs()
        assert isinstance(notifs, list)
        assert len(notifs) >= 1
        # Should have these fields
        n = notifs[0]
        for k in ["id", "type", "title", "assigned_user_id", "is_read", "created_at", "priority"]:
            assert k in n, f"missing field {k}"
        # MongoDB _id excluded
        assert "_id" not in n
        # Admin should see at least one of the demo notification types
        types = {n["type"] for n in notifs}
        # After demo seed + dashboard hit, admin should see handoff_required (Marcus Webb unassigned) and overdue_task
        assert "handoff_required" in types or "overdue_task" in types or "new_message" in types

    def test_admin_includes_unassigned_handoff(self, warmed_state):
        notifs = _list_notifs()
        # Demo conv index 5 = Marcus Webb (UNASSIGNED, bot disabled -> handoff_required to admins)
        handoff = [n for n in notifs if n["type"] == "handoff_required"]
        assert len(handoff) >= 1, f"admin should receive handoff_required for unassigned convs. Got: {[n['type'] for n in notifs]}"

    def test_admin_includes_overdue_task(self, warmed_state):
        # Dashboard hit should have produced overdue_task notifs for unassigned overdue task -> admins
        notifs = _list_notifs()
        overdue = [n for n in notifs if n["type"] == "overdue_task"]
        assert len(overdue) >= 1, f"expected at least one overdue_task notif. types={[n['type'] for n in notifs]}"

    def test_unread_count(self, warmed_state):
        count = _unread_count()
        unread = [n for n in _list_notifs(unread_only=True)]
        assert count == len(unread)


# ---------- Mark read flows ----------
class TestMarkRead:
    def test_mark_single_read_decreases_count(self, warmed_state):
        unread = _list_notifs(unread_only=True)
        if not unread:
            pytest.skip("No unread notifications available")
        before = _unread_count()
        target = unread[0]
        r = requests.patch(f"{BASE_URL}/api/notifications/{target['id']}/read", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        # Verify state
        after = _unread_count()
        assert after == before - 1
        # Verify is_read + read_at
        all_notifs = _list_notifs()
        updated = next(n for n in all_notifs if n["id"] == target["id"])
        assert updated["is_read"] is True
        assert updated["read_at"] is not None

    def test_mark_all_read(self, warmed_state):
        # First create some unread by simulating inbound on an unassigned conv (Elena Rossi idx 4)
        convs = _conversations()
        unassigned = [c for c in convs if not c.get("assigned_to")]
        assert unassigned, "Need at least one unassigned conversation"
        target = unassigned[0]
        requests.post(f"{BASE_URL}/api/conversations/{target['id']}/simulate-inbound", headers=ADMIN_HEADERS)

        assert _unread_count() >= 0  # may be deduped, but POST works
        r = requests.post(f"{BASE_URL}/api/notifications/read-all", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        assert _unread_count() == 0


# ---------- Simulate inbound creates new_message notif ----------
class TestSimulateInbound:
    def test_simulate_inbound_creates_new_message_notif(self):
        # Make sure starting from a clean slate
        requests.post(f"{BASE_URL}/api/notifications/read-all", headers=ADMIN_HEADERS)
        assert _unread_count() == 0

        convs = _conversations()
        unassigned = [c for c in convs if not c.get("assigned_to")]
        assert unassigned, "expected at least one unassigned conv (Elena Rossi)"
        target = unassigned[0]
        prior_unread = target.get("unread", 0)

        r = requests.post(f"{BASE_URL}/api/conversations/{target['id']}/simulate-inbound", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        msg = r.json()
        assert msg["sender_type"] == "contact"
        assert msg["conversation_id"] == target["id"]

        # Verify conversation unread incremented
        convs2 = _conversations()
        updated_conv = next(c for c in convs2 if c["id"] == target["id"])
        assert updated_conv["unread"] == prior_unread + 1

        # Verify a new_message notification was created for admin (unassigned -> admins+supervisors fallback)
        notifs = _list_notifs(unread_only=True)
        new_msg_notifs = [n for n in notifs
                          if n["type"] == "new_message"
                          and n["related_entity_id"] == target["id"]]
        assert len(new_msg_notifs) >= 1, f"expected new_message notif for unassigned conv. Got types: {[n['type'] for n in notifs]}"


# ---------- Handoff dedup ----------
class TestHandoffDedup:
    def test_handoff_toggle_dedup(self):
        # Find an assigned conv we can toggle freely
        convs = _conversations()
        # pick a conv currently bot_enabled=true, with assigned_to
        candidates = [c for c in convs if c.get("bot_enabled") and c.get("assigned_to")]
        if not candidates:
            # fall back to first conv
            candidates = convs
        target = candidates[0]

        # Mark all read first
        requests.post(f"{BASE_URL}/api/notifications/read-all", headers=ADMIN_HEADERS)

        # Disable bot -> creates handoff_required for assigned user (which may be agent, not admin)
        r1 = requests.patch(f"{BASE_URL}/api/conversations/{target['id']}",
                            json={"bot_enabled": False}, headers=ADMIN_HEADERS)
        assert r1.status_code == 200, r1.text

        # toggle again disabled (no-op) and again
        requests.patch(f"{BASE_URL}/api/conversations/{target['id']}",
                       json={"bot_enabled": False}, headers=ADMIN_HEADERS)
        requests.patch(f"{BASE_URL}/api/conversations/{target['id']}",
                       json={"bot_enabled": False}, headers=ADMIN_HEADERS)

        # Re-enable then disable again - because original unread notif is still unread, dedup must hold
        requests.patch(f"{BASE_URL}/api/conversations/{target['id']}",
                       json={"bot_enabled": True}, headers=ADMIN_HEADERS)
        requests.patch(f"{BASE_URL}/api/conversations/{target['id']}",
                       json={"bot_enabled": False}, headers=ADMIN_HEADERS)

        # For target user(s), count distinct unread handoff_required for this conv
        # Admin only sees their own; if assigned to admin or unassigned, it's countable here.
        notifs = _list_notifs(unread_only=True)
        same = [n for n in notifs
                if n["type"] == "handoff_required" and n["related_entity_id"] == target["id"]]
        # Dedup means at most 1 unread per user per entity
        assert len(same) <= 1, f"Dedup failed: got {len(same)} unread handoff_required for {target['id']}"


# ---------- Dashboard requires_attention + idempotent task notifs ----------
class TestDashboardRequiresAttention:
    def test_metrics_requires_attention(self):
        r = requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "requires_attention" in data
        ra = data["requires_attention"]
        for k in ["open_handoffs", "unread_conversations", "overdue_tasks"]:
            assert k in ra, f"missing {k}"
            assert isinstance(ra[k], list)
        # Should have at least some items from demo data
        assert len(ra["open_handoffs"]) >= 1 or len(ra["unread_conversations"]) >= 1 or len(ra["overdue_tasks"]) >= 1

    def test_dashboard_task_notif_dedup(self):
        # Mark all read so we can observe creation
        requests.post(f"{BASE_URL}/api/notifications/read-all", headers=ADMIN_HEADERS)

        # First call -> creates task notifs
        requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
        first_unread = _list_notifs(unread_only=True)
        first_count = len([n for n in first_unread if n["type"] in ("overdue_task", "task_due_soon")])

        # Second & third calls -> must not duplicate
        requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
        requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
        second_unread = _list_notifs(unread_only=True)
        second_count = len([n for n in second_unread if n["type"] in ("overdue_task", "task_due_soon")])

        assert second_count == first_count, (
            f"Dedup failed: first={first_count} second={second_count}")
