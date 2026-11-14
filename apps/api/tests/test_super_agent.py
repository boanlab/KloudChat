"""Super Agent: classes from rules, the table's picks, stickiness, escalation, strict-local."""

from __future__ import annotations

import pytest

from app.services import super_agent as sa


def _model(model_id: str, *, boundary: str = "external", strict: bool = False, cost: int = 10,
           window: int = 128_000, tools: bool = True, vision: bool = False,
           kinds: tuple[str, ...] = ("chat", "report", "slides"), reasoning=None) -> dict:
    return {
        "id": model_id, "label": model_id, "kinds": list(kinds), "dataBoundary": boundary,
        "strictLocal": strict, "inputCreditCost": cost, "creditCost": cost,
        "contextWindow": window, "supportsTools": tools, "supportsVision": vision,
        "reasoning": reasoning,
    }


CATALOGUE = [
    _model("local/qwen3.8-flash", boundary="hybrid", cost=0),
    _model("local/qwen3.8-27b", boundary="hybrid", cost=0),
    _model("strict-local/qwen3.8-flash", boundary="self_hosted", strict=True, cost=0,
           vision=True),
    _model("strict-local/qwen3.8-27b", boundary="self_hosted", strict=True, cost=0),
    _model("deepseek/deepseek-v4.1-flash", reasoning={
        "efforts": ["none", "low", "high", "max"], "default": "high", "mandatory": False}),
    _model("z-ai/glm-5.3", reasoning={
        "efforts": ["low", "high", "max"], "default": "max", "mandatory": True}),
]
RULES = sa.normalise_rules({})


def _class(text: str, **kwargs) -> sa.Decision:
    return sa.classify(sa.Turn(text=text, **kwargs))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("안녕하세요!", "simple"),
        ("고마워요", "simple"),
        ("파이썬으로 CSV 열 평균 내는 함수를 짜 줘", "code"),
        ("```js\nconst x = 1\n```\n이거 왜 안 돼?", "code"),
        ("다음을 영어로 번역해 줘: 회의는 목요일로 옮겼습니다.", "transform"),
        ("1,000만 원을 연 4.2% 월복리로 3년 넣으면 얼마가 돼?", "calc"),
        ("우리 팀이 모놀리식을 마이크로서비스로 쪼갤지 판단해 줘. 위험도 같이.", "reasoning"),
        ("Design a rate limiter and compare token bucket with sliding window.", "reasoning"),
    ],
)
def test_rules_sort_plain_requests(text: str, expected: str) -> None:
    assert _class(text).task_class == expected


def test_search_switched_on_makes_a_current_turn() -> None:
    decision = _class("Python 3.14에서 새로 들어온 기능을 출처와 함께 알려 줘", web_search=True)
    assert decision.task_class == "current"


def test_words_in_pasted_material_are_not_the_task() -> None:
    names = ("김민준", "이서연", "박지호", "최유나", "정하늘", "김민준", "이서연", "박지호")
    line = "{}: 코드 프리즈는 12월 3일, API 연동은 2일 남았다고 했다."
    memo = "\n".join(line.format(name) for name in names)
    decision = _class(f"회의 녹취\n{memo}\n\n이 회의 내용을 영어로 5문장 이내 요약해 줘.")
    assert decision.task_class == "transform"


def test_documents_are_their_own_class() -> None:
    assert _class("보고서 써 줘", kind="report").task_class == "document"
    assert _class("슬라이드 6장", kind="slides").source == "document"


def test_short_unmarked_requests_are_left_to_the_classifier() -> None:
    decision = _class("주말에 아이랑 갈 만한 실내 놀거리 추천해 줘")
    assert decision.ambiguous is True


def _history(task: str = "reasoning", model: str = "deepseek/deepseek-v4.1-flash",
             answer: str = "답") -> tuple:
    return (
        ("user", "앞 질문", None),
        ("assistant", answer, {"superAgent": {"selectedModel": model, "taskClass": task}}),
    )


def test_short_follow_up_keeps_the_class_and_model() -> None:
    decision = _class("그럼 두 번째 안은?", history=_history())
    assert (decision.task_class, decision.source) == ("reasoning", "sticky")


def test_asking_again_escalates() -> None:
    decision = _class("틀렸어, 다시 계산해 줘", history=_history("calc"))
    assert (decision.task_class, decision.source) == (sa.ESCALATION_CLASS, "escalated")
    assert sa.effort_for(RULES, decision) == "high"


def test_an_empty_answer_escalates_the_next_turn() -> None:
    decision = _class("요약해 줘", history=_history("chat", answer=""))
    assert decision.source == "escalated"


def test_thanks_after_a_hard_turn_comes_down() -> None:
    decision = _class("고마워!", history=_history())
    assert (decision.task_class, decision.source) == ("simple", "deescalated")


def test_the_table_picks_and_falls_back() -> None:
    model, how = sa.pick(RULES, "escalation", CATALOGUE, kind="chat", strict=False,
                         allowed=set())
    assert (model["id"], how) == ("deepseek/deepseek-v4.1-flash", "rule")
    allowed = {"local/qwen3.8-flash", "local/qwen3.8-27b"}
    model, how = sa.pick(RULES, "current", CATALOGUE, kind="chat", strict=False,
                         allowed=allowed)
    assert model["id"] in allowed and how == "fallback"


def test_strict_never_picks_a_model_that_may_leave() -> None:
    rules = sa.normalise_rules({"reasoning": {"strictModel": "deepseek/deepseek-v4.1-flash"}})
    for task in sa.CLASSES:
        model, _ = sa.pick(rules, task, CATALOGUE, kind="chat", strict=True, allowed=set(),
                           default=CATALOGUE[4])
        assert model is not None and sa.strict_model(model), task
    only_external = [m for m in CATALOGUE if m["dataBoundary"] == "external"]
    assert sa.pick(rules, "chat", only_external, kind="chat", strict=True, allowed=set(),
                   default=only_external[0]) == (None, "none")


def test_a_long_conversation_needs_a_window_that_holds_it() -> None:
    small = [_model("local/qwen3.8-flash", boundary="hybrid", cost=0, window=8_000),
             _model("big/model", window=1_000_000)]
    model, _ = sa.pick(RULES, "chat", small, kind="chat", strict=False, allowed=set(),
                       context_bytes=120_000)
    assert model["id"] == "big/model"


def test_pictures_need_a_vision_model() -> None:
    model, _ = sa.pick(RULES, "chat", CATALOGUE, kind="chat", strict=True, allowed=set(),
                       needs_vision=True)
    assert model["id"] == "strict-local/qwen3.8-flash"


def test_admin_rules_merge_over_defaults_and_store_only_changes() -> None:
    raw = {"code": {"model": "z-ai/glm-5.3", "effort": "low"}, "bogus": {"model": "x"},
           "chat": {"effort": "turbo"}}
    merged = sa.normalise_rules(raw)
    assert merged["code"] == {**sa.DEFAULT_RULES["code"], "model": "z-ai/glm-5.3",
                              "effort": "low"}
    assert merged["chat"] == sa.DEFAULT_RULES["chat"]
    assert "bogus" not in merged
    assert sa.stored_rules(raw) == {"code": {"model": "z-ai/glm-5.3", "effort": "low"}}


def test_effort_fields_by_model_family() -> None:
    assert sa.reasoning_fields("deepseek/deepseek-v4.1-flash", "default") == {}
    assert sa.reasoning_fields("deepseek/deepseek-v4.1-flash", "high", CATALOGUE[4]) == {
        "reasoning": {"effort": "high"}}
    # reasoning that cannot be turned off goes to its lowest level
    assert sa.reasoning_fields("z-ai/glm-5.3", "none", CATALOGUE[5]) == {
        "reasoning": {"effort": "low"}}
    assert sa.reasoning_fields("z-ai/glm-5.3", "high", CATALOGUE[5]) == {
        "reasoning": {"effort": "high"}}
    assert sa.reasoning_fields("local/deepseek-v4-flash", "none") == {
        "chat_template_kwargs": {"thinking": False}}
    assert sa.reasoning_fields("strict-local/qwen3.8-flash", "none") == {
        "chat_template_kwargs": {"enable_thinking": False}}


def test_the_picker_estimate_spans_the_table() -> None:
    estimate = sa.estimate(RULES, CATALOGUE, strict=False, allowed=set())
    assert estimate["min"] == 0 and estimate["max"] > 0
    strict = sa.estimate(RULES, CATALOGUE, strict=True, allowed=set())
    assert strict == {"min": 0, "max": 0, "models": ["strict-local/qwen3.8-flash"]}


def test_the_classifier_payload_is_short() -> None:
    turn = sa.Turn(text="가" * 50_000, history=(("user", "나" * 5_000, None),))
    assert len(sa.classifier_payload(turn)) < 3_000


@pytest.mark.asyncio
async def test_classifier_answer_is_parsed_and_failures_return_none(monkeypatch) -> None:
    import httpx

    replies = iter([
        {"choices": [{"message": {"content": '{"class":"calc","confidence":0.9}'}}],
         "usage": {"prompt_tokens": 300, "completion_tokens": 9}},
        {"choices": [{"message": {"content": '{"class":"poetry","confidence":0.9}'}}]},
    ])
    sent: list[dict] = []

    class Client:
        def __init__(self, **_kwargs): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None

        async def post(self, _path, json):
            sent.append(json)
            return httpx.Response(200, json=next(replies),
                                  request=httpx.Request("POST", "http://x.invalid"))

    async def config():
        return "http://x.invalid", "master"

    monkeypatch.setattr(sa.httpx, "AsyncClient", Client)
    monkeypatch.setattr(sa.settings_store, "litellm_config", config)
    turn = sa.Turn(text="회비 정산 좀")
    first = await sa.classify_with_model(turn, model_id="strict-local/c", user_id="u", api_key="k")
    assert first is not None and first.task_class == "calc" and first.input_tokens == 300
    assert sent[0]["disable_fallbacks"] is True and sent[0]["max_tokens"] <= 40
    assert await sa.classify_with_model(turn, model_id="strict-local/c", user_id="u",
                                        api_key="k") is None


# --- admin settings, picker offer, migration, writer effort -------------------------------

from starlette.requests import Request  # noqa: E402

from app.models.governance import Governance  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.routers import models as models_router  # noqa: E402
from app.routers import usage as usage_router  # noqa: E402
from app.schemas.admin import GovernanceIn  # noqa: E402


def _put_request() -> Request:
    return Request({"type": "http", "method": "PUT", "path": "/api/admin/governance",
                    "headers": [], "client": ("127.0.0.1", 1)})


async def _put(monkeypatch, policy: Governance, payload: GovernanceIn) -> dict:
    admin = User(email="a@example.test", password_hash="h", name="A", role=UserRole.admin)

    class Db:
        async def get(self, *_args):
            return policy

        def add(self, _value):
            return None

        async def commit(self):
            return None

    async def catalogue():
        return {"models": CATALOGUE, "litellmAvailable": True}

    async def sweep(_db):
        return 0

    monkeypatch.setattr(usage_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(usage_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(usage_router.governance, "sweep_expired", sweep)
    monkeypatch.setattr(usage_router.governance, "invalidate", lambda: None)
    return await usage_router.put_governance(payload, _put_request(), admin, Db())


@pytest.mark.asyncio
async def test_admin_table_stores_changes_and_refuses_bad_picks(monkeypatch) -> None:
    policy = Governance()
    await _put(monkeypatch, policy, GovernanceIn(super_agent_rules={
        **{name: dict(rule) for name, rule in sa.DEFAULT_RULES.items()},
        "code": {"model": "z-ai/glm-5.3", "strictModel": "strict-local/qwen3.8-27b",
                 "effort": "low"}}))
    assert policy.super_agent_rules == {"code": {"model": "z-ai/glm-5.3",
                                                 "strictModel": "strict-local/qwen3.8-27b",
                                                 "effort": "low"}}
    for rules, code in (
        ({"code": {"strictModel": "deepseek/deepseek-v4.1-flash"}},
         "super_agent_strict_model_must_be_strict_local"),
        ({"code": {"model": "gone/model"}}, "super_agent_model_invalid"),
        ({"poems": {"model": "z-ai/glm-5.3"}}, "super_agent_class_unknown"),
        ({"code": {"effort": "turbo"}}, "super_agent_effort_invalid"),
    ):
        with pytest.raises(Exception) as caught:
            await _put(monkeypatch, Governance(), GovernanceIn(super_agent_rules=rules))
        assert getattr(caught.value, "detail", None) == code


@pytest.mark.asyncio
async def test_picker_offers_both_entries_with_a_credit_range(monkeypatch) -> None:
    user = User(email="p@example.test", password_hash="h", name="P")

    async def catalogue():
        return {"models": CATALOGUE, "litellmAvailable": True}

    async def policy():
        return Governance(super_agent_enabled=True)

    monkeypatch.setattr(models_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(models_router.governance, "current", policy)
    offer = (await models_router.list_models(user))["superAgent"]
    assert offer["default"]["available"] and offer["strict"]["available"]
    assert offer["strict"]["estimate"]["max"] == 0
    assert offer["default"]["estimate"]["max"] > 0

    async def off():
        return Governance(super_agent_enabled=False)

    monkeypatch.setattr(models_router.governance, "current", off)
    offer = (await models_router.list_models(user))["superAgent"]
    assert offer["default"] == {**offer["default"], "available": False, "reason": "disabled"}


def _migration():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0083_super_agent.py"
    spec = importlib.util.spec_from_file_location("super_agent_0083", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_carries_the_auto_lists_and_moves_auto_sessions() -> None:
    import json as json_module

    import sqlalchemy as sa_core
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0083", "0082")
    assert migration.carried_rules('["local/cheap"]', ["deepseek/x", "z/y"]) == {
        "simple": {"model": "local/cheap"}, "reasoning": {"model": "deepseek/x"}}
    # lists still at the old defaults leave the evaluated table alone
    assert migration.carried_rules(
        '["local/qwen3.8-flash"]', ["deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3-flash"]) == {}
    assert migration.carried_rules(None, []) == {}

    engine = sa_core.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa_core.text(
            "CREATE TABLE governance (id VARCHAR PRIMARY KEY, adaptive_routing_enabled BOOLEAN, "
            "adaptive_quality_enabled BOOLEAN, adaptive_economy_model_ids JSON, "
            "adaptive_quality_model_ids JSON)"))
        conn.execute(sa_core.text(
            "INSERT INTO governance VALUES ('default', 0, 0, '[]', '[\"deepseek/x\"]')"))
        conn.execute(sa_core.text("CREATE TABLE sessions (id VARCHAR, routing_mode VARCHAR)"))
        conn.execute(sa_core.text(
            "INSERT INTO sessions VALUES ('a','auto'),('b','auto_quality'),('c','manual')"))
        ops = Operations(MigrationContext.configure(conn))
        original = migration.op
        migration.op = ops
        try:
            migration.upgrade()
        finally:
            migration.op = original
        enabled, rules = conn.execute(sa_core.text(
            "SELECT super_agent_enabled, super_agent_rules FROM governance")).one()
        modes = dict(conn.execute(sa_core.text("SELECT id, routing_mode FROM sessions")).all())
    assert bool(enabled) is True
    stored = json_module.loads(rules) if isinstance(rules, str) else rules
    assert stored == {"reasoning": {"model": "deepseek/x"}}
    assert modes == {"a": "super", "b": "super", "c": "manual"}


@pytest.mark.asyncio
async def test_the_writer_call_carries_the_routed_effort(monkeypatch) -> None:
    from app.services import agent

    sent: list[dict] = []

    class Stream:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
            yield "data: [DONE]"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _path, json):
            sent.append(json)
            return Stream()

    async def client(*_args, **_kwargs):
        return Client()

    async def roles():
        return sa_roles

    from app.services import settings_store

    sa_roles = settings_store.model_roles_from({})
    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent.settings_store, "model_roles", roles)
    agent.TURN_REASONING.set({"deepseek/deepseek-v4.1-flash": {"reasoning": {"effort": "high"}}})
    try:
        async for _ in agent._stream_once("deepseek/deepseek-v4.1-flash", [], [], "u", "key"):
            pass
        async for _ in agent._stream_once("other/model", [], [], "u", "key"):
            pass
    finally:
        agent.TURN_REASONING.set(None)
    assert sent[0]["reasoning"] == {"effort": "high"}
    assert sent[1].get("reasoning") != {"effort": "high"}


def test_hard_classes_go_to_deepseek_with_reasoning_high() -> None:
    # owner decision 2026-11-14: hard turns on DeepSeek V4.1 Flash @high; the strict column
    # keeps flash for the hard classes, and the escalation row's strict pick is self-hosted
    # DeepSeek V4 Flash (second decision of 2026-11-14)
    for name in ("calc", "code", "current", "long", "reasoning", "escalation"):
        rule = sa.DEFAULT_RULES[name]
        assert (rule["model"], rule["effort"]) == ("deepseek/deepseek-v4.1-flash", "high"), name
        assert rule["strictModel"] == ("strict-local/deepseek-v4-flash" if name == "escalation"
                                       else "strict-local/qwen3.8-flash"), name
    for name in ("simple", "chat", "transform"):
        assert sa.DEFAULT_RULES[name]["model"] == "local/qwen3.8-flash"
    assert sa.DEFAULT_RULES["document"]["strictModel"] == "strict-local/qwen3.8-27b"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strict_rule", "allowed", "expected"),
    [
        (True, [], "super_strict"),
        # nothing strict-local this person may use: stays on `super`
        (True, ["deepseek/deepseek-v4.1-flash"], "super"),
        (False, [], "super"),
    ],
)
async def test_a_new_project_conversation_on_super_agent_starts_strict_when_required(
    monkeypatch, strict_rule: bool, allowed: list[str], expected: str
) -> None:
    from app.routers import sessions as sessions_router

    user = User(email="p@example.test", password_hash="h", name="P", allowed_models=allowed)

    async def catalogue():
        return {"models": CATALOGUE, "litellmAvailable": True}

    async def policy():
        return Governance(super_agent_enabled=True, internal_data_strict_local=strict_rule)

    monkeypatch.setattr(sessions_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions_router.governance, "current", policy)
    assert (await sessions_router._project_super_mode(user)).value == expected
