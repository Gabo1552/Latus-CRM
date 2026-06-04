"""HTTP-level smoke tests for new AI bot endpoints."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://lead-scan-scheduler.preview.latusagent.com").rstrip("/")
ADMIN = {"Authorization": "Bearer test_session_admin_persist", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def conv_id():
    r = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN, timeout=15)
    assert r.status_code == 200, r.text
    convs = r.json()
    assert len(convs) > 0, "No conversations available for testing"
    return convs[0]["id"]


# ---- /api/admin/bot-settings ---------------------------------------------

class TestBotSettings:
    def test_get_bot_settings(self):
        r = requests.get(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for key in ("bot_enabled_default", "confidence_threshold", "model", "faqs",
                    "tone", "business_instructions", "handoff_rules",
                    "recent_messages_context_max"):
            assert key in data, f"missing key {key}"
        assert isinstance(data["faqs"], list)

    def test_patch_threshold_out_of_range_400(self):
        r = requests.patch(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN,
                           json={"confidence_threshold": 1.5}, timeout=15)
        assert r.status_code == 400, r.text

    def test_patch_threshold_valid_persists(self):
        r = requests.patch(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN,
                           json={"confidence_threshold": 0.55}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["confidence_threshold"] == 0.55

        g = requests.get(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN, timeout=15)
        assert g.json()["confidence_threshold"] == 0.55

    def test_patch_invalid_model_400(self):
        r = requests.patch(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN,
                           json={"model": "gpt-3.5-turbo"}, timeout=15)
        assert r.status_code == 400, r.text

    def test_patch_invalid_ctxmax_400(self):
        r = requests.patch(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN,
                           json={"recent_messages_context_max": 2}, timeout=15)
        assert r.status_code == 400, r.text

    def test_patch_restore_ctxmax_12(self):
        r = requests.patch(f"{BASE_URL}/api/admin/bot-settings", headers=ADMIN,
                           json={"recent_messages_context_max": 12}, timeout=15)
        assert r.status_code == 200
        assert r.json()["recent_messages_context_max"] == 12


# ---- per-conversation bot endpoints --------------------------------------

class TestBotConversation:
    def test_reactivate_bot(self, conv_id):
        r = requests.post(f"{BASE_URL}/api/conversations/{conv_id}/bot/reactivate",
                          headers=ADMIN, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("bot_enabled") is True
        assert data.get("bot_status") == "bot_activo"

    def test_summary_regenerate(self, conv_id):
        r = requests.post(f"{BASE_URL}/api/conversations/{conv_id}/summary/regenerate",
                          headers=ADMIN, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, dict)
        assert "summary" in body, f"summary key missing: {body}"
        assert isinstance(body["summary"], str)

    def test_suggest_reply(self, conv_id):
        r = requests.post(f"{BASE_URL}/api/conversations/{conv_id}/bot/suggest-reply",
                          headers=ADMIN, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body, dict)
        assert "draft" in body, f"draft key missing: {body}"
        assert "confidence" in body, f"confidence key missing: {body}"
        assert "intent" in body, f"intent key missing: {body}"


# ---- regression: pre-existing endpoints still work -----------------------

class TestRegression:
    def test_list_conversations(self):
        r = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN, timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_conversation_detail(self, conv_id):
        r = requests.get(f"{BASE_URL}/api/conversations/{conv_id}", headers=ADMIN, timeout=15)
        assert r.status_code == 200
        assert r.json()["id"] == conv_id

    def test_post_message(self, conv_id):
        r = requests.post(f"{BASE_URL}/api/conversations/{conv_id}/messages",
                          headers=ADMIN, json={"body": "TEST_regression_msg"}, timeout=15)
        assert r.status_code in (200, 201), r.text
