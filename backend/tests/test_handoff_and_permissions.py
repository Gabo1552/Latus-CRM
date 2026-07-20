from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Create a simple mock DB tailored to these tests
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)
    def sort(self, key_or_list, direction=None):
        return self
    async def to_list(self, n=None):
        return list(self._docs if n is None else self._docs[:n])

class _Coll:
    def __init__(self):
        self.docs = []
    def find(self, query=None, projection=None):
        docs_matched = []
        for d in self.docs:
            match = True
            if query:
                for k, v in query.items():
                    if d.get(k) != v:
                        match = False
                        break
            if match:
                docs_matched.append(d)
        return _Cursor(docs_matched)
    async def find_one(self, query, projection=None, sort=None):
        for d in self.docs:
            match = True
            for k, v in query.items():
                if d.get(k) != v:
                    match = False
                    break
            if match:
                return dict(d)
        return None
    async def insert_one(self, doc):
        self.docs.append(dict(doc))
    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            match = True
            for k, v in query.items():
                if d.get(k) != v:
                    match = False
                    break
            if match:
                if "$set" in update:
                    d.update(update["$set"])
                return

class _FakeDB:
    def __init__(self):
        self.users = _Coll()
        self.user_sessions = _Coll()
        self.contacts = _Coll()
        self.leads = _Coll()
        self.conversations = _Coll()
        self.messages = _Coll()
        self.app_secrets = _Coll()
        self.tasks = _Coll()
        self.organizations = _Coll()
        self.memberships = _Coll()
        self.whatsapp_routes = _Coll()

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@pytest.fixture
def srv(monkeypatch):
    for mod in list(sys.modules):
        if mod == "server" or mod.startswith("whatsapp") or mod.startswith("utils"):
            sys.modules.pop(mod, None)
    import server
    fake = _FakeDB()
    monkeypatch.setattr(server, "db", fake)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)
    monkeypatch.setattr(server, "send_text_message", AsyncMock(return_value={"messages": [{"id": "wa_msg_123"}]}))

    # seed users + sessions
    # 1. Admin
    _run(fake.users.insert_one({"user_id": "u_admin", "email": "admin@latus.test", "name": "Admin", "role": "admin", "active": True}))
    _run(fake.user_sessions.insert_one({"user_id": "u_admin", "session_token": "T-ADMIN", "expires_at": "2099-01-01T00:00:00+00:00"}))
    # 2. Agent 1 (assigned)
    _run(fake.users.insert_one({"user_id": "u_agent1", "email": "agent1@latus.test", "name": "Agent 1", "role": "agent", "active": True}))
    _run(fake.user_sessions.insert_one({"user_id": "u_agent1", "session_token": "T-AGENT1", "expires_at": "2099-01-01T00:00:00+00:00"}))
    # 3. Agent 2 (unassigned)
    _run(fake.users.insert_one({"user_id": "u_agent2", "email": "agent2@latus.test", "name": "Agent 2", "role": "agent", "active": True}))
    _run(fake.user_sessions.insert_one({"user_id": "u_agent2", "session_token": "T-AGENT2", "expires_at": "2099-01-01T00:00:00+00:00"}))

    client = TestClient(server.app)
    return server, fake, client

def test_sync_assigned_to_on_update_lead(srv):
    server, fake, client = srv
    # Seed data with required title
    _run(fake.leads.insert_one({"id": "lead_123", "contact_id": "contact_123", "title": "CRM Interest", "status": "nuevo", "assigned_to": "u_agent1"}))
    _run(fake.conversations.insert_one({"id": "conv_123", "contact_id": "contact_123", "lead_id": "lead_123", "assigned_to": "u_agent1"}))

    # Update lead to assign to u_agent2
    r = client.patch("/api/leads/lead_123", headers={"Authorization": "Bearer T-ADMIN"}, json={"assigned_to": "u_agent2"})
    assert r.status_code == 200
    assert r.json()["assigned_to"] == "u_agent2"

    # Verify conversation was synced
    conv = _run(fake.conversations.find_one({"id": "conv_123"}))
    assert conv["assigned_to"] == "u_agent2"

    # Unassign lead (None)
    r = client.patch("/api/leads/lead_123", headers={"Authorization": "Bearer T-ADMIN"}, json={"assigned_to": None})
    assert r.status_code == 200
    assert r.json()["assigned_to"] is None

    # Verify conversation was synced to None
    conv = _run(fake.conversations.find_one({"id": "conv_123"}))
    assert conv["assigned_to"] is None

def test_sync_assigned_to_on_update_conversation(srv):
    server, fake, client = srv
    # Seed data with required title
    _run(fake.leads.insert_one({"id": "lead_123", "contact_id": "contact_123", "title": "CRM Interest", "status": "nuevo", "assigned_to": "u_agent1"}))
    _run(fake.conversations.insert_one({"id": "conv_123", "contact_id": "contact_123", "lead_id": "lead_123", "assigned_to": "u_agent1"}))

    # Update conversation to assign to u_agent2
    r = client.patch("/api/conversations/conv_123", headers={"Authorization": "Bearer T-ADMIN"}, json={"assigned_to": "u_agent2"})
    assert r.status_code == 200
    assert r.json()["assigned_to"] == "u_agent2"

    # Verify lead was synced
    lead = _run(fake.leads.find_one({"id": "lead_123"}))
    assert lead["assigned_to"] == "u_agent2"

    # Unassign conversation (None)
    r = client.patch("/api/conversations/conv_123", headers={"Authorization": "Bearer T-ADMIN"}, json={"assigned_to": None})
    assert r.status_code == 200
    assert r.json()["assigned_to"] is None

    # Verify lead was synced to None
    lead = _run(fake.leads.find_one({"id": "lead_123"}))
    assert lead["assigned_to"] is None

def test_permissions_sending_messages(srv):
    server, fake, client = srv
    # Seed data
    _run(fake.contacts.insert_one({"id": "contact_123", "name": "Ana", "whatsapp_id": "5491155551234"}))
    _run(fake.conversations.insert_one({"id": "conv_123", "contact_id": "contact_123", "assigned_to": "u_agent1"}))
    _run(fake.messages.insert_one({
        "id": "msg_window", "conversation_id": "conv_123", "sender_type": "contact",
        "direction": "inbound", "body": "Hola", "created_at": datetime.now(timezone.utc).isoformat(),
    }))
    
    # Mock effective WA config
    async def mock_wa_config(db):
        from whatsapp.config import WAConfig
        return WAConfig(
            verify_token="verify_me",
            access_token="abc123token",
            phone_number_id="123456",
            business_account_id="78910"
        )
    with patch("server.wa_config_effective", mock_wa_config):
        # 1. Admin sends message -> allowed
        r = client.post("/api/conversations/conv_123/send-whatsapp", headers={"Authorization": "Bearer T-ADMIN"}, json={"text": "Hello"})
        assert r.status_code == 200

        # 2. Assigned Agent sends message -> allowed
        r = client.post("/api/conversations/conv_123/send-whatsapp", headers={"Authorization": "Bearer T-AGENT1"}, json={"text": "Hello"})
        assert r.status_code == 200

        # 3. Unassigned Agent sends message -> 403 Forbidden
        r = client.post("/api/conversations/conv_123/send-whatsapp", headers={"Authorization": "Bearer T-AGENT2"}, json={"text": "Hello"})
        assert r.status_code == 403
        assert "Solo el operador asignado" in r.json()["detail"]


def test_lead_creation_and_healing(srv):
    server, fake, client = srv
    # 1. Post a new contact
    r = client.post("/api/contacts", headers={"Authorization": "Bearer T-ADMIN"}, json={
        "name": "Juan Perez",
        "phone": "+54911223344",
        "email": "juan@perez.com",
        "company": "Perez Co"
    })
    assert r.status_code == 200
    contact_id = r.json()["id"]

    # Verify a lead was automatically created
    lead = _run(fake.leads.find_one({"contact_id": contact_id}))
    assert lead is not None
    assert lead["title"] == "Lead de Juan Perez"
    assert lead["status"] == "new"

    # 2. Test self-healing on list_contacts
    # Insert contact manually without a lead
    _run(fake.contacts.insert_one({"id": "contact_no_lead", "name": "Maria", "phone": "+5491100000"}))
    r = client.get("/api/contacts", headers={"Authorization": "Bearer T-ADMIN"})
    assert r.status_code == 200

    # Verify a lead was healed for Maria
    lead_healed = _run(fake.leads.find_one({"contact_id": "contact_no_lead"}))
    assert lead_healed is not None
    assert lead_healed["title"] == "Lead de Maria"


def test_lead_products_value_calculation(srv):
    server, fake, client = srv
    # Seed lead
    _run(fake.leads.insert_one({"id": "lead_val_123", "contact_id": "contact_123", "title": "CRM Interest", "status": "new", "value": 0.0, "products": []}))

    # Patch products list
    products_payload = [
        {"name": "Consultoria", "price": 150.0, "quantity": 2},
        {"name": "Soporte Anual", "price": 500.0, "quantity": 1}
    ]
    r = client.patch("/api/leads/lead_val_123", headers={"Authorization": "Bearer T-ADMIN"}, json={"products": products_payload})
    assert r.status_code == 200
    data = r.json()
    
    # 2 * 150 + 1 * 500 = 800
    assert data["value"] == 800.0
    assert len(data["products"]) == 2
    assert data["products"][0]["name"] == "Consultoria"
    assert data["products"][0]["price"] == 150.0
    assert data["products"][0]["quantity"] == 2


def test_task_list_enrichment(srv):
    server, fake, client = srv
    # Seed contact, lead, and task
    _run(fake.contacts.insert_one({"id": "contact_abc", "name": "Carlos Gomez", "phone": "+54911222233"}))
    _run(fake.leads.insert_one({"id": "lead_abc", "contact_id": "contact_abc", "title": "CRM Interest", "status": "new"}))
    _run(fake.tasks.insert_one({"id": "task_abc", "title": "Llamar a Carlos", "lead_id": "lead_abc", "status": "todo"}))

    # Fetch tasks
    r = client.get("/api/tasks", headers={"Authorization": "Bearer T-ADMIN"})
    assert r.status_code == 200
    tasks_list = r.json()
    
    # Find our task
    task_doc = next((t for t in tasks_list if t["id"] == "task_abc"), None)
    assert task_doc is not None
    assert task_doc["lead"] is not None
    assert task_doc["lead"]["title"] == "CRM Interest"
    assert task_doc["lead"]["contact"] is not None
    assert task_doc["lead"]["contact"]["name"] == "Carlos Gomez"
