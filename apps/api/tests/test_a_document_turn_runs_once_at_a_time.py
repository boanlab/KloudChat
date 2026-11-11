"""A report or deck turn holds the session like a chat turn, and a long turn keeps its hold."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.dml import Update
from sqlmodel.ext.asyncio.session import AsyncSession
from turn_fakes import _Db, _patch_route_services, _request, _user

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage


class _Held(_Db):
    """A session another tab or replica already claimed: the claim updates no row."""

    async def exec(self, query):
        if isinstance(query, Update):
            return type("Result", (), {"rowcount": 0})()
        return await super().exec(query)


@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
@pytest.mark.asyncio
async def test_a_second_document_send_is_refused_while_one_is_written_here(monkeypatch, kind):
    db = _Db(ChatSession(id="session-1", user_id="user-1", kind=kind))
    _patch_route_services(monkeypatch, {"calls": 0})
    sessions._STOPPING["session-1"] = {asyncio.Event()}
    try:
        with pytest.raises(sessions.HTTPException) as refused:
            await sessions.send_message("session-1", SendMessage(content="보고서 써 줘"),
                                        _request("/sessions/session-1/messages"), _user(), db)
    finally:
        sessions._STOPPING.pop("session-1", None)
    assert (refused.value.status_code, refused.value.detail) == (409, "session_busy")
    assert db.added == []


@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
@pytest.mark.asyncio
async def test_a_second_document_send_is_refused_while_another_replica_writes(monkeypatch, kind):
    db = _Held(ChatSession(id="session-1", user_id="user-1", kind=kind))
    _patch_route_services(monkeypatch, {"calls": 0})
    with pytest.raises(sessions.HTTPException) as refused:
        await sessions.send_message("session-1", SendMessage(content="보고서 써 줘"),
                                    _request("/sessions/session-1/messages"), _user(), db)
    assert (refused.value.status_code, refused.value.detail) == (409, "session_busy")
    assert db.added == []


class _Broken(_Db):
    async def exec(self, query):
        if isinstance(query, Update):
            raise RuntimeError("database went away")
        return await super().exec(query)


@pytest.mark.asyncio
async def test_a_claim_that_cannot_be_checked_is_refused_not_waved_through(monkeypatch):
    db = _Broken(ChatSession(id="session-1", user_id="user-1", kind=SessionKind.report))
    assert not await sessions._claim_turn(db, db.session)


@pytest.fixture
async def claims(monkeypatch):
    from test_shared_notes import _DDL

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)

    def local() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(sessions, "SessionLocal", local)
    # a short life, renewed well inside it
    monkeypatch.setattr(sessions, "_CLAIM_TTL_SEC", 0.4)
    monkeypatch.setattr(sessions, "_CLAIM_REFRESH_SEC", 0.05)
    async with local() as db:
        user = User(email="p@example.test", password_hash="x", name="P")
        db.add(user)
        await db.commit()
        session = ChatSession(user_id=user.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
    yield local, session
    await engine.dispose()


async def _claim(local, session: ChatSession) -> bool:
    async with local() as db:
        taken = await sessions._claim_turn(db, session)
        await db.commit()
    return taken


@pytest.mark.asyncio
async def test_a_long_turn_keeps_its_claim_and_a_dead_one_lets_it_go(claims):
    local, session = claims
    assert await _claim(local, session)
    keeper = asyncio.create_task(sessions._keep_claim(session.id, sessions._CLAIMS[session.id]))
    try:
        # Past the TTL with the owner alive: still held.
        await asyncio.sleep(0.8)
        assert not await _claim(local, session)
    finally:
        keeper.cancel()
    # The owner died (no renewal): after the TTL the claim is free.
    await asyncio.sleep(0.8)
    assert await _claim(local, session)


@pytest.mark.asyncio
async def test_a_streamed_turn_renews_its_claim_and_gives_it_back(claims):
    local, session = claims
    assert await _claim(local, session)

    async def slow():
        yield "data: {}\n\n"
        await asyncio.sleep(0.8)
        yield 'data: {"type": "done"}\n\n'

    stream = sessions._survive_disconnect(slow())
    # The stream took the claim over from the request.
    assert sessions._TURN_CLAIM.get() is None
    first = await anext(stream)
    assert first == "data: {}\n\n"
    await asyncio.sleep(0.6)
    assert not await _claim(local, session)
    rest = [chunk async for chunk in stream]
    assert rest[-1].startswith('data: {"type": "done"}')
    # Released at the end: the next send goes through.
    assert await _claim(local, session)


@pytest.mark.asyncio
async def test_a_stopped_turn_stops_renewing_a_claim_it_no_longer_holds(claims):
    local, session = claims
    assert await _claim(local, session)
    token = sessions._CLAIMS[session.id]
    keeper = asyncio.create_task(sessions._keep_claim(session.id, token))
    # 중단 releases whatever holds the session; a new turn claims it.
    await sessions._release_turn(session.id, token="")
    assert await _claim(local, session)
    await asyncio.wait_for(keeper, timeout=1)
    # The old turn's end does not release the new turn's claim.
    await sessions._release_turn(session.id, token)
    assert not await _claim(local, session)
