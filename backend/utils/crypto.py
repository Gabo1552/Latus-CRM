"""Fernet-based at-rest encryption for sensitive settings.

Key is read from ``APP_ENCRYPTION_KEY`` (urlsafe-base64, 32 bytes).
If the key is missing or invalid, :func:`encrypt`/:func:`decrypt` raise
:class:`EncryptionUnavailable`. Callers should catch this and degrade
gracefully (e.g. fall back to ``.env`` values).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class EncryptionUnavailable(Exception):
    pass


_cached_fernet: Optional[Fernet] = None
_cached_key: Optional[str] = None


def _get_fernet() -> Fernet:
    global _cached_fernet, _cached_key
    key = (os.environ.get("APP_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise EncryptionUnavailable(
            "APP_ENCRYPTION_KEY no configurado — la configuración de "
            "WhatsApp por UI está deshabilitada"
        )
    if _cached_fernet is not None and _cached_key == key:
        return _cached_fernet
    try:
        f = Fernet(key.encode("utf-8"))
    except Exception as e:  # invalid base64 / wrong length
        raise EncryptionUnavailable(f"APP_ENCRYPTION_KEY inválida: {e}") from e
    _cached_fernet = f
    _cached_key = key
    return f


def is_available() -> bool:
    try:
        _get_fernet()
        return True
    except EncryptionUnavailable:
        return False


def encrypt(plain: str) -> str:
    if plain is None:
        return ""
    f = _get_fernet()
    return f.encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        logger.error("decrypt(): InvalidToken — likely APP_ENCRYPTION_KEY changed")
        raise EncryptionUnavailable("Token cifrado inválido (¿cambió APP_ENCRYPTION_KEY?)") from e


def mask_tail(value: str, n: int = 4) -> str:
    """Return ``••••XXXX`` (last ``n`` chars) for a non-empty value."""
    if not value:
        return ""
    if len(value) <= n:
        return "•" * len(value)
    return "•" * 4 + value[-n:]
