"""Local password helpers: hashing, validation, temporary-password generation,
and a tiny in-memory login throttler.
"""

from __future__ import annotations

import re
import secrets
import string
import time
from collections import defaultdict
from typing import Tuple

import bcrypt

_PWD_RE = re.compile(r"^(?=.*[A-Z])(?=.*\d).{8,128}$")


def hash_password(plain: str) -> str:
    plain_bytes = plain.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(plain_bytes, salt)
    return hashed_bytes.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        plain_bytes = plain.encode("utf-8")
        hashed_bytes = hashed.encode("utf-8")
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


def validate_password_policy(pwd: str) -> Tuple[bool, str]:
    """Min 8 chars, at least 1 uppercase + 1 digit."""
    if not pwd:
        return False, "La contraseña no puede estar vacía"
    if len(pwd) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres"
    if not _PWD_RE.match(pwd):
        return False, "La contraseña debe incluir al menos una mayúscula y un número"
    return True, ""


def generate_temp_password(length: int = 12) -> str:
    """Random ASCII password, guaranteed to contain at least 1 uppercase + 1 digit."""
    if length < 8:
        length = 8
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


# ----- login throttler (in-memory, per-email) -----------------------------

_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_WINDOW_SEC = 5 * 60
LOGIN_MAX_FAILS = 5


def _now() -> float:
    return time.monotonic()


def login_too_many(email: str) -> bool:
    cutoff = _now() - LOGIN_WINDOW_SEC
    arr = [t for t in _attempts.get(email, []) if t >= cutoff]
    _attempts[email] = arr
    return len(arr) >= LOGIN_MAX_FAILS


def login_register_failure(email: str) -> None:
    _attempts[email].append(_now())


def login_reset(email: str) -> None:
    _attempts.pop(email, None)
