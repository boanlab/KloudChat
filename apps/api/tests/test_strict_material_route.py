"""Strict-local turns over internal material, and 「다시」 turns, go to self-hosted DeepSeek V4
Flash; when it fails mid-turn Qwen3.8 Flash carries the same turn on, and a model that failed
lately is not picked (owner decision 2026-11-14)."""

from __future__ import annotations

import asyncio
import json

import pytest
import test_super_agent_turns as turns
from test_super_agent_turns import DEEPSEEK, FLASH, STRICT_27B, STRICT_FLASH, _model, _turn

from app.models.chat import Message, Role
from app.models.governance import Governance
from app.routers import sessions as sessions_router
from app.services import agent, model_health
from app.services import super_agent as sa
from app.services.tools.base import Tool, ToolContext, ToolResult

STRICT_DS = _model("strict-local/deepseek-v4-flash", boundary="self_hosted", strict=True,
                   privacy_only=True)
MATERIAL_ASK = "첨부한 3분기 실적 자료에서 매출이 줄어든 이유를 근거와 함께 정리해 줘"


@pytest.fixture(autouse=True)
def _catalogue(monkeypatch):
    monkeypatch.setattr(turns, "CATALOGUE", [FLASH, STRICT_FLASH, STRICT_27B, DEEPSEEK,
                                             STRICT_DS])
    model_health.reset()
    yield
    model_health.reset()


def _material(monkeypatch, present: bool = True) -> None:
    async def material(*_args, **_kwargs):
        return present

    monkeypatch.setattr(sessions_router, "_material_present", material)


# ── the table ────────────────────────────────────────────────────────────────


def test_the_table_sends_strict_material_and_escalation_to_deepseek() -> None:
    rules = sa.DEFAULT_RULES
    assert rules["material"] == {"model": "", "strictModel": STRICT_DS["id"], "effort": "high"}
    assert rules["escalation"]["strictModel"] == STRICT_DS["id"]
    assert rules["escalation"]["effort"] == "high"
    # every other strict row is unchanged
    assert {r["strictModel"] for k, r in rules.items()
            if k not in ("material", "escalation", "document")} == {STRICT_FLASH["id"]}
    assert "material" in sa.CLASSES and "material" in sa.OVERRIDE_ROWS


def test_an_admin_may_empty_or_fill_the_material_row_but_no_other() -> None:
    rules = sa.normalise_rules({"material": {"strictModel": "", "model": DEEPSEEK["id"]},
                                "chat": {"model": ""}})
    assert rules["material"]["strictModel"] == "" and rules["material"]["model"] == DEEPSEEK["id"]
    assert rules["chat"]["model"] == sa.DEFAULT_RULES["chat"]["model"]
    assert sa.stored_rules({"material": {"strictModel": ""}}) == {"material": {"strictModel": ""}}


def test_steer_moves_material_turns_only_in_a_column_the_row_fills() -> None:
    rules = sa.normalise_rules({})
    reasoning = sa.Decision("reasoning", "rule", "reasoning_markers")
    assert sa.steer(rules, reasoning, sa.AUTO, material=True, strict=True) == ("material", "high")
    # super column empty: the class's own row
    assert sa.steer(rules, reasoning, sa.AUTO, material=True, strict=False) == (
        "reasoning", "high")
    for kept in ("simple", "document", "escalation"):
        row, _ = sa.steer(rules, sa.Decision(kept, "rule", "x"), sa.AUTO, material=True,
                          strict=True)
        assert row == kept
    # 빠르게 keeps the turn off the reasoning row; 깊게 goes to the escalation row
    assert sa.steer(rules, reasoning, sa.FAST, material=True, strict=True) == ("chat", "none")
    assert sa.steer(rules, reasoning, sa.DEEP, material=True, strict=True) == (
        "escalation", "high")


def test_the_strict_fallback_is_flash_for_any_other_writer() -> None:
    catalogue = [STRICT_FLASH, STRICT_27B, STRICT_DS]
    pick = sa.strict_fallback
    assert pick(catalogue, STRICT_DS, kind="chat", allowed=set())["id"] == STRICT_FLASH["id"]
    assert pick(catalogue, STRICT_27B, kind="chat", allowed=set())["id"] == STRICT_FLASH["id"]
    assert pick(catalogue, STRICT_FLASH, kind="chat", allowed=set()) is None


# ── routing through the send path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_strict_material_turn_goes_to_deepseek_with_flash_behind_it(monkeypatch) -> None:
    _material(monkeypatch)
    out = await _turn(monkeypatch, MATERIAL_ASK, mode="super_strict")
    call = out["calls"][0]
    assert call["model"] == STRICT_DS["id"]
    assert call["reasoning"][STRICT_DS["id"]] == {"chat_template_kwargs": {"thinking": True}}
    assert call["fallback_model"] == STRICT_FLASH["id"]
    # the fallback thinks as the writer would have
    assert STRICT_FLASH["id"] in call["reasoning"]
    assert out["route"]["row"] == "material" and out["route"]["taskClass"] != "material"
    assert out["route"]["effort"] == "high"


@pytest.mark.asyncio
async def test_a_strict_turn_without_material_stays_on_flash(monkeypatch) -> None:
    _material(monkeypatch, False)
    out = await _turn(monkeypatch, "마이크로서비스로 쪼갤지 판단해 줘. 위험도 같이.",
                      mode="super_strict")
    assert out["calls"][0]["model"] == STRICT_FLASH["id"]
    assert out["calls"][0]["fallback_model"] is None
    assert "row" not in out["route"]


@pytest.mark.asyncio
async def test_a_thanks_over_material_stays_simple(monkeypatch) -> None:
    _material(monkeypatch)
    out = await _turn(monkeypatch, "고마워요", mode="super_strict")
    assert out["calls"][0]["model"] == STRICT_FLASH["id"]


@pytest.mark.asyncio
async def test_asking_again_in_strict_escalates_to_deepseek(monkeypatch) -> None:
    _material(monkeypatch, False)
    history = [
        Message(session_id="s", role=Role.user, content="3년 원리금 계산해 줘"),
        Message(session_id="s", role=Role.assistant, content="약 1,200만 원입니다.", routing={
            "superAgent": {"selectedModel": STRICT_FLASH["id"], "taskClass": "calc"}}),
    ]
    out = await _turn(monkeypatch, "틀렸어. 다시 계산해 줘", mode="super_strict",
                      history=history)
    assert out["route"]["taskClass"] == "escalation"
    assert out["calls"][0]["model"] == STRICT_DS["id"]
    # the escalation writer falls back to flash too, not only the rescuer
    assert out["calls"][0]["fallback_model"] == STRICT_FLASH["id"]


@pytest.mark.asyncio
async def test_a_failed_deepseek_is_not_picked_while_it_is_down(monkeypatch) -> None:
    _material(monkeypatch)
    model_health.mark_failed(STRICT_DS["id"], "upstream_unreachable: ConnectError")
    out = await _turn(monkeypatch, MATERIAL_ASK, mode="super_strict")
    assert out["calls"][0]["model"] == STRICT_FLASH["id"]
    assert out["calls"][0]["fallback_model"] is None
    assert out["route"]["skipped"] == STRICT_DS["id"] and out["route"]["pick"] == "fallback"


@pytest.mark.asyncio
async def test_super_turns_outside_strict_are_unchanged(monkeypatch) -> None:
    _material(monkeypatch)
    # internal material allowed out: the material row's super column is empty
    out = await _turn(monkeypatch, MATERIAL_ASK, policy=Governance(
        external_data_guard=True, internal_data_strict_local=False))
    assert out["calls"][0]["model"] == DEEPSEEK["id"]
    assert out["calls"][0]["fallback_model"] is None
    assert "row" not in out["route"]
    # internal material kept inside: the strict column, so DeepSeek inside
    out = await _turn(monkeypatch, MATERIAL_ASK, policy=Governance(
        external_data_guard=True, internal_data_strict_local=True), project="p1")
    assert out["calls"][0]["model"] == STRICT_DS["id"]
    assert out["route"]["strictReason"] == "internal_data"


# ── the fallback inside one turn ─────────────────────────────────────────────


class _Response:
    def __init__(self, lines: list[str], status: int = 200, stall: float = 0.0,
                 drop_after: int | None = None) -> None:
        self.status_code, self._lines, self._stall, self._drop = status, lines, stall, drop_after
        self.headers: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aread(self) -> bytes:
        return b'{"error": "strict_local_unavailable"}'

    async def aiter_lines(self):
        import httpx

        if self._stall:
            await asyncio.sleep(self._stall)
        for index, line in enumerate(self._lines):
            if self._drop is not None and index == self._drop:
                raise httpx.RemoteProtocolError("peer closed connection")
            yield line


def _said(text: str) -> str:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}"


_USAGE = 'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":10}}'
_CALL = ('data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
         '"function":{"name":"search_knowledge","arguments":"{\\"query\\":\\"매출\\"}"}}]}}]}')
ANSWER = "3분기 매출은 주요 고객사 발주 감소로 12% 줄었습니다."


class _Client:
    def __init__(self, seen: list[dict], deepseek) -> None:
        self._seen, self._deepseek = seen, deepseek

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if json["model"] == STRICT_DS["id"]:
            return self._deepseek(len([s for s in self._seen if s["model"] == STRICT_DS["id"]]))
        return _Response([_said(ANSWER), _USAGE, "data: [DONE]"])


async def _run(monkeypatch, deepseek, *, fallback: str | None = STRICT_FLASH["id"]):
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen, deepseek)

    async def search(_args):
        return ToolResult(content="3분기 매출 120억 원, 전분기 136억 원. 주요 고객사 발주 감소.")

    monkeypatch.setattr(agent, "_client", client)
    monkeypatch.setattr(agent, "_FALLBACK_PATIENCE", 0.2)
    tool = Tool(name="search_knowledge", description="자료 검색", parameters={"type": "object"},
                run=search, label="자료 찾기", read_only=True)
    events = [
        event async for event in agent.run_turn(
            STRICT_DS["id"],
            [{"role": "system", "content": "시스템"}, {"role": "user", "content": MATERIAL_ASK}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key",
                        request=MATERIAL_ASK),
            strict_local=True,
            **({"fallback_model": fallback} if fallback else {}),
        )
    ]
    return events, seen


def _text(events: list[dict]) -> str:
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


@pytest.mark.asyncio
async def test_a_busy_gateway_hands_the_turn_to_flash_at_once(monkeypatch) -> None:
    events, seen = await _run(monkeypatch, lambda _n: _Response([], status=503))
    assert ANSWER in _text(events)
    switch = next(e for e in events if e["type"] == "writer_fallback")
    assert switch == {"type": "writer_fallback", "from": STRICT_DS["id"],
                      "to": STRICT_FLASH["id"], "reason": "busy"}
    assert [s["model"] for s in seen] == [STRICT_DS["id"], STRICT_FLASH["id"]]
    # never outside strict-local, no gateway fallback either
    assert all(s["disable_fallbacks"] is True for s in seen)
    assert not model_health.healthy(STRICT_DS["id"])
    assert model_health._until[STRICT_DS["id"]] - asyncio.get_running_loop().time() < 61


@pytest.mark.asyncio
async def test_a_broken_stream_mid_answer_is_taken_back_and_flash_writes_it(monkeypatch) -> None:
    events, seen = await _run(monkeypatch, lambda _n: _Response(
        [_said("3분기 매출은"), _said(" 줄었"), "data: [DONE]"], drop_after=2))
    assert _text(events).strip() == ANSWER
    assert any(e["type"] == "retract" for e in events)
    assert next(e for e in events if e["type"] == "writer_fallback")["reason"] == "error"
    assert seen[-1]["model"] == STRICT_FLASH["id"]


@pytest.mark.asyncio
async def test_a_long_silence_hands_the_turn_to_flash(monkeypatch) -> None:
    events, seen = await _run(monkeypatch, lambda _n: _Response(
        [_said("늦은 답"), "data: [DONE]"], stall=5.0))
    assert _text(events).strip() == ANSWER
    assert next(e for e in events if e["type"] == "writer_fallback")["reason"] == "timeout"
    steps = [e for e in events if e.get("id") == "writer-thinking"]
    # the long think is announced while it runs, then said what happened
    assert steps[0]["status"] == "running" and steps[-1]["label"] == "다른 모델로 이어 쓰기"
    assert not model_health.healthy(STRICT_DS["id"])


@pytest.mark.asyncio
async def test_after_tool_hops_flash_carries_on_and_deepseek_tokens_stay_its_own(
        monkeypatch) -> None:
    # DeepSeek searches the material, then fails while writing
    def deepseek(n: int) -> _Response:
        if n == 1:
            return _Response([_CALL, _USAGE, "data: [DONE]"])
        return _Response([], status=502)

    events, seen = await _run(monkeypatch, deepseek)
    assert _text(events).strip() == ANSWER
    # flash continues the same conversation: the search result is in its request
    assert any(m.get("role") == "tool" for m in seen[-1]["messages"])
    usage = next(e for e in events if e["type"] == "usage")
    assert usage[f"model:{STRICT_DS['id']}:inputTokens"] == 100
    assert usage["inputTokens"] == 100 and usage["outputTokens"] == 10  # flash's own


@pytest.mark.asyncio
async def test_a_writer_that_answers_is_left_as_it_was(monkeypatch) -> None:
    events, seen = await _run(monkeypatch, lambda _n: _Response(
        [_said(ANSWER), _USAGE, "data: [DONE]"]))
    assert _text(events).strip() == ANSWER
    assert not any(e["type"] == "writer_fallback" for e in events)
    assert [s["model"] for s in seen] == [STRICT_DS["id"]]
    assert model_health.healthy(STRICT_DS["id"])


@pytest.mark.asyncio
async def test_without_a_fallback_a_failure_still_ends_the_turn(monkeypatch) -> None:
    with pytest.raises(agent.ChatStreamError):
        await _run(monkeypatch, lambda _n: _Response([], status=500), fallback=None)


@pytest.mark.asyncio
async def test_the_answer_says_which_model_carried_it_and_bills_both(monkeypatch) -> None:
    _material(monkeypatch)

    async def answer(model_id, messages, tools, _context, **kwargs):
        yield {"type": "writer_fallback", "from": model_id, "to": kwargs["fallback_model"],
               "reason": "timeout"}
        yield {"type": "delta", "text": ANSWER}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 5,
               f"model:{model_id}:inputTokens": 7, f"model:{model_id}:outputTokens": 3}

    out = await _turn(monkeypatch, MATERIAL_ASK, mode="super_strict", answer_stub=answer)
    route = out["route"]
    assert route["selectedModel"] == STRICT_DS["id"]
    assert route["fallbackModel"] == STRICT_FLASH["id"] and route["fallbackReason"] == "timeout"
    assert out["answer"].model == STRICT_FLASH["id"]


_THOUGHT = 'data: {"choices":[{"delta":{"reasoning_content":"매출 감소 근거를 찾자."}}]}'


@pytest.mark.asyncio
async def test_deepseek_reads_its_own_reasoning_back_between_tool_steps(monkeypatch) -> None:
    def deepseek(n: int) -> _Response:
        if n == 1:
            return _Response([_THOUGHT, _CALL, _USAGE, "data: [DONE]"])
        return _Response([_said(ANSWER), _USAGE, "data: [DONE]"])

    events, seen = await _run(monkeypatch, deepseek)
    assert _text(events).strip() == ANSWER
    step = next(m for m in seen[1]["messages"] if m.get("tool_calls"))
    assert step["reasoning_content"] == "매출 감소 근거를 찾자."


@pytest.mark.asyncio
async def test_the_fallback_never_reads_the_failed_writers_reasoning(monkeypatch) -> None:
    def deepseek(n: int) -> _Response:
        if n == 1:
            return _Response([_THOUGHT, _CALL, _USAGE, "data: [DONE]"])
        return _Response([], status=503)

    events, seen = await _run(monkeypatch, deepseek)
    assert seen[-1]["model"] == STRICT_FLASH["id"]
    assert not any("reasoning_content" in m for m in seen[-1]["messages"])
    assert any(m.get("tool_calls") for m in seen[-1]["messages"])
