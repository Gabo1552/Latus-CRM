"""Reset the WhatsApp DB-stored config back to .env-only.

Usage:
    cd /app/backend && python scripts/reset_whatsapp_db_config.py

Effect:
    Sets all 5 encrypted fields (verify_token, access_token, phone_number_id,
    app_secret, business_account_id) to None via the existing storage helper,
    which $unsets them. ``api_version`` is left untouched.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from whatsapp.storage import save_db_config, SENSITIVE_FIELDS  # noqa: E402


async def main() -> None:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL and DB_NAME must be set in backend/.env")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    updates = {f: None for f in SENSITIVE_FIELDS}
    await save_db_config(db, updates, updated_by="cli-reset")
    doc = await db.app_secrets.find_one({"_id": "whatsapp"}, {"_id": 0}) or {}
    print("Cleared DB-stored secrets. Remaining doc keys:", sorted(doc.keys()))
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
