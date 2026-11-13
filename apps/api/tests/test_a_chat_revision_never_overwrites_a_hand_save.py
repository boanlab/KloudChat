"""A chat revision saves onto the version it read: a hand save made while it ran is kept; the
revision's own changes are put on top when they touch other parts, and kept apart as a draft
when they touch the same one. The person's standing rules reach the rewrite."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion, StoredFile
from app.routers import sessions
from app.services import artifact_versions, deck, revise


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


_USERS = """
CREATE TABLE users (
    id VARCHAR PRIMARY KEY, email VARCHAR, password_hash VARCHAR, name VARCHAR,
    role VARCHAR, status VARCHAR, monthly_credits INTEGER, credits_used INTEGER,
    cycle_resets_at DATETIME, litellm_user_id VARCHAR, litellm_key VARCHAR,
    litellm_key_preview VARCHAR, litellm_key_issued_at DATETIME, avatar_color VARCHAR,
    allowed_models JSON, preferences JSON, created_at DATETIME, last_active_at DATETIME,
    email_verified_at DATETIME
)
"""

SLIDES = [
    {"id": "s0", "layout": "title", "title": "3분기 실적 보고"},
    {"id": "s1", "layout": "bullets", "title": "성과", "bullets": ["매출 증가"]},
    {"id": "s2", "layout": "bullets", "title": "요청 사항", "bullets": ["예산 승인"]},
]


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = [ChatSession.__table__, Message.__table__, Artifact.__table__,
              ArtifactVersion.__table__, StoredFile.__table__]
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_USERS)
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sessions, "SessionLocal", made)
    monkeypatch.setattr(sessions, "settle", lambda *a, **k: None)
    yield made
    await engine.dispose()


async def _deck(maker, *said: str) -> tuple[str, str]:
    async with maker() as db:
        db.add(User(id="u1", email="u1@example.test", password_hash="x", name="U"))
        session = ChatSession(user_id="u1", kind=SessionKind.slides)
        artifact = Artifact(user_id="u1", session_id=session.id, kind=ArtifactKind.deck,
                            title="3분기 실적 보고", data={"kind": "deck", "slides": SLIDES})
        session.artifact_id = artifact.id
        db.add(session)
        db.add(artifact)
        for text in ("실적 보고 슬라이드", *said):
            db.add(Message(session_id=session.id, role=Role.user, content=text))
        await db.commit()
        return session.id, artifact.id


async def _hand_save(maker, artifact_id: str, slide: int, bullets: list[str]) -> None:
    """The person saves from the panel while the agent works: a conditional save."""
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
        slides = [dict(s) for s in artifact.data["slides"]]
        slides[slide] = {**slides[slide], "bullets": bullets}
        assert await artifact_versions.advance(db, artifact, artifact.version, "편집기에서 저장")
        artifact.data = {**artifact.data, "slides": slides}
        db.add(artifact)
        await db.commit()


def _rewriting(maker, artifact_id: str, *, saved_slide: int, notes: list[str]):
    async def rewrite(**kwargs):
        notes.append(kwargs.get("note") or "")
        # the hand save lands while the model writes
        await _hand_save(maker, artifact_id, saved_slide, ["손으로 고친 줄"])
        target = next(s for s in kwargs["slides"] if s["id"] == kwargs["target_id"])
        return {**target, "bullets": ["에이전트가 고친 줄"]}, {"inputTokens": 1,
                                                          "outputTokens": 1}
    return rewrite


async def _turn(session_id: str, target: int) -> list[dict]:
    return [json.loads(chunk.removeprefix("data: "))
            async for chunk in sessions._revise_document(
                user_id="u1", api_key="k", session_id=session_id, model={"id": "m"},
                instruction=f"{target + 1}장을 다듬어 줘",
                plan=revise.Plan(scope="parts", targets=[target], note="다듬기"))]


async def test_a_revision_of_another_slide_goes_on_top_of_the_hand_save(maker, monkeypatch):
    session_id, artifact_id = await _deck(maker, "앞으로 기관명은 익명으로 써 줘")
    notes: list[str] = []
    monkeypatch.setattr(deck, "rewrite_slide",
                        _rewriting(maker, artifact_id, saved_slide=1, notes=notes))
    events = await _turn(session_id, 2)
    assert "앞으로 기관명은 익명으로 써 줘" in notes[0]
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
        reply = (await db.exec(select(Message).where(Message.role == Role.assistant))).one()
    assert artifact.version == 3
    slides = artifact.data["slides"]
    assert slides[1]["bullets"] == ["손으로 고친 줄"]
    assert slides[2]["bullets"] == ["에이전트가 고친 줄"]
    assert "v2" in reply.content and "합쳐" in reply.content
    assert any(e.get("type") == "artifact" and e["artifactId"] == artifact_id for e in events)


async def test_a_revision_of_the_same_slide_is_kept_apart_as_a_draft(maker, monkeypatch):
    session_id, artifact_id = await _deck(maker)
    monkeypatch.setattr(deck, "rewrite_slide",
                        _rewriting(maker, artifact_id, saved_slide=2, notes=[]))
    events = await _turn(session_id, 2)
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
        session = await db.get(ChatSession, session_id)
        drafts = (await db.exec(select(Artifact).where(Artifact.id != artifact_id))).all()
        reply = (await db.exec(select(Message).where(Message.role == Role.assistant))).one()
    # the hand save stands, untouched
    assert artifact.version == 2 and artifact.data["slides"][2]["bullets"] == ["손으로 고친 줄"]
    assert session.artifact_id == artifact_id
    [draft] = drafts
    assert draft.title == "3분기 실적 보고 (수정안)"
    assert draft.data["slides"][2]["bullets"] == ["에이전트가 고친 줄"]
    assert "덮어쓰지 않았습니다" in reply.content and "(수정안)" in reply.content
    assert any(e.get("type") == "artifact" and e["artifactId"] == draft.id for e in events)


def test_merge_puts_only_untouched_parts_on_top():
    base = {"slides": [{"id": "a", "t": 1}, {"id": "b", "t": 1}], "theme": "x"}
    ours = {"slides": [{"id": "a", "t": 2}, {"id": "b", "t": 1}, {"id": "c", "t": 0}],
            "theme": "x"}
    theirs = {"slides": [{"id": "a", "t": 1}, {"id": "b", "t": 3}], "theme": "y"}
    body, title = artifact_versions.merge(base, ours, theirs, parts="slides",
                                          title=("T", "T", "T2"))
    assert body["slides"] == [{"id": "a", "t": 2}, {"id": "b", "t": 3}, {"id": "c", "t": 0}]
    assert body["theme"] == "y" and title == "T2"
    clash = {"slides": [{"id": "a", "t": 9}, {"id": "b", "t": 1}]}
    assert artifact_versions.merge(base, ours, clash, parts="slides",
                                   title=("T", "T", "T")) is None
    # both renamed the document differently: a clash too
    assert artifact_versions.merge(base, ours, theirs, parts="slides",
                                   title=("T", "A", "B")) is None


async def test_a_rewrite_of_the_whole_document_keeps_a_hand_save_made_meanwhile(maker):
    session_id, artifact_id = await _deck(maker)
    base = await sessions._document_base(session_id)
    assert base == {"artifactId": artifact_id, "version": 1, "title": "3분기 실적 보고"}
    await _hand_save(maker, artifact_id, 1, ["손으로 고친 줄"])
    drafted: list[str] = []
    async with maker() as db:
        session = await db.get(ChatSession, session_id)
        made = await sessions._store_document(
            db, session, user_id="u1", project_id=None, kind=ArtifactKind.deck,
            title="새 덱", data={"kind": "deck", "slides": SLIDES[:1]}, summary="재생성 전",
            base=base, drafted=drafted)
        await db.commit()
        artifact = await db.get(Artifact, artifact_id)
    assert made != artifact_id and drafted and "v2" in drafted[0]
    assert artifact.data["slides"][1]["bullets"] == ["손으로 고친 줄"]


async def test_a_corrected_file_sent_later_wins_the_budget(maker, monkeypatch):
    session_id, artifact_id = await _deck(maker)
    async with maker() as db:
        old = StoredFile(user_id="u1", session_id=session_id, name="실적.txt",
                         text="매출 100억 " * 50)
        new = StoredFile(user_id="u1", session_id=session_id, name="실적.txt",
                         text="매출 120억(정정) " * 50)
        other = StoredFile(user_id="u1", session_id=session_id, name="참고.txt",
                           text="참고 자료 " * 50)
        db.add_all([old, new, other])
        for row in (old, other, new):
            db.add(Message(session_id=session_id, role=Role.user, content="자료",
                           attachments=[{"id": row.id, "name": row.name}]))
        await db.commit()
    monkeypatch.setattr(sessions, "file_budget", lambda model: 600)
    seen: list[list[str]] = []

    async def rewrite(**kwargs):
        seen.append(list(kwargs.get("material") or []))
        target = next(s for s in kwargs["slides"] if s["id"] == kwargs["target_id"])
        return dict(target), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "rewrite_slide", rewrite)
    await _turn(session_id, 2)
    joined = "\n".join(seen[0])
    assert "120억(정정)" in joined and "100억" not in joined


async def test_a_move_moves_the_part_and_rewrites_nothing(maker, monkeypatch):
    session_id, artifact_id = await _deck(maker)

    async def no_rewrite(**_kwargs):
        raise AssertionError("a move rewrites nothing")

    monkeypatch.setattr(deck, "rewrite_slide", no_rewrite)
    plan, _ = await _plan(maker, session_id, "요청 사항 장을 맨 앞으로 옮겨 줘")
    assert plan.targets == [] and not plan.restructures
    async for _ in sessions._revise_document(
            user_id="u1", api_key="k", session_id=session_id, model={"id": "m"},
            instruction="요청 사항 장을 맨 앞으로 옮겨 줘", plan=plan):
        pass
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
    assert [s["id"] for s in artifact.data["slides"]] == ["s2", "s0", "s1"]
    assert artifact.data["slides"][0] == SLIDES[2]


async def _plan(maker, session_id: str, text: str):
    async with maker() as db:
        session = await db.get(ChatSession, session_id)
        return await sessions._revision_plan(db, session, instruction=text, model={"id": "m"},
                                             api_key="k")


async def test_undo_restores_the_version_before_as_the_newest(maker):
    from app.services import revise

    session_id, artifact_id = await _deck(maker)
    await _hand_save(maker, artifact_id, 1, ["손으로 고친 줄"])
    assert revise.asks_undo("방금 고친 거 취소해 줘") == (True, None)
    assert revise.asks_undo("v1으로 되돌려 줘") == (True, 1)
    assert revise.asks_undo("3장을 줄여 줘")[0] is False
    events = [e async for e in sessions._undo_document(user_id="u1", session_id=session_id,
                                                        version=None)]
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
    assert artifact.version == 3 and artifact.data["slides"] == SLIDES
    assert any("v1의 내용으로 되돌려 v3" in e for e in events)
