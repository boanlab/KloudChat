"""Super Agent through the real send path: model, effort, tools, strict-local and privacy.

The answer model, classifier HTTP and database are synthetic; routing, privacy and the SSE
path are the app's own."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
from test_privacy import _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import AuditEvent, User
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage, SessionCreate, SessionPatch
from app.services import settings_store
from app.services import super_agent as sa
from app.services.tools.base import Tool


def _model(model_id: str, *, boundary: str, strict: bool = False, cost: int = 0,
           privacy_only: bool = False) -> dict:
    return {
        "id": model_id, "label": model_id, "kinds": ["chat", "report", "slides"],
        "dataBoundary": boundary, "strictLocal": strict, "privacyOnly": privacy_only,
        "inputCreditCost": cost, "creditCost": cost, "contextWindow": 128_000,
        "supportsTools": True,
        "reasoning": {"efforts": ["none", "low", "high"], "default": "high",
                      "mandatory": False} if boundary == "external" else None,
    }


FLASH = _model("local/qwen3.8-flash", boundary="hybrid")
STRICT_FLASH = _model("strict-local/qwen3.8-flash", boundary="self_hosted", strict=True,
                      privacy_only=True)
STRICT_27B = _model("strict-local/qwen3.8-27b", boundary="self_hosted", strict=True,
                    privacy_only=True)
DEEPSEEK = _model("deepseek/deepseek-v4.1-flash", boundary="external", cost=2)
CATALOGUE = [FLASH, STRICT_FLASH, STRICT_27B, DEEPSEEK]


class _Db(_NoWriteDb):
    def __init__(self, session: ChatSession, user: User) -> None:
        super().__init__()
        self.session, self.user = session, user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def is_modified(self, _value):
        return False

    async def get(self, kind, key):
        if kind is ChatSession and key == self.session.id:
            return self.session
        if kind is User and key == self.user.id:
            return self.user
        return next((row for row in self.added if isinstance(row, kind) and row.id == key), None)


async def _turn(monkeypatch, text: str, *, mode: str = "super", history: list | None = None,
                policy: Governance | None = None, project: str | None = None,
                classify: str | None = None, web_search=False) -> dict:
    def no_network(*_args, **_kwargs):
        pytest.fail("unexpected network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)

    async def no_stored_settings(force: bool = False) -> dict:
        return {}

    monkeypatch.setattr(settings_store, "all_values", no_stored_settings)
    user = User(email="p@example.test", password_hash="h", name="P", monthly_credits=10_000)
    session = ChatSession(user_id=user.id, model="", routing_mode=mode, project_id=project)
    await _patch_guard_dependencies(monkeypatch, session=session, models=CATALOGUE, blocks=[])
    db = _Db(session, user)
    calls: list[dict] = []
    classifier_calls: list[str] = []

    async def history_rows(*_args, **_kwargs):
        return list(history or [])

    async def current(*_args, **_kwargs):
        return policy or Governance(external_data_guard=True,
                                    adaptive_classifier_model_id=STRICT_FLASH["id"])

    async def classify_stub(turn, *, model_id, user_id, api_key):
        classifier_calls.append(sa.classifier_payload(turn))
        if classify is None:
            return None
        return sa.Classified(classify, 0.9, 300, 8, 120)

    async def answer(model_id, messages, tools, _context, **kwargs):
        calls.append({"model": model_id, "tools": [t.name for t in tools],
                      "reasoning": sessions_router.agent_service.TURN_REASONING.get(),
                      **{k: kwargs.get(k) for k in ("disable_fallbacks",)}})
        yield {"type": "model_route", "routedModel": model_id, "actualModel": model_id}
        yield {"type": "delta", "text": "답입니다."}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 5}

    async def tools(*_args, **_kwargs):
        async def run(_arguments):
            return None
        return [Tool(name="web_search", label="search", description="d",
                     parameters={"type": "object", "properties": {}}, run=run)]

    async def writer(writer, **_kwargs):
        return writer

    async def title(*_args, **_kwargs):
        return None, {"inputTokens": 0, "outputTokens": 0}

    async def nothing(**_kwargs):
        return None

    async def key(*_args, **_kwargs):
        return "virtual-key"

    async def credentials(*_args, **_kwargs):
        return "http://answer.invalid", "answer-key"

    monkeypatch.setattr(sessions_router.answer_plan, "wanted", lambda *_args: False)
    monkeypatch.setattr(sessions_router, "_history", history_rows)
    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router.super_agent, "classify_with_model", classify_stub)
    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.litellm_service, "user_key", lambda _u: "virtual-key")
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router, "build_tools", tools)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", answer)
    monkeypatch.setattr(sessions_router, "_enrichment_model", writer)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions_router, "_enrich_memory", nothing)

    response = await sessions_router.send_message(
        session.id, SendMessage(content=text, web_search=web_search), _request(), user, db)
    if not hasattr(response, "body_iterator"):
        return {"response": response, "calls": calls, "db": db, "classifier": classifier_calls}
    events = [json.loads(chunk.removeprefix("data: ")) async for chunk in response.body_iterator]
    messages = [row for row in db.added if isinstance(row, Message)]
    answer_row = next(row for row in messages if row.role is Role.assistant)
    return {
        "events": events, "calls": calls, "db": db, "session": session,
        "route": answer_row.routing.get("superAgent"), "answer": answer_row,
        "classifier": classifier_calls,
        "audits": [r for r in db.added
                   if isinstance(r, AuditEvent) and r.action == "routing.super"],
    }


@pytest.mark.asyncio
async def test_a_greeting_runs_on_flash_without_tools_and_says_why(monkeypatch) -> None:
    out = await _turn(monkeypatch, "안녕하세요!")
    assert out["calls"][0]["model"] == FLASH["id"]
    assert out["calls"][0]["tools"] == []
    assert out["route"]["taskClass"] == "simple" and out["route"]["tools"] == "none"
    assert out["route"]["selectedModel"] == FLASH["id"]
    assert out["audits"] and out["audits"][0].event_metadata["taskClass"] == "simple"
    # the pick is the turn's, not the conversation's
    assert out["session"].model == "" and out["session"].routing_mode == RoutingMode.super
    assert out["events"][-1]["type"] == "done"


@pytest.mark.asyncio
async def test_a_hard_question_goes_up_with_high_effort(monkeypatch) -> None:
    out = await _turn(monkeypatch, "우리 DB를 MySQL에서 PostgreSQL로 옮길지 판단 기준과 위험을 "
                      "정리해 줘")
    call = out["calls"][0]
    assert call["model"] == DEEPSEEK["id"]
    assert call["reasoning"] == (DEEPSEEK["id"], {"reasoning": {"effort": "high"}})
    assert out["route"]["effort"] == "high" and out["route"]["taskClass"] == "reasoning"


@pytest.mark.asyncio
async def test_strict_entry_never_leaves_even_when_the_table_says_so(monkeypatch) -> None:
    policy = Governance(external_data_guard=True, super_agent_rules={
        "reasoning": {"strictModel": DEEPSEEK["id"]}})
    out = await _turn(monkeypatch, "마이크로서비스로 쪼갤지 판단해 줘. 위험도 같이.",
                      mode="super_strict", policy=policy)
    assert sa.strict_model(next(m for m in CATALOGUE if m["id"] == out["calls"][0]["model"]))
    assert out["route"]["mode"] == "super_strict"


@pytest.mark.asyncio
async def test_personal_data_picks_inside_and_skips_the_classifier(monkeypatch) -> None:
    out = await _turn(monkeypatch, "고객 연락처 010-2345-6789 로 사과 문자 초안 하나")
    assert out["calls"][0]["model"].startswith("strict-local/")
    assert out["route"]["strictReason"] == "personal_data"
    assert out["classifier"] == []


@pytest.mark.asyncio
async def test_project_material_keeps_the_turn_inside(monkeypatch) -> None:
    policy = Governance(external_data_guard=True, internal_data_strict_local=True)
    out = await _turn(monkeypatch, "이 설계를 MSA로 바꿀지 판단해 줘. 위험도 같이.", policy=policy,
                      project="p1")
    assert out["calls"][0]["model"].startswith("strict-local/")
    assert out["route"]["strictReason"] == "internal_data"


@pytest.mark.asyncio
async def test_ambiguous_turn_asks_the_classifier_with_a_short_payload(monkeypatch) -> None:
    out = await _turn(monkeypatch, "주말에 아이랑 갈 만한 실내 놀거리 알려줘", classify="calc")
    assert len(out["classifier"]) == 1 and len(out["classifier"][0]) < 600
    assert out["route"]["source"] == "classifier" and out["route"]["taskClass"] == "calc"
    assert out["route"]["classifierInputTokens"] == 300


@pytest.mark.asyncio
async def test_classifier_failure_keeps_the_rule_answer(monkeypatch) -> None:
    out = await _turn(monkeypatch, "주말에 아이랑 갈 만한 실내 놀거리 알려줘", classify=None)
    assert out["route"]["source"] == "rule"
    assert out["calls"][0]["model"] == FLASH["id"]


@pytest.mark.asyncio
async def test_short_follow_up_stays_on_the_same_model(monkeypatch) -> None:
    history = [
        Message(session_id="s", role=Role.user, content="MSA 전환 판단해 줘"),
        Message(session_id="s", role=Role.assistant, content="판단입니다.", routing={
            "superAgent": {"selectedModel": DEEPSEEK["id"], "taskClass": "reasoning"}}),
    ]
    out = await _turn(monkeypatch, "그럼 2단계는?", history=history)
    assert out["calls"][0]["model"] == DEEPSEEK["id"]
    assert out["route"]["source"] == "sticky" and out["route"]["pick"] == "sticky"


@pytest.mark.asyncio
async def test_asking_again_escalates_to_the_reasoning_row(monkeypatch) -> None:
    history = [
        Message(session_id="s", role=Role.user, content="안녕"),
        Message(session_id="s", role=Role.assistant, content="안녕하세요", routing={
            "superAgent": {"selectedModel": FLASH["id"], "taskClass": "simple"}}),
    ]
    out = await _turn(monkeypatch, "틀렸어. 더 자세히 다시 설명해 줘", history=history)
    assert out["route"]["source"] == "escalated" and out["route"]["effort"] == "high"
    assert out["calls"][0]["model"] == DEEPSEEK["id"]


class _SessionWriteDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value: object) -> None:
        return None


@pytest.mark.asyncio
async def test_super_modes_need_the_switch_and_a_routed_surface(monkeypatch) -> None:
    user = User(email="p@example.test", password_hash="h", name="P")
    switch = {"on": False}

    async def current(*_args, **_kwargs):
        return Governance(super_agent_enabled=switch["on"])

    async def enabled_kinds():
        return {"chat", "image"}

    async def links(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sessions_router.governance, "current", current)
    monkeypatch.setattr(sessions_router.settings_store, "enabled_kinds", enabled_kinds)
    monkeypatch.setattr(sessions_router, "_validate_session_links", links)
    db = _SessionWriteDb()
    with pytest.raises(Exception) as caught:
        await sessions_router.create_session(
            SessionCreate(kind=SessionKind.chat, routing_mode=RoutingMode.super), user, db)
    assert getattr(caught.value, "detail", None) == "super_agent_disabled"
    switch["on"] = True
    with pytest.raises(Exception) as caught:
        await sessions_router.create_session(
            SessionCreate(kind=SessionKind.image, routing_mode=RoutingMode.super_strict),
            user, db)
    assert getattr(caught.value, "detail", None) == "auto_routing_chat_only"
    created = await sessions_router.create_session(
        SessionCreate(kind=SessionKind.chat, routing_mode=RoutingMode.super_strict), user, db)
    assert created.routing_mode is RoutingMode.super_strict

    session = ChatSession(user_id=user.id, kind=SessionKind.chat, model="m")

    async def owned(*_args, **_kwargs):
        return session

    monkeypatch.setattr(sessions_router, "_owned", owned)
    switch["on"] = False
    with pytest.raises(Exception) as caught:
        await sessions_router.patch_session(
            session.id, SessionPatch(routing_mode=RoutingMode.super), user, db)
    assert getattr(caught.value, "detail", None) == "super_agent_disabled"
