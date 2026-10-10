"""「에이전트로 저장」: a conversation as a draft agent, nothing saved until the form is.

The draft's 「이 대화에서 정한 것」 is distilled from the person's own messages only, by the
model the conversation ran on (its strict-local model when any turn ran strict-local, with no
fallback and nothing masked away from it); a sealed agent's prompt is never copied. The
conversation's files go onto the agent's shelf as copies, the caller's own only.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.models.workspace import (
    Agent,
    DesignSystem,
    Project,
    Skill,
    StoredFile,
    UserPipeline,
    Visibility,
)
from app.routers import sessions
from app.routers import workspace as ws
from app.schemas.workspace import BulkDelete
from app.services import agent_draft
from app.services import files as file_service

_CLOUD = {"id": "cloud/big", "kinds": ["chat"], "creditCost": 0, "dataBoundary": "external"}
_LOCAL = {"id": "local/strict", "kinds": ["chat"], "creditCost": 0,
          "dataBoundary": "self_hosted", "strictLocal": True}


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all, tables=[
                metadata.tables[m.__tablename__] for m in (
                    User, ChatSession, Message, Agent, Skill, StoredFile, Project,
                    DesignSystem, UserPipeline)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []

    async def distill(model, messages, api_key, *, strict_local, masker=None):
        seen.append({"model": model, "messages": list(messages), "strict": strict_local,
                     "masked": masker is not None})
        return ["보고 대상은 팀장. 결론을 맨 앞에 둔다.", "한 쪽을 넘기지 않는다."], {
            "inputTokens": 10, "outputTokens": 5}

    async def catalogue():
        return {"models": [_CLOUD, _LOCAL]}

    async def credentials(_user):
        return "u", "k"

    monkeypatch.setattr(agent_draft, "distill", distill)
    monkeypatch.setattr(sessions.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_a: True)
    monkeypatch.setattr(sessions, "settle", lambda *_a, **_k: None)
    return seen


async def _people(db):
    me = User(email="me@example.com", name="나", password_hash="x")
    other = User(email="o@example.com", name="남", password_hash="x")
    db.add_all([me, other])
    await db.commit()
    return me, other


async def _talk(db, me, *, models=("cloud/big",), agent=None, kind=SessionKind.chat):
    session = ChatSession(user_id=me.id, kind=kind, title="주간 보고",
                          agent_id=agent.id if agent else None)
    db.add(session)
    await db.commit()
    rows = []
    for i, model in enumerate(models):
        rows += [Message(session_id=session.id, role=Role.user, content=f"요청 {i}: 팀장님께 1쪽"),
                 Message(session_id=session.id, role=Role.assistant, content=f"답 {i}",
                         model=model)]
    db.add_all(rows)
    await db.commit()
    return session


async def test_the_draft_reads_only_the_persons_words_and_saves_nothing(db, calls):
    me, other = await _people(db)
    session = await _talk(db, me)
    draft = await sessions.agent_draft(session.id, me, db)
    assert calls[0]["messages"] == ["요청 0: 팀장님께 1쪽"]
    # A cloud model reads the words masked.
    assert calls[0] == {**calls[0], "model": "cloud/big", "strict": False, "masked": True}
    assert draft["systemPrompt"] == (
        "이 대화에서 정한 것:\n- 보고 대상은 팀장. 결론을 맨 앞에 둔다.\n- 한 쪽을 넘기지 않는다.")
    assert (draft["name"], draft["mode"], draft["base"]) == ("주간 보고 에이전트", "direct", None)
    assert (await db.exec(select(Agent))).all() == []
    with pytest.raises(HTTPException):
        await sessions.agent_draft(session.id, other, db)


async def test_a_strict_local_conversation_is_distilled_on_its_strict_model(db, calls):
    me, _ = await _people(db)
    session = await _talk(db, me, models=("local/strict", "cloud/big"))
    draft = await sessions.agent_draft(session.id, me, db)
    assert calls[0]["model"] == "local/strict" and calls[0]["strict"] is True
    assert calls[0]["masked"] is False and draft["strictLocal"] is True


async def test_a_sealed_prompt_is_never_copied_and_the_base_comes_along(db, calls):
    me, other = await _people(db)
    sealed = Agent(owner_id=other.id, name="비밀", slug="s", system_prompt="비밀 지침",
                   share_mode="sealed", visibility=Visibility.org, kinds=["report"],
                   surface="report", base_key="minutes", doc_format="minutes")
    mine = Agent(owner_id=me.id, name="내 것", slug="m", system_prompt="내 지침",
                 kinds=["report"], surface="report", base_key="gaejo-report",
                 bundles=[{"bundle": "review", "items": {}}])
    db.add_all([sealed, mine])
    await db.commit()
    from_sealed = await sessions.agent_draft(
        (await _talk(db, me, agent=sealed, kind=SessionKind.report)).id, me, db)
    assert "비밀 지침" not in from_sealed["systemPrompt"]
    assert from_sealed["base"] == "minutes"
    assert [b["bundle"] for b in from_sealed["bundles"]] == ["prepare", "review", "finish"]
    from_mine = await sessions.agent_draft(
        (await _talk(db, me, agent=mine, kind=SessionKind.report)).id, me, db)
    assert from_mine["systemPrompt"].startswith("내 지침\n\n이 대화에서 정한 것:")
    assert from_mine["base"] == "gaejo-report" and from_mine["bundles"] == mine.bundles


async def test_a_report_conversation_infers_its_base_from_its_template(db, calls):
    me, _ = await _people(db)
    session = await _talk(db, me, kind=SessionKind.report)
    session.render_template_id = "doc-minutes"
    db.add(session)
    await db.commit()
    draft = await sessions.agent_draft(session.id, me, db)
    assert (draft["base"], draft["renderTemplateId"]) == ("minutes", "doc-minutes")
    assert draft["kinds"] == ["report"]


async def test_the_files_go_onto_the_shelf_as_copies_of_the_callers_own(db, calls, tmp_path,
                                                                        monkeypatch):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    monkeypatch.setattr(ws.index_client, "available", _false)
    me, other = await _people(db)
    session = await _talk(db, me)
    mine = StoredFile(user_id=me.id, session_id=session.id, name="자료.txt", text="본문", size=6)
    theirs = StoredFile(user_id=other.id, name="남의.txt", text="남의 본문", size=9)
    for row, owner in ((mine, me), (theirs, other)):
        row.storage_key = file_service.write_blob(owner.id, row.id, row.name, b"text")
    db.add_all([mine, theirs])
    await db.commit()
    draft = await sessions.agent_draft(session.id, me, db)
    assert draft["files"] == [{"id": mine.id, "name": "자료.txt", "size": 6}]

    agent = Agent(owner_id=me.id, name="새", slug="n")
    db.add(agent)
    await db.commit()
    made = await ws.add_agent_files_from(agent.id, ws.KnowledgeFromFiles(
        ids=[mine.id, theirs.id]), me, db)
    assert [f.name for f in made] == ["자료.txt"] and made[0].id != mine.id
    shelf = (await db.exec(select(StoredFile).where(StoredFile.agent_id == agent.id))).all()
    assert len(shelf) == 1 and shelf[0].storage_key != mine.storage_key
    with pytest.raises(HTTPException):
        await ws.add_agent_files_from(agent.id, BulkDelete(ids=[mine.id]), other, db)


async def _false():
    return False


def test_only_bullet_lines_are_kept():
    reply = "다음과 같습니다\n- 결론을 먼저\n* 표는 한 개\n\n- 결론을 먼저\n그 밖의 말"
    assert agent_draft.lines_of(reply) == ["결론을 먼저", "표는 한 개"]
    assert agent_draft.prompt_from([]) == ""


def test_the_model_reads_rules_in_english_and_writes_korean():
    text = agent_draft._INSTRUCTION
    assert text.startswith("You read only the messages a person sent")
    assert "Korean" in text and "「" in text

