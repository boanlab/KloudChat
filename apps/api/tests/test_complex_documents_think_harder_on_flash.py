"""Complex document turns move to the `complex_document` row (Qwen3.8 Flash thinking at high);
simple ones stay on `document`; the chip steers effort, never the model.

An admin-chosen model gets the `document` row's model behind it and may cap concurrent turns."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_super_agent_turns import DEEPSEEK, FLASH, STRICT_27B, STRICT_FLASH, _model

from app.models.chat import ChatSession, Message, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.services import complex_turn, deck, model_health
from app.services import super_agent as sa

LOCAL_27B = _model("local/qwen3.8-27b", boundary="hybrid")
#: a slower self-hosted model an admin may put in the complex row (the built-in table names none)
SLOW = {**_model("local/slow-writer", boundary="hybrid"), "contextWindow": 258_048}
STRICT_SLOW = {**_model("strict-local/slow-writer", boundary="self_hosted", strict=True,
                        privacy_only=True), "contextWindow": 258_048}
CATALOGUE = [FLASH, LOCAL_27B, SLOW, STRICT_27B, STRICT_FLASH, STRICT_SLOW]
USER = User(id="u-cx", email="cx@example.test", password_hash="h", name="Cx")
POLICY = Governance(external_data_guard=True, internal_data_strict_local=False)
#: the complex row pointed at the slower model: the `document` row's flash is behind it
SLOW_ROW = Governance(external_data_guard=True, internal_data_strict_local=False,
                      super_agent_rules={"complex_document": {
                          "model": SLOW["id"], "strictModel": STRICT_SLOW["id"]}})

FORM = sa.DocSignals(forms=("hwpx",))
MANY = sa.DocSignals(sources=sa.COMPLEX_MANY_FILES)
EACH = sa.DocSignals(sources=sa.COMPLEX_COVERAGE_FILES, asks_each=True)
TOOLS = sa.DocSignals(chart_data=True)
LONG = sa.DocSignals(source_chars=sa.COMPLEX_SOURCE_CHARS)


@pytest.fixture(autouse=True)
def _health(monkeypatch):
    model_health.reset()
    complex_turn.PLAN.set(None)
    complex_turn._waiting.clear()
    complex_turn._local_held.clear()
    # the per-process cap (the shared one is Postgres advisory locks)
    monkeypatch.setattr(complex_turn, "_postgres", lambda: False)
    yield
    model_health.reset()
    complex_turn.PLAN.set(None)


def _session(mode=RoutingMode.super, kind=SessionKind.report) -> ChatSession:
    return ChatSession(id="s-cx", user_id=USER.id, kind=kind, model=LOCAL_27B["id"],
                       routing_mode=mode)


async def _route(doc, *, mode=RoutingMode.super, kind=SessionKind.report, chip=sa.AUTO,
                 policy=POLICY, history=None, catalogue=None, request="보고서를 써 줘"):
    out = await sessions._document_route(
        db=None, user=USER, session=_session(mode, kind), policy=policy,
        catalogue=catalogue or CATALOGUE, model=LOCAL_27B, blocks=[], attachments=None,
        history=history or [], request=request, reasoning_mode=chip, doc=doc)
    model, strict, note = out
    return model, strict, (note or {}).get("superAgent") or {}


# ── the table and the signals ────────────────────────────────────────────────


def test_the_table_has_a_proper_complex_document_row() -> None:
    rule = sa.DEFAULT_RULES["complex_document"]
    assert rule == {"model": "local/qwen3.8-flash", "strictModel": "strict-local/qwen3.8-flash",
                    "effort": "high", "strictEffort": "high"}
    assert "complex_document" in sa.CLASSES and "complex_document" in sa.OVERRIDE_ROWS
    # an admin may empty a column (the row then moves nothing there) or change the model
    rules = sa.normalise_rules({"complex_document": {"model": "", "effort": "none"}})
    assert rules["complex_document"]["model"] == ""
    assert rules["complex_document"]["effort"] == "none"
    assert sa.stored_rules({"complex_document": {"model": ""}}) == {
        "complex_document": {"model": ""}}
    # the document row writes on the same model without thinking
    assert sa.DEFAULT_RULES["document"] == {
        "model": "local/qwen3.8-flash", "strictModel": "strict-local/qwen3.8-flash",
        "effort": "none"}


def test_no_default_names_a_self_hosted_model_that_is_going_away() -> None:
    gone = ("deepseek-v4-flash", "coder-next")
    for name, rule in sa.DEFAULT_RULES.items():
        for key in ("model", "strictModel"):
            assert not any(word in rule[key] for word in gone), (name, key)
    for model_id in (*sa._FALLBACKS, *sa._STRICT_FALLBACKS):
        assert not any(word in model_id for word in gone), model_id


@pytest.mark.parametrize(("signals", "reasons"), [
    (sa.DocSignals(forms=("hwpx",)), ["form"]),
    (sa.DocSignals(forms=("docx",)), ["form"]),
    (sa.DocSignals(forms=("pptx",)), ["form"]),
    (sa.DocSignals(forms=("hwp",)), []),
    (EACH, ["coverage"]),
    (sa.DocSignals(sources=sa.COMPLEX_COVERAGE_FILES - 1, asks_each=True), []),
    (sa.DocSignals(sources=sa.COMPLEX_MANY_FILES - 1), []),
    (MANY, ["coverage"]),
    (sa.DocSignals(chart_data=True), ["tools"]),
    (sa.DocSignals(paper_figures=True), ["tools"]),
    (sa.DocSignals(source_chars=sa.COMPLEX_SOURCE_CHARS - 1), []),
    (LONG, ["long_source"]),
    (sa.DocSignals(forms=("hwpx",), chart_data=True, source_chars=10**6),
     ["form", "tools", "long_source"]),
    (sa.DocSignals(), []),
    (None, []),
])
def test_the_signals_that_make_a_document_complex(signals, reasons) -> None:
    assert sa.complexity(signals) == reasons


def test_the_thresholds_live_in_one_place() -> None:
    assert sa.COMPLEX_COVERAGE_FILES == 2  # the Coverage stage's own floor
    assert sa.COMPLEX_SOURCE_CHARS == sa.LONG_CONTEXT_BYTES // 3
    assert sa.COMPLEX_REASONS == ("form", "coverage", "tools", "long_source")


def test_steer_moves_complex_documents_and_the_chip_steers_effort_only() -> None:
    rules = sa.normalise_rules({})
    document = sa.Decision("document", "document", "document_surface")
    steer = sa.steer
    assert steer(rules, document, sa.AUTO, complex_doc=True) == ("complex_document", "high")
    assert steer(rules, document, sa.AUTO, complex_doc=True, strict=True) == (
        "complex_document", "high")
    assert steer(rules, document, sa.FAST, complex_doc=True) == ("complex_document", "none")
    assert steer(rules, document, sa.DEEP, complex_doc=True) == (
        "complex_document", sa.HIGHEST)
    # a simple document: the document row, without thinking
    assert steer(rules, document, sa.AUTO) == ("document", "none")
    assert steer(rules, document, sa.DEEP) == ("document", sa.HIGHEST)
    # a column the row leaves empty moves nothing
    empty = sa.normalise_rules({"complex_document": {"strictModel": ""}})
    assert steer(empty, document, sa.AUTO, complex_doc=True, strict=True) == (
        "document", "none")
    assert steer(empty, document, sa.AUTO, complex_doc=True, strict=False) == (
        "complex_document", "high")


# ── routing through the document route ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
@pytest.mark.parametrize("doc", [FORM, EACH, MANY, TOOLS, LONG])
async def test_each_signal_sends_super_documents_to_flash_thinking_high(doc, kind) -> None:
    model, strict, route = await _route(doc, kind=kind)
    assert model["id"] == FLASH["id"] and not strict
    assert route["row"] == "complex_document" and route["taskClass"] == "document"
    assert route["effort"] == "high" and route["complexReason"] == sa.complexity(doc)[0]
    assert route["complexSignals"] == sa.complexity(doc)
    # effort high on flash is its thinking switch with the 2000-token budget
    assert sa.reasoning_fields(model["id"], route["effort"], model) == {
        "chat_template_kwargs": {"enable_thinking": True}, "thinking_token_budget": 2000}


@pytest.mark.asyncio
async def test_the_strict_entry_goes_to_strict_flash_thinking_high() -> None:
    model, strict, route = await _route(FORM, mode=RoutingMode.super_strict)
    assert model["id"] == STRICT_FLASH["id"] and strict
    assert route["mode"] == "super_strict" and route["row"] == "complex_document"
    assert route["effort"] == "high"


@pytest.mark.asyncio
async def test_a_simple_document_stays_on_the_document_row_without_thinking() -> None:
    for doc in (None, sa.DocSignals(), sa.DocSignals(sources=1, source_chars=900),
                sa.DocSignals(sources=sa.COMPLEX_MANY_FILES - 1)):
        model, _, route = await _route(doc)
        assert model["id"] == FLASH["id"] and "row" not in route
        assert "complexReason" not in route and route["effort"] == "none"
    model, _, _ = await _route(sa.DocSignals(), mode=RoutingMode.super_strict)
    assert model["id"] == STRICT_FLASH["id"]


@pytest.mark.asyncio
async def test_a_model_picked_by_hand_is_left_alone() -> None:
    model, _, note = await sessions._document_route(
        db=None, user=USER, session=_session(RoutingMode.manual), policy=POLICY,
        catalogue=CATALOGUE, model=LOCAL_27B, blocks=[], attachments=None, history=[],
        request="양식대로 써 줘", doc=FORM)
    assert model["id"] == LOCAL_27B["id"] and note is None


@pytest.mark.asyncio
async def test_the_chip_never_moves_a_complex_document_off_its_row() -> None:
    model, _, route = await _route(FORM, chip=sa.FAST)
    assert model["id"] == FLASH["id"] and route["effort"] == "none"
    assert route["row"] == "complex_document" and route["reasoningMode"] == "fast"
    assert sa.reasoning_fields(model["id"], "none") == {
        "chat_template_kwargs": {"enable_thinking": False}}
    model, _, route = await _route(FORM, chip=sa.DEEP)
    assert model["id"] == FLASH["id"] and route["effort"] == sa.HIGHEST
    # an admin's slower model keeps its row under 빠르게 too
    model, _, route = await _route(FORM, chip=sa.FAST, policy=SLOW_ROW)
    assert model["id"] == SLOW["id"] and route["effort"] == "none"


@pytest.mark.asyncio
async def test_a_revision_keeps_the_first_versions_writer_and_row() -> None:
    first = Message(session_id="s-cx", role=sessions.Role.assistant, content="v1", routing={
        "superAgent": {"selectedModel": SLOW["id"], "row": "complex_document",
                       "complexSignals": ["form"], "taskClass": "document"}})
    # The revision's own signals are empty; the first version was complex.
    model, _, route = await _route(sa.DocSignals(), history=[first])
    assert model["id"] == SLOW["id"] and route["row"] == "complex_document"
    assert route["pick"] == "sticky" and route["complexSignals"] == ["form"]
    # a document the 27B wrote first stays on it even when the revision brings a form
    simple = Message(session_id="s-cx", role=sessions.Role.assistant, content="v1", routing={
        "superAgent": {"selectedModel": LOCAL_27B["id"], "taskClass": "document"}})
    model, _, route = await _route(FORM, history=[simple])
    assert model["id"] == LOCAL_27B["id"] and "row" not in route


@pytest.mark.asyncio
async def test_a_complex_model_that_failed_lately_is_passed_over_for_the_document_row() -> None:
    model_health.mark_failed(SLOW["id"], "upstream_unreachable")
    model, _, route = await _route(FORM, policy=SLOW_ROW)
    # the document row's model, not the table's fallback list
    assert model["id"] == FLASH["id"]
    assert route["skipped"] == SLOW["id"] and "row" not in route
    assert route["effort"] == "none"
    model_health.reset()
    # not served here: the same
    model, _, route = await _route(FORM, policy=SLOW_ROW, catalogue=[FLASH, STRICT_FLASH])
    assert model["id"] == FLASH["id"] and "complexReason" not in route


@pytest.mark.asyncio
async def test_an_admin_can_point_the_row_elsewhere_or_switch_it_off() -> None:
    off = Governance(external_data_guard=True, internal_data_strict_local=False,
                     super_agent_rules={"complex_document": {"model": "", "strictModel": ""}})
    model, _, route = await _route(FORM, policy=off)
    assert model["id"] == FLASH["id"] and "row" not in route and route["effort"] == "none"
    other = Governance(external_data_guard=True, internal_data_strict_local=False,
                       super_agent_rules={"complex_document": {"model": LOCAL_27B["id"],
                                                               "effort": "low"}})
    model, _, route = await _route(FORM, policy=other)
    assert model["id"] == LOCAL_27B["id"] and route["row"] == "complex_document"
    assert route["effort"] == "low"


# ── the fallback behind it ───────────────────────────────────────────────────


def test_the_built_in_complex_row_has_no_fallback_behind_it() -> None:
    """Both rows name flash: the same model again would not help, so none (and no wait)."""
    rules = sa.normalise_rules({})
    for writer, strict in ((FLASH, False), (STRICT_FLASH, True)):
        assert sa.complex_fallback(rules, CATALOGUE, writer, kind="report", strict=strict,
                                   allowed=set()) is None
        route = {"row": "complex_document", "mode": "super_strict" if strict else "super"}
        assert sessions._super_fallback(route, user=USER, catalogue=CATALOGUE, writer=writer,
                                        strict_local=strict, needs_tools=True,
                                        kind="report") is None


def test_a_complex_documents_fallback_is_the_document_rows_model_in_its_boundary() -> None:
    rules = sa.normalise_rules(SLOW_ROW.super_agent_rules)
    found = sa.complex_fallback(rules, CATALOGUE, SLOW, kind="report", strict=False,
                                allowed=set())
    assert found["id"] == FLASH["id"]
    found = sa.complex_fallback(rules, CATALOGUE, STRICT_SLOW, kind="report", strict=True,
                                allowed=set())
    assert found["id"] == STRICT_FLASH["id"]
    # the writer is the document row's model already, or it failed lately: none
    assert sa.complex_fallback(rules, CATALOGUE, FLASH, kind="report", strict=False,
                               allowed=set()) is None
    assert sa.complex_fallback(rules, CATALOGUE, SLOW, kind="report", strict=False,
                               allowed=set(), skip=frozenset({FLASH["id"]})) is None


def test_the_router_names_the_fallback_for_complex_turns_only() -> None:
    route = {"row": "complex_document", "mode": "super"}
    raw = SLOW_ROW.super_agent_rules
    found = sessions._super_fallback(route, user=USER, catalogue=CATALOGUE, writer=SLOW,
                                     strict_local=False, needs_tools=False, kind="report",
                                     rules_raw=raw)
    assert found["id"] == FLASH["id"]
    strict = sessions._super_fallback({**route, "mode": "super_strict"}, user=USER,
                                      catalogue=CATALOGUE, writer=STRICT_SLOW, strict_local=True,
                                      needs_tools=True, rules_raw=raw)
    assert strict["id"] == STRICT_FLASH["id"]
    # a plain turn outside strict-local has none, as before
    assert sessions._super_fallback({"mode": "super"}, user=USER, catalogue=CATALOGUE,
                                    writer=SLOW, strict_local=False,
                                    needs_tools=True) is None


def test_the_fallback_thinks_as_the_document_row_says() -> None:
    # an admin's writer that takes effort levels, the document row's flash behind it
    fields = sessions._super_reasoning(DEEPSEEK, "high", None, "default", STRICT_FLASH,
                                       "default")
    assert DEEPSEEK["id"] in fields and STRICT_FLASH["id"] not in fields
    fields = sessions._super_reasoning(DEEPSEEK, "high", None, "default", STRICT_FLASH, None)
    assert STRICT_FLASH["id"] in fields  # a plain fallback carries on at the writer's effort


# ── slots ────────────────────────────────────────────────────────────────────


async def _drain(plan, poll=0.01):
    return [event async for event in complex_turn.wait_for_slot(plan, poll=poll)]


@pytest.mark.asyncio
async def test_two_slots_then_a_line_then_the_fallback(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 2)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 0.15)
    first, second, third = (complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
                            for _ in range(3))
    assert await _drain(first) == [] and await _drain(second) == []
    assert first.slot is not None and second.slot is not None
    events = await _drain(third)
    # in line (「대기 중 · 1번째」), then no slot in time: the fallback writes
    assert events[0]["label"] == "대기 중 · 1번째" and events[0]["status"] == "running"
    assert events[0]["queuePosition"] == 1 and events[0]["id"] == "complex-queue"
    assert third.use_fallback and third.reason == "queue" and third.slot is None
    assert third.model_for(SLOW["id"]) == FLASH["id"]
    assert not complex_turn._waiting


@pytest.mark.asyncio
async def test_a_released_slot_goes_to_the_next_turn(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 1)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 5.0)
    holder = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    waiter = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    await _drain(holder)
    task = asyncio.create_task(_drain(waiter))
    await asyncio.sleep(0.05)
    assert not task.done()
    await complex_turn.release(holder)
    events = await task
    assert waiter.slot is not None and not waiter.use_fallback
    assert events[0]["status"] == "running" and events[-1]["status"] == "done"
    await complex_turn.release(waiter)
    assert not complex_turn._local_held


@pytest.mark.asyncio
async def test_the_waiting_line_counts_turns_in_order(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 1)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 5.0)
    holder = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    await _drain(holder)
    plans = [complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True) for _ in range(2)]
    seen: list[list[int]] = [[], []]

    async def wait(index):
        async for event in complex_turn.wait_for_slot(plans[index], poll=0.01):
            if "queuePosition" in event:
                seen[index].append(event["queuePosition"])

    tasks = [asyncio.create_task(wait(0))]
    await asyncio.sleep(0.05)
    tasks.append(asyncio.create_task(wait(1)))
    await asyncio.sleep(0.05)
    assert seen[0][0] == 1 and seen[1][0] == 2
    for plan in (holder, *plans):
        await complex_turn.release(plan)
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_no_cap_means_no_wait(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 0)
    assert not complex_turn.capped()
    plan = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    assert await _drain(plan) == [] and plan.slot is None


def test_the_default_cap_is_off() -> None:
    """The built-in row is flash with no fallback, so a cap would only queue turns."""
    from app.core.config import Settings

    assert Settings.model_fields["complex_document_slots"].default == 0
    assert Settings.model_fields["complex_document_wait_sec"].default == 120.0
    assert Settings.model_fields["complex_document_call_sec"].default == 480.0


@pytest.mark.asyncio
async def test_a_built_in_complex_turn_is_not_held_for_a_slot(monkeypatch) -> None:
    from app.core.config import Settings

    monkeypatch.setattr(complex_turn.settings, "complex_document_slots",
                        Settings.model_fields["complex_document_slots"].default)
    # the built-in row's turn: flash, nothing behind it
    plan = complex_turn.Plan(FLASH["id"], None, queued=True)
    assert not complex_turn.capped()
    assert await _drain(plan) == [] and plan.slot is None and not plan.use_fallback


@pytest.mark.asyncio
async def test_the_stream_holds_its_slot_and_gives_it_back(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 1)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 5.0)
    plan = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)

    async def events():
        assert plan.slot is not None
        yield 'data: {"type": "step", "id": "x"}\n\n'
        yield 'data: {"type": "section", "content": "본문"}\n\n'
        yield 'data: {"type": "done"}\n\n'

    out = [json.loads(chunk.removeprefix("data: ")) async for chunk in
           sessions._with_slot(plan, events())]
    thinking = [e for e in out if e.get("id") == "writer-thinking"]
    # 「깊이 생각하는 중」 runs until the first output, so the line never looks stuck
    assert [e["status"] for e in thinking] == ["running", "done"]
    assert out.index(thinking[1]) < next(i for i, e in enumerate(out)
                                         if e.get("type") == "section") + 1
    assert plan.slot is None and not complex_turn._local_held


@pytest.mark.asyncio
async def test_a_stream_that_dies_still_gives_the_slot_back(monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 1)
    plan = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True, surface="chat")

    async def events():
        yield 'data: {"type": "delta", "text": "a"}\n\n'
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _ = [c async for c in sessions._with_slot(plan, events())]
    assert plan.slot is None and not complex_turn._local_held


# ── whole-answer calls ───────────────────────────────────────────────────────


def _plan(**kwargs) -> complex_turn.Plan:
    plan = complex_turn.Plan(SLOW["id"], FLASH["id"], **kwargs)
    complex_turn.PLAN.set(plan)
    return plan


@pytest.mark.asyncio
async def test_a_failed_call_is_asked_again_on_the_fallback_and_the_rest_follow() -> None:
    plan = _plan()
    seen: list[str] = []

    async def call(model: str):
        seen.append(model)
        if model == SLOW["id"]:
            raise httpx.ConnectError("down")
        return "본문", {}

    assert (await complex_turn.run_call(SLOW["id"], call))[0] == "본문"
    assert (await complex_turn.run_call(SLOW["id"], call))[0] == "본문"
    # the second call goes straight to the fallback; the failed model is left out of picks
    assert seen == [SLOW["id"], FLASH["id"], FLASH["id"]]
    assert plan.use_fallback and plan.reason == "error"
    assert SLOW["id"] in model_health.unhealthy()


@pytest.mark.asyncio
async def test_a_busy_gateway_and_a_silent_call_hand_over_too(monkeypatch) -> None:
    plan = _plan()

    async def busy(model: str):
        if model == SLOW["id"]:
            request = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("503", request=request, response=httpx.Response(
                503, text='{"error":"strict_local_unavailable"}', request=request))
        return "ok", {}

    assert (await complex_turn.run_call(SLOW["id"], busy))[0] == "ok"
    assert plan.reason == "busy"
    assert model_health._until[SLOW["id"]] - asyncio.get_running_loop().time() < 61

    model_health.reset()
    plan = _plan()
    monkeypatch.setattr(complex_turn.settings, "complex_document_call_sec", 0.05)

    async def silent(model: str):
        if model == SLOW["id"]:
            await asyncio.sleep(5)
        return "늦지 않은 답", {}

    assert (await complex_turn.run_call(SLOW["id"], silent))[0] == "늦지 않은 답"
    assert plan.reason == "timeout"


@pytest.mark.asyncio
async def test_calls_that_are_not_the_complex_writers_are_left_alone() -> None:
    _plan()
    seen: list[str] = []

    async def call(model: str):
        seen.append(model)
        raise ValueError("a bug of the call's own")

    with pytest.raises(ValueError):
        await complex_turn.run_call(SLOW["id"], call)  # not a server failure: no hand-over
    assert seen == [SLOW["id"]]
    seen.clear()
    with pytest.raises(ValueError):
        await complex_turn.run_call("other/model", call)
    assert seen == ["other/model"]
    complex_turn.PLAN.set(None)
    with pytest.raises(ValueError):
        await complex_turn.run_call(SLOW["id"], call)
    assert not model_health.unhealthy()


@pytest.mark.asyncio
async def test_a_turn_that_lost_its_slot_writes_on_the_fallback_from_the_start() -> None:
    _plan(use_fallback=True)
    seen: list[str] = []

    async def call(model: str):
        seen.append(model)
        return "x", {}

    await complex_turn.run_call(SLOW["id"], call)
    assert seen == [FLASH["id"]]


@pytest.mark.asyncio
async def test_the_deck_writer_goes_through_the_fallback(monkeypatch) -> None:
    _plan()
    seen: list[str] = []

    async def once(model, messages, api_key, max_tokens):
        seen.append(model)
        if model == SLOW["id"]:
            raise httpx.ConnectError("down")
        return "슬라이드", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete_once", once)
    monkeypatch.setattr(deck.model_retry, "PAUSE", 0.0)
    text, _ = await deck._complete(SLOW["id"], [], "key", 100)
    assert text == "슬라이드"
    # a dropped connection is asked once more on the same model first (`model_retry`), then
    # the fallback writes it
    assert seen[-1] == FLASH["id"] and seen.count(SLOW["id"]) == 2


# ── the signals read from the turn ───────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows=(), objects=None):
        self.rows, self.objects = list(rows), objects or {}

    async def get(self, kind, key):
        return self.objects.get((kind, key))

    async def exec(self, _statement):
        return _Rows(self.rows)


def _file(name, text="", mime="text/plain"):
    from app.models.workspace import StoredFile

    return StoredFile(user_id=USER.id, name=name, mime=mime, text=text, storage_key=name)


async def _signals(monkeypatch, *, shelf=(), rows=(), request="보고서", kind=SessionKind.report,
                   session=None, forms=None, output_format=None, objects=None):
    async def knowledge(*_args, **_kwargs):
        return list(shelf), ""

    monkeypatch.setattr(sessions, "_knowledge_shelf", knowledge)
    return await sessions._document_signals(
        _Db(rows, objects), USER, session or _session(kind=kind), request=request,
        attachments=[r.id for r in rows] or None, turn_forms=forms, output_format=output_format)


@pytest.mark.asyncio
async def test_signals_count_files_once_and_their_text(monkeypatch) -> None:
    shelf = [("1강.pdf", "가" * 100, None), ("2강.pdf", "나" * 50, None)]
    rows = [_file("2강.pdf", "나" * 50), _file("3강.pdf", "다" * 10),
            _file("사진.png", "", "image/png")]
    out = await _signals(monkeypatch, shelf=shelf, rows=rows, request="각 강의를 정리해 줘")
    assert out.sources == 3 and out.source_chars == 160 and out.asks_each
    assert sa.complexity(out) == ["coverage"]
    quiet = await _signals(monkeypatch, shelf=shelf[:1], request="이 자료를 요약해 줘")
    assert quiet.sources == 1 and not quiet.asks_each and sa.complexity(quiet) == []


@pytest.mark.asyncio
async def test_signals_read_the_form_the_document_follows(monkeypatch) -> None:
    from app.services import form_files

    forms = form_files.TurnForms(forms={"pptx": form_files.FormChoice(
        "pptx", "f1", "양식.pptx", "attached")})
    out = await _signals(monkeypatch, kind=SessionKind.slides, forms=forms)
    assert out.forms == ("pptx",)
    # a Word form on a web report (no Word file asked) is not followed by this turn
    out = await _signals(monkeypatch, forms=forms)
    assert out.forms == ()
    # the conversation's kept form, for a report asked as a Word file
    session = _session()
    session.form_files = {"docx": "f2"}
    out = await _signals(monkeypatch, session=session, output_format="docx")
    assert out.forms == ("docx",)
    # the person took the saved one off
    session.form_files = {"docx": ""}
    out = await _signals(monkeypatch, session=session, output_format="docx")
    assert out.forms == ()


@pytest.mark.asyncio
async def test_signals_read_the_tool_heavy_deck_asks(monkeypatch) -> None:
    table = _file("매출.xlsx", "", "application/vnd.ms-excel")
    out = await _signals(monkeypatch, kind=SessionKind.slides, rows=[table],
                         request="매출 데이터로 차트가 있는 발표 자료를 만들어 줘")
    assert out.chart_data and not out.paper_figures
    # a chart word without data, or the same ask on a report, is not a tool-heavy deck
    out = await _signals(monkeypatch, kind=SessionKind.slides, request="차트가 있는 발표 자료")
    assert not out.chart_data
    out = await _signals(monkeypatch, rows=[table], request="차트가 있는 보고서")
    assert not out.chart_data
    paper = _file("paper.pdf", "본문", "application/pdf")
    out = await _signals(monkeypatch, kind=SessionKind.slides, rows=[paper],
                         request="이 논문의 그림을 넣은 발표 자료를 만들어 줘")
    assert out.paper_figures and sa.complexity(out) == ["tools"]
    out = await _signals(monkeypatch, kind=SessionKind.slides, rows=[paper],
                         request="이 논문을 요약한 슬라이드")
    assert not out.paper_figures


@pytest.mark.asyncio
async def test_slots_are_advisory_locks_shared_by_every_replica(monkeypatch) -> None:
    """On Postgres a slot is a session-level advisory lock on a connection of its own: another
    replica sees it taken, and a replica that dies lets go with its connection."""
    import asyncpg

    taken: dict[int, object] = {}
    opened: list = []

    class Conn:
        def __init__(self) -> None:
            self.closed = False

        async def fetchval(self, _sql, key):
            if key in taken and taken[key] is not self:
                return False
            taken[key] = self
            return True

        async def close(self, timeout=None):
            self.closed = True
            for key in [k for k, v in taken.items() if v is self]:
                del taken[key]

    async def connect(*_args, **_kwargs):
        opened.append(Conn())
        return opened[-1]

    monkeypatch.setattr(complex_turn, "_postgres", lambda: True)
    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 2)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 0.05)
    plans = [complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True) for _ in range(3)]
    for plan in plans[:2]:
        await _drain(plan)
    assert sorted(taken) == [complex_turn.LOCK_BASE, complex_turn.LOCK_BASE + 1]
    await _drain(plans[2])
    assert plans[2].use_fallback
    # every failed try closed its connection: nothing is left open but the two slots
    assert sum(not c.closed for c in opened) == 2
    await complex_turn.release(plans[0])
    assert sorted(taken) == [complex_turn.LOCK_BASE + 1]
    retry = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    await _drain(retry)
    assert retry.slot is not None and not retry.use_fallback


@pytest.mark.asyncio
async def test_a_report_written_as_an_office_file_routes_as_a_chat_turn_on_the_row() -> None:
    """HWPX, Word and PowerPoint files are worked on through the chat path: the same row, with
    tools, and (for an admin's other model) the document row's model behind it."""
    session = _session()

    async def route(doc, history=(), content="이 양식의 설계 위험을 분석해서 채워 줘",
                    policy=POLICY):
        return await sessions._super_route(
            db=None, user=USER, session=session, policy=policy, catalogue=CATALOGUE,
            default=LOCAL_27B, history=list(history), content=content,
            kind=SessionKind.chat, web_search=False, attachments=None, tool_bound=False,
            doc=doc)

    model, note, effort = await route(FORM)
    assert model["id"] == FLASH["id"] and effort == "high"
    assert note["row"] == "complex_document" and note["complexReason"] == "form"
    assert sessions._super_fallback(note, user=USER, catalogue=CATALOGUE, writer=model,
                                    strict_local=False, needs_tools=True,
                                    rules_raw=POLICY.super_agent_rules, kind="chat") is None
    model, note, effort = await route(FORM, policy=SLOW_ROW)
    assert model["id"] == SLOW["id"] and effort == "high"
    fallback = sessions._super_fallback(note, user=USER, catalogue=CATALOGUE, writer=model,
                                        strict_local=False, needs_tools=True,
                                        rules_raw=SLOW_ROW.super_agent_rules, kind="chat")
    assert fallback["id"] == FLASH["id"]
    # no signals: the ordinary class route, none of this
    model, note, _ = await route(sa.DocSignals())
    assert "complexReason" not in note and note.get("row") != "complex_document"


# ── the office-file path: a form turn the class rules call `long` or `reasoning` ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("office", [SessionKind.slides, SessionKind.report])
@pytest.mark.parametrize("text", [
    "이 양식에 맞춰 발표 자료를 채워 줘. 위험도 짚어 줘",
    "이 복학원서를 첨부한 정보로 채워 줘.",
    "회의록 원문입니다. 매출과 비용 항목을 정리했습니다.\n" * 600 + "\n이 양식에 맞춰 채워 줘",
])
async def test_a_form_turn_on_the_office_path_goes_to_the_complex_row_whatever_its_class(
        monkeypatch, office, text) -> None:
    import test_super_agent_turns as turns

    ext = turns.DEEPSEEK
    monkeypatch.setattr(turns, "CATALOGUE", [*CATALOGUE, ext])
    out = await turns._turn(monkeypatch, text, office=office, doc=FORM,
                            classify="reasoning")
    call = out["calls"][0]
    # the table's hard-class rows name the external DeepSeek; the complex row wins over them
    assert call["model"] == FLASH["id"]
    assert out["route"]["row"] == "complex_document"
    assert out["route"]["complexSignals"] == ["form"]
    assert call["fallback_model"] is None
    # an admin's other model in the row has the document row's flash behind it
    out = await turns._turn(monkeypatch, text, office=office, doc=FORM, classify="reasoning",
                            policy=Governance(external_data_guard=True,
                                              adaptive_classifier_model_id=STRICT_FLASH["id"],
                                              super_agent_rules=SLOW_ROW.super_agent_rules))
    call = out["calls"][0]
    assert call["model"] == SLOW["id"] and call["fallback_model"] == FLASH["id"]
    # the note is stored on the question and the answer, and audited
    assert out["audits"] and out["audits"][0].event_metadata["row"] == "complex_document"


@pytest.mark.asyncio
@pytest.mark.parametrize("office", [SessionKind.slides, SessionKind.report])
async def test_the_web_clients_model_field_does_not_keep_an_office_turn_off_super_agent(
        monkeypatch, office) -> None:
    """The report and deck screens always send the model they show; a conversation on Super
    Agent is routed by its mode (the observed bug: form turns ran on the external DeepSeek with
    no route note)."""
    import test_super_agent_turns as turns

    monkeypatch.setattr(turns, "CATALOGUE", [*CATALOGUE, turns.DEEPSEEK])
    out = await turns._turn(monkeypatch, "이 양식에 맞춰 채워 줘", office=office, doc=FORM,
                            payload_model=turns.DEEPSEEK["id"])
    assert out["calls"][0]["model"] == FLASH["id"]
    assert out["route"]["row"] == "complex_document"
    # a simple office turn is a document turn too: the `document` row, never the hard-class rows
    out = await turns._turn(monkeypatch, "이 슬라이드를 위험 분석 중심으로 고쳐 줘", office=office,
                            doc=sa.DocSignals(), payload_model=turns.DEEPSEEK["id"])
    assert out["calls"][0]["model"] == FLASH["id"] and out["route"]["effort"] == "none"
    assert out["route"]["taskClass"] == "document" and "row" not in out["route"]
    assert out["audits"]


@pytest.mark.asyncio
async def test_a_turn_behind_two_running_ones_shows_the_line_then_the_fallback_writes(
        monkeypatch) -> None:
    monkeypatch.setattr(complex_turn.settings, "complex_document_slots", 2)
    monkeypatch.setattr(complex_turn.settings, "complex_document_wait_sec", 0.2)
    monkeypatch.setattr(complex_turn, "POLL_SEC", 0.02)
    holders = [complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True) for _ in range(2)]
    for holder in holders:
        await _drain(holder)
    plan = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)

    async def events():
        yield 'data: {"type": "section", "content": "본문"}\n\n'
        yield 'data: {"type": "done"}\n\n'

    out = [json.loads(c.removeprefix("data: ")) async for c in sessions._with_slot(plan, events())]
    labels = [e.get("label") for e in out if e.get("id") == "complex-queue"]
    assert labels[0] == "대기 중 · 1번째" and labels[-1] == "다른 모델로 쓰기"
    assert plan.use_fallback and plan.slot is None
    assert out[-1]["type"] == "done"
    # the turns holding slots are untouched, and one finishing lets the next turn in
    await complex_turn.release(holders[0])
    nxt = complex_turn.Plan(SLOW["id"], FLASH["id"], queued=True)
    await _drain(nxt)
    assert nxt.slot is not None and not nxt.use_fallback


@pytest.mark.asyncio
async def test_a_failing_complex_model_hands_a_report_to_the_document_model(monkeypatch) -> None:
    """The fallback check that needs no dead model id in the table: a fake failing model."""
    from app.services import report

    _plan()
    seen: list[str] = []

    async def on(model, messages, api_key, max_tokens):
        seen.append(model)
        if model == SLOW["id"]:
            raise httpx.ReadTimeout("silent")
        return "본문", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete_on", on)
    assert (await report._complete(SLOW["id"], [], "key", 100))[0] == "본문"
    assert seen == [SLOW["id"], FLASH["id"]]
    assert SLOW["id"] in model_health.unhealthy()
