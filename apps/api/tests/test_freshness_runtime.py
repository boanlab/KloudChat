"""Current-state display limits preserve permissions and usage, not certify answer truth."""

import json

import pytest
from fastapi.responses import StreamingResponse
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, RoutingMode, SessionKind
from app.models.user import AuditEvent, User
from app.routers import sessions
from app.schemas.chat import CompareRequest, SendMessage
from app.services import agent
from app.services.context import search_plan
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

QUESTION = "현재 대한민국 대통령은 누구야?"
_PAST_OUTPUT = (
    "- 과거 사실: 2022년에 기존 담당자가 취임했다.\n- 현재 상태: UNSUPPORTED_CURRENT_CLAIM"
)
_PAST_RENDERED = (
    "- 학습지식의 과거 정보(미검증): 2022년에 기존 담당자가 취임했다.\n"
    "- 현재 상태: 현재 상태는 이번 요청에서 확인하지 못했습니다."
)


@pytest.mark.parametrize("toggle", [False, "auto", True])
@pytest.mark.parametrize(
    "question",
    [
        "웹 검색 없이 현재 대한민국 대통령은 누구야?",
        "현재 대한민국 대통령은 누구야? 검색하지 마.",
        "Do not search the web. Who is the current president of Korea?",
        "Who is the current president of Korea? Without web search.",
    ],
)
def test_explicit_search_refusal_overrides_toggle(toggle, question):
    assert search_plan(toggle, question) == (False, None)


@pytest.mark.parametrize(
    "question",
    [
        'Translate "Do not search the web" into Korean. '
        "Then look up the current president of Korea.",
        '"웹 검색 없이"라는 문장을 영어로 번역하고, 현재 대한민국 대통령을 검색해서 알려줘.',
        '다음 문장을 요약해 줘: "검색하지 마". 현재 대통령은 검색해서 알려줘.',
    ],
)
def test_search_opt_out_in_a_quoted_transform_source_is_not_user_policy(question):
    assert search_plan(False, question) == (True, "web_search")


@pytest.mark.parametrize(
    "question",
    [
        '"웹 검색 없이"라는 정책을 지켜서 현재 대한민국 대통령은 누구야?',
        'Follow this rule: "Do not search the web". Who is the current president of Korea?',
        'Do not translate "Do not search the web". '
        "Follow that rule and answer who is president now.",
        '"검색하지 마"를 번역하지 말고 내 규칙으로 삼아. 현재 대한민국 대통령은 누구야?',
        'Translate "Do not search the web" into Korean. '
        "Do not search the web for the current president.",
    ],
)
def test_quoted_user_policy_and_real_opt_out_stay_authoritative(question):
    assert search_plan(True, question) == (False, None)


@pytest.mark.parametrize(
    "question",
    [
        'Translate "Do not search the web" into Korean.',
        'Translate "Search the web" into Korean.',
        '"웹 검색 없이"라는 문장을 영어로 번역해 줘.',
    ],
)
def test_transform_source_alone_does_not_opt_in_to_external_search(question):
    assert search_plan(False, question) == (False, None)


async def _events(response):
    return [
        json.loads(chunk.removeprefix("data: ").strip()) async for chunk in response.body_iterator
    ]


def _capture_normal_route(monkeypatch):
    captured = {}

    async def run(**kwargs):
        captured.update(kwargs)
        yield sessions.chat_service.sse({"type": "done"})

    async def key(*_args):
        return None

    async def credentials(*_args):
        return "synthetic-origin", "synthetic-noncredential"

    monkeypatch.setattr(sessions, "_run_turn", run)
    monkeypatch.setattr(sessions, "_run_comparison", run)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    return captured


class _RouteDb(_NoWriteDb):
    def is_modified(self, _row):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(RoutingMode))
@pytest.mark.parametrize("unavailable", ["off", "strict", "no_tools", "allowlist"])
@pytest.mark.parametrize(
    "question",
    [
        QUESTION,
        "오늘 서울 날씨는 어때?",
        "React의 최신 버전은 무엇이야?",
        "이 물리 현상의 원리를 설명해줘",
        "1990년 대한민국의 대통령은 누구였어?",
    ],
)
async def test_unavailable_verification_keeps_best_effort_route_and_permissions(
    monkeypatch, mode, unavailable, question
):
    user = User(email="synthetic@example.test", password_hash="hash", name="Synthetic")
    model = {**_external_model("synthetic/qwen"), "supportsTools": unavailable != "no_tools"}
    if unavailable == "strict":
        model.update(strictLocal=True, dataBoundary="self_hosted", creditCost=0)
    session = ChatSession(user_id=user.id, model=model["id"], routing_mode=mode)
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    captured = _capture_normal_route(monkeypatch)

    async def no_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(sessions, "build_tools", no_tools)
    if unavailable == "allowlist":

        async def restricted(*_args, **_kwargs):
            return None, ["calculate"], None

        monkeypatch.setattr(sessions, "agent_settings", restricted)
    db = _RouteDb()
    response = await sessions.send_message(
        session.id,
        SendMessage(content=question, web_search=unavailable != "off"),
        _request(),
        user,
        db,
    )
    assert isinstance(response, StreamingResponse)
    events = await _events(response)
    assert captured["model"]["id"] == model["id"]
    assert captured["tools"] == []
    assert captured["preset_call"] is None
    assert captured["force_tool"] is None
    assert not any(event["type"] == "freshness_abstention" for event in events)
    assert [row.content for row in db.added if isinstance(row, Message)] == [question]
    if unavailable == "strict":
        assert captured["model"]["strictLocal"] is True


@pytest.mark.asyncio
async def test_comparison_reaches_its_models_for_current_facts(monkeypatch):
    user = User(email="synthetic@example.test", password_hash="hash", name="Synthetic")
    session = ChatSession(user_id=user.id)
    models = [_external_model("synthetic/one"), _external_model("synthetic/two")]
    await _patch_guard_dependencies(monkeypatch, session=session, models=models, blocks=[])
    captured = _capture_normal_route(monkeypatch)
    db = _RouteDb()
    response = await sessions.compare_models(
        session.id,
        CompareRequest(content=QUESTION, models=[m["id"] for m in models]),
        _request(),
        user,
        db,
    )
    assert isinstance(response, StreamingResponse)
    await _events(response)
    assert [model["id"] for model in captured["models"]] == [model["id"] for model in models]
    assert [row.content for row in db.added if isinstance(row, Message)] == [QUESTION]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "empty", "blank", "success"])
async def test_optional_lookup_precedes_model_and_failed_lookup_keeps_only_dated_past(
    monkeypatch, outcome
):
    calls = []

    async def lookup(_arguments):
        calls.append("search")
        return ToolResult(
            content="" if outcome == "blank" else "Synthetic current source: OFFICIAL_MARKER",
            failed=outcome == "failed",
            empty=outcome == "empty",
            search_evidence=(
                SearchEvidence(("https://example.test/source",)) if outcome == "success" else None
            ),
        )

    async def completion(_model, messages, *_args, **_kwargs):
        calls.append("model")
        if outcome == "success":
            assert "OFFICIAL_MARKER" in json.dumps(messages)
        acc = agent._Accumulator()
        acc.content = ["GROUNDED_MOCK_RESPONSE" if outcome == "success" else _PAST_OUTPUT]
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        yield "delta", acc.content[0]
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    tool = Tool(
        name="web_search",
        description="synthetic",
        parameters={"type": "object"},
        run=lookup,
        label="search",
        read_only=True,
    )
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/qwen",
            [{"role": "user", "content": QUESTION}],
            [tool],
            ToolContext(user_id="synthetic", session_id="synthetic"),
            preset_call=("web_search", {"query": QUESTION}),
            freshness_request=QUESTION,
        )
    ]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert calls == ["search", "model"]
    assert text == ("GROUNDED_MOCK_RESPONSE" if outcome == "success" else _PAST_RENDERED)
    assert "UNSUPPORTED_CURRENT_CLAIM" not in json.dumps(events)
    assert next(e for e in events if e["type"] == "usage")["outputTokens"] == 1
    assert not any(e["type"] == "freshness_abstention" for e in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "empty"])
@pytest.mark.parametrize("mode", [RoutingMode.auto, RoutingMode.auto_quality])
async def test_failed_lookup_stores_only_bounded_past_usage_and_preserves_auto_audit(
    monkeypatch, outcome, mode
):
    user = User(id="synthetic-user", email="synthetic@example.test", password_hash="hash")
    session = ChatSession(id="synthetic-session", user_id=user.id)
    cost_route = {
        "mode": mode.value,
        "decision": "bypass",
        "reasonCode": "unsupported_turn",
        "routedModel": "synthetic/qwen",
        "executedModel": "synthetic/qwen",
    }
    routing_audit = AuditEvent(
        id="synthetic-audit",
        actor_id=user.id,
        action="routing.auto",
        target=session.id,
        event_metadata=dict(cost_route),
    )
    added = []

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            if model is AuditEvent and key == routing_audit.id:
                return routing_audit
            return session if model is ChatSession else user if model is User else None

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    calls = []

    async def completion(_model, _messages, *_args, **_kwargs):
        calls.append("model")
        acc = agent._Accumulator()
        acc.content = [_PAST_OUTPUT]
        acc.usage = {"inputTokens": 4, "outputTokens": 6}
        yield "delta", acc.content[0]
        yield "done", acc

    async def unused_enrichment(*_args, **_kwargs):
        return None

    async def lookup(_arguments):
        return ToolResult(
            content="synthetic unavailable result",
            failed=outcome == "failed",
            empty=outcome == "empty",
        )

    monkeypatch.setattr(sessions, "SessionLocal", Db)
    monkeypatch.setattr(agent, "_stream_once", completion)
    for name in ["_store_artifacts", "_enrich_memory"]:
        monkeypatch.setattr(sessions, name, unused_enrichment)
    monkeypatch.setattr(sessions, "settle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    tool = Tool(
        name="web_search",
        description="synthetic",
        parameters={"type": "object"},
        run=lookup,
        label="search",
        read_only=True,
    )
    chunks = [
        chunk
        async for chunk in sessions._run_turn(
            user_id=user.id,
            api_key="synthetic-noncredential",
            auto_memory=False,
            session_id=session.id,
            model=_external_model("synthetic/qwen"),
            messages=[{"role": "user", "content": QUESTION}],
            tools=[tool],
            first_user_message=QUESTION,
            is_first_turn=False,
            freshness_request=QUESTION,
            routing={
                "actualModel": "synthetic/qwen",
                "costRouting": cost_route,
            },
            routing_audit_id=routing_audit.id,
            preset_call=("web_search", {"query": QUESTION}),
        )
    ]
    answer = next(row for row in added if isinstance(row, Message))
    assert calls == ["model"]
    assert answer.model == "synthetic/qwen"
    assert answer.usage["inputTokens"] == 4 and answer.usage["outputTokens"] == 6
    assert answer.content == _PAST_RENDERED
    assert "UNSUPPORTED_CURRENT_CLAIM" not in "".join(chunks)
    assert answer.routing["accuracy"]["policy"] == "grounded-best-effort-v1"
    assert answer.artifact_ids is None and answer.failure is None
    assert any('"executedModel"' in chunk for chunk in chunks)
    assert routing_audit.event_metadata["executedModel"] == "synthetic/qwen"
    assert routing_audit.event_metadata["mode"] == mode.value


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
async def test_document_routes_reach_normal_planning_without_a_freshness_block(monkeypatch, kind):
    user = User(email="synthetic@example.test", password_hash="hash")
    session = ChatSession(user_id=user.id, kind=kind)
    model = {**_external_model("synthetic/model"), "kinds": [kind.value]}
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])

    class PlanningRoute(Exception):
        pass

    def reached(*_args, **_kwargs):
        raise PlanningRoute

    monkeypatch.setattr(sessions, "has_headroom", reached)
    db = _NoWriteDb()
    with pytest.raises(PlanningRoute):
        await sessions.send_message(
            session.id,
            SendMessage(content=QUESTION),
            _request(),
            user,
            db,
        )
    assert db.added == [] and db.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "현재 대한민국 대통령은 누구인지 말하지 마. 미적분을 설명해 줘.",
        "정치 상황 얘기는 하지 말고 개발 계획을 정리해 줘.",
        "Who was the president of Korea in 1990?",
        "Translate into Korean: 'Who is the current president of Korea?'",
    ],
)
async def test_noncurrent_or_declined_political_tasks_keep_the_normal_route(monkeypatch, question):
    user = User(email="synthetic@example.test", password_hash="hash")
    session = ChatSession(user_id=user.id)
    await _patch_guard_dependencies(
        monkeypatch, session=session, models=[_external_model("synthetic/model")], blocks=[]
    )

    class ReachedNormalRoute(Exception):
        pass

    def reached(*_args, **_kwargs):
        raise ReachedNormalRoute

    monkeypatch.setattr(sessions, "has_headroom", reached)
    with pytest.raises(ReachedNormalRoute):
        await sessions.send_message(
            session.id,
            SendMessage(content=question, web_search=False),
            _request(),
            user,
            _NoWriteDb(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(RoutingMode))
async def test_available_lookup_is_a_trusted_preset_even_for_implicit_officeholder(
    monkeypatch, mode
):
    question = "한국 대통령은 누구야?"
    user = User(email="synthetic@example.test", password_hash="hash")
    model = {**_external_model("synthetic/qwen"), "supportsTools": True}
    session = ChatSession(user_id=user.id, model=model["id"], routing_mode=mode)
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    captured = {}

    class Db(_NoWriteDb):
        def is_modified(self, _row):
            return False

    async def tools(*_args, **_kwargs):
        async def unused(_arguments):
            pytest.fail(
                "the route executed a tool instead of handing the trusted preset to the loop"
            )

        return [
            Tool(
                name="web_search",
                description="synthetic",
                parameters={"type": "object"},
                run=unused,
                label="search",
                read_only=True,
            )
        ]

    async def run(**kwargs):
        captured.update(kwargs)
        yield sessions.chat_service.sse({"type": "done"})

    async def key(*_args):
        return None

    async def credentials(*_args):
        return "synthetic-origin", "synthetic-noncredential"

    def forbidden(*_args, **_kwargs):
        pytest.fail("freshness lookup was sent through Auto classification")

    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "_run_turn", run)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.adaptive_routing, "classify", forbidden)
    response = await sessions.send_message(
        session.id,
        SendMessage(content=question, web_search="auto"),
        _request(),
        user,
        Db(),
    )
    await _events(response)
    assert captured["freshness_request"] == question
    assert captured["preset_call"][0] == "web_search"
    assert any(tool.name == "web_search" for tool in captured["tools"])
    assert captured["force_tool"] is None
