"""One agent's finding, reaching the next one.

Before this, agents in this workspace could each do their work and hand back
their own answer, and there was no way for one of them to give another
anything. A researcher's sources, a reviewer's verdict, a schema somebody's
analyst worked out — all of it ended when the turn ended, and the person had to
carry it to the next conversation by hand. Running agents one after another
with a human clipboard in between is not orchestration, and this is the piece
that was missing.

`share_note` is the write side. What these tests hold:

1. A note is scoped by where the turn is running, not by what the model asks
   for. Inside a project it reaches every conversation and agent in it; outside
   one it belongs to that conversation alone and is not broadcast.
2. The same `key` revises rather than accumulates, so a finding that changed
   leaves one current note instead of three that disagree.
3. Whatever is in scope comes back in the next turn's context, which is the
   whole point — a handoff nobody is handed is not a handoff.
4. A turn that failed leaves nothing. A conclusion nobody reached must not be
   passed on as one.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import Memory, MemoryType, Project
from app.routers.sessions import _store_notes
from app.services.tools.base import ToolContext
from app.services.tools.builtin import share_note
from app.services.workspace_context import _memory_block

#: Hand-written because the models use JSONB, which SQLite cannot compile.
#: Only the columns these tests read — a fuller schema here would be a second
#: copy of the model file to keep in step for no gain.
_DDL = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT,
        password_hash TEXT,
        name TEXT,
        role TEXT,
        status TEXT,
        monthly_credits INTEGER DEFAULT 0,
        credits_used INTEGER DEFAULT 0,
        cycle_resets_at DATETIME,
        litellm_user_id TEXT,
        litellm_key TEXT,
        litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT,
        allowed_models TEXT,
        preferences TEXT,
        created_at DATETIME,
        last_active_at DATETIME
    )
    """,
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        description TEXT DEFAULT '',
        emoji TEXT DEFAULT '',
        instructions TEXT DEFAULT '',
        skill_ids TEXT,
        design_system_id TEXT,
        render_templates TEXT,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        kind TEXT,
        title TEXT DEFAULT '',
        project_id TEXT,
        agent_id TEXT,
        model TEXT DEFAULT '',
        routing_mode TEXT,
        artifact_id TEXT,
        render_template_id TEXT,
        pinned BOOLEAN DEFAULT 0,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE memories (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        description TEXT DEFAULT '',
        type TEXT,
        body TEXT DEFAULT '',
        scope TEXT DEFAULT 'global',
        links TEXT,
        pinned BOOLEAN DEFAULT 0,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
)


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _user(db: AsyncSession) -> User:
    user = User(email="person@example.test", password_hash="x", name="Person")
    db.add(user)
    await db.commit()
    return user


async def test_the_tool_only_collects_and_says_where_it_will_reach():
    """Nothing is written during the stream; the turn's own transaction does it.

    Same deferral as `create_artifact`, and for the same reason: a row committed
    mid-turn belongs to a turn that may still fail.
    """
    ctx = ToolContext(user_id="u", session_id="s", project_id="p", agent_name="조사원")

    result = await share_note({"title": "출처 3건", "body": "A, B, C"}, ctx)

    assert not result.failed
    assert ctx.pending_notes == [
        {"key": "출처 3건", "title": "출처 3건", "body": "A, B, C", "author": "조사원"}
    ]
    # The sentence the model reads back has to say how far this went, or it
    # cannot tell the user.
    assert "프로젝트" in result.content


async def test_an_empty_note_is_refused():
    ctx = ToolContext(user_id="u", session_id="s")

    assert (await share_note({"title": "제목", "body": "   "}, ctx)).failed
    assert (await share_note({"title": "", "body": "본문"}, ctx)).failed
    assert ctx.pending_notes == []


async def test_a_project_note_reaches_every_conversation_in_that_project(db):
    user = await _user(db)
    project = Project(user_id=user.id, name="연구")
    db.add(project)
    await db.commit()

    await _store_notes(
        db,
        user.id,
        "session-written-in",
        project.id,
        [{"key": "결론", "title": "확정된 스키마", "body": "id 는 uuid", "author": "분석가"}],
    )
    await db.commit()

    # A different conversation, in the same project — the next agent's turn.
    other = ChatSession(user_id=user.id, kind=SessionKind.chat, project_id=project.id)
    block, names, total = await _memory_block(db, user, project, other)

    assert "id 는 uuid" in block
    assert names == ("결론",)
    assert total == 1


async def test_a_note_outside_a_project_stays_in_its_own_conversation(db):
    user = await _user(db)
    mine = ChatSession(user_id=user.id, kind=SessionKind.chat)
    theirs = ChatSession(user_id=user.id, kind=SessionKind.chat)
    db.add(mine)
    db.add(theirs)
    await db.commit()

    await _store_notes(
        db,
        user.id,
        mine.id,
        "",
        [{"key": "메모", "title": "메모", "body": "여기서만", "author": ""}],
    )
    await db.commit()

    assert "여기서만" in (await _memory_block(db, user, None, mine))[0]
    # The conversation next door has nothing to do with it.
    assert (await _memory_block(db, user, None, theirs))[0] == ""


async def test_the_same_key_revises_rather_than_piles_up(db):
    user = await _user(db)
    project = Project(user_id=user.id, name="연구")
    db.add(project)
    await db.commit()

    for body in ("초안", "수정본"):
        await _store_notes(
            db,
            user.id,
            "s",
            project.id,
            [{"key": "verdict", "title": "판정", "body": body, "author": "검토자"}],
        )
        await db.commit()

    rows = (await db.exec(select(Memory).where(Memory.scope == project.id))).all()
    assert len(rows) == 1
    assert rows[0].body == "수정본"
    # Found by the work rather than told to us by the person.
    assert rows[0].type is MemoryType.reference
    # And the byline stays on the description, out of the body the next agent acts on.
    assert "검토자" in rows[0].description
    assert "검토자" not in rows[0].body
