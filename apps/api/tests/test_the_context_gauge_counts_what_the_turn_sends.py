"""The composer's context gauge (`routers.context_usage`) counts what the next turn sends: the
same envelope, cut and summary as `_send_message`, against the same window."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.config import settings
from app.models.chat import ChatSession, Message, Role, RoutingMode
from app.models.governance import Governance
from app.models.user import User
from app.routers import context_usage
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage
from app.services import conversation_summary, governance
from app.services.context import effective_window, envelope_tokens
from app.services.workspace_context import ContextBlock, WorkspaceContext

QUESTION = "이 회의록에서 다음 할 일만 정리해 줘."


def _model(model_id: str, *, window: int | None, boundary: str) -> dict:
    return {
        "id": model_id, "label": model_id, "kinds": ["chat"], "dataBoundary": boundary,
        "strictLocal": False, "privacyOnly": False, "inputCreditCost": 1, "creditCost": 1,
        "contextWindow": window, "supportsTools": False,
    }


LOCAL = _model("local/qwen", window=126_976, boundary="self_hosted")
EXTERNAL = _model("vendor/flash", window=None, boundary="external")


def _history(n: int, *, chars: int = 1_500) -> list[Message]:
    rows: list[Message] = []
    for i in range(n):
        rows.append(Message(id=f"q{i}", session_id="s1", role=Role.user,
                            content=f"회의록 {i} 정리해 줘.\n---\n" + "회의 내용 " * chars))
        rows.append(Message(id=f"a{i}", session_id="s1", role=Role.assistant,
                            content="| 결정 | 담당 |\n|---|---|\n" + "| 항목 | 이름 |\n" * 200,
                            model="local/qwen", usage={"inputTokens": 1_000 + i,
                                                       "outputTokens": 10}))
    return rows


KNOWLEDGE = WorkspaceContext(
    (
        ContextBlock("project.instructions", "# 프로젝트 지침\n항상 존댓말로, 표 먼저.", True),
        ContextBlock("project.knowledge", "# 프로젝트 자료\n## 계약서\n" + "조항 " * 4_000,
                     False),
        ContextBlock("files.report", "# 읽은 자료\n- 계약서: 전체", True),
    ),
    (),
)


class _Rows:
    def all(self):
        return []

    def first(self):
        return None


class _Db:
    def add(self, _row):
        pass

    async def exec(self, _query):
        return _Rows()

    async def get(self, *_args):
        return None

    async def commit(self):
        pass

    def is_modified(self, _row):
        return False


def _patch(monkeypatch, models: list[dict], rows: list[Message], session: ChatSession,
           workspace: WorkspaceContext) -> dict:
    captured: dict = {}
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")

    async def owned(*_args, **_kwargs):
        return session

    async def policy(*_args, **_kwargs):
        return Governance(pii_masking=False, external_data_guard=False,
                          allow_user_raw_external=True)

    async def catalogue(*_args, **_kwargs):
        return {"models": models}

    async def history(*_args, **_kwargs):
        return list(rows)

    async def context(*_args, **_kwargs):
        return workspace

    async def agent_settings(*_args, **_kwargs):
        return None, [], None

    async def nothing(*_args, **_kwargs):
        return None

    async def no_tools(*_args, **_kwargs):
        return []

    async def key(*_args, **_kwargs):
        return "virtual-key"

    async def enrichment(writer, **_kwargs):
        return writer

    async def summarize(_model, previous, turns, _key, *, pinned=None, language="ko", **kw):
        merged = conversation_summary.merge_pinned(pinned, [("이름", "한서윤")])
        return conversation_summary.Memory("앞선 회의록 정리", merged), {
            "inputTokens": 10, "outputTokens": 5}

    async def run_turn(**kwargs):
        captured.update(kwargs)
        yield sessions_router.chat_service.sse({"type": "done"})

    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(sessions_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions_router, "_history", history)
    monkeypatch.setattr(sessions_router, "assemble", context)
    monkeypatch.setattr(sessions_router, "agent_settings", agent_settings)
    monkeypatch.setattr(sessions_router, "_audit_policy", nothing)
    monkeypatch.setattr(sessions_router, "build_tools", no_tools)
    monkeypatch.setattr(sessions_router.litellm_service, "user_key", lambda _user: "key")
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_a, **_k: True)
    monkeypatch.setattr(sessions_router, "_enrichment_model", enrichment)
    monkeypatch.setattr(sessions_router.conversation_summary, "summarize", summarize)
    monkeypatch.setattr(sessions_router, "_run_turn", run_turn)
    return captured


def _user() -> User:
    return User(email="person@example.test", password_hash="hash", name="Person",
                monthly_credits=10**9)


async def _turn(user: User, session: ChatSession, captured: dict) -> int:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                       "client": ("127.0.0.1", 1)})
    response = await sessions_router.send_message(
        session.id, SendMessage(content=QUESTION), request, user, _Db())
    _ = [chunk async for chunk in response.body_iterator]
    return envelope_tokens(captured["messages"])


async def _gauge(user: User, session: ChatSession | None, **query) -> context_usage.ContextUsage:
    return await context_usage.usage(
        _Db(), user, context_usage.ContextQuery(
            session_id=session.id if session else None, draft=QUESTION, **query))


def _close(gauge: int, sent: int) -> bool:
    """Within 2%: the turn adds a few wording-driven rules the gauge leaves out."""
    return abs(gauge - sent) <= max(16, sent * 0.02)


@pytest.mark.asyncio
async def test_a_plain_chat_counts_what_the_turn_sends(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=LOCAL["id"])
    rows = _history(2)
    captured = _patch(monkeypatch, [LOCAL], rows, session, WorkspaceContext((), ()))
    gauge = await _gauge(user, session)
    sent = await _turn(user, session, captured)
    assert _close(gauge.used, sent), (gauge.used, sent)
    assert gauge.window == 126_976 and not gauge.foreign_tokenizer
    assert sum(gauge.parts.values()) == gauge.used
    assert gauge.parts["history"] > 0 and gauge.parts["draft"] > 0
    assert gauge.parts["files"] == 0 and not gauge.summarised
    assert gauge.last_turn is not None and gauge.last_turn.input_tokens == 1_001
    assert gauge.headroom > 0


@pytest.mark.asyncio
async def test_a_project_with_knowledge_counts_its_files_and_instructions(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=LOCAL["id"], project_id="p1")
    captured = _patch(monkeypatch, [LOCAL], _history(1), session, KNOWLEDGE)
    gauge = await _gauge(user, session)
    sent = await _turn(user, session, captured)
    assert _close(gauge.used, sent), (gauge.used, sent)
    assert gauge.parts["files"] > 2_000
    assert gauge.parts["instructions"] > 0
    assert sum(gauge.parts.values()) == gauge.used


@pytest.mark.asyncio
async def test_a_new_conversation_shows_its_base_cost(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=LOCAL["id"])
    _patch(monkeypatch, [LOCAL], [], session, KNOWLEDGE)
    gauge = await context_usage.usage(_Db(), user, context_usage.ContextQuery(
        project_id="p1", model=LOCAL["id"]))
    assert gauge.parts["history"] == 0 and gauge.parts["draft"] == 0
    assert gauge.parts["files"] > 0 and gauge.used > gauge.parts["files"]
    assert gauge.last_turn is None and not gauge.summarised


@pytest.mark.asyncio
async def test_a_long_conversation_reports_its_summary_and_matches_the_turn(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=EXTERNAL["id"])
    rows = _history(40)
    captured = _patch(monkeypatch, [EXTERNAL], rows, session, WorkspaceContext((), ()))
    before = await _gauge(user, session)
    # past the window before any summary exists: the gauge already says older turns go
    assert before.summarised and before.summarised_messages > 2
    first = await _turn(user, session, captured)
    assert session.summary and session.summary.get("text")
    gauge = await _gauge(user, session)
    assert gauge.summarised and gauge.summarised_messages == before.summarised_messages
    assert gauge.parts["memory"] > 0
    assert _close(gauge.used, first), (gauge.used, first)
    assert gauge.used <= gauge.window


@pytest.mark.asyncio
async def test_an_external_models_window_is_taken_smaller_than_a_self_hosted_ones(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=LOCAL["id"])
    _patch(monkeypatch, [LOCAL, EXTERNAL], _history(1), session, WorkspaceContext((), ()))
    local = await _gauge(user, session, model=LOCAL["id"])
    external = await _gauge(user, session, model=EXTERNAL["id"])
    assert local.window == effective_window(LOCAL) == 126_976
    assert external.stated_window == settings.default_context_window
    assert external.window == int(settings.default_context_window / 1.5)
    assert external.foreign_tokenizer and not local.foreign_tokenizer
    assert external.reserve <= local.reserve or external.window < local.window


@pytest.mark.asyncio
async def test_under_auto_the_window_is_the_last_answering_models(monkeypatch):
    user = _user()
    session = ChatSession(id="s1", user_id=user.id, model=EXTERNAL["id"],
                          routing_mode=RoutingMode.auto)
    _patch(monkeypatch, [LOCAL, EXTERNAL], _history(1), session, WorkspaceContext((), ()))
    gauge = await _gauge(user, session)
    assert gauge.routed and gauge.routed_basis == "last"
    assert gauge.model == LOCAL["id"]
    fresh = ChatSession(id="s1", user_id=user.id, model=EXTERNAL["id"],
                        routing_mode=RoutingMode.auto)
    _patch(monkeypatch, [LOCAL, EXTERNAL], [], fresh, WorkspaceContext((), ()))
    gauge = await _gauge(user, fresh)
    assert gauge.routed_basis == "default" and gauge.model == EXTERNAL["id"]


def test_the_route_answers_in_camel_case():
    out = context_usage.ContextUsage(
        model="m", stated_window=1, window=1, foreign_tokenizer=False, reserve=1, used=1,
        parts={}, summarised=False, summarised_messages=0, headroom=0,
        last_turn=context_usage.LastTurn(input_tokens=3), routed=False,
    ).model_dump(by_alias=True)
    assert {"statedWindow", "summarisedMessages", "lastTurn", "routedBasis"} <= set(out)
    assert out["lastTurn"]["inputTokens"] == 3
