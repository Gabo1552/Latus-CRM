"""WhatsApp Cloud API config — DB+env merge, never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class WAConfig:
    verify_token: str = ""
    access_token: str = ""
    phone_number_id: str = ""
    api_version: str = "v21.0"
    app_secret: str = ""
    business_account_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id and self.verify_token)

    def checklist(self) -> dict:
        return {
            "phone_number_id": bool(self.phone_number_id),
            "access_token": bool(self.access_token),
            "verify_token": bool(self.verify_token),
            "app_secret": bool(self.app_secret),
            "business_account_id": bool(self.business_account_id),
        }

    def masked_phone_id(self) -> str:
        if not self.phone_number_id:
            return ""
        tail = self.phone_number_id[-4:]
        return f"\u2022\u2022\u2022\u2022{tail}"


def env_values() -> dict[str, str]:
    """Plain env-only dict (used by storage layer to merge sources)."""
    return {
        "verify_token": os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip(),
        "access_token": os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip(),
        "phone_number_id": os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        "api_version": os.environ.get("WHATSAPP_API_VERSION", "v21.0").strip() or "v21.0",
        "app_secret": os.environ.get("WHATSAPP_APP_SECRET", "").strip(),
        "business_account_id": os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip(),
    }


def wa_config() -> WAConfig:
    """Env-only config. Synchronous, no DB. For places that can't await."""
    v = env_values()
    return WAConfig(**v)


async def wa_config_effective(db) -> WAConfig:
    """Effective config = DB (decrypted) merged on top of env."""
    from whatsapp.storage import load_db_config
    base = env_values()
    try:
        overlay = await load_db_config(db)
    except Exception:
        overlay = {}
    base.update({k: v for k, v in (overlay or {}).items() if v})
    return WAConfig(**base)
