"""Each answer's link to the document and version its turn wrote."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.user import User
from app.routers import sessions
from app.services import message_links

_T0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    tables = [metadata.tables[m.__tablename__] for m in (User, ChatSession, Message, PipelineRun)]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _transcript(db) -> tuple[User, ChatSession, list[Message]]:
    owner = User(id="u1", email="owner@example.test", name="owner", password_hash="x")
    session = ChatSession(id="s1", user_id=owner.id, kind=SessionKind.report, title="보고서")
    rows = [
        Message(id="m1", session_id="s1", role=Role.user, content="써 줘", created_at=_T0),
        Message(id="m2", session_id="s1", role=Role.assistant, created_at=_T0 + dt.timedelta(1),
                content="시작하기 전에 확인할 것이 있습니다."),
        Message(id="m3", session_id="s1", role=Role.assistant, created_at=_T0 + dt.timedelta(2),
                content="4개 섹션으로 보고서를 작성했습니다."),
        Message(id="m4", session_id="s1", role=Role.assistant, created_at=_T0 + dt.timedelta(3),
                content="", artifact_ids=["picture"]),
    ]
    db.add(owner)
    db.add(session)
    for row in rows:
        db.add(row)
    # The clarify card's run waited and wrote nothing; the write run settled m3 at v2.
    db.add(PipelineRun(id="r-pre", session_id="s1", user_id="u1", message_id="m2",
                       pipeline="report", flow="pre", status="waiting", created_at=_T0))
    db.add(PipelineRun(id="r-1", session_id="s1", user_id="u1", message_id="m3",
                       artifact_id="doc", artifact_version=1, pipeline="report",
                       status="done", created_at=_T0 + dt.timedelta(2)))
    db.add(PipelineRun(id="r-2", session_id="s1", user_id="u1", message_id="m3",
                       artifact_id="doc", artifact_version=2, pipeline="report",
                       status="done", created_at=_T0 + dt.timedelta(2, 5)))
    await db.commit()
    return owner, session, rows


@pytest.mark.asyncio
async def test_a_document_answer_names_the_artifact_and_version_its_run_wrote(db):
    owner, _session, _rows = await _transcript(db)

    out = await sessions.get_session("s1", owner, db)
    by_id = {m.id: m for m in out.messages}

    assert by_id["m3"].artifact_ids == ["doc"]
    # The rerun that settled the same answer wrote the later version.
    assert by_id["m3"].artifact_version == 2


@pytest.mark.asyncio
async def test_a_question_card_and_a_media_answer_keep_what_they_had(db):
    owner, _session, _rows = await _transcript(db)

    listed = await sessions.list_messages("s1", owner, db)
    by_id = {m.id: m for m in listed}

    assert by_id["m1"].artifact_ids is None
    assert by_id["m2"].artifact_ids is None and by_id["m2"].artifact_version is None
    assert by_id["m4"].artifact_ids == ["picture"] and by_id["m4"].artifact_version is None


def test_a_link_never_replaces_ids_the_message_stored():
    stored = Message(id="m", session_id="s", role=Role.assistant, content="", artifact_ids=["a"])

    out = message_links.linked(stored, {"m": ("b", 4)})

    assert out.artifact_ids == ["a"]
    assert out.artifact_version is None
