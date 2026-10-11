"""Password hashing, stateless access JWTs, refresh tokens stored as SHA-256 digests only."""

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
    """Stored hash predates a parameter bump."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    """(token, ttl seconds); `jti` only distinguishes same-second tokens, never revoked."""
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


def new_refresh_token() -> tuple[str, str]:
    """(plaintext, sha256); only the digest persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)


def new_family_id() -> str:
    return uuid.uuid4().hex
