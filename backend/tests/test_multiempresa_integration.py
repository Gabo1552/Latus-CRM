"""End-to-end request tests for tenant isolation inside one backend process."""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_multiempresa_tests")
os.environ.setdefault("CORS_ORIGINS", "*")


def _matches(document: dict, query: dict | None) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, item) for item in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$exists" in expected:
                exists = key in document and actual is not None
                if exists != bool(expected["$exists"]):
                    return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$regex" in expected:
                flags = re.IGNORECASE if expected.get("$options") == "i" else 0
                if not re.search(str(expected["$regex"]), str(actual or ""), flags):
                    return False
            known = {"$in", "$exists", "$ne", "$gte", "$lte", "$regex", "$options"}
            if set(expected) - known:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents):
        self.documents = [dict(item) for item in documents]

    def sort(self, key_or_list, direction=None):
        pairs = [(key_or_list, direction)] if isinstance(key_or_list, str) else key_or_list
        for key, order in reversed(pairs):
            self.documents.sort(
                key=lambda item: item.get(key, "") or "", reverse=order == -1
            )
        return self

    async def to_list(self, length=None):
        return list(self.documents if length is None else self.documents[:length])

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self):
        self.documents: list[dict] = []

    def find(self, query=None, projection=None):
        return _Cursor(item for item in self.documents if _matches(item, query))

    async def find_one(self, query=None, projection=None, sort=None):
        items = [item for item in self.documents if _matches(item, query)]
        if sort:
            for key, order in reversed(sort):
                items.sort(key=lambda item: item.get(key, "") or "", reverse=order == -1)
        return dict(items[0]) if items else None

    async def insert_one(self, document, *args, **kwargs):
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=document.get("_id"))

    async def insert_many(self, documents, *args, **kwargs):
        for document in documents:
            await self.insert_one(document)
        return SimpleNamespace(inserted_ids=[])

    async def update_one(self, query, update, upsert=False, *args, **kwargs):
        for document in self.documents:
            if _matches(document, query):
                self._apply_update(document, update)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            document = {
                key: value for key, value in (query or {}).items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            document.update(update.get("$setOnInsert", {}))
            self._apply_update(document, update)
            self.documents.append(document)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update, *args, **kwargs):
        matched = 0
        for document in self.documents:
            if _matches(document, query):
                self._apply_update(document, update)
                matched += 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    @staticmethod
    def _apply_update(document, update):
        document.update(update.get("$set", {}))
        for key in update.get("$unset", {}):
            document.pop(key, None)
        for key, amount in update.get("$inc", {}).items():
            document[key] = (document.get(key) or 0) + amount

    async def replace_one(self, query, replacement, upsert=False, *args, **kwargs):
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                self.documents[index] = dict(replacement)
                return SimpleNamespace(matched_count=1)
        if upsert:
            self.documents.append(dict(replacement))
        return SimpleNamespace(matched_count=0)

    async def delete_one(self, query, *args, **kwargs):
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query, *args, **kwargs):
        previous = len(self.documents)
        self.documents[:] = [item for item in self.documents if not _matches(item, query)]
        return SimpleNamespace(deleted_count=previous - len(self.documents))

    async def count_documents(self, query, *args, **kwargs):
        return sum(1 for item in self.documents if _matches(item, query))

    async def distinct(self, key, query=None, *args, **kwargs):
        return list({item.get(key) for item in self.documents if _matches(item, query)})

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name") or "idx"


class _RawDatabase:
    def __init__(self):
        self._collections: dict[str, _Collection] = {}

    def collection(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self.collection(name)


class _TenantDatabase:
    def __init__(self, raw: _RawDatabase, tenant_collection):
        self.raw = raw
        self.tenant_collection = tenant_collection

    def __getattr__(self, name: str):
        return self.tenant_collection(self.raw.collection(name), name)


def _run(coroutine):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


def _header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def multiempresa(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "")
    for module_name in list(sys.modules):
        if module_name == "server" or module_name.startswith("utils.tenancy"):
            sys.modules.pop(module_name, None)
    server = importlib.import_module("server")
    raw = _RawDatabase()
    tenant_db = _TenantDatabase(raw, server.tenant_collection)
    monkeypatch.setattr(server, "db", tenant_db)
    monkeypatch.setattr(server, "_raw_collection", raw.collection)
    monkeypatch.setattr(server, "_start_scheduler", lambda: None, raising=False)

    now = datetime.now(timezone.utc).isoformat()
    expires = "2099-01-01T00:00:00+00:00"
    for organization_id, name in (("org_a", "Estética Aurora"), ("org_b", "Belleza Sur"),
                                  ("org_c", "Empresa sin acceso")):
        raw.organizations.documents.append({
            "organization_id": organization_id,
            "name": name,
            "status": "active",
            "plan_code": "base",
            "subscription_status": "not_configured",
            "license_status": "not_configured",
            "created_at": now,
        })

    users = [
        ("shared", "persona@ejemplo.com", "Persona compartida", "viewer", "T-SHARED", "org_a"),
        ("admin_a", "admin-a@ejemplo.com", "Administración A", "admin", "T-ADMIN-A", "org_a"),
        ("admin_b", "admin-b@ejemplo.com", "Administración B", "admin", "T-ADMIN-B", "org_b"),
    ]
    for user_id, email, name, role, token, organization_id in users:
        raw.users.documents.append({
            "user_id": user_id, "email": email, "name": name, "role": role,
            "active": True, "auth_provider": "google", "created_at": now,
            "default_organization_id": organization_id,
        })
        raw.user_sessions.documents.append({
            "session_token": token, "user_id": user_id,
            "organization_id": organization_id, "expires_at": expires, "created_at": now,
        })

    raw.memberships.documents.extend([
        {"organization_id": "org_a", "user_id": "shared", "role": "viewer", "status": "active"},
        {"organization_id": "org_b", "user_id": "shared", "role": "viewer", "status": "active"},
        {"organization_id": "org_a", "user_id": "admin_a", "role": "admin", "status": "active"},
        {"organization_id": "org_b", "user_id": "admin_b", "role": "admin", "status": "active"},
    ])
    raw.roles.documents.extend([
        {"organization_id": "org_a", "role_id": "viewer", "permissions": ["crm_use"]},
        {"organization_id": "org_b", "role_id": "viewer", "permissions": ["crm_view"]},
    ])
    raw.contacts.documents.extend([
        {"organization_id": "org_a", "id": "contact_shared", "name": "Ana Aurora",
         "phone": "+54 11 4000-0001", "created_at": now},
        {"organization_id": "org_b", "id": "contact_shared", "name": "Brenda Sur",
         "phone": "+54 11 4000-0002", "created_at": now},
        {"organization_id": "org_b", "id": "contact_only_b", "name": "Cliente B",
         "phone": "+54 11 4000-0003", "created_at": now},
    ])
    raw.settings.documents.extend([
        {"organization_id": "org_a", "key": "app", "lead_no_response_threshold_hours": 3},
        {"organization_id": "org_b", "key": "app", "lead_no_response_threshold_hours": 9},
    ])
    return server, raw, tenant_db, TestClient(server.app)


def test_session_switch_crud_permissions_and_reads_are_isolated(multiempresa):
    _, raw, _, client = multiempresa

    response = client.get("/api/contacts", headers=_header("T-SHARED"))
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Ana Aurora"]

    created = client.post(
        "/api/contacts",
        headers=_header("T-SHARED"),
        json={"name": "Nueva clienta A", "phone": "+54 11 4555-0101"},
    )
    assert created.status_code == 200
    created_id = created.json()["id"]
    assert client.get(
        "/api/contacts/contact_only_b", headers=_header("T-SHARED")
    ).status_code == 404

    updated = client.patch(
        "/api/contacts/contact_shared",
        headers=_header("T-SHARED"),
        json={"name": "Ana actualizada"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Ana actualizada"
    assert client.get("/api/settings", headers=_header("T-SHARED")).json()[
        "lead_no_response_threshold_hours"
    ] == 3

    organizations = client.get("/api/organizations", headers=_header("T-SHARED"))
    assert organizations.status_code == 200
    assert {item["organization_id"] for item in organizations.json()} == {"org_a", "org_b"}

    switched = client.post("/api/organizations/org_b/switch", headers=_header("T-SHARED"))
    assert switched.status_code == 200
    assert switched.json()["organization_id"] == "org_b"
    assert switched.json()["role"] == "viewer"

    response = client.get("/api/contacts", headers=_header("T-SHARED"))
    assert response.status_code == 200
    assert {item["name"] for item in response.json()} == {"Brenda Sur", "Cliente B"}
    assert client.get(
        f"/api/contacts/{created_id}", headers=_header("T-SHARED")
    ).status_code == 404
    assert client.get(
        "/api/contacts/contact_shared", headers=_header("T-SHARED")
    ).json()["name"] == "Brenda Sur"
    assert client.post(
        "/api/contacts",
        headers=_header("T-SHARED"),
        json={"name": "No autorizada", "phone": "+54 11 4555-0102"},
    ).status_code == 403
    assert client.get("/api/settings", headers=_header("T-SHARED")).json()[
        "lead_no_response_threshold_hours"
    ] == 9

    forbidden = client.post("/api/organizations/org_c/switch", headers=_header("T-SHARED"))
    assert forbidden.status_code == 403
    session = next(item for item in raw.user_sessions.documents if item["session_token"] == "T-SHARED")
    assert session["organization_id"] == "org_b"

    new_document = next(item for item in raw.contacts.documents if item.get("id") == created_id)
    assert new_document["organization_id"] == "org_a"
    assert all(item.get("organization_id") for item in raw.contacts.documents)


def test_tenant_settings_updates_do_not_cross_companies(multiempresa):
    _, raw, _, client = multiempresa

    updated_a = client.patch(
        "/api/admin/settings",
        headers=_header("T-ADMIN-A"),
        json={"lead_no_response_threshold_hours": 4},
    )
    assert updated_a.status_code == 200
    assert updated_a.json()["lead_no_response_threshold_hours"] == 4
    assert client.get("/api/settings", headers=_header("T-ADMIN-B")).json()[
        "lead_no_response_threshold_hours"
    ] == 9

    updated_b = client.patch(
        "/api/admin/settings",
        headers=_header("T-ADMIN-B"),
        json={"lead_no_response_threshold_hours": 12},
    )
    assert updated_b.status_code == 200
    assert client.get("/api/settings", headers=_header("T-ADMIN-A")).json()[
        "lead_no_response_threshold_hours"
    ] == 4

    values = {
        item["organization_id"]: item["lead_no_response_threshold_hours"]
        for item in raw.settings.documents if item.get("key") == "app"
    }
    assert values == {"org_a": 4, "org_b": 12}


def test_composite_configuration_ids_are_separate_per_company(multiempresa):
    server, raw, tenant_db, _ = multiempresa

    token_a = server.set_organization_id("org_a")
    try:
        _run(tenant_db.bot_settings.update_one(
            {"_id": "default"}, {"$set": {"enabled": True, "tone": "cálido"}}, upsert=True
        ))
        config_a = _run(tenant_db.bot_settings.find_one({"_id": "default"}))
    finally:
        server.reset_organization_id(token_a)

    token_b = server.set_organization_id("org_b")
    try:
        _run(tenant_db.bot_settings.update_one(
            {"_id": "default"}, {"$set": {"enabled": False, "tone": "formal"}}, upsert=True
        ))
        config_b = _run(tenant_db.bot_settings.find_one({"_id": "default"}))
    finally:
        server.reset_organization_id(token_b)

    assert config_a["enabled"] is True and config_a["tone"] == "cálido"
    assert config_b["enabled"] is False and config_b["tone"] == "formal"
    assert {item["_id"] for item in raw.bot_settings.documents} == {
        "org_a:default", "org_b:default",
    }
    assert {item["organization_id"] for item in raw.bot_settings.documents} == {"org_a", "org_b"}
