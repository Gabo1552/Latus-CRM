"""Tenant context and transparent MongoDB collection isolation.

Every request selects one organization.  Tenant-owned collections are then
automatically filtered by ``organization_id`` so a missing filter in a route
cannot expose another company's records.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Iterable


_organization_id: ContextVar[str | None] = ContextVar("organization_id", default=None)


TENANT_SCOPED_COLLECTIONS = frozenset({
    "ai_usage_logs",
    "app_secrets",
    "appointments",
    "billing_requests",
    "bot_events",
    "bot_settings",
    "contacts",
    "conversations",
    "leads",
    "messages",
    "notes",
    "notifications",
    "products",
    "roles",
    "settings",
    "tags",
    "tasks",
    "wa_status",
    "whatsapp_events",
    "work_areas",
})

# These collections historically used a shared, meaningful ``_id`` such as
# ``default`` or ``whatsapp``. Mongo requires _id to be globally unique, so it
# is namespaced internally while callers can keep using the legacy identifier.
COMPOSITE_ID_COLLECTIONS = frozenset({"app_secrets", "bot_settings"})

# Index administration does not read or mutate tenant-owned documents and is
# intentionally available during bootstrap. Every data operation must be
# implemented explicitly below so a future route cannot silently bypass the
# organization filter through ``__getattr__``.
SAFE_COLLECTION_METADATA_METHODS = frozenset({
    "create_index",
    "create_indexes",
    "index_information",
    "list_indexes",
    "options",
})


def get_organization_id() -> str | None:
    return _organization_id.get()


def set_organization_id(organization_id: str | None) -> Token:
    return _organization_id.set(organization_id)


def reset_organization_id(token: Token) -> None:
    _organization_id.reset(token)


def _scoped_filter(filter_: dict[str, Any] | None, organization_id: str) -> dict[str, Any]:
    query = dict(filter_ or {})
    query["organization_id"] = organization_id
    return query


class TenantCollection:
    """Small Motor collection facade that injects the active tenant."""

    def __init__(self, collection: Any, name: str):
        self._collection = collection
        self._name = name

    def _org(self) -> str:
        organization_id = get_organization_id()
        if not organization_id:
            raise RuntimeError(
                f"No hay una empresa activa para acceder a la colección '{self._name}'"
            )
        return organization_id

    def _filter(self, filter_: dict[str, Any] | None) -> dict[str, Any]:
        organization_id = self._org()
        query = _scoped_filter(filter_, organization_id)
        if self._name in COMPOSITE_ID_COLLECTIONS and isinstance(query.get("_id"), str):
            public_id = query["_id"]
            prefix = f"{organization_id}:"
            if not public_id.startswith(prefix):
                query["_id"] = f"{prefix}{public_id}"
        return query

    def _document(self, document: dict[str, Any]) -> dict[str, Any]:
        organization_id = self._org()
        result = dict(document)
        result["organization_id"] = organization_id
        if self._name in COMPOSITE_ID_COLLECTIONS and isinstance(result.get("_id"), str):
            public_id = result["_id"]
            prefix = f"{organization_id}:"
            if not public_id.startswith(prefix):
                result["_id"] = f"{prefix}{public_id}"
        return result

    def find(self, filter_: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        return self._collection.find(self._filter(filter_), *args, **kwargs)

    def aggregate(self, pipeline: list[dict[str, Any]], *args: Any, **kwargs: Any):
        """Run a tenant-scoped aggregation by forcing organization matching first."""
        scoped_pipeline = [{"$match": {"organization_id": self._org()}}, *list(pipeline or [])]
        return self._collection.aggregate(scoped_pipeline, *args, **kwargs)

    async def find_one(self, filter_: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        return await self._collection.find_one(self._filter(filter_), *args, **kwargs)

    async def insert_one(self, document: dict[str, Any], *args: Any, **kwargs: Any):
        scoped = self._document(document)
        document.update(scoped)
        return await self._collection.insert_one(document, *args, **kwargs)

    async def insert_many(self, documents: Iterable[dict[str, Any]], *args: Any, **kwargs: Any):
        scoped = []
        for document in documents:
            item = self._document(document)
            document.update(item)
            scoped.append(document)
        return await self._collection.insert_many(scoped, *args, **kwargs)

    def _update(self, update: dict[str, Any], *, upsert: bool = False) -> dict[str, Any]:
        organization_id = self._org()
        result = {key: dict(value) if isinstance(value, dict) else value for key, value in update.items()}
        if any(str(key).startswith("$") for key in result):
            if isinstance(result.get("$set"), dict):
                result["$set"].pop("_id", None)
            target = "$setOnInsert" if upsert else "$set"
            result.setdefault(target, {})["organization_id"] = organization_id
        else:
            result = self._document(result)
        return result

    async def update_one(self, filter_: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.update_one(
            self._filter(filter_), self._update(update, upsert=bool(kwargs.get("upsert"))), *args, **kwargs
        )

    async def update_many(self, filter_: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.update_many(
            self._filter(filter_), self._update(update, upsert=bool(kwargs.get("upsert"))), *args, **kwargs
        )

    async def replace_one(self, filter_: dict[str, Any], replacement: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.replace_one(
            self._filter(filter_), self._document(replacement), *args, **kwargs
        )

    async def find_one_and_update(
        self, filter_: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any
    ):
        return await self._collection.find_one_and_update(
            self._filter(filter_),
            self._update(update, upsert=bool(kwargs.get("upsert"))),
            *args,
            **kwargs,
        )

    async def find_one_and_replace(
        self, filter_: dict[str, Any], replacement: dict[str, Any], *args: Any, **kwargs: Any
    ):
        return await self._collection.find_one_and_replace(
            self._filter(filter_), self._document(replacement), *args, **kwargs
        )

    async def find_one_and_delete(
        self, filter_: dict[str, Any], *args: Any, **kwargs: Any
    ):
        return await self._collection.find_one_and_delete(
            self._filter(filter_), *args, **kwargs
        )

    async def delete_one(self, filter_: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.delete_one(self._filter(filter_), *args, **kwargs)

    async def delete_many(self, filter_: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.delete_many(self._filter(filter_), *args, **kwargs)

    async def count_documents(self, filter_: dict[str, Any], *args: Any, **kwargs: Any):
        return await self._collection.count_documents(self._filter(filter_), *args, **kwargs)

    async def distinct(self, key: str, filter_: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        return await self._collection.distinct(key, self._filter(filter_), *args, **kwargs)

    def aggregate(self, pipeline: list[dict[str, Any]], *args: Any, **kwargs: Any):
        scoped_pipeline = [{"$match": {"organization_id": self._org()}}, *pipeline]
        return self._collection.aggregate(scoped_pipeline, *args, **kwargs)

    def __getattr__(self, name: str):
        if name in SAFE_COLLECTION_METADATA_METHODS:
            return getattr(self._collection, name)
        raise AttributeError(
            f"La operación '{name}' no está habilitada sin aislamiento para '{self._name}'"
        )


def tenant_collection(collection: Any, name: str) -> Any:
    if name in TENANT_SCOPED_COLLECTIONS:
        return TenantCollection(collection, name)
    return collection
