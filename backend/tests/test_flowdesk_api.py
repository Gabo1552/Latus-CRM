"""FlowDesk CRM API - full backend regression tests."""
import os
import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EXTERNAL_TESTS") != "1",
    reason="Pruebas E2E externas: ejecutar con RUN_EXTERNAL_TESTS=1",
)

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://lead-scan-scheduler.preview.latusagent.com').rstrip('/')
ADMIN_TOKEN = "test_session_admin_persist"
AGENT_TOKEN = "test_session_agent_persist"

ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}
AGENT_HEADERS = {"Authorization": f"Bearer {AGENT_TOKEN}", "Content-Type": "application/json"}


# ---------- Auth ----------
class TestAuth:
    def test_me_unauthenticated(self):
        r = requests.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_me_admin(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "admin@flowdesk.test"
        assert data["role"] == "admin"
        assert data["active"] is True


# ---------- Dashboard ----------
class TestDashboard:
    def test_metrics(self):
        r = requests.get(f"{BASE_URL}/api/dashboard/metrics", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        data = r.json()
        for key in ["pipeline_value", "leads_by_status", "conversion_rate",
                    "open_conversations", "human_handled", "open_tasks"]:
            assert key in data, f"missing key {key}"
        assert isinstance(data["leads_by_status"], dict)
        # demo data => should have leads
        assert data["total_leads"] >= 8


# ---------- Contacts ----------
class TestContacts:
    def test_list(self):
        r = requests.get(f"{BASE_URL}/api/contacts", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert len(r.json()) >= 8

    def test_search(self):
        r = requests.get(f"{BASE_URL}/api/contacts?search=Carlos", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert any("Carlos" in c["name"] for c in r.json())

    def test_create(self):
        payload = {"name": "TEST_Contact_X", "phone": "+1 999 555 0000",
                   "email": "test@example.com", "company": "TEST Co"}
        r = requests.post(f"{BASE_URL}/api/contacts", json=payload, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        c = r.json()
        assert c["name"] == payload["name"]
        assert "id" in c
        # verify GET
        g = requests.get(f"{BASE_URL}/api/contacts/{c['id']}", headers=ADMIN_HEADERS)
        assert g.status_code == 200
        assert g.json()["phone"] == payload["phone"]


# ---------- Leads ----------
class TestLeads:
    def test_list_and_filters(self):
        r = requests.get(f"{BASE_URL}/api/leads", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        all_leads = r.json()
        assert len(all_leads) >= 8
        # each lead should have a contact embedded
        assert any(l.get("contact") for l in all_leads)

        r2 = requests.get(f"{BASE_URL}/api/leads?status=qualified", headers=ADMIN_HEADERS)
        assert r2.status_code == 200
        for l in r2.json():
            assert l["status"] == "qualified"

        r3 = requests.get(f"{BASE_URL}/api/leads?priority=high", headers=ADMIN_HEADERS)
        assert r3.status_code == 200
        for l in r3.json():
            assert l["priority"] == "high"

    def test_create_get_update_delete(self):
        # need contact_id
        contacts = requests.get(f"{BASE_URL}/api/contacts", headers=ADMIN_HEADERS).json()
        cid = contacts[0]["id"]
        payload = {"contact_id": cid, "title": "TEST_Lead", "status": "new",
                   "priority": "medium", "value": 1234.5}
        r = requests.post(f"{BASE_URL}/api/leads", json=payload, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        lead = r.json()
        lid = lead["id"]
        assert lead["title"] == "TEST_Lead"

        # GET detail
        g = requests.get(f"{BASE_URL}/api/leads/{lid}", headers=ADMIN_HEADERS)
        assert g.status_code == 200
        detail = g.json()
        assert detail["contact"] is not None
        assert "notes" in detail and "tasks" in detail

        # PATCH
        u = requests.patch(f"{BASE_URL}/api/leads/{lid}",
                           json={"status": "qualified", "value": 9999.0, "priority": "high"},
                           headers=ADMIN_HEADERS)
        assert u.status_code == 200
        assert u.json()["status"] == "qualified"
        assert u.json()["value"] == 9999.0

        # DELETE
        d = requests.delete(f"{BASE_URL}/api/leads/{lid}", headers=ADMIN_HEADERS)
        assert d.status_code == 200

        # confirm 404
        g2 = requests.get(f"{BASE_URL}/api/leads/{lid}", headers=ADMIN_HEADERS)
        assert g2.status_code == 404


# ---------- Notes ----------
class TestNotes:
    def test_create_note_appears_in_lead(self):
        leads = requests.get(f"{BASE_URL}/api/leads", headers=ADMIN_HEADERS).json()
        lid = leads[0]["id"]
        r = requests.post(f"{BASE_URL}/api/notes",
                          json={"lead_id": lid, "body": "TEST_Note_body"},
                          headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert r.json()["body"] == "TEST_Note_body"
        # verify in lead detail
        detail = requests.get(f"{BASE_URL}/api/leads/{lid}", headers=ADMIN_HEADERS).json()
        assert any(n["body"] == "TEST_Note_body" for n in detail["notes"])


# ---------- Conversations ----------
class TestConversations:
    def test_list_and_filters(self):
        r = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        convs = r.json()
        assert len(convs) >= 8
        assert all(c.get("contact") for c in convs)

        r2 = requests.get(f"{BASE_URL}/api/conversations?status=open", headers=ADMIN_HEADERS)
        assert r2.status_code == 200
        for c in r2.json():
            assert c["status"] == "open"

    def test_detail_send_handoff(self):
        convs = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN_HEADERS).json()
        cid = convs[0]["id"]
        d = requests.get(f"{BASE_URL}/api/conversations/{cid}", headers=ADMIN_HEADERS)
        assert d.status_code == 200
        detail = d.json()
        assert "messages" in detail
        assert detail.get("contact") is not None
        before = len(detail["messages"])

        # send message
        s = requests.post(f"{BASE_URL}/api/conversations/{cid}/messages",
                          json={"body": "TEST_agent_reply", "sender_type": "agent"},
                          headers=ADMIN_HEADERS)
        assert s.status_code == 200
        assert s.json()["body"] == "TEST_agent_reply"
        d2 = requests.get(f"{BASE_URL}/api/conversations/{cid}", headers=ADMIN_HEADERS).json()
        assert len(d2["messages"]) == before + 1

        # toggle bot_enabled -> human handoff
        current_bot = detail.get("bot_enabled", True)
        u = requests.patch(f"{BASE_URL}/api/conversations/{cid}",
                           json={"bot_enabled": not current_bot},
                           headers=ADMIN_HEADERS)
        assert u.status_code == 200
        assert u.json()["bot_enabled"] == (not current_bot)

        # patch status/priority
        u2 = requests.patch(f"{BASE_URL}/api/conversations/{cid}",
                            json={"status": "pending", "priority": "high"},
                            headers=ADMIN_HEADERS)
        assert u2.status_code == 200
        assert u2.json()["status"] == "pending"
        assert u2.json()["priority"] == "high"


# ---------- AI (real LLM) ----------
class TestAI:
    def test_ai_summary_real(self):
        convs = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN_HEADERS).json()
        cid = convs[0]["id"]
        r = requests.post(f"{BASE_URL}/api/conversations/{cid}/ai-summary",
                          headers=ADMIN_HEADERS, timeout=60)
        assert r.status_code == 200, r.text
        s = r.json().get("summary", "")
        assert isinstance(s, str) and len(s) > 30, f"summary too short: {s!r}"

    def test_ai_suggest_real(self):
        convs = requests.get(f"{BASE_URL}/api/conversations", headers=ADMIN_HEADERS).json()
        cid = convs[0]["id"]
        r = requests.post(f"{BASE_URL}/api/conversations/{cid}/ai-suggest",
                          headers=ADMIN_HEADERS, timeout=60)
        assert r.status_code == 200, r.text
        s = r.json().get("suggestion", "")
        assert isinstance(s, str) and len(s) > 5, f"suggestion too short: {s!r}"


# ---------- Tasks ----------
class TestTasks:
    def test_crud_toggle(self):
        r = requests.get(f"{BASE_URL}/api/tasks", headers=ADMIN_HEADERS)
        assert r.status_code == 200

        c = requests.post(f"{BASE_URL}/api/tasks",
                          json={"title": "TEST_Task_A", "priority": "high"},
                          headers=ADMIN_HEADERS)
        assert c.status_code == 200
        tid = c.json()["id"]
        assert c.json()["status"] == "todo"

        u = requests.patch(f"{BASE_URL}/api/tasks/{tid}",
                           json={"status": "done"}, headers=ADMIN_HEADERS)
        assert u.status_code == 200
        assert u.json()["status"] == "done"

        d = requests.delete(f"{BASE_URL}/api/tasks/{tid}", headers=ADMIN_HEADERS)
        assert d.status_code == 200


# ---------- Admin RBAC ----------
class TestAdminRBAC:
    def test_list_users_admin(self):
        r = requests.get(f"{BASE_URL}/api/users", headers=ADMIN_HEADERS)
        assert r.status_code == 200
        users = r.json()
        assert any(u["email"] == "valentina@auraestetica.com.ar" for u in users)

    def test_role_change_admin(self):
        # promote/demote demo sales agent
        target = "user_demo_a2"
        r = requests.patch(f"{BASE_URL}/api/users/{target}",
                           json={"role": "supervisor"}, headers=ADMIN_HEADERS)
        assert r.status_code == 200
        assert r.json()["role"] == "supervisor"
        # revert
        r2 = requests.patch(f"{BASE_URL}/api/users/{target}",
                            json={"role": "sales_agent", "active": True},
                            headers=ADMIN_HEADERS)
        assert r2.status_code == 200
        assert r2.json()["role"] == "sales_agent"

    def test_non_admin_forbidden(self):
        r = requests.patch(f"{BASE_URL}/api/users/user_demo_a1",
                           json={"role": "admin"}, headers=AGENT_HEADERS)
        assert r.status_code == 403
