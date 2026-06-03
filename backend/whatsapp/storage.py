"""DB-backed encrypted WhatsApp credentials.

The document lives in Mongo at ``app_secrets._id == "whatsapp"``. Sensitive
fields are stored Fernet-encrypted; non-sensitive fields (api_version) live
in clear.

Precedence at read time (handled by :func:`whatsapp.config.wa_config`):
    1. DB-stored value (if non-empty after decrypt)
    2. ``.env`` value
    3. empty -> not configured
"""

from __future__ import annotations

import logging
from typing import Any

from utils.crypto import decrypt, encrypt, EncryptionUnavailable

logger = logging.getLogger(__name__)


# Sensitive fields (encrypted at rest); plus api_version which is plain.
SENSITIVE_FIELDS = (
    "verify_token",
    "access_token",
    "phone_number_id",
    "app_secret",
    "business_account_id",
)
ALL_FIELDS = SENSITIVE_FIELDS + ("api_version",)


def _enc_field_name(field: str) -> str:
    return f"{field}_enc"


async def load_db_config(db) -> dict[str, str]:
    """Return a dict ``{field: value}`` of decrypted DB-stored fields.

    Missing/empty fields are omitted. If encryption is unavailable, returns
    only the plain ``api_version`` field (if present) — never raises.
    """
    doc = await db.app_secrets.find_one({"_id": "whatsapp"}, {"_id": 0})
    if not doc:
        return {}
    out: dict[str, str] = {}
    # plain fields
    api_v = (doc.get("api_version") or "").strip()
    if api_v:
        out["api_version"] = api_v
    # encrypted fields
    for f in SENSITIVE_FIELDS:
        enc = doc.get(_enc_field_name(f))
        if not enc:
            continue
        try:
            val = decrypt(enc)
        except EncryptionUnavailable as e:
            logger.error("load_db_config: cannot decrypt %s: %s", f, e)
            continue
        if val:
            out[f] = val
    return out


async def save_db_config(db, updates: dict[str, Any], *, updated_by: str) -> None:
    """Persist a partial update.

    Semantics for each key in ``updates``:
      * present + non-empty string -> encrypt & set
      * present + ``None`` -> ``$unset`` (clear back to env fallback)
      * present + ``""`` (empty string) -> ignored (no-op) to avoid accidental wipe;
        callers wanting to clear must pass ``None`` explicitly.
      * ``api_version`` is stored in clear.
    """
    set_fields: dict[str, Any] = {"_id": "whatsapp", "updated_by": updated_by}
    unset_fields: dict[str, str] = {}
    has_any_set = False

    for key, val in updates.items():
        if key == "api_version":
            if val is None:
                unset_fields["api_version"] = ""
            elif isinstance(val, str) and val.strip():
                set_fields["api_version"] = val.strip()
                has_any_set = True
            continue
        if key not in SENSITIVE_FIELDS:
            continue
        if val is None:
            unset_fields[_enc_field_name(key)] = ""
        elif isinstance(val, str) and val.strip():
            set_fields[_enc_field_name(key)] = encrypt(val.strip())
            has_any_set = True
        # empty string -> ignore (do not wipe accidentally)

    from datetime import datetime, timezone
    set_fields["updated_at"] = datetime.now(timezone.utc).isoformat()

    update: dict[str, Any] = {"$set": set_fields}
    if unset_fields:
        update["$unset"] = unset_fields
    await db.app_secrets.update_one({"_id": "whatsapp"}, update, upsert=True)
    # Never log values
    logger.info(
        "WhatsApp config updated by=%s set=%s unset=%s",
        updated_by,
        sorted(k for k in set_fields if k not in ("_id", "updated_by", "updated_at")),
        sorted(unset_fields.keys()),
    )


async def per_field_sources(db, env_values: dict[str, str]) -> dict[str, dict[str, Any]]:
    """For the admin UI: per-field metadata without exposing the value.

    Returns ``{field: {"configured": bool, "source": "db"|"env"|"none",
                       "masked": "\u2022\u2022\u2022\u2022XXXX"}}``.
    """
    from utils.crypto import mask_tail
    db_cfg = await load_db_config(db)
    out: dict[str, dict[str, Any]] = {}
    for f in ALL_FIELDS:
        db_v = db_cfg.get(f, "")
        env_v = env_values.get(f, "")
        if db_v:
            out[f] = {"configured": True, "source": "db", "masked": mask_tail(db_v)}
        elif env_v:
            out[f] = {"configured": True, "source": "env", "masked": mask_tail(env_v)}
        else:
            out[f] = {"configured": False, "source": "none", "masked": ""}
    return out
