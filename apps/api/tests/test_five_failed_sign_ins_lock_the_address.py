"""Five failed sign-ins in a row lock the address for a while; a success ends the run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import AuditEvent, User, UserStatus
from app.routers import auth as auth_router
from app.schemas.auth import LoginRequest


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )


_DDL = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
        monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
        litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT, allowed_models TEXT, preferences TEXT, created_at DATETIME,
        last_active_at DATETIME, email_verified_at DATETIME
    )
    """,
    """
    CREATE TABLE audit_events (
        id TEXT PRIMARY KEY, at DATETIME, actor_id TEXT, action TEXT, target TEXT, detail TEXT,
        metadata TEXT, ip TEXT, user_agent TEXT, severity TEXT
    )
    """,
    """
    CREATE TABLE refresh_tokens (
        id TEXT PRIMARY KEY, user_id TEXT, token_hash TEXT, family_id TEXT, expires_at DATETIME,
        revoked_at DATETIME, revoked_reason TEXT, created_at DATETIME, ip TEXT, user_agent TEXT,
        last_used_at DATETIME
    )
    """,
)


async def _session() -> AsyncSession:
    """An in-memory database with the three tables sign-in touches; `users` carries JSONB in
    PostgreSQL, so the schema is written by hand as the other router tests do."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    return AsyncSession(engine, expire_on_commit=False)


async def _login(db: AsyncSession, email: str, password: str):
    return await auth_router.login(
        LoginRequest(email=email, password=password), _request(), Response(), db
    )


async def _fails_with(db: AsyncSession, email: str, password: str) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await _login(db, email, password)
    return caught.value


@pytest.mark.asyncio
async def test_the_sixth_attempt_is_refused_even_with_the_right_password() -> None:
    db = await _session()
    db.add(
        User(
            email="lock@example.com",
            name="잠금",
            password_hash=hash_password("correct-horse-battery"),
            status=UserStatus.pending,
        )
    )
    await db.commit()

    for _ in range(settings.login_max_failures):
        assert (await _fails_with(db, "lock@example.com", "wrong")).status_code == 401

    locked = await _fails_with(db, "lock@example.com", "correct-horse-battery")
    assert locked.status_code == 429
    assert locked.detail == "account_locked"
    assert int(locked.headers["Retry-After"]) > 0
    # The refusal is logged as `locked`, not as another failure.
    latest = (await db.exec(select(AuditEvent).order_by(col(AuditEvent.at).desc()).limit(1))).one()
    assert latest.detail == "locked"


@pytest.mark.asyncio
async def test_the_lock_lifts_after_the_window_and_a_success_ends_the_run() -> None:
    db = await _session()
    db.add(
        User(
            email="lock@example.com",
            name="잠금",
            password_hash=hash_password("correct-horse-battery"),
            status=UserStatus.pending,
        )
    )
    await db.commit()
    for _ in range(settings.login_max_failures):
        await _fails_with(db, "lock@example.com", "wrong")
    assert (await _fails_with(db, "lock@example.com", "correct-horse-battery")).status_code == 429

    # Age the failures past the window: the address opens again.
    stale = datetime.now(UTC) - timedelta(minutes=settings.login_lockout_min + 1)
    for row in (await db.exec(select(AuditEvent))).all():
        row.at = stale
        db.add(row)
    await db.commit()
    session = await _login(db, "lock@example.com", "correct-horse-battery")
    assert session.user.email == "lock@example.com"

    # A success in the run means four fresh failures do not lock.
    for _ in range(settings.login_max_failures - 1):
        assert (await _fails_with(db, "lock@example.com", "wrong")).status_code == 401
    assert (await _login(db, "lock@example.com", "correct-horse-battery")).user is not None


@pytest.mark.asyncio
async def test_an_unknown_address_locks_the_same_way() -> None:
    """The response does not say whether the address exists."""
    db = await _session()
    for _ in range(settings.login_max_failures):
        assert (await _fails_with(db, "nobody@example.com", "wrong")).status_code == 401
    assert (await _fails_with(db, "nobody@example.com", "wrong")).status_code == 429
