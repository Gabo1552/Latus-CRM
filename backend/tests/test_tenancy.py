import asyncio

import pytest

from utils.tenancy import (
    TenantCollection,
    reset_organization_id,
    set_organization_id,
)


class RecordingCollection:
    def __init__(self):
        self.calls = []

    def find(self, query, *args, **kwargs):
        self.calls.append(("find", query, args, kwargs))
        return self

    async def insert_one(self, document, *args, **kwargs):
        self.calls.append(("insert_one", dict(document), args, kwargs))
        return object()

    async def update_one(self, query, update, *args, **kwargs):
        self.calls.append(("update_one", query, update, args, kwargs))
        return object()

    async def find_one_and_update(self, query, update, *args, **kwargs):
        self.calls.append(("find_one_and_update", query, update, args, kwargs))
        return object()

    async def find_one_and_replace(self, query, replacement, *args, **kwargs):
        self.calls.append(("find_one_and_replace", query, replacement, args, kwargs))
        return object()

    async def find_one_and_delete(self, query, *args, **kwargs):
        self.calls.append(("find_one_and_delete", query, args, kwargs))
        return object()

    async def create_index(self, *args, **kwargs):
        self.calls.append(("create_index", args, kwargs))
        return "idx"


def test_queries_are_always_scoped_to_active_organization():
    raw = RecordingCollection()
    collection = TenantCollection(raw, "contacts")
    token = set_organization_id("org_a")
    try:
        collection.find({"status": "active", "organization_id": "org_intruder"})
    finally:
        reset_organization_id(token)
    assert raw.calls[0][1] == {"status": "active", "organization_id": "org_a"}


def test_inserts_receive_organization_id():
    raw = RecordingCollection()
    collection = TenantCollection(raw, "appointments")
    document = {"id": "appt_1"}
    token = set_organization_id("org_a")
    try:
        asyncio.run(collection.insert_one(document))
    finally:
        reset_organization_id(token)
    assert document["organization_id"] == "org_a"


def test_logical_ids_are_namespaced_for_each_organization():
    raw = RecordingCollection()
    collection = TenantCollection(raw, "bot_settings")
    token = set_organization_id("org_a")
    try:
        asyncio.run(collection.update_one(
            {"_id": "default"}, {"$set": {"_id": "default", "enabled": True}}, upsert=True
        ))
    finally:
        reset_organization_id(token)
    _, query, update, _, _ = raw.calls[0]
    assert query == {"_id": "org_a:default", "organization_id": "org_a"}
    assert "_id" not in update["$set"]
    assert update["$setOnInsert"]["organization_id"] == "org_a"


def test_unscoped_tenant_access_fails_closed():
    collection = TenantCollection(RecordingCollection(), "messages")
    with pytest.raises(RuntimeError, match="empresa activa"):
        collection.find({})


def test_find_and_modify_operations_are_scoped():
    raw = RecordingCollection()
    collection = TenantCollection(raw, "contacts")
    token = set_organization_id("org_a")
    try:
        asyncio.run(collection.find_one_and_update(
            {"id": "shared", "organization_id": "org_b"},
            {"$set": {"name": "Empresa A"}},
        ))
        asyncio.run(collection.find_one_and_replace(
            {"id": "shared"}, {"id": "shared", "name": "Reemplazo A"}
        ))
        asyncio.run(collection.find_one_and_delete({"id": "shared"}))
    finally:
        reset_organization_id(token)

    assert raw.calls[0][1]["organization_id"] == "org_a"
    assert raw.calls[1][1]["organization_id"] == "org_a"
    assert raw.calls[1][2]["organization_id"] == "org_a"
    assert raw.calls[2][1]["organization_id"] == "org_a"


def test_unknown_raw_data_operations_are_blocked_but_index_bootstrap_is_allowed():
    collection = TenantCollection(RecordingCollection(), "contacts")
    with pytest.raises(AttributeError, match="no está habilitada"):
        collection.bulk_write
    assert asyncio.run(collection.create_index("id")) == "idx"
