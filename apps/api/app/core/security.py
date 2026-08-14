"""Password hashing, access tokens, and refresh-token material.

Deliberately asymmetric:

* **Access tokens are stateless** — a 15-minute JWT verified from the signature
  alone, with no DB round-trip per request.
* **Refresh tokens are stateful** — only a SHA-256 hash is stored, so a database
  leak yields no usable tokens and rotation can detect replay
  (see `routers/auth.py`).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates a parameter bump."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ── access tokens ──────────────────────────────────────────────────────


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    """Returns the encoded token and its lifetime in seconds.

    `jti` is not for revocation — access tokens stay stateless — but without it
    two tokens minted in the same second are byte-identical and cannot be
    correlated in a log.
    """
    ttl = timedelta(minutes=settings.access_token_ttl_min)
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "role": role,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return claims if claims.get("typ") == "access" else None


# ── refresh tokens ─────────────────────────────────────────────────────


def new_refresh_token() -> tuple[str, str]:
    """Returns `(plaintext, sha256)`. Only the digest is ever persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)


def new_family_id() -> str:
    return uuid.uuid4().hex
