"""A person deleting their own content leaves one audit event per request.

On 2026-10-12 one bulk delete removed 1,416 conversations and wrote nothing an
administrator could find. Every delete of a person's own conversations, projects,
files, artifacts and memories now records what went (counts and ids), never what
it said.
"""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, Role
from app.models.user import AuditEvent, User
from app.models.workspace import (
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Job,
    Memory,
    Project,
    StoredFile,
)
from app.routers import sessions as sessions_router
from app.routers import workspace as ws
from app.schemas.chat import SessionBulkDelete
from app.schemas.workspace import BulkDelete
from app.services import audit as audit_service
from app.services import index_client


@pytest.fixture
async def db():
    # Adapt only a copied schema: PostgreSQL JSON defaults are not SQLite SQL.
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    models = (
        User, ChatSession, Message, Job, Artifact, ArtifactVersion, StoredFile, Project,
        Memory, AuditEvent,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[metadata.tables[model.__tablename__] for model in models],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _no_index(monkeypatch):
    async def forget(**_kw):
        return True

    monkeypatch.setattr(index_client, "forget_collection", forget)
    monkeypatch.setattr(index_client, "forget_document", forget)


class _Request:
    headers = {"User-Agent": "tests", "X-Forwarded-For": "10.0.0.7"}
    client = None


async def _person(db, email="kim@example.com") -> User:
    user = User(email=email, name="김", password_hash="x")
    db.add(user)
    await db.commit()
    return user


async def _conversation(db, user: User, *, messages: int, artifacts: int, files: int) -> str:
    session = ChatSession(user_id=user.id, title="비밀 제목")
    db.add(session)
    await db.flush()
    for i in range(messages):
        db.add(Message(session_id=session.id, role=Role.user, content=f"비밀 내용 {i}"))
    for _ in range(artifacts):
        db.add(Artifact(user_id=user.id, session_id=session.id, kind=ArtifactKind.report,
                        title="비밀 보고서"))
    for i in range(files):
        db.add(StoredFile(user_id=user.id, session_id=session.id, name=f"secret-{i}.pdf"))
    await db.commit()
    return session.id


async def _events(db) -> list[AuditEvent]:
    return list((await db.exec(select(AuditEvent))).all())


def _no_content(event: AuditEvent) -> None:
    said = f"{event.detail} {event.event_metadata}"
    assert "비밀" not in said and "secret" not in said


@pytest.mark.asyncio
async def test_a_bulk_delete_writes_one_event_with_its_counts(db):
    me = await _person(db)
    other = await _person(db, "lee@example.com")
    ids = [
        await _conversation(db, me, messages=3, artifacts=1, files=1),
        await _conversation(db, me, messages=2, artifacts=2, files=0),
        await _conversation(db, me, messages=1, artifacts=0, files=0),
    ]
    theirs = await _conversation(db, other, messages=4, artifacts=1, files=0)

    out = await sessions_router.delete_sessions(
        SessionBulkDelete(ids=[*ids, theirs]), me, db, _Request()
    )
    assert out == {"deleted": 3, "artifactsDeleted": 3}

    (event,) = await _events(db)
    assert (event.action, event.actor_id, event.target) == (
        "conversation.bulk_delete", me.id, "kim@example.com"
    )
    meta = event.event_metadata
    assert (meta["sessions"], meta["messages"], meta["artifacts"], meta["files"]) == (3, 6, 3, 1)
    assert sorted(meta["ids"]) == sorted(ids) and "all" not in meta
    assert event.detail.startswith("대화 3개 · 메시지 6개 · 아티팩트 3개 · 파일 1개")
    assert event.ip == "10.0.0.7" and event.severity == "info"
    _no_content(event)
    # The other person's conversation is untouched.
    assert await db.get(ChatSession, theirs) is not None


@pytest.mark.asyncio
async def test_deleting_everything_says_so_and_is_flagged(db):
    me = await _person(db)
    for _ in range(3):
        await _conversation(db, me, messages=1, artifacts=0, files=0)

    await sessions_router.delete_sessions(SessionBulkDelete(all=True), me, db, _Request())

    (event,) = await _events(db)
    assert event.event_metadata["all"] is True and event.event_metadata["sessions"] == 3
    assert event.detail.startswith("전체 삭제 · 대화 3개")
    assert event.severity == "warn"


@pytest.mark.asyncio
async def test_a_bulk_delete_that_finds_nothing_records_nothing(db):
    me = await _person(db)
    assert await sessions_router.delete_sessions(SessionBulkDelete(ids=["nope"]), me, db) == {
        "deleted": 0
    }
    assert await _events(db) == []


@pytest.mark.asyncio
async def test_a_single_delete_writes_one_event(db):
    me = await _person(db)
    sid = await _conversation(db, me, messages=2, artifacts=1, files=0)

    await sessions_router.delete_session(sid, me, db, _Request())

    (event,) = await _events(db)
    assert event.action == "conversation.delete" and event.target == "kim@example.com"
    meta = event.event_metadata
    assert (meta["sessions"], meta["messages"], meta["artifacts"], meta["ids"]) == (1, 2, 1, [sid])
    _no_content(event)


@pytest.mark.asyncio
async def test_projects_artifacts_and_memories_are_audited_once_per_request(db):
    me = await _person(db)
    projects = [Project(user_id=me.id, name=f"p{i}") for i in range(2)]
    db.add_all(projects)
    await db.flush()
    db.add(StoredFile(user_id=me.id, project_id=projects[0].id, name="doc.pdf"))
    made = [Artifact(user_id=me.id, kind=ArtifactKind.report, title="t") for _ in range(4)]
    memory = Memory(user_id=me.id, name="비밀 기억", body="비밀")
    db.add_all([*made, memory])
    await db.commit()

    await ws.delete_projects(BulkDelete(ids=[p.id for p in projects]), me, db, _Request())
    await ws.delete_artifacts(BulkDelete(ids=[a.id for a in made[:3]]), me, db, _Request())
    await ws.delete_artifact(made[3].id, me, db, _Request())
    await ws.delete_memory(memory.id, me, db, _Request())

    events = {e.action: e for e in await _events(db)}
    assert set(events) == {
        "project.bulk_delete", "artifact.bulk_delete", "artifact.delete", "memory.delete"
    }
    assert events["project.bulk_delete"].event_metadata["projects"] == 2
    assert events["project.bulk_delete"].event_metadata["files"] == 1
    assert events["artifact.bulk_delete"].event_metadata["artifacts"] == 3
    assert events["memory.delete"].detail == "메모리 1개"
    for event in events.values():
        assert event.target == "kim@example.com"
        _no_content(event)


def test_a_long_id_list_is_capped_and_counted():
    me = User(id="u1", email="kim@example.com", name="김", password_hash="x")
    ids = [f"s{i}" for i in range(1416)]
    event = audit_service.deletion(
        me, None, action="conversation.bulk_delete", ids=ids,
        counts={"sessions": 1416, "messages": 8400, "artifacts": 980},
    )
    assert len(event.event_metadata["ids"]) == 50
    assert event.event_metadata["idsOmitted"] == 1366
    assert event.detail == "대화 1,416개 · 메시지 8,400개 · 아티팩트 980개"
    assert event.severity == "warn"
