"""A report or deck turn holds the session like a chat turn, and a long turn keeps its hold."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
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


class _Clock:
    """The claim's clock, moved by hand: no test waits out a real TTL."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def skip_past_ttl(self) -> None:
        self.now += timedelta(seconds=sessions._CLAIM_TTL_SEC * 2)


@pytest.fixture
async def claims(monkeypatch, tmp_path):
    from test_shared_notes import _DDL

    # A file, not one shared in-memory connection: cancelling the keeper mid-query drops
    # that connection, and a fresh in-memory one would come back with no tables.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}")
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)

    def local() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    clock = _Clock()
    monkeypatch.setattr(sessions, "SessionLocal", local)
    monkeypatch.setattr(sessions, "utcnow", clock)
    monkeypatch.setattr(sessions, "_CLAIM_REFRESH_SEC", 0.01)
    async with local() as db:
        user = User(email="p@example.test", password_hash="x", name="P")
        db.add(user)
        await db.commit()
        session = ChatSession(user_id=user.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
    yield local, session, clock
    await engine.dispose()


async def _claim(local, session: ChatSession) -> bool:
    async with local() as db:
        taken = await sessions._claim_turn(db, session)
        await db.commit()
    return taken


async def _renewed(local, session: ChatSession, clock: _Clock) -> None:
    """Waits until the keeper has stamped the claim with the clock's current time."""
    async def stamped() -> None:
        while True:
            async with local() as db:
                row = await db.get(ChatSession, session.id, populate_existing=True)
                since = row.running_since if row else None
            if since is not None and since.replace(tzinfo=UTC) == clock.now:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(stamped(), timeout=10)


@pytest.mark.asyncio
async def test_a_long_turn_keeps_its_claim_and_a_dead_one_lets_it_go(claims):
    local, session, clock = claims
    assert await _claim(local, session)
    keeper = asyncio.create_task(sessions._keep_claim(session.id, sessions._CLAIMS[session.id]))
    # Past the TTL with the owner alive: renewed, so still held.
    clock.skip_past_ttl()
    await _renewed(local, session, clock)
    assert not await _claim(local, session)
    keeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await keeper
    # The owner died (no renewal): after the TTL the claim is free.
    clock.skip_past_ttl()
    assert await _claim(local, session)


@pytest.mark.asyncio
async def test_a_streamed_turn_renews_its_claim_and_gives_it_back(claims):
    local, session, clock = claims
    assert await _claim(local, session)
    finish = asyncio.Event()

    async def slow():
        yield "data: {}\n\n"
        await finish.wait()
        yield 'data: {"type": "done"}\n\n'

    stream = sessions._survive_disconnect(slow())
    # The stream took the claim over from the request.
    assert sessions._TURN_CLAIM.get() is None
    first = await anext(stream)
    assert first == "data: {}\n\n"
    clock.skip_past_ttl()
    await _renewed(local, session, clock)
    assert not await _claim(local, session)
    finish.set()
    rest = [chunk async for chunk in stream]
    assert rest[-1].startswith('data: {"type": "done"}')
    # Released at the end: the next send goes through.
    assert await _claim(local, session)


@pytest.mark.asyncio
async def test_a_stopped_turn_stops_renewing_a_claim_it_no_longer_holds(claims):
    local, session, _clock = claims
    assert await _claim(local, session)
    token = sessions._CLAIMS[session.id]
    keeper = asyncio.create_task(sessions._keep_claim(session.id, token))
    # 중단 releases whatever holds the session; a new turn claims it.
    await sessions._release_turn(session.id, token="")
    assert await _claim(local, session)
    await asyncio.wait_for(keeper, timeout=10)
    # The old turn's end does not release the new turn's claim.
    await sessions._release_turn(session.id, token)
    assert not await _claim(local, session)
