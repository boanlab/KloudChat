"""관리자 › 사용자 detail: one account with what it holds and when its credits refill."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User, UserRole, UserStatus
from app.models.workspace import Project
from app.routers import admin
from app.services.credits import next_cycle_reset


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[
                    metadata.tables[n]
                    for n in ("users", "projects", "sessions", "files", "artifacts")
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


_ADMIN = User(id="adm", email="a@x", name="a", password_hash="x")


def _user(uid: str, resets: datetime | None) -> User:
    return User(
        id=uid, email=f"{uid}@example.com", name=uid, password_hash="x", role=UserRole.user,
        status=UserStatus.active, monthly_credits=1000, credits_used=250, cycle_resets_at=resets,
    )


async def test_the_overview_counts_what_the_account_holds(db) -> None:
    later = datetime.now(UTC) + timedelta(days=3)
    db.add(_user("u1", later))
    db.add(_user("u2", None))
    db.add(Project(id="p1", user_id="u1", name="강의"))
    for i in range(3):
        db.add(ChatSession(id=f"s{i}", user_id="u1", kind=SessionKind.chat, title=f"대화 {i}"))
    db.add(ChatSession(id="other", user_id="u2", kind=SessionKind.chat, title="남의 대화"))
    await db.commit()

    out = await admin.user_overview("u1", _ADMIN, db)
    assert out.user.email == "u1@example.com"
    assert (out.sessions, out.projects, out.files, out.artifacts) == (3, 1, 0, 0)
    # its own cycle, when that is still ahead
    assert out.next_reset.replace(tzinfo=UTC) == later


async def test_a_cycle_not_yet_rolled_over_refills_at_the_next_first(db) -> None:
    db.add(_user("u1", datetime(2026, 10, 31, 15, 0, tzinfo=UTC)))
    await db.commit()
    out = await admin.user_overview("u1", _ADMIN, db)
    assert out.next_reset == next_cycle_reset()


async def test_a_missing_account_is_not_found(db) -> None:
    with pytest.raises(HTTPException) as caught:
        await admin.user_overview("nobody", _ADMIN, db)
    assert caught.value.status_code == 404
