"""The composer's reasoning chip (자동 / 빠르게 / 깊게): what Super Agent and a hand-picked model
do under each choice, what the answer's line is told, and how the conversation keeps it."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions as sessions_router
from app.schemas.chat import SessionPatch
from app.services import reasoning as reasoning_service
from app.services import super_agent as sa
from app.services import thinking


def _model(model_id: str, *, boundary: str = "external", strict: bool = False,
           kinds: tuple[str, ...] = ("chat", "report", "slides"), reasoning=None) -> dict:
    return {
        "id": model_id, "label": model_id, "kinds": list(kinds), "dataBoundary": boundary,
        "strictLocal": strict, "inputCreditCost": 0, "creditCost": 0,
        "contextWindow": 128_000, "supportsTools": True, "supportsVision": False,
        "reasoning": reasoning,
    }


DEEPSEEK = _model("deepseek/deepseek-v4.1-flash", reasoning={
    "efforts": ["none", "low", "high", "max"], "default": "high", "mandatory": False})
#: reasoning that cannot be turned off
GLM = _model("z-ai/glm-5.3", reasoning={
    "efforts": ["low", "high", "max"], "default": "max", "mandatory": True})
#: no effort levels at all: the chip cannot change it
PLAIN = _model("vendor/plain-model")
CATALOGUE = [
    _model("local/qwen3.8-flash", boundary="hybrid"),
    _model("local/qwen3.8-27b", boundary="hybrid"),
    _model("strict-local/qwen3.8-flash", boundary="self_hosted", strict=True),
    _model("strict-local/qwen3.8-27b", boundary="self_hosted", strict=True),
    DEEPSEEK,
    GLM,
    PLAIN,
]
RULES = sa.normalise_rules({})

GREETING = "안녕하세요!"
ARITHMETIC = "1,000만 원을 연 4.2% 월복리로 3년 넣으면 얼마가 돼?"
ANALYSIS = "우리 팀이 모놀리식을 마이크로서비스로 쪼갤지 판단해 줘. 위험도 같이."


# --- the table, steered -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "mode", "row", "effort"),
    [
        # 자동 leaves the table as it is
        ("calc", sa.AUTO, "calc", "high"),
        ("simple", sa.AUTO, "simple", "none"),
        ("document", sa.AUTO, "document", "default"),
        # 빠르게: a row that reasons gives way to the everyday row; nothing reasons
        ("calc", sa.FAST, "chat", "none"),
        ("reasoning", sa.FAST, "chat", "none"),
        ("escalation", sa.FAST, "chat", "none"),
        ("simple", sa.FAST, "simple", "none"),
        ("document", sa.FAST, "document", "none"),
        # 깊게: the escalation row at effort high for a chat class; a document keeps its writer
        # at the highest level it takes
        ("simple", sa.DEEP, "escalation", "high"),
        ("reasoning", sa.DEEP, "escalation", "high"),
        ("document", sa.DEEP, "document", sa.HIGHEST),
    ],
)
def test_the_chip_steers_the_row_and_effort(task: str, mode: str, row: str, effort: str) -> None:
    assert sa.steer(RULES, sa.Decision(task, "rule", "x"), mode) == (row, effort)


def test_only_models_with_levels_or_a_thinking_switch_can_be_steered() -> None:
    assert reasoning_service.controllable(DEEPSEEK["id"], DEEPSEEK["reasoning"])
    assert reasoning_service.controllable(GLM["id"], GLM["reasoning"])
    assert not reasoning_service.controllable(PLAIN["id"], None)
    assert not reasoning_service.controllable("x/only-none", {"efforts": ["none"]})
    # self-hosted: Qwen flash has a thinking switch, the 27B does not
    assert reasoning_service.controllable("strict-local/qwen3.8-flash", None)
    assert not reasoning_service.controllable("strict-local/qwen3.8-27b", None)
    assert reasoning_service.controllable("local/deepseek-v4-flash", None)


def test_deep_on_a_hand_picked_model_asks_for_its_highest_level() -> None:
    assert sa.reasoning_fields(DEEPSEEK["id"], sa.HIGHEST, DEEPSEEK) == {
        "reasoning": {"effort": "max"}}
    assert sa.reasoning_fields("strict-local/qwen3.8-flash", sa.HIGHEST) == {
        "chat_template_kwargs": {"enable_thinking": True}}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (None, "default"),
        ({}, "default"),
        ({"reasoning": {"effort": "high"}}, "high"),
        ({"reasoning": {"enabled": False}}, "none"),
        ({"chat_template_kwargs": {"enable_thinking": True}}, "on"),
        ({"chat_template_kwargs": {"thinking": False}}, "none"),
    ],
)
def test_the_line_names_what_was_sent(fields, expected: str) -> None:
    assert sa.applied_effort(fields) == expected


# --- a hand-picked model ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "mode", "sent", "effort"),
    [
        # 자동 keeps the model's configured default: nothing is sent
        (DEEPSEEK, sa.AUTO, None, "default"),
        (DEEPSEEK, sa.FAST, {"reasoning": {"enabled": False}}, "none"),
        (DEEPSEEK, sa.DEEP, {"reasoning": {"effort": "max"}}, "max"),
        # reasoning that cannot be turned off: 빠르게 is its lowest level
        (GLM, sa.FAST, {"reasoning": {"effort": "low"}}, "low"),
        (GLM, sa.DEEP, {"reasoning": {"effort": "max"}}, "max"),
    ],
)
def test_a_hand_picked_model_takes_the_chips_effort(model, mode, sent, effort) -> None:
    fields, note = sessions_router._turn_reasoning_fields(mode, model, None)
    assert (fields or {}).get(model["id"]) == sent
    assert note == {"mode": mode, "effort": effort}


@pytest.mark.parametrize("mode", [sa.FAST, sa.DEEP])
def test_a_model_without_reasoning_control_is_not_pretended(mode: str) -> None:
    fields, note = sessions_router._turn_reasoning_fields(mode, PLAIN, None)
    assert fields is None
    assert note == {"mode": mode, "effort": "default", "unsupported": True}
    fields, note = sessions_router._turn_reasoning_fields(
        mode, _model("strict-local/qwen3.8-27b", boundary="self_hosted", strict=True), None)
    assert fields is None and note["unsupported"] is True


# --- Super Agent per choice ---------------------------------------------------------------


async def _route(text: str, *, mode: str, routing: RoutingMode = RoutingMode.super,
                 kind: SessionKind = SessionKind.chat, catalogue=None) -> tuple[str, dict, str]:
    user = User(email="r@example.test", password_hash="h", name="R")
    session = ChatSession(user_id=user.id, kind=kind, routing_mode=routing)
    catalogue = catalogue or CATALOGUE
    model, route, effort = await sessions_router._super_route(
        db=None, user=user, session=session, policy=Governance(),
        catalogue=catalogue, default=catalogue[0], history=[], content=text, kind=kind,
        web_search=False, attachments=None, tool_bound=False, reasoning_mode=mode,
    )
    assert route["effort"] == effort
    return model["id"], route, effort


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("routing", "text", "mode", "model", "effort"),
    [
        # 자동: today's table
        (RoutingMode.super, GREETING, sa.AUTO, "local/qwen3.8-flash", "none"),
        (RoutingMode.super, ARITHMETIC, sa.AUTO, "deepseek/deepseek-v4.1-flash", "high"),
        (RoutingMode.super, ANALYSIS, sa.AUTO, "deepseek/deepseek-v4.1-flash", "high"),
        # 빠르게: never a reasoning route
        (RoutingMode.super, GREETING, sa.FAST, "local/qwen3.8-flash", "none"),
        (RoutingMode.super, ARITHMETIC, sa.FAST, "local/qwen3.8-flash", "none"),
        (RoutingMode.super, ANALYSIS, sa.FAST, "local/qwen3.8-flash", "none"),
        # 깊게: the strong reasoning route
        (RoutingMode.super, GREETING, sa.DEEP, "deepseek/deepseek-v4.1-flash", "high"),
        (RoutingMode.super, ANALYSIS, sa.DEEP, "deepseek/deepseek-v4.1-flash", "high"),
        # strict-local: the same choices, never outside the boundary
        (RoutingMode.super_strict, ARITHMETIC, sa.AUTO, "strict-local/qwen3.8-flash", "high"),
        (RoutingMode.super_strict, ARITHMETIC, sa.FAST, "strict-local/qwen3.8-flash", "none"),
        (RoutingMode.super_strict, GREETING, sa.DEEP, "strict-local/qwen3.8-flash", "high"),
    ],
)
async def test_super_agent_routes_by_the_chip(routing, text, mode, model, effort) -> None:
    picked, route, sent = await _route(text, mode=mode, routing=routing)
    assert (picked, sent) == (model, effort)
    if mode == sa.AUTO:
        assert "reasoningMode" not in route
    else:
        assert route["reasoningMode"] == mode


@pytest.mark.asyncio
async def test_fast_keeps_the_class_and_names_the_row_it_moved_to() -> None:
    _, route, _ = await _route(ARITHMETIC, mode=sa.FAST)
    assert route["taskClass"] == "calc" and route["row"] == "chat"


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "effort"), [(sa.AUTO, "default"), (sa.FAST, "none"),
                                              (sa.DEEP, sa.HIGHEST)])
async def test_a_document_keeps_its_writer_and_only_its_effort_moves(mode: str,
                                                                    effort: str) -> None:
    picked, route, sent = await _route("분기 실적 보고서 써 줘", mode=mode,
                                       kind=SessionKind.report)
    # the writer 자동 would pick: no switch to the escalation row
    assert (picked, sent) == ("local/qwen3.8-27b", effort)
    assert "row" not in route


@pytest.mark.asyncio
async def test_a_deep_revision_keeps_the_documents_first_writer() -> None:
    user = User(email="r@example.test", password_hash="h", name="R")
    session = ChatSession(user_id=user.id, kind=SessionKind.report,
                          routing_mode=RoutingMode.super)

    class Asked:
        role = type("R", (), {"value": "user"})()
        content = "보고서 써 줘"
        failure = None
        attachments = None
        routing = None

    class Wrote:
        role = type("R", (), {"value": "assistant"})()
        content = "보고서"
        failure = None
        attachments = None
        routing = {"superAgent": {"selectedModel": "deepseek/deepseek-v4.1-flash",
                                  "taskClass": "document"}}

    model, route, effort = await sessions_router._super_route(
        db=None, user=user, session=session, policy=Governance(),
        catalogue=CATALOGUE, default=CATALOGUE[0], history=[Asked(), Wrote()],
        content="2절을 더 깊게 분석해 줘", kind=SessionKind.report, web_search=False,
        attachments=None, tool_bound=False, reasoning_mode=sa.DEEP,
    )
    assert (model["id"], route["pick"], effort) == (
        "deepseek/deepseek-v4.1-flash", "sticky", sa.HIGHEST)
    fields, note = sessions_router._turn_reasoning_fields(sa.DEEP, model, route)
    assert fields == {model["id"]: {"reasoning": {"effort": "max"}}}
    assert note == {"mode": sa.DEEP, "effort": "max"}


@pytest.mark.parametrize("mode", [sa.FAST, sa.DEEP])
def test_a_document_writer_without_reasoning_control_is_marked(mode: str) -> None:
    writer = CATALOGUE[1]  # local/qwen3.8-27b: no thinking switch
    route = {"effort": sa.HIGHEST if mode == sa.DEEP else "none"}
    fields, note = sessions_router._turn_reasoning_fields(mode, writer, route)
    assert fields is None
    assert note == {"mode": mode, "effort": "default", "unsupported": True}


@pytest.mark.asyncio
async def test_a_super_route_line_says_what_its_writer_was_sent() -> None:
    _, route, _ = await _route(ANALYSIS, mode=sa.DEEP)
    fields, note = sessions_router._turn_reasoning_fields(sa.DEEP, DEEPSEEK, route)
    assert fields == {DEEPSEEK["id"]: {"reasoning": {"effort": "high"}}}
    assert note == {"mode": sa.DEEP, "effort": "high"}
    _, route, _ = await _route(ANALYSIS, mode=sa.FAST)
    flash = CATALOGUE[0]
    fields, note = sessions_router._turn_reasoning_fields(sa.FAST, flash, route)
    assert note == {"mode": sa.FAST, "effort": "none"}


def test_fast_rescues_an_empty_answer_without_reasoning() -> None:
    user = User(email="r@example.test", password_hash="h", name="R")
    writer = CATALOGUE[0]
    route = {"mode": "super", "reasoningMode": sa.FAST}
    rescue, effort = sessions_router._super_rescue(
        route, policy=Governance(), user=user, catalogue=CATALOGUE, writer=writer,
        strict_local=False)
    assert rescue is not None and effort == "none"
    rescue, effort = sessions_router._super_rescue(
        {"mode": "super"}, policy=Governance(), user=user, catalogue=CATALOGUE, writer=writer,
        strict_local=False)
    assert effort == "high"


# --- document writers ---------------------------------------------------------------------


def test_document_writer_calls_take_the_turns_effort() -> None:
    model = DEEPSEEK["id"]
    assert thinking.writer_switch(model) == thinking.switch(model)
    token = thinking.TURN_REASONING.set({model: {"reasoning": {"effort": "high"}}})
    try:
        assert thinking.writer_switch(model) == {"reasoning": {"effort": "high"}}
        # thinking needs room on top of the answer
        assert thinking.writer_room(model, 1000) == 1000 + thinking.THINK_ROOM
        # other models keep thinking off
        assert thinking.writer_switch("other/model") == thinking.switch("other/model")
    finally:
        thinking.TURN_REASONING.reset(token)
    assert thinking.writer_room(model, 1000) == thinking.room(model, 1000)


# --- kept on the conversation -------------------------------------------------------------


class _Db:
    def __init__(self, session: ChatSession) -> None:
        self.session = session

    async def get(self, *_args):
        return self.session

    def add(self, _value) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _value) -> None:
        return None


@pytest.mark.asyncio
async def test_the_conversation_keeps_the_chip_without_moving_up_the_list() -> None:
    user = User(email="r@example.test", password_hash="h", name="R")
    session = ChatSession(user_id=user.id, kind=SessionKind.chat, routing_mode=RoutingMode.super)
    before = session.updated_at
    out = await sessions_router.patch_session(
        session.id, SessionPatch(reasoning_mode="deep"), user, _Db(session))
    assert out.reasoning_mode == "deep" and session.reasoning_mode == "deep"
    assert session.updated_at == before
    out = await sessions_router.patch_session(
        session.id, SessionPatch(reasoning_mode="auto"), user, _Db(session))
    # 자동 is stored as nothing: the behaviour before the chip
    assert out.reasoning_mode is None


def test_the_chip_is_a_turn_option_and_part_of_the_send() -> None:
    from app.schemas.chat import SendMessage

    assert SendMessage(content="질문", reasoning="fast").reasoning == "fast"
    with pytest.raises(ValueError):
        SendMessage(content="질문", reasoning="turbo")
    assert sessions_router._stored_reasoning("auto") is None
    assert sessions_router._stored_reasoning("deep") == "deep"


def test_migration_adds_the_column_and_takes_it_back() -> None:
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa_core
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0088_reasoning_chip.py"
    spec = importlib.util.spec_from_file_location("reasoning_chip_0088", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0088", "0087")
    engine = sa_core.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa_core.text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa_core.text("INSERT INTO sessions VALUES ('a')"))
        ops = Operations(MigrationContext.configure(conn))
        original = migration.op
        migration.op = ops
        try:
            migration.upgrade()
            assert conn.execute(sa_core.text(
                "SELECT reasoning_mode FROM sessions")).scalar_one() is None
            migration.downgrade()
        finally:
            migration.op = original
        columns = [row[1] for row in conn.execute(sa_core.text("PRAGMA table_info(sessions)"))]
    assert columns == ["id"]
