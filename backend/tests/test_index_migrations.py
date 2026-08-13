from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "latus_index_migration_tests")
os.environ.setdefault("CORS_ORIGINS", "*")


def run(coro):
    return asyncio.run(coro)


class AggregateCursor:
    async def to_list(self, limit):
        return [{
            "_id": "cw_duplicate",
            "conversation_ids": ["keep_webchat", "old_whatsapp", "old_webchat"],
            "count": 3,
        }]


class Conversations:
    def __init__(self):
        self.pipeline = None
        self.update = None

    def aggregate(self, pipeline, **kwargs):
        self.pipeline = pipeline
        return AggregateCursor()

    async def update_many(self, query, update):
        self.update = (query, update)
        return SimpleNamespace(modified_count=2)


class Products:
    def __init__(self):
        self.pipeline = None
        self.update = None

    def aggregate(self, pipeline, **kwargs):
        self.pipeline = pipeline

        class Cursor:
            async def to_list(self, limit):
                return [{
                    "_id": {"organization_id": "org_a", "sku": "SKU-1"},
                    "product_ids": ["keep_active", "old_duplicate"],
                    "count": 2,
                }]

        return Cursor()

    async def update_many(self, query, update):
        self.update = (query, update)
        return SimpleNamespace(modified_count=1)


def test_deduplicates_legacy_webchat_tokens_before_unique_index(monkeypatch):
    import server

    conversations = Conversations()
    monkeypatch.setattr(server, "_raw_collection", lambda name: conversations)

    cleared = run(server._dedupe_webchat_session_tokens())

    assert cleared == 2
    assert conversations.pipeline[1]["$addFields"]["_webchat_token_priority"]
    query, update = conversations.update
    assert query == {"_id": {"$in": ["old_whatsapp", "old_webchat"]}}
    assert update["$unset"] == {"webchat_session_token": ""}
    assert update["$set"]["webchat_token_deduplicated_at"]


def test_deduplicates_legacy_product_skus_without_deleting_products(monkeypatch):
    import server

    products = Products()
    monkeypatch.setattr(server, "_raw_collection", lambda name: products)

    cleared = run(server._dedupe_product_skus())

    assert cleared == 1
    assert products.pipeline[1]["$addFields"]["_sku_priority"]
    query, update = products.update
    assert query == {"_id": {"$in": ["old_duplicate"]}}
    assert update["$set"]["sku"] is None
    assert update["$set"]["legacy_duplicate_sku"] == "SKU-1"
    assert update["$set"]["sku_deduplicated_at"]
