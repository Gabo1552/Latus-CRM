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
