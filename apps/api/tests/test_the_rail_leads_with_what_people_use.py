"""`design_template_usage` counts the asker's own 서식 use and everyone's."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.routers.workspace import design_template_usage


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    """SQLite has no JSONB; the schema only needs to be creatable in memory."""
    return "JSON"


@pytest.fixture
async def db():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        # One table: `users` carries `'[]'::jsonb` defaults SQLite cannot parse,
        # and SQLite does not enforce the foreign key.
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ChatSession.__table__])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


#: Ids rather than rows; the fixture creates no `users` table.
MINE = SimpleNamespace(id="user-mine")
OTHER = SimpleNamespace(id="user-other")


async def _started(db: AsyncSession, who, template: str | None, times: int = 1) -> None:
    for _ in range(times):
        db.add(
            ChatSession(
                user_id=who.id,
                kind=SessionKind.report,
                title="t",
                render_template_id=template,
            )
        )
    await db.commit()


@pytest.mark.asyncio
async def test_my_own_habit_and_everybody_elses_are_counted_apart(db):
    await _started(db, MINE, "doc-minutes", 3)
    await _started(db, MINE, "doc-report", 1)
    await _started(db, OTHER, "doc-report", 9)

    usage = await design_template_usage(MINE, db)
    assert usage.mine == {"doc-minutes": 3, "doc-report": 1}
    # `popular` includes the asker.
    assert usage.popular == {"doc-minutes": 3, "doc-report": 10}


@pytest.mark.asyncio
async def test_a_session_started_from_no_template_is_not_counted(db):
    """Sessions with no 서식 are not counted."""
    await _started(db, MINE, None, 20)
    await _started(db, MINE, "doc-brief", 1)

    usage = await design_template_usage(MINE, db)
    assert usage.mine == {"doc-brief": 1}
    assert None not in usage.popular


@pytest.mark.asyncio
async def test_somebodys_first_day_reports_nothing_of_their_own(db):
    """A user with no history gets an empty `mine`, not a missing one."""
    await _started(db, OTHER, "doc-report", 4)

    usage = await design_template_usage(MINE, db)
    assert usage.mine == {}
    assert usage.popular == {"doc-report": 4}
