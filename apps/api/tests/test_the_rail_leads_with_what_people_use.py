"""The 서식 rail's ordering.

The catalogue is ordered by template id — the order the files happen to sit in
— and the home screen showed the first few of it. So the shapes people reach
for most were as likely to be on the second screen of the catalogue as on the
front door, and the ordering carried no information at all.

Two counts rather than one ordering. `mine` is empty on somebody's first day,
so `popular` has to carry it until they have a habit of their own; `popular`
goes on describing the average user long after this one does not resemble them.
The endpoint only has to count honestly — the rail decides how to weigh them.
"""

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
    """SQLite has no JSONB, and these tables carry several.

    Only so the schema can be created in memory — nothing here reads a JSON
    column. The alternative is hand-written DDL for two tables, which is two
    more places to update when a column is added and a test that passes
    against a schema the application no longer has.
    """
    return "JSON"


@pytest.fixture
async def db():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        # One table. `create_all` over the whole metadata drags in every
        # model, and `users` carries `'[]'::jsonb` defaults SQLite will not
        # parse — which is fine, because nothing here reads a user. SQLite does
        # not enforce the foreign key, so an owner is just an id.
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ChatSession.__table__])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


#: The person asking, and somebody else. Ids rather than rows — see the fixture.
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
    # Everyone, including me — this person is part of the installation.
    assert usage.popular == {"doc-minutes": 3, "doc-report": 10}


@pytest.mark.asyncio
async def test_a_session_started_from_no_template_is_not_counted(db):
    """Most sessions have no 서식, and counting them would drown the ones that do."""
    await _started(db, MINE, None, 20)
    await _started(db, MINE, "doc-brief", 1)

    usage = await design_template_usage(MINE, db)
    assert usage.mine == {"doc-brief": 1}
    assert None not in usage.popular


@pytest.mark.asyncio
async def test_somebodys_first_day_reports_nothing_of_their_own(db):
    """The case the fallback exists for, and it has to be empty rather than absent."""
    await _started(db, OTHER, "doc-report", 4)

    usage = await design_template_usage(MINE, db)
    assert usage.mine == {}
    assert usage.popular == {"doc-report": 4}
