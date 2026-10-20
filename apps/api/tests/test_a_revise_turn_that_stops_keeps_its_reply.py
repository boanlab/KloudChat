"""A revise turn that ends before saving a new version still keeps its reply: the ask-back
naming where the charts are, or why the edit failed, so a reload shows it and the chat is free."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_a_file_request_does_not_edit_the_document import _DDL

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions
from app.services import deck, revise

SLIDES = [
    {"id": "s0", "layout": "title", "title": "3분기 실적 보고"},
    {"id": "s1", "layout": "chart", "title": "매출 추이",
     "chart": {"kind": "bar", "unit": "억 원", "categories": ["전년", "올해"],
               "series": [{"name": "매출", "values": [114, 128]}]}},
    {"id": "s2", "layout": "bullets", "title": "요청 사항", "bullets": ["예산 1.5억 승인"]},
]


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sessions, "SessionLocal", made)
    yield made
    await engine.dispose()


async def _deck_session(maker) -> str:
    async with maker() as db:
        session = ChatSession(user_id="u1", kind=SessionKind.slides)
        artifact = Artifact(user_id="u1", session_id=session.id, kind=ArtifactKind.deck,
                            title="3분기 실적 보고", data={"kind": "deck", "slides": SLIDES})
        session.artifact_id = artifact.id
        db.add(session)
        db.add(artifact)
        db.add(Message(session_id=session.id, role=Role.user, content="실적 보고 슬라이드"))
        await db.commit()
        return session.id


async def _turn(session_id: str, instruction: str, plan: revise.Plan | None = None) -> list:
    return [json.loads(chunk.removeprefix("data: "))
            async for chunk in sessions._revise_document(
                user_id="u1", api_key="k", session_id=session_id, model={"id": "m"},
                instruction=instruction, plan=plan)]


async def _replies(maker, session_id: str) -> list[Message]:
    async with maker() as db:
        rows = (await db.exec(select(Message).where(Message.session_id == session_id,
                                                    Message.role == Role.assistant))).all()
        return list(rows)


async def test_the_ask_back_about_charts_is_the_kept_reply(maker, monkeypatch):
    async def no_planner(**_kwargs):
        raise AssertionError("not planned")

    monkeypatch.setattr(revise, "plan", no_planner)
    session_id = await _deck_session(maker)
    events = await _turn(session_id, "그림 빼줘.")
    said = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert "차트는 2장 「매출 추이」에 있습니다" in said
    [reply] = await _replies(maker, session_id)
    assert reply.content == said and reply.failure is None


async def test_a_rewrite_that_could_not_be_read_keeps_a_failed_reply_with_its_reason(
        maker, monkeypatch):
    async def unreadable(**_kwargs):
        raise deck.SlideNotWritten("빈 슬라이드", {"inputTokens": 1, "outputTokens": 1})

    monkeypatch.setattr(deck, "rewrite_slide", unreadable)
    session_id = await _deck_session(maker)
    events = await _turn(session_id, "3장을 더 자세히 써 줘",
                         revise.Plan(scope="parts", targets=[2], note="자세히"))
    errors = [e["message"] for e in events if e.get("type") == "error"]
    assert errors == ["모델이 보낸 답을 읽지 못해 고치지 못했습니다. 다시 요청해 주세요."]
    [reply] = await _replies(maker, session_id)
    assert reply.content == errors[0] and reply.failure == TurnFailure.no_answer
