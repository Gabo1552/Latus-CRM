"""HMAC-SHA256 signature verification for WhatsApp Cloud webhooks."""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(app_secret: str, raw_body: bytes, header_value: str | None) -> bool:
    """Return ``True`` when the X-Hub-Signature-256 header matches the body.

    Header format: ``"sha256=<hex>"`` (lowercase).
    Uses :func:`hmac.compare_digest` to avoid timing attacks.
    """
    if not app_secret:
        # No secret configured -> caller decides (dev mode logs a warning)
        return True
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = header_value.split("=", 1)[1].strip()
    try:
        return hmac.compare_digest(expected, received)
    except Exception:
        return False
