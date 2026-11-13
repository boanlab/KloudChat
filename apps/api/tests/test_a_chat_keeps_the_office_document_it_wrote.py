"""A chat whose HWP, Word or PowerPoint tools saved a document keeps it: the answer carries its
card and the conversation reopens on it, after a reload as well as live."""

from __future__ import annotations

import pytest
from test_side_calls_are_billed import _TurnDb

from app.models.chat import ChatSession, Message
from app.models.user import User
from app.routers import sessions as sessions_router
from app.services.tools.base import ToolContext


async def _turn(monkeypatch, saved: dict) -> tuple[_TurnDb, list[dict]]:
    session = ChatSession(id="session-1", user_id="user-1")
    user = User(id="user-1", email="person@example.test", password_hash="hash", name="Person")
    db = _TurnDb(session, user)

    async def run_turn(*args, **_kwargs):
        ctx = next(a for a in args if isinstance(a, ToolContext))
        # what pptx_write leaves on the turn's context
        ctx.pptx.update(saved)
        yield {"type": "delta", "text": "발표 자료를 편집기에 띄웠습니다."}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 10}

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions_router, "_enrich_memory", nothing)
    events = []
    async for chunk in sessions_router._run_turn(
        user_id=user.id, api_key="key", auto_memory=False, session_id=session.id,
        model={"id": "local/m", "creditCost": 1, "inputCreditCost": 1}, messages=[], tools=[],
        first_user_message="발표 자료 만들어 줘", is_first_turn=False,
    ):
        events.append(chunk)
    return db, events


@pytest.mark.asyncio
async def test_a_saved_deck_is_on_the_answer_and_the_conversation(monkeypatch):
    db, events = await _turn(monkeypatch, {"id": "deck-1", "version": 1, "changed": True})
    answer = next(row for row in db.added if isinstance(row, Message))
    assert answer.artifact_ids == ["deck-1"]
    assert db.session.artifact_id == "deck-1"
    assert any('"artifactId": "deck-1"' in str(chunk) for chunk in events)


@pytest.mark.asyncio
async def test_a_deck_only_read_leaves_the_conversation_as_it_was(monkeypatch):
    db, _ = await _turn(monkeypatch, {"id": "deck-1", "version": 1})
    answer = next(row for row in db.added if isinstance(row, Message))
    assert not answer.artifact_ids
    assert db.session.artifact_id is None


@pytest.mark.asyncio
async def test_a_chat_with_no_record_reopens_on_its_newest_office_document():
    import datetime as dt

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.workspace import Artifact, ArtifactKind

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Artifact.__table__.create(sync))
    at = dt.datetime(2026, 11, 13, tzinfo=dt.UTC)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        rows = [
            ("deck-web", ArtifactKind.deck, "session-1", 3),
            ("deck-old", ArtifactKind.pptx, "session-1", 1),
            ("word-new", ArtifactKind.docx, "session-1", 2),
            ("elsewhere", ArtifactKind.hwp, "session-2", 4),
        ]
        for id_, kind, session_id, minute in rows:
            db.add(Artifact(id=id_, user_id="user-1", session_id=session_id, kind=kind, title=id_,
                            data={}, updated_at=at + dt.timedelta(minutes=minute)))
        await db.commit()
        # office documents only, this chat only, the newest
        assert await sessions_router._newest_office_document(db, "session-1") == "word-new"
        assert await sessions_router._newest_office_document(db, "session-3") is None
    await engine.dispose()
