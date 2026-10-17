"""Calculation handoff to the outline model when the writer will not call the calculator."""

from __future__ import annotations

import json

import pytest
from test_calculation_hold_copy import CALCULATION_HOLD
from test_tool_result_answer import _database

from app.routers import sessions
from app.services import agent
from app.services.chat import ChatStreamError
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import ToolContext

WRITER = "local/qwen3.8-27b"
FLASH = "local/qwen3.8-flash"
QUESTION = "한 달 구독료 35,800원을 12개월 내면 모두 얼마야?"
CALL = {"id": "c1", "name": "calculate", "arguments": json.dumps({"expression": "35800*12"})}


def _models(plans: dict[str, list], seen: list[dict]):
    """A fake model server: each model's replies in order; every call recorded."""
    used = {name: 0 for name in plans}

    async def stream(model, messages, offered, *_args, **options):
        seen.append({"model": model, "tools": [t.name for t in offered],
                     "options": dict(options), "messages": list(messages)})
        if model not in plans or used[model] >= len(plans[model]):
            raise ChatStreamError(f"upstream_503: {model} unavailable")
        plan = plans[model][used[model]]
        used[model] += 1
        acc = agent._Accumulator()
        acc.actual_model = model
        acc.usage = {"inputTokens": 100, "outputTokens": 10}
        if isinstance(plan, str):
            acc.content.append(plan)
            yield "delta", plan
        else:
            acc.calls = {0: dict(plan)}
        yield "done", acc

    return stream


async def _turn(monkeypatch, plans, *, handoff=FLASH, writer=WRITER, **kwargs):
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_stream_once", _models(plans, seen))
    ctx = ToolContext(user_id="synthetic", session_id="synthetic")
    events = [event async for event in agent.run_turn(
        writer, [{"role": "user", "content": QUESTION}], [CALCULATE], ctx,
        preflight_tool="calculate", calculation_required=True,
        calculation_handoff_model=handoff, **kwargs,
    )]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    return ctx, seen, text, events


async def test_writer_skips_the_calculator_and_flash_computes_for_a_verified_answer(monkeypatch):
    ctx, seen, text, events = await _turn(monkeypatch, {
        WRITER: ["429,600원입니다.", "429,600원입니다.", "모두 429,600원입니다."],
        FLASH: [CALL],
    })
    assert [hop["model"] for hop in seen] == [WRITER, WRITER, FLASH, WRITER]
    assert seen[2]["tools"] == ["calculate"]
    assert seen[2]["options"].get("force_tool") == "calculate"
    assert ctx.tool_calls == {"calculate": 1}
    assert text == "모두 429,600원입니다."
    assert CALCULATION_HOLD not in text
    # The writer answers with the calculator's result in front of it.
    assert any(m.get("role") == "tool" and "429600" in str(m.get("content"))
               for m in seen[3]["messages"])
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["inputTokens"] == 300 and usage["outputTokens"] == 30
    assert usage[f"model:{FLASH}:inputTokens"] == 100
    assert usage[f"model:{FLASH}:outputTokens"] == 10
    handoff = [e for e in events if e["type"] == "calculation_handoff"]
    assert handoff == [{"type": "calculation_handoff", "routedModel": FLASH,
                        "actualModel": FLASH}]
    # The writer stays the turn's model.
    assert [e["actualModel"] for e in events if e["type"] == "model_route"] == [WRITER]


async def test_the_strict_local_route_hands_off_only_inside_the_boundary(monkeypatch):
    twin = "strict-local/qwen3.8-flash"
    _, seen, text, _ = await _turn(monkeypatch, {
        "strict-local/qwen3.8-27b": ["429,600원", "429,600원", "429,600원입니다."],
        twin: [CALL],
    }, writer="strict-local/qwen3.8-27b", handoff=twin, strict_local=True)
    assert [hop["model"] for hop in seen][2] == twin
    assert all(hop["options"]["strict_local"] is True for hop in seen)
    assert text == "429,600원입니다."


def test_the_strict_route_hands_the_calculation_to_the_outline_model_s_twin():
    """The hand-off model is the turn's outline model, chosen under its boundary rules."""

    class _User:
        allowed_models = None

    catalogue = [
        {"id": FLASH, "dataBoundary": "self_hosted", "kinds": ["chat"]},
        {"id": "strict-local/qwen3.8-flash", "dataBoundary": "self_hosted", "kinds": ["chat"]},
        {"id": "vendor/x", "dataBoundary": "external", "kinds": ["chat"]},
    ]
    writer = {"id": "strict-local/qwen3.8-27b", "dataBoundary": "self_hosted"}
    chosen = sessions._planner_model(FLASH, user=_User(), catalogue=catalogue, kind="chat",
                                     writer=writer, strict_local=True)
    assert chosen["id"] == "strict-local/qwen3.8-flash"
    assert sessions._planner_model("vendor/x", user=_User(), catalogue=catalogue, kind="chat",
                                   writer=writer, strict_local=True) is None


async def test_when_the_hand_off_also_skips_the_note_stays(monkeypatch):
    ctx, seen, text, _ = await _turn(monkeypatch, {
        WRITER: ["429,600원", "429,600원"],
        FLASH: ["아마 429,600원"],
    })
    assert [hop["model"] for hop in seen] == [WRITER, WRITER, FLASH]
    assert text == CALCULATION_HOLD and ctx.tool_calls == {}


async def test_when_the_hand_off_model_is_unreachable_the_note_stays(monkeypatch):
    _, seen, text, events = await _turn(monkeypatch, {WRITER: ["429,600원", "429,600원"]})
    assert [hop["model"] for hop in seen] == [WRITER, WRITER, FLASH]
    assert text == CALCULATION_HOLD
    assert not any(e["type"] == "calculation_handoff" for e in events)


async def test_without_a_hand_off_model_the_writer_s_miss_is_the_note(monkeypatch):
    _, seen, text, _ = await _turn(monkeypatch, {WRITER: ["429,600원", "429,600원"]},
                                   handoff=None)
    assert len(seen) == 2 and text == CALCULATION_HOLD


async def test_a_writer_that_calls_the_calculator_never_hands_off(monkeypatch):
    _, seen, text, _ = await _turn(monkeypatch, {WRITER: [CALL, "429,600원입니다."]})
    assert [hop["model"] for hop in seen] == [WRITER, WRITER]
    assert text == "429,600원입니다."


@pytest.mark.parametrize("strict", [False, True])
async def test_the_turn_bills_both_models_and_names_both_on_its_line(monkeypatch, strict):
    user, session, _, _, added = _database(monkeypatch)
    prefix = "strict-local/" if strict else "local/"
    writer = {"id": prefix + "qwen3.8-27b", "label": "27B", "dataBoundary": "self_hosted",
              "kinds": ["chat"], "inputCreditCost": 10, "creditCost": 20,
              "strictLocal": strict}
    flash = {"id": prefix + "qwen3.8-flash", "label": "Flash", "dataBoundary": "self_hosted",
             "kinds": ["chat"], "inputCreditCost": 10, "creditCost": 20,
             "strictLocal": strict}
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_stream_once", _models({
        writer["id"]: ["429,600원", "429,600원", "모두 429,600원입니다."],
        flash["id"]: [CALL],
    }, seen))
    settlements: list[tuple] = []

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(sessions, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions, "_enrich_memory", nothing)
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sessions, "settle", lambda _db, _user, credits, **kwargs:
                        settlements.append((kwargs["reason"], credits, kwargs.get("model"))))
    chunks = [chunk async for chunk in sessions._run_turn(
        user_id=user.id, api_key="synthetic-noncredential", auto_memory=False,
        session_id=session.id, model=writer, messages=[{"role": "user", "content": QUESTION}],
        tools=[CALCULATE], first_user_message=QUESTION, is_first_turn=False,
        preflight_tool="calculate", calculation_required=True, calculation_handoff=flash,
    )]
    assert all(hop["options"]["strict_local"] is strict for hop in seen)
    answer = next(row for row in added if getattr(row, "role", None) is not None
                  and getattr(row, "usage", None))
    assert "429,600원" in answer.content and CALCULATION_HOLD not in answer.content
    assert answer.model == writer["id"]
    assert answer.routing["calculationModel"] == flash["id"]
    assert answer.usage[f"model:{flash['id']}:inputTokens"] == 100
    writer_credits = sessions.charge_for_tokens(writer, 300, 30)
    flash_credits = sessions.charge_for_tokens(flash, 100, 10)
    assert answer.usage["credits"] == writer_credits + flash_credits
    assert ("chat.calculation", flash_credits, flash["id"]) in settlements
    usage_event = next(json.loads(c.removeprefix("data: ")) for c in chunks
                       if '"type": "usage"' in c)
    assert usage_event["credits"] == writer_credits + flash_credits
