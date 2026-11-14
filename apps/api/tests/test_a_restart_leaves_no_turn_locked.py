"""A restart or a crash never leaves a conversation locked: at shutdown running turns get
a bounded time to finish, the rest are stopped and stored as interrupted, and every claim
this process held is freed; at startup a replica frees what an earlier boot of itself held
(a crash), without touching other replicas' claims."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_a_file_request_does_not_edit_the_document import _DDL

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.routers import sessions as sessions_router


@pytest.fixture
async def local(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'turns.db'}")
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)

    def factory():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(sessions_router, "SessionLocal", factory)
    monkeypatch.setattr(sessions_router, "_SHUTDOWN", {"since": 0.0, "stopping": False})
    monkeypatch.setattr(sessions_router, "_CLAIMS", {})
    yield factory
    await engine.dispose()


async def _asked(local, text: str = "질문") -> str:
    async with local() as db:
        session = ChatSession(user_id="u1", kind=SessionKind.chat)
        db.add(session)
        await db.commit()
        db.add(Message(session_id=session.id, role=Role.user, content=text))
        await db.commit()
        return session.id


async def _state(local, session_id: str) -> tuple[str | None, str | None]:
    async with local() as db:
        session = await db.get(ChatSession, session_id)
        last = (await db.exec(select(Message).where(Message.session_id == session_id))).all()[-1]
        return session.running_turn, last.failure


async def _claim(local, session_id: str) -> str:
    async with local() as db:
        session = await db.get(ChatSession, session_id)
        assert await sessions_router._claim_turn(db, session)
        await db.commit()
    return sessions_router._CLAIMS[session_id]


def _turn(claim: tuple[str, str], finish: asyncio.Event):
    """A detached turn whose reader is gone; it ends when `finish` is set."""

    async def events():
        await finish.wait()
        yield "data: {}\n\n"

    reader = sessions_router._detached(events(), claim)
    return asyncio.create_task(anext(reader, None))


@pytest.mark.asyncio
async def test_the_claim_names_its_replica_and_boot(local):
    session_id = await _asked(local)
    token = await _claim(local, session_id)
    replica, boot, _ = token.split(":")
    assert (replica, boot) == (sessions_router._REPLICA, sessions_router._BOOT)


@pytest.mark.asyncio
async def test_startup_frees_only_an_earlier_boot_of_this_replica(local):
    crashed, live, other = [await _asked(local) for _ in range(3)]
    tokens = {
        crashed: f"{sessions_router._REPLICA}:deadboot:1",
        live: f"{sessions_router._REPLICA}:{sessions_router._BOOT}:2",
        other: "another-replica:itsboot:3",
    }
    async with local() as db:
        for session_id, token in tokens.items():
            row = await db.get(ChatSession, session_id)
            row.running_turn = token
            row.running_since = sessions_router.utcnow()
            db.add(row)
        await db.commit()
    assert await sessions_router.free_claims_of_earlier_boots() == 1
    assert await _state(local, crashed) == (None, TurnFailure.interrupted)
    assert await _state(local, live) == (tokens[live], None)
    assert await _state(local, other) == (tokens[other], None)


@pytest.mark.asyncio
async def test_a_turn_that_finishes_within_the_drain_is_left_alone(local):
    session_id = await _asked(local)
    token = await _claim(local, session_id)
    finish = asyncio.Event()
    turn = _turn((session_id, token), finish)
    await asyncio.sleep(0)
    asyncio.get_running_loop().call_later(0.2, finish.set)
    await sessions_router.drain_turns(5.0, settle_sec=1.0)
    await turn
    # its own release, as on any finished turn: free, and the question not marked
    assert await _state(local, session_id) == (None, None)


@pytest.mark.asyncio
async def test_a_turn_still_running_is_stopped_freed_and_marked_interrupted(local):
    session_id = await _asked(local)
    token = await _claim(local, session_id)
    turn = _turn((session_id, token), asyncio.Event())  # never finishes
    await asyncio.sleep(0)
    await sessions_router.drain_turns(0.1, settle_sec=0.1)
    await turn
    assert await _state(local, session_id) == (None, TurnFailure.interrupted)
    assert session_id not in sessions_router._CLAIMS
    # the next send is let through at once, not after the claim's TTL
    async with local() as db:
        assert await sessions_router._claim_turn(db, await db.get(ChatSession, session_id))


@pytest.mark.asyncio
async def test_a_claim_no_turn_took_over_is_freed_at_shutdown(local):
    session_id = await _asked(local)
    await _claim(local, session_id)
    await sessions_router.drain_turns(0.0)
    assert await _state(local, session_id) == (None, TurnFailure.interrupted)


@pytest.mark.asyncio
async def test_the_drain_counts_from_the_stop_signal(local, monkeypatch):
    session_id = await _asked(local)
    token = await _claim(local, session_id)
    turn = _turn((session_id, token), asyncio.Event())
    await asyncio.sleep(0)
    # the signal came 100 s ago (uvicorn waited on open connections): 20 s of 120 are left
    since = sessions_router.time.monotonic() - 119.8
    monkeypatch.setitem(sessions_router._SHUTDOWN, "since", since)
    # what the drain waits for, read from its own waits, not from a wall clock a busy runner
    # stretches: what is left of 120 s after the signal (0.2 s, or nothing when it is gone),
    # then the 0.1 s settle and the 5 s for cancelled turns; never 120 s from now, never unbounded
    waits: list[float | None] = []
    real_wait = sessions_router.asyncio.wait

    async def wait(tasks, *, timeout=None, **kwargs):
        waits.append(timeout)
        return await real_wait(tasks, timeout=timeout, **kwargs)

    monkeypatch.setattr(sessions_router.asyncio, "wait", wait)
    try:
        await sessions_router.drain_turns(120.0, settle_sec=0.1)
    finally:
        monkeypatch.setattr(sessions_router.asyncio, "wait", real_wait)
    await turn
    assert waits and all(w is not None and w <= 5.0 for w in waits), waits
    assert waits[0] <= 0.2, waits


def test_a_stop_during_shutdown_is_stored_as_interrupted(monkeypatch):
    assert sessions_router._stopped_failure() is TurnFailure.stopped
    monkeypatch.setitem(sessions_router._SHUTDOWN, "stopping", True)
    assert sessions_router._stopped_failure() is TurnFailure.interrupted
