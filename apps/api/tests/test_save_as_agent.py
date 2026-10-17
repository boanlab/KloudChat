"""「에이전트로 저장」: a conversation as a draft agent, nothing saved until the form is."""

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


async def test_a_conversation_with_an_official_agent_saves_as_a_draft_post_agents_accepts(
        db, calls, monkeypatch):
    """B-1/B-2: the official agent's theme is the administrator's; the draft names the
    person's own theme of that name (or none), carries no official instructions, no line about
    this conversation's material and no date in its name — and saving it succeeds."""
    me, admin = await _people(db)
    theirs = DesignSystem(owner_id=admin.id, name="문서용 명조", tokens={})
    mine = DesignSystem(owner_id=me.id, name="문서용 명조", tokens={})
    official = Agent(owner_id=admin.id, name="기록 정리", slug="minutes",
                     system_prompt="You write meeting minutes. (official)",
                     visibility=Visibility.org, kinds=["report"], surface="report",
                     catalog_key="minutes", doc_format="minutes",
                     render_template_id="doc-minutes", design_system_id=theirs.id)
    db.add_all([theirs, mine, official])
    await db.commit()

    async def distill(model, messages, api_key, *, strict_local, masker=None):
        return ["결정과 할 일을 표로 둔다.", "아래 회의 메모로 회의록을 만든다."], {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(agent_draft, "distill", distill)
    session = await _talk(db, me, agent=official, kind=SessionKind.report)
    session.title, session.render_template_id = "10/8 학사운영팀 주간회의록", "doc-minutes"
    db.add(session)
    await db.commit()
    draft = await sessions.agent_draft(session.id, me, db)
    assert draft["designSystemId"] == mine.id
    assert "official" not in draft["systemPrompt"]
    assert draft["systemPrompt"] == "이 대화에서 정한 것:\n- 결정과 할 일을 표로 둔다."
    assert draft["name"] == "회의록 에이전트" and draft["base"] == "minutes"
    from app.schemas.workspace import AgentIn

    made = await ws.create_agent(AgentIn(
        name=draft["name"], system_prompt=draft["systemPrompt"], kinds=draft["kinds"],
        surface=draft["surface"], render_template_id=draft["renderTemplateId"],
        design_system_id=draft["designSystemId"], skill_ids=draft["skillIds"]), me, db)
    assert made.design_system_id == mine.id
    # Without a theme of that name, none.
    await db.delete(mine)
    await db.commit()
    assert (await sessions.agent_draft(session.id, me, db))["designSystemId"] is None


def test_a_draft_name_never_carries_a_date():
    assert agent_draft.name_for("10/8 학사운영팀 주간회의록") == "학사운영팀 주간회의록 에이전트"
    assert agent_draft.name_for("2026년 10월 8일 (화) 보고") == "보고 에이전트"
    assert agent_draft.name_for("10/8") == "새 에이전트"
    assert agent_draft.name_for("아무 제목", "회의록") == "회의록 에이전트"
    assert not agent_draft.keep("이 자료로 비교표를 만든다.")
    assert agent_draft.keep("결론을 맨 앞에 둔다.")


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



async def test_a_draft_with_nothing_settled_has_no_empty_header(db, monkeypatch, calls):
    """The lines the model returns that say nothing (「없음」, the header echoed, a dash)
    leave the 지시 empty, never a bare 「이 대화에서 정한 것:」."""

    async def nothing(model, messages, api_key, *, strict_local, masker=None):
        return ["없음", "(해당 없음)", "이 대화에서 정한 것:", "…", "이 메모로 회의록을 만든다."], {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(agent_draft, "distill", nothing)
    me, _ = await _people(db)
    session = await _talk(db, me)
    draft = await sessions.agent_draft(session.id, me, db)
    assert draft["systemPrompt"] == "" and draft["decided"] == []
    assert "정한 것" not in agent_draft.prompt_from(["없음", "  ", "N/A"])
    assert agent_draft.keep("없음 표시는 빈칸으로 둔다.")


async def test_a_distillation_that_fails_says_so_instead_of_an_empty_field(
        db, monkeypatch, calls):
    """A distillation that timed out is 「failed」, never an empty 지시 that reads as
    「filled in」; nothing settled is 「empty」; the form can ask for the rest first."""
    me, _ = await _people(db)
    session = await _talk(db, me)
    draft = await sessions.agent_draft(session.id, me, db)
    assert draft["distilled"] == "ok"

    quick = await sessions.agent_draft(session.id, me, db, distill=False)
    assert quick["distilled"] == "pending" and quick["systemPrompt"] == ""
    assert len(calls) == 1  # the quick draft never calls the model

    decided = await sessions.agent_draft_decided(session.id, me, db)
    assert decided == {
        "decided": ["보고 대상은 팀장. 결론을 맨 앞에 둔다.", "한 쪽을 넘기지 않는다."],
        "prompt": "이 대화에서 정한 것:\n- 보고 대상은 팀장. 결론을 맨 앞에 둔다.\n"
                  "- 한 쪽을 넘기지 않는다.",
        "distilled": "ok"}

    async def timed_out(model, messages, api_key, *, strict_local, masker=None):
        raise agent_draft.DistillFailed("ReadTimeout")

    monkeypatch.setattr(agent_draft, "distill", timed_out)
    draft = await sessions.agent_draft(session.id, me, db)
    assert draft["distilled"] == "failed" and draft["systemPrompt"] == ""
    assert (await sessions.agent_draft_decided(session.id, me, db))["distilled"] == "failed"

    async def nothing(model, messages, api_key, *, strict_local, masker=None):
        return [], {"inputTokens": 1, "outputTokens": 0}

    monkeypatch.setattr(agent_draft, "distill", nothing)
    assert (await sessions.agent_draft(session.id, me, db))["distilled"] == "empty"


async def test_the_distillation_has_its_own_timeout_and_one_retry(monkeypatch):
    """The call waits `agent_draft_timeout_sec` (not the title's), retries a timeout once,
    keeps strict-local's no-fallback rule, and raises when both tries fail."""
    import httpx

    from app.core.config import settings

    async def config():
        return "http://litellm", "k"

    monkeypatch.setattr(agent_draft.settings_store, "litellm_config", config)
    monkeypatch.setattr(settings, "agent_draft_timeout_sec", 45.0)
    seen: list[dict] = []
    replies = [httpx.ReadTimeout("slow"), {"choices": [{"message": {"content": "- 결론 먼저"}}],
                                           "usage": {"prompt_tokens": 3,
                                                     "completion_tokens": 2}}]
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        seen.append(_json.loads(request.content))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json=reply)

    def client(**kwargs):
        seen.append({"timeout": kwargs["timeout"]})
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(agent_draft.httpx, "AsyncClient", client)
    lines, spent = await agent_draft.distill("local/strict", ["팀장께 1쪽"], "k",
                                             strict_local=True)
    assert lines == ["결론 먼저"] and spent == {"inputTokens": 3, "outputTokens": 2}
    assert [x["timeout"] for x in seen if "timeout" in x] == [45.0, 45.0]
    assert all(x.get("disable_fallbacks") is True for x in seen if "model" in x)

    replies[:] = [httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow")]
    with pytest.raises(agent_draft.DistillFailed):
        await agent_draft.distill("cloud/big", ["팀장께 1쪽"], "k", strict_local=False)
    assert replies == []
