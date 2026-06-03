"""WhatsApp Cloud API config — read from environment, never logged."""

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
        """Minimum env to *receive and send*: token + phone id + access token."""
        return bool(self.access_token and self.phone_number_id and self.verify_token)

    def checklist(self) -> dict:
        """Booleans for the Admin panel — never returns the actual secrets."""
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


def wa_config() -> WAConfig:
    """Build a fresh config from the current environment.

    Called per-request so changes to ``.env`` (after backend restart) are
    picked up without import-time caching.
    """
    return WAConfig(
        verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip(),
        access_token=os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip(),
        phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        api_version=os.environ.get("WHATSAPP_API_VERSION", "v21.0").strip() or "v21.0",
        app_secret=os.environ.get("WHATSAPP_APP_SECRET", "").strip(),
        business_account_id=os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip(),
    )
