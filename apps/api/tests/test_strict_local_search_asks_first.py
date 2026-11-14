"""A strict-local turn that needs the web shows the queries that would leave and asks; only
those queries leave, read by the local model; a refusal, a policy or a withheld query is said."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_privacy import _patch_guard_dependencies, _rrn, _workspace
from test_side_calls_are_billed import _TurnDb

from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.governance import Governance
from app.models.user import AuditEvent, User
from app.models.workspace import Agent, Artifact, Project, Skill, StoredFile
from app.routers import sessions
from app.schemas.chat import SearchConsentAnswer
from app.services import research, search_consent
from app.services.tools import builtin
from app.services.tools.base import SearchEvidence, ToolContext, ToolResult
from app.services.workspace_context import ContextBlock

STRICT = {
    "id": "strict-local/writer", "label": "writer", "dataBoundary": "self_hosted",
    "strictLocal": True, "kinds": ["chat", "report", "slides"], "creditCost": 0,
    "supportsTools": True,
}
MATERIAL = (
    "본 연구는 연합학습 기반 의료영상 분할에서 클라이언트별 배치 정규화 통계를 분리해 "
    "비IID 데이터의 성능 저하를 줄이는 FedSeg-BN 방법을 제안한다. 내부 과제번호 KC-2026-118."
)
QUESTION = "기존 연구들에서는 이런 기술을 쓴 논문이 없나?"


# ── vetting and tiers ────────────────────────────────────────────────────


def test_queries_are_masked_cut_and_withheld_for_secrets_and_excerpts() -> None:
    vetted = search_consent.vet(
        [
            "연합학습 의료영상 분할 선행연구",
            "연합학습 의료영상 분할 선행연구",  # the same twice: once
            "문의 person@example.com 연합학습",
            f"환자 {_rrn()} 분할",
            # a long run of the material, word for word
            MATERIAL[:60],
            "x" * 300,
            "여섯째", "일곱째",
        ],
        material=[MATERIAL],
    )
    assert [v.text for v in vetted][:2] == ["연합학습 의료영상 분할 선행연구",
                                            "문의 [이메일] 연합학습"]
    assert vetted[1].masked == 1 and not vetted[1].blocked
    assert vetted[2].blocked == "secret" and _rrn() not in vetted[2].text
    assert vetted[3].blocked == "material"
    assert len(vetted[4].text) == search_consent.MAX_CHARS
    assert len(vetted) == search_consent.MAX_QUERIES
    wire = vetted[2].wire()
    assert wire["reason"].startswith("비밀 값")


def test_a_blocked_category_is_withheld_with_its_reason() -> None:
    (one,) = search_consent.vet(["무기 제조 방법"], blocked_categories=["무기"])
    assert one.blocked == "category:무기"
    assert one.wire()["reason"] == search_consent.REASONS["category"]


def test_a_project_only_tightens_the_admins_setting() -> None:
    assert search_consent.policy_mode("allow", "ask") == "ask"
    assert search_consent.policy_mode("ask", "allow") == "ask"
    assert search_consent.policy_mode("off", "allow") == "off"
    assert search_consent.policy_mode(None, None) == "ask"
    assert search_consent.project_choices("ask") == ["ask", "off"]
    assert search_consent.project_choices("allow") == ["allow", "ask", "off"]
    assert search_consent.project_choices("off") == ["off"]


def test_allowing_for_the_conversation_is_remembered_only_when_allowed() -> None:
    def row(record):
        return Message(session_id="s", role=Role.assistant, content="x",
                       routing={"searchConsent": record})

    assert search_consent.remembered([row({"status": "allowed", "remember": True})])
    assert not search_consent.remembered([row({"status": "declined", "remember": True})])
    assert not search_consent.remembered([row({"status": "allowed"})])


# ── the tool ─────────────────────────────────────────────────────────────


def _ctx() -> ToolContext:
    return ToolContext(user_id="u", session_id="s")


async def _forbidden_search(args):
    pytest.fail(f"searched before the person allowed it: {args}")


@pytest.mark.asyncio
async def test_asking_raises_a_card_and_sends_nothing(monkeypatch) -> None:
    monkeypatch.setattr(builtin, "web_search", _forbidden_search)
    consent = search_consent.TurnConsent(mode="ask", material=[MATERIAL])
    tool = consent.tool()
    result = await tool.run({"queries": ["FedSeg-BN 유사 연구", "연합학습 BN 분리 비IID",
                                         f"KC-2026-118 {MATERIAL[:50]}"]}, _ctx())
    assert result.final_text == search_consent.PAUSE_TEXT
    card = consent.record(by="u")
    assert card["status"] == "pending"
    assert [q["text"] for q in card["queries"]][:2] == ["FedSeg-BN 유사 연구",
                                                       "연합학습 BN 분리 비IID"]
    assert card["queries"][2]["blocked"] == "material"
    assert consent.note() == ""


@pytest.mark.asyncio
async def test_allowed_queries_alone_go_to_the_web_search(monkeypatch) -> None:
    sent: list[dict] = []

    async def web_search(args):
        sent.append(args)
        return ToolResult(
            content=f"'{args['query']}' 검색 결과:\n\n[1] 논문\nhttps://arxiv.org/abs/1\n요약",
            search_evidence=SearchEvidence(("https://arxiv.org/abs/1",)),
        )

    monkeypatch.setattr(builtin, "web_search", web_search)
    consent = search_consent.TurnConsent(mode="allow", source="policy", material=[MATERIAL])
    ctx = _ctx()
    result = await consent.tool().run(
        {"queries": ["연합학습 BN 분리", "문의 person@example.com"]}, ctx)
    assert sent == [{"query": "연합학습 BN 분리"}, {"query": "문의 [이메일]"}]
    assert "arxiv.org" in result.content and result.search_evidence is not None
    assert ctx.tool_calls["web_search"] == 2
    record = consent.record(by="u")
    assert record["status"] == "auto" and record["searches"] == 2
    assert record["queries"] == ["연합학습 BN 분리", "문의 [이메일]"]


def test_the_decision_is_settled_on_the_server_not_the_browser() -> None:
    card = {"status": "pending", "queries": [{"text": "연합학습 BN 분리", "masked": 0},
                                             {"text": "x", "masked": 0, "blocked": "secret"}]}
    kwargs = dict(card=card, backend=True, user_id="u", message_id="m", material=[MATERIAL])
    as_is = search_consent.decide(SearchConsentAnswer(message_id="m", decision="allow"),
                                  mode="ask", **kwargs)
    assert (as_is.status, as_is.queries) == ("allowed", ["연합학습 BN 분리"])
    edited = search_consent.decide(
        SearchConsentAnswer(message_id="m", decision="allow", remember=True,
                            queries=["sk-abcdefghijklmnopqrstuvwxyz123456 찾아줘"]),
        mode="ask", **kwargs)
    assert edited.status == "blocked" and edited.queries == [] and not edited.remember
    denied = search_consent.decide(SearchConsentAnswer(message_id="m", decision="deny"),
                                   mode="ask", **kwargs)
    assert denied.status == "declined"
    # the admin turned it off between the card and the click
    off = search_consent.decide(SearchConsentAnswer(message_id="m", decision="allow"),
                                mode="off", **kwargs)
    assert off.status == "unavailable"


def test_material_only_is_said_whenever_the_web_was_wanted_but_not_used() -> None:
    declined = search_consent.TurnConsent(mode="ask")
    declined.decision = search_consent.decide(
        SearchConsentAnswer(message_id="m", decision="deny"), card={}, mode="ask", backend=True,
        user_id="u", message_id="m")
    assert "외부 검색 없이 자료만으로" in declined.note()
    off = search_consent.TurnConsent(mode="off", needs_outside=True)
    assert "외부 검색 없이" in off.note() and off.record(by="u")["status"] == "unavailable"
    # offered, not needed, not asked: nothing to say
    quiet = search_consent.TurnConsent(mode="ask")
    quiet.offered = True
    assert quiet.note() == "" and quiet.record(by="u")["status"] == "none"
    assert search_consent.needs_outside(QUESTION)
    assert not search_consent.needs_outside("이 논문의 3장을 요약해 줘")


# ── the turn ─────────────────────────────────────────────────────────────


async def _turn(monkeypatch, consent, run_turn) -> tuple[_TurnDb, list[dict]]:
    session = ChatSession(id="session-1", user_id="user-1")
    user = User(id="user-1", email="person@example.test", password_hash="hash", name="Person")
    db = _TurnDb(session, user)

    async def nothing(**_kwargs):
        return None

    async def title(*_args, **_kwargs):
        db.titled = True
        return "제목", {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(sessions, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions, "_enrich_memory", nothing)
    db.titled = False
    monkeypatch.setattr(sessions.chat_service, "generate_title", title)
    events = []
    async for chunk in sessions._run_turn(
        user_id=user.id, api_key="key", auto_memory=False, session_id=session.id,
        model=STRICT, messages=[{"role": "user", "content": QUESTION}],
        tools=[consent.tool()] if consent.offers else [],
        first_user_message=QUESTION, is_first_turn=True,
        routing={"action": "internal_strict_local", "requestedModels": [STRICT["id"]],
                 "effectiveModels": [STRICT["id"]]},
        consent=consent,
    ):
        events.extend(json.loads(line[6:]) for line in chunk.splitlines()
                      if line.startswith("data: "))
    return db, events


def _calls_tool(arguments: dict):
    async def run_turn(_model, _messages, tools, ctx, **_kwargs):
        tool = next(t for t in tools if t.name == search_consent.TOOL_NAME)
        result = await tool.run(arguments, ctx)
        yield {"type": "delta", "text": result.final_text or "기존 연구로는 FedAvg가 있습니다 [1]."}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 5}

    return run_turn


@pytest.mark.asyncio
async def test_a_paused_turn_stores_the_card_on_its_answer(monkeypatch) -> None:
    monkeypatch.setattr(builtin, "web_search", _forbidden_search)
    consent = search_consent.TurnConsent(mode="ask", material=[MATERIAL])
    db, events = await _turn(monkeypatch, consent,
                             _calls_tool({"queries": ["연합학습 BN 분리 선행연구"]}))
    answer = next(row for row in db.added if isinstance(row, Message))
    assert answer.content == search_consent.PAUSE_TEXT
    assert answer.routing["searchConsent"]["status"] == "pending"
    assert answer.routing["action"] == "internal_strict_local"
    audit = next(row for row in db.added if isinstance(row, AuditEvent))
    assert audit.action == "search.consent_asked"
    assert audit.event_metadata["queries"] == ["연합학습 BN 분리 선행연구"]
    assert any(e.get("searchConsent", {}).get("status") == "pending" for e in events)
    # a card is not an answer: no title, no finish checks
    assert db.titled is False


@pytest.mark.asyncio
async def test_an_allowed_card_runs_its_queries_and_records_who_and_what(monkeypatch) -> None:
    sent: list[str] = []

    async def web_search(args):
        sent.append(args["query"])
        return ToolResult(content="[1] FedAvg\nhttps://arxiv.org/abs/1602.05629\n요약",
                          search_evidence=SearchEvidence(("https://arxiv.org/abs/1602.05629",)))

    monkeypatch.setattr(builtin, "web_search", web_search)
    consent = search_consent.TurnConsent(mode="ask", material=[MATERIAL])
    consent.decision = search_consent.decide(
        SearchConsentAnswer(message_id="paused-1", decision="allow", remember=True,
                            queries=["연합학습 배치정규화 분리"]),
        card={"queries": []}, mode="ask", backend=True, user_id="user-1",
        message_id="paused-1", material=[MATERIAL])
    # the model's own arguments do not widen what was allowed
    db, _ = await _turn(monkeypatch, consent, _calls_tool({"queries": ["다른 검색어"]}))
    assert sent == ["연합학습 배치정규화 분리"]
    answer = next(row for row in db.added if isinstance(row, Message))
    record = answer.routing["searchConsent"]
    assert (record["status"], record["queries"], record["searches"]) == (
        "allowed", ["연합학습 배치정규화 분리"], 1)
    assert record["decidedBy"] == "user-1" and record["remember"] is True
    assert "외부 검색 없이" not in answer.content
    audit = next(row for row in db.added if isinstance(row, AuditEvent))
    assert audit.action == "search.consent_allowed"
    assert audit.event_metadata["pausedMessageId"] == "paused-1"
    assert audit.event_metadata["queries"] == ["연합학습 배치정규화 분리"]


@pytest.mark.asyncio
async def test_material_only_is_said_under_the_answer(monkeypatch) -> None:
    consent = search_consent.TurnConsent(mode="ask")
    consent.decision = search_consent.decide(
        SearchConsentAnswer(message_id="paused-1", decision="deny"), card={}, mode="ask",
        backend=True, user_id="user-1", message_id="paused-1")

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": "자료의 관련 연구 절에는 FedBN이 나옵니다."}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 5}

    db, events = await _turn(monkeypatch, consent, run_turn)
    answer = next(row for row in db.added if isinstance(row, Message))
    assert answer.content.endswith("_외부 검색 없이 자료만으로 답했습니다._")
    assert answer.routing["searchConsent"]["status"] == "declined"
    assert any(e.get("text", "").strip() == "_외부 검색 없이 자료만으로 답했습니다._"
               for e in events if e.get("type") == "delta")
    # sent back as history, the note is the server's, not the model's
    stripped = sessions.server_notes.strip(answer.content)
    assert stripped == "자료의 관련 연구 절에는 FedBN이 나옵니다."


# ── through the endpoint (SQLite; two requests may land on different replicas) ──


@pytest.fixture
async def engine():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                metadata.create_all,
                tables=[metadata.tables[m.__tablename__] for m in (
                    User, Agent, ChatSession, Message, StoredFile, PipelineRun, Skill, Artifact,
                    Project, AuditEvent)],
            )
        yield engine
    finally:
        await engine.dispose()


async def _app(monkeypatch, engine, *, kind=SessionKind.chat, admin="ask", project=None,
               history: list[Message] | None = None):
    user = User(id="u1", email="u1@example.test", name="U", password_hash="x")
    proj = Project(id="p1", user_id=user.id, name="P", strict_local_web_search=project)
    session = ChatSession(id="s1", user_id=user.id, kind=kind, title="T", model=STRICT["id"],
                          project_id=proj.id)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([user, proj, session, *(history or [])])
        await db.commit()
    real_owned, real_history = sessions._owned, sessions._history
    await _patch_guard_dependencies(
        monkeypatch, session=session, models=[STRICT],
        blocks=[ContextBlock(source="project.knowledge", text=MATERIAL, trusted=False)])
    monkeypatch.setattr(sessions, "_owned", real_owned)
    monkeypatch.setattr(sessions, "_history", real_history)

    async def policy(*_args, **_kwargs):
        return Governance(external_data_guard=True, strict_local_web_search=admin)

    async def tools_config():
        return SimpleNamespace(search="http://search.invalid", fetch="http://fetch.invalid",
                               exec="")

    async def build_tools(*_args, **_kwargs):
        return []

    async def credentials(*_args, **_kwargs):
        return "http://fixture.invalid", "fixture-only"

    async def ensure_key(*_args, **_kwargs):
        return "fixture-only"

    async def assemble(*_args, **_kwargs):
        return _workspace([ContextBlock(source="project.knowledge", text=MATERIAL,
                                        trusted=False)])

    monkeypatch.setattr(sessions.governance, "current_for_egress", policy)
    monkeypatch.setattr(search_consent.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(research.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(sessions, "assemble", assemble)
    monkeypatch.setattr(sessions, "build_tools", build_tools)
    monkeypatch.setattr(sessions, "has_headroom", lambda *args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "SessionLocal",
                        lambda: AsyncSession(engine, expire_on_commit=False))
    seen: list[dict] = []

    async def run(**kwargs):
        seen.append({**kwargs, "allowed": research.ALLOWED.get()})
        yield sessions.chat_service.sse({"type": "delta", "text": "답"})
        yield sessions.chat_service.sse({"type": "done"})

    for name in ("_run_turn", "_run_report", "_run_deck"):
        monkeypatch.setattr(sessions, name, run)

    async def db_session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app = FastAPI()
    app.include_router(sessions.router)
    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[current_user] = lambda: user
    return app, seen


def _paused_pair(card_queries=("연합학습 BN 분리 선행연구",)) -> list[Message]:
    question = Message(id="q1", session_id="s1", role=Role.user, content=QUESTION,
                       routing={"turnOptions": {"webSearch": "auto"}})
    paused = Message(
        id="a1", session_id="s1", role=Role.assistant, content=search_consent.PAUSE_TEXT,
        routing={"action": "internal_strict_local", "searchConsent": {
            "status": "pending", "queries": [{"text": q, "masked": 0} for q in card_queries]}})
    return [question, paused]


@pytest.mark.asyncio
async def test_a_strict_local_turn_is_offered_the_ask_not_the_search(monkeypatch, engine):
    app, seen = await _app(monkeypatch, engine)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        response = await client.post("/sessions/s1/messages",
                                     json={"content": QUESTION, "webSearch": "auto"})
    assert response.status_code == 200, response.text
    (turn,) = seen
    names = [t.name for t in turn["tools"]]
    assert search_consent.TOOL_NAME in names and "web_search" not in names
    assert turn["consent"].mode == "ask" and turn["consent"].decision is None
    system = turn["messages"][0]["content"]
    assert "request_web_search" in system


@pytest.mark.asyncio
async def test_off_offers_nothing_and_says_so(monkeypatch, engine):
    app, seen = await _app(monkeypatch, engine, admin="allow", project="off")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        response = await client.post("/sessions/s1/messages",
                                     json={"content": QUESTION, "webSearch": "auto"})
    assert response.status_code == 200, response.text
    consent = seen[0]["consent"]
    assert search_consent.TOOL_NAME not in [t.name for t in seen[0]["tools"]]
    assert consent.mode == "off" and consent.needs_outside
    assert "외부 검색 없이" in consent.note()


@pytest.mark.asyncio
async def test_the_card_is_answered_by_rerunning_its_question(monkeypatch, engine):
    app, seen = await _app(monkeypatch, engine, history=_paused_pair())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        response = await client.post("/sessions/s1/messages", json={
            "content": "ignored", "retryOf": "q1",
            "searchConsent": {"messageId": "a1", "decision": "allow"}})
        assert response.status_code == 200, response.text
        # answered once: the same card cannot be answered again (another tab, another replica)
        again = await client.post("/sessions/s1/messages", json={
            "content": "ignored", "retryOf": "q1",
            "searchConsent": {"messageId": "a1", "decision": "allow"}})
    assert again.status_code == 409 and again.json()["detail"] == "search_consent_stale"
    (turn,) = seen
    consent = turn["consent"]
    assert consent.decision.status == "allowed"
    assert consent.decision.queries == ["연합학습 BN 분리 선행연구"]
    assert turn["preset_call"] == (search_consent.TOOL_NAME, {})
    assert turn["superseded_ids"] is None  # the card is no earlier answer
    async with AsyncSession(engine) as db:
        rows = (await db.exec(select(Message).where(Message.session_id == "s1"))).all()
    assert [r.id for r in rows] == ["q1"]


@pytest.mark.asyncio
async def test_an_edited_query_carrying_a_secret_is_not_sent(monkeypatch, engine):
    app, seen = await _app(monkeypatch, engine, history=_paused_pair())
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        response = await client.post("/sessions/s1/messages", json={
            "content": "ignored", "retryOf": "q1",
            "searchConsent": {"messageId": "a1", "decision": "allow",
                              "queries": [f"환자 {_rrn()} 연합학습"]}})
    assert response.status_code == 200, response.text
    consent = seen[0]["consent"]
    assert consent.decision.status == "blocked" and not consent.offers
    assert search_consent.TOOL_NAME not in [t.name for t in seen[0]["tools"]]
    assert consent.record(by="u1")["withheld"][0]["blocked"] == "secret"


@pytest.mark.asyncio
async def test_a_report_asks_before_its_research_and_then_sends_only_that(monkeypatch, engine):
    planned = ["연합학습 의료영상 분할 동향", "FedBN 배치정규화"]

    async def plan(request, model, api_key, **_kwargs):
        return planned, {"inputTokens": 5, "outputTokens": 5}

    monkeypatch.setattr(research, "plan", plan)
    app, seen = await _app(monkeypatch, engine, kind=SessionKind.report)
    request = "연합학습 의료영상 분할의 최신 연구 동향 보고서를 써 줘"
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        response = await client.post("/sessions/s1/messages",
                                     json={"content": request, "webSearch": "auto"})
        assert response.status_code == 200, response.text
        assert '"type": "search_consent"' in response.text and not seen
        async with AsyncSession(engine) as db:
            (card,) = (await db.exec(
                select(Message).where(Message.role == Role.assistant))).all()
            question = (await db.exec(select(Message).where(Message.role == Role.user))).one()
        assert [q["text"] for q in card.routing["searchConsent"]["queries"]] == planned
        response = await client.post("/sessions/s1/messages", json={
            "content": request, "retryOf": question.id,
            "searchConsent": {"messageId": card.id, "decision": "allow",
                              "queries": [planned[0]]}})
    assert response.status_code == 200, response.text
    (turn,) = seen
    assert turn["web_search"] is True
    assert turn["allowed"].queries == (planned[0],)
    assert turn["routing"]["searchConsent"]["status"] == "allowed"


@pytest.mark.asyncio
async def test_research_sends_only_the_allowed_queries_once(monkeypatch) -> None:
    searched: list[str] = []

    async def tools_config():
        return SimpleNamespace(search="http://search.invalid", fetch="", exec="")

    async def gather(_base, query, **_kwargs):
        searched.append(query)
        return []

    async def no_plan(*_args, **_kwargs):
        pytest.fail("research planned its own queries")

    monkeypatch.setattr(research.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(research, "_gather", gather)
    monkeypatch.setattr(research, "_plan", no_plan)
    token = research.ALLOWED.set(research.Allowed(("연합학습 BN",)))
    try:
        await research.run("한국 연합학습 연구 동향", model="m", api_key="k")
        await research.run("다른 섹션", model="m", api_key="k")
        assert searched == ["연합학습 BN"]
        research.ALLOWED.set(research.Allowed(()))
        assert (await research.run("아무거나", model="m", api_key="k")).searched is False
        assert searched == ["연합학습 BN"]
    finally:
        research.ALLOWED.reset(token)


@pytest.mark.asyncio
async def test_a_project_setting_looser_than_the_admins_is_refused(monkeypatch, engine):
    from app.routers import workspace

    user = User(id="u1", email="u1@example.test", name="U", password_hash="x")
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([user, Project(id="p1", user_id=user.id, name="P")])
        await db.commit()

    async def current(force=False):
        return Governance(strict_local_web_search="ask")

    monkeypatch.setattr(workspace.governance_service, "current", current)

    async def db_session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app), base_url="http://t") as client:
        looser = await client.patch("/projects/p1", json={"strictLocalWebSearch": "allow"})
        stricter = await client.patch("/projects/p1", json={"strictLocalWebSearch": "off"})
        back = await client.patch("/projects/p1", json={"strictLocalWebSearch": None})
    assert looser.status_code == 422
    assert looser.json()["detail"] == "strict_local_web_search_looser_than_admin"
    assert stricter.status_code == 200 and stricter.json()["strictLocalWebSearch"] == "off"
    assert back.status_code == 200 and back.json()["strictLocalWebSearch"] is None
