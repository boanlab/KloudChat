"""「빈 문서로 시작」 then leaving without doing anything drops the document and its empty
conversation; one edit, message, share, rename or file keeps it (`services/blank_docs.py`)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL as _ARTIFACT_DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, Message, Role
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactVersion, Job, Share, StoredFile
from app.routers import docx as docx_router
from app.routers import hwp as hwp_router
from app.routers import pptx as pptx_router
from app.routers import sheet as sheet_router
from app.routers import workspace
from app.services import blank_docs, files

ME = User(id="blank-me", email="me@example.test", password_hash="unused", name="Me")
OTHER = User(id="blank-other", email="other@example.test", password_hash="unused", name="Other")

_DDL = (
    *_ARTIFACT_DDL,
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, title TEXT DEFAULT '', project_id TEXT,
        agent_id TEXT, model TEXT DEFAULT '', routing_mode TEXT, reasoning_mode TEXT,
        form_files TEXT, artifact_id TEXT,
        render_template_id TEXT, pipeline_id TEXT, index_key TEXT, pending TEXT, summary TEXT,
        skill_ids TEXT,
        running_turn TEXT, running_since DATETIME, pinned BOOLEAN DEFAULT 0,
        publication_id TEXT, visitor_id TEXT, publication_version_id TEXT,
        shared_at DATETIME, viewable BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME, updated_at DATETIME,
        title_set_by_user BOOLEAN NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT DEFAULT '', steps TEXT,
        attachments TEXT, usage TEXT, variants TEXT, superseded TEXT, routing TEXT,
        artifact_ids TEXT, started_from TEXT, model TEXT, rating TEXT, failure TEXT,
        created_at DATETIME
    )
    """,
)

SLUGS = ["pptx", "docx", "hwp", "xlsx"]


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
        for model in (StoredFile, Job, Share):
            await conn.run_sync(lambda sync, m=model: m.__table__.create(sync))

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    who = {"user": ME}
    app = FastAPI()
    for router in (hwp_router, docx_router, pptx_router, sheet_router, workspace):
        app.include_router(router.router)

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: who["user"]
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client, local, who
    finally:
        await engine.dispose()


async def _blank(client, local, slug: str, *, title: str = "새 문서") -> tuple[str, str]:
    """A conversation and a blank document in it, as 「빈 문서로 시작」 makes them."""
    async with local() as db:
        chat = ChatSession(user_id=ME.id, title="")
        db.add(chat)
        await db.commit()
        sid = chat.id
    made = await client.post(f"/artifacts/{slug}", json={"sessionId": sid, "title": title})
    assert made.status_code == 201, made.text
    return sid, made.json()["id"]


async def _discard(client, artifact_id: str) -> bool:
    answer = await client.post(f"/artifacts/{artifact_id}/discard-if-blank")
    assert answer.status_code == 200
    return answer.json()["discarded"]


async def _exists(local, artifact_id: str, session_id: str) -> tuple[bool, bool]:
    async with local() as db:
        return (
            await db.get(Artifact, artifact_id) is not None,
            await db.get(ChatSession, session_id) is not None,
        )


@pytest.mark.parametrize("slug", SLUGS)
async def test_leaving_an_untouched_blank_drops_the_document_and_its_conversation(stack, slug):
    client, local, _ = stack
    sid, aid = await _blank(client, local, slug)
    async with local() as db:
        key = (await db.get(Artifact, aid)).storage_key
    assert key and files.read_blob(key)

    assert await _discard(client, aid) is True

    assert await _exists(local, aid, sid) == (False, False)
    with pytest.raises(OSError):
        files.read_blob(key)
    # again: nothing left, nothing wrong
    assert await _discard(client, aid) is False


@pytest.mark.parametrize("slug", SLUGS)
async def test_a_blank_made_from_an_upload_is_not_a_blank(stack, slug):
    """Only a document the route made empty is marked; a document opened from a file is not."""
    client, local, _ = stack
    sid, aid = await _blank(client, local, slug)
    async with local() as db:
        row = await db.get(Artifact, aid)
        row.data = {k: v for k, v in (row.data or {}).items() if k != blank_docs.BLANK_KEY}
        db.add(row)
        await db.commit()
    assert await _discard(client, aid) is False
    assert await _exists(local, aid, sid) == (True, True)


async def test_a_save_keeps_the_document(stack):
    client, local, _ = stack
    sid, aid = await _blank(client, local, "docx")
    loaded = await client.get(f"/artifacts/{aid}/docx")
    saved = await client.put(
        f"/artifacts/{aid}/docx", params={"expectedVersion": 1}, content=loaded.content
    )
    assert saved.status_code == 200
    assert await _discard(client, aid) is False
    assert await _exists(local, aid, sid) == (True, True)


async def test_a_rename_keeps_the_document(stack):
    client, local, _ = stack
    sid, aid = await _blank(client, local, "pptx")
    async with local() as db:
        row = await db.get(Artifact, aid)
        row.title = "분기 보고"
        row.updated_at = utcnow() + timedelta(minutes=1)
        db.add(row)
        await db.commit()
    assert await _discard(client, aid) is False
    assert await _exists(local, aid, sid) == (True, True)


async def _touch(local, sid: str, aid: str, what: str) -> None:
    async with local() as db:
        if what == "message":
            db.add(Message(session_id=sid, role=Role.user, content="안녕"))
        elif what == "share":
            db.add(Share(token="tok-" + what, owner_id=ME.id, artifact_id=aid))
        elif what == "file":
            db.add(StoredFile(
                user_id=ME.id, session_id=sid, name="a.txt", mime="text/plain", size=1,
                storage_key="k", origin="upload",
            ))
        elif what == "second_artifact":
            db.add(Artifact(user_id=ME.id, session_id=sid, kind="html", title="x", data={}))
        elif what == "job":
            db.add(Job(user_id=ME.id, session_id=sid, kind="video", status="running", data={}))
        elif what == "pinned":
            row = await db.get(ChatSession, sid)
            row.pinned = True
            db.add(row)
        elif what == "named":
            row = await db.get(ChatSession, sid)
            row.title_set_by_user = True
            db.add(row)
        await db.commit()


@pytest.mark.parametrize(
    "what", ["message", "share", "file", "second_artifact", "pinned", "named"]
)
async def test_anything_done_in_the_conversation_keeps_it(stack, what):
    client, local, _ = stack
    sid, aid = await _blank(client, local, "pptx")
    await _touch(local, sid, aid, what)
    assert await _discard(client, aid) is False
    assert (await _exists(local, aid, sid))[0] is True


async def test_another_persons_blank_cannot_be_dropped_and_tells_nothing(stack):
    client, local, who = stack
    sid, aid = await _blank(client, local, "pptx")
    who["user"] = OTHER
    assert await _discard(client, aid) is False
    assert await _discard(client, "no-such-id") is False
    assert await _exists(local, aid, sid) == (True, True)


async def test_the_sweep_drops_only_old_untouched_blanks_outside_projects(stack):
    client, local, _ = stack
    old, old_aid = await _blank(client, local, "pptx")
    young, young_aid = await _blank(client, local, "docx")
    said, said_aid = await _blank(client, local, "hwp")
    await _touch(local, said, said_aid, "message")
    async with local() as db:
        for aid in (old_aid, said_aid):
            row = await db.get(Artifact, aid)
            row.created_at = row.updated_at = utcnow() - timedelta(hours=7)
            db.add(row)
        await db.commit()

    async with local() as db:
        assert await blank_docs.sweep(db, older_than_hours=6) == 1

    assert (await _exists(local, old_aid, old)) == (False, False)
    assert (await _exists(local, young_aid, young)) == (True, True)
    assert (await _exists(local, said_aid, said)) == (True, True)
    async with local() as db:
        assert (await db.exec(select(ArtifactVersion))).all() == []
