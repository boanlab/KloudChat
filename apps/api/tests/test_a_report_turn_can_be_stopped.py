"""중단 reaches a report or deck turn: nothing half-written is stored, only what ran is billed."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.user import CreditLedger, User
from app.models.workspace import Artifact
from app.pipeline.bundle import Bundle, Request
from app.routers import sessions

MODEL = {"id": "vendor/model", "label": "m", "kinds": ["report", "slides"],
         "dataBoundary": "external", "inputCreditCost": 10, "creditCost": 10}


class _Store:
    """A real (in-memory) database with one person, one document session and its question."""

    def __init__(self, engine, kind: SessionKind):
        self.engine = engine
        self.user = User(email="p@example.test", password_hash="x", name="P")
        self.session = ChatSession(user_id=self.user.id, kind=kind)
        self.question = Message(session_id=self.session.id, role=Role.user, content="써 줘")

    def __call__(self) -> AsyncSession:
        return AsyncSession(self.engine, expire_on_commit=False)


@pytest.fixture
async def engine():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__]
            for m in (User, ChatSession, Message, Artifact, CreditLedger)])
    yield engine
    await engine.dispose()


async def _store(engine, monkeypatch, kind: SessionKind) -> _Store:
    store = _Store(engine, kind)
    async with store() as db:
        db.add(store.user)
        await db.commit()
        db.add(store.session)
        await db.commit()
        db.add(store.question)
        await db.commit()
    monkeypatch.setattr(sessions, "SessionLocal", store)
    return store


async def _stop_midway(events, started: asyncio.Event, session_id: str) -> list[dict]:
    """Drains a turn wrapped as the route wraps it, pressing 중단 once it has started writing."""
    stopping = asyncio.Event()
    seen: list[str] = []

    async def drain():
        async for chunk in sessions._stoppable(session_id, stopping, events(stopping)):
            seen.append(chunk)

    task = asyncio.create_task(drain())
    await asyncio.wait_for(started.wait(), timeout=2)
    # what `stop_turn` does on this replica (and the listener on another)
    sessions.fire_stop_locally(session_id)
    await asyncio.wait_for(task, timeout=2)
    return [json.loads(c.removeprefix("data: ")) for c in seen if c.startswith("data: ")]


async def _settled(store: _Store) -> tuple[list[Artifact], Message, list[CreditLedger]]:
    async with store() as db:
        artifacts = (await db.exec(select(Artifact))).all()
        question = await db.get(Message, store.question.id)
        ledger = (await db.exec(select(CreditLedger))).all()
    return list(artifacts), question, list(ledger)


@pytest.mark.asyncio
async def test_a_stopped_report_stores_no_half_written_document(engine, monkeypatch):
    store = await _store(engine, monkeypatch, SessionKind.report)
    started = asyncio.Event()
    closed = asyncio.Event()

    async def write(**kwargs):
        work = Bundle(request=Request(text="써 줘", model=MODEL["id"], api_key="k",
                                      session_id=store.session.id))
        # the writer's tokens so far, as the stages count them on the bundle
        work.usage = {"inputTokens": 1000, "outputTokens": 500}
        kwargs["bundle_out"].append((work, "report", "write"))
        try:
            yield {"type": "section", "sectionId": "s1", "heading": "개요", "content": "반쯤",
                   "done": False}
            started.set()
            await asyncio.Event().wait()
            yield {"type": "report", "sections": [{"id": "s1", "content": "다 씀"}]}
        finally:
            closed.set()

    monkeypatch.setattr(sessions.report_service, "write", write)

    def events(stopping):
        return sessions._run_report(
            user_id=store.user.id, api_key="k", session_id=store.session.id, model=MODEL,
            request="써 줘", project_id=None, approved_plan={"title": "T", "sections": []},
            stopping=stopping)

    seen = await _stop_midway(events, started, store.session.id)

    # The writer is closed, not left running unread.
    assert closed.is_set()
    assert [e["type"] for e in seen][-2:] == ["usage", "done"]
    assert not [e for e in seen if e["type"] in ("artifact", "error")]
    artifacts, question, ledger = await _settled(store)
    assert artifacts == []
    assert question.failure == TurnFailure.stopped
    # 1000 in and 500 out at 10 credits per 1k each: what ran, nothing more
    assert [row.delta for row in ledger] == [-15]
    assert seen[-2]["credits"] == 15
    # The stop signal is gone: the next send is not refused as busy.
    assert not sessions.turn_running_here(store.session.id)


@pytest.mark.asyncio
async def test_a_stopped_deck_stores_no_half_written_deck(engine, monkeypatch):
    store = await _store(engine, monkeypatch, SessionKind.slides)
    started = asyncio.Event()

    async def write(**_kwargs):
        yield {"type": "slide", "slide": {"id": "a", "title": "표지"}, "done": True}
        yield {"type": "usage", "inputTokens": 2000, "outputTokens": 1000}
        started.set()
        await asyncio.Event().wait()
        yield {"type": "deck", "slides": [{"id": "a", "title": "표지"}]}

    monkeypatch.setattr(sessions.deck_service, "write", write)

    def events(stopping):
        return sessions._run_deck(
            user_id=store.user.id, api_key="k", session_id=store.session.id, model=MODEL,
            request="써 줘", project_id=None, web_search=False,
            approved_plan={"title": "T", "slides": [{"title": "표지"}]}, stopping=stopping)

    seen = await _stop_midway(events, started, store.session.id)

    assert [e["type"] for e in seen][-2:] == ["usage", "done"]
    assert not [e for e in seen if e["type"] in ("artifact", "error")]
    artifacts, question, ledger = await _settled(store)
    assert artifacts == []
    assert question.failure == TurnFailure.stopped
    assert [row.delta for row in ledger] == [-30]
    assert not sessions.turn_running_here(store.session.id)


@pytest.mark.asyncio
async def test_a_report_that_is_not_stopped_is_stored_as_before(engine, monkeypatch):
    store = await _store(engine, monkeypatch, SessionKind.report)
    saved = {"artifactId": "art-1", "credits": 3}

    async def write(**kwargs):
        work = Bundle(request=Request(text="써 줘", model=MODEL["id"], api_key="k",
                                      session_id=store.session.id))
        work.document.saved = saved
        kwargs["bundle_out"].append((work, "report", "write"))
        yield {"type": "section", "sectionId": "s1", "heading": "개요", "content": "다 씀",
               "done": True}

    monkeypatch.setattr(sessions.report_service, "write", write)
    stopping = asyncio.Event()
    chunks = [c async for c in sessions._stoppable(store.session.id, stopping, sessions._run_report(
        user_id=store.user.id, api_key="k", session_id=store.session.id, model=MODEL,
        request="써 줘", project_id=None, approved_plan={"title": "T", "sections": []},
        stopping=stopping))]
    seen = [json.loads(c.removeprefix("data: ")) for c in chunks]
    assert {"type": "artifact", "artifactId": "art-1"} in seen
    _, question, _ = await _settled(store)
    assert question.failure is None
    assert not sessions.turn_running_here(store.session.id)
