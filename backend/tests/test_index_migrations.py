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
