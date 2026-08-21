"""Regenerating a document keeps the one it replaces.

Editing an artifact has always snapshotted the old body into
`artifact_versions` first. Generating never did, although it is the more
destructive of the two: a request produced a fresh artifact row and moved the
session's pointer onto it. Nothing was deleted — the previous row stayed in the
gallery — but from inside the conversation the earlier document was simply
gone, with no 되돌리기 anywhere on the screen. A deck built over an afternoon
could be displaced by one sentence.

So a document produced into a session that already holds one of the same kind
now becomes the next version of that artifact, and every version before it
stays restorable through the history the panel already draws.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.routers.sessions import _regeneration_summary, _store_document

_DDL = (
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
        pending TEXT,
        pinned BOOLEAN DEFAULT 0,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        session_id TEXT,
        project_id TEXT,
        kind TEXT,
        title TEXT,
        data TEXT,
        storage_key TEXT,
        version INTEGER DEFAULT 1,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    CREATE TABLE artifact_versions (
        id TEXT PRIMARY KEY,
        artifact_id TEXT,
        version INTEGER,
        data TEXT,
        storage_key TEXT,
        summary TEXT DEFAULT '',
        created_at DATETIME
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


async def _session_with_deck(db: AsyncSession) -> tuple[ChatSession, Artifact]:
    session = ChatSession(user_id="u1", kind=SessionKind.slides)
    db.add(session)
    await db.commit()
    artifact_id = await _store_document(
        db,
        session,
        user_id="u1",
        project_id=None,
        kind=ArtifactKind.deck,
        title="첫 덱",
        data={"kind": "deck", "slides": [{"title": "원래 있던 장"}]},
        summary=_regeneration_summary("처음 요청"),
    )
    session.artifact_id = artifact_id
    await db.commit()
    return session, await db.get(Artifact, artifact_id)


async def test_the_first_document_creates_an_artifact(db):
    _, artifact = await _session_with_deck(db)

    assert artifact.version == 1
    assert (await db.exec(select(ArtifactVersion))).all() == []


async def test_regenerating_versions_the_same_artifact(db):
    session, first = await _session_with_deck(db)

    again = await _store_document(
        db,
        session,
        user_id="u1",
        project_id=None,
        kind=ArtifactKind.deck,
        title="다시 만든 덱",
        data={"kind": "deck", "slides": [{"title": "새로 쓴 장"}]},
        summary=_regeneration_summary("평가 결과 중심으로 다시"),
    )
    await db.commit()

    # The same artifact, not a second one — so the panel's own version history
    # is where the earlier deck went, and 되돌리기 reaches it.
    assert again == first.id
    assert len((await db.exec(select(Artifact))).all()) == 1

    kept = (await db.exec(select(ArtifactVersion))).all()
    assert len(kept) == 1
    assert kept[0].version == 1
    assert kept[0].data["slides"] == [{"title": "원래 있던 장"}]
    # Named by the request that replaced it: a history of "재생성" six times
    # over is a list nobody can choose from.
    assert "평가 결과 중심으로 다시" in kept[0].summary

    current = await db.get(Artifact, first.id)
    assert current.version == 2
    assert current.title == "다시 만든 덱"


async def test_a_different_kind_gets_its_own_artifact(db):
    """A report in a session that last made a deck is a different thing.

    A version history alternating between two documents is a history of
    neither.
    """
    session, deck = await _session_with_deck(db)

    report_id = await _store_document(
        db,
        session,
        user_id="u1",
        project_id=None,
        kind=ArtifactKind.report,
        title="보고서",
        data={"kind": "report", "sections": []},
        summary=_regeneration_summary("보고서로"),
    )
    await db.commit()

    assert report_id != deck.id
    assert len((await db.exec(select(Artifact))).all()) == 2
    assert (await db.exec(select(ArtifactVersion))).all() == []


async def test_somebody_elses_artifact_is_never_versioned_into(db):
    """`artifact_id` is a pointer, and a pointer can be stale or wrong.

    Writing into a row this account does not own would be a data leak dressed
    as a version.
    """
    session = ChatSession(user_id="u1", kind=SessionKind.slides)
    theirs = Artifact(
        user_id="someone-else",
        session_id=session.id,
        kind=ArtifactKind.deck,
        title="남의 덱",
        data={"kind": "deck", "slides": []},
    )
    db.add(session)
    db.add(theirs)
    await db.commit()
    session.artifact_id = theirs.id
    await db.commit()

    mine = await _store_document(
        db,
        session,
        user_id="u1",
        project_id=None,
        kind=ArtifactKind.deck,
        title="내 덱",
        data={"kind": "deck", "slides": []},
        summary="",
    )
    await db.commit()

    assert mine != theirs.id
    assert (await db.exec(select(ArtifactVersion))).all() == []


def test_the_version_summary_names_the_request_that_replaced_it():
    assert "평가 결과" in _regeneration_summary("  평가 결과\n중심으로  ")
    assert _regeneration_summary("   ") == "재생성 전"
