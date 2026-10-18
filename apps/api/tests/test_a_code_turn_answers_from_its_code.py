"""Code-run chat answers drawn from the code's own values."""

from __future__ import annotations

import copy

import pytest

from app.services import agent
from app.services.tools.base import Tool, ToolContext, ToolResult

_OUTPUT = (
    "stdout:\nFCFS | 평균 대기시간 = 4.75\nSRTF | 평균 대기시간 = 3.00\n"
    "RR(q=2) | 평균 대기시간 = 5.00"
)


def _stream(monkeypatch, steps, snapshots, hops=2):
    async def completion(_model, messages, tools, *_args, **kwargs):
        snapshots.append((copy.deepcopy(messages), [tool.name for tool in tools], kwargs))
        step = steps[min(len(snapshots) - 1, len(steps) - 1)]
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.content = [step["text"]] if step.get("text") else []
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    monkeypatch.setattr(agent.settings, "max_tool_hops", hops)


def _code(ran):
    async def run(arguments):
        ran.append(arguments)
        return ToolResult(content=_OUTPUT)

    return Tool(name="run_code", description="code", parameters={"type": "object"}, run=run,
                label="코드 실행")


def _call(n):
    return {"id": f"c{n}", "name": "run_code", "arguments": f'{{"code": "print({n})"}}'}


async def _turn(tools):
    events = [
        event
        async for event in agent.run_turn(
            "synthetic/qwen",
            [{"role": "user",
              "content": "P1: 0, 7\nP2: 2, 4 로 FCFS·SRTF·RR 평균 대기시간을 코드로"}],
            tools,
            ToolContext(user_id="u", session_id="s"),
        )
    ]
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            at = text.rfind(event["text"])
            text = text[:at] + text[at + len(event["text"]):] if at >= 0 else text
    return events, text


@pytest.mark.asyncio
async def test_code_called_at_the_cap_still_runs_and_the_answer_comes_from_it(monkeypatch):
    ran, snapshots = [], []
    _stream(monkeypatch, [
        {"calls": [_call(1)]}, {"calls": [_call(2)]}, {"calls": [_call(3)]},
        {"text": "FCFS 4.75, SRTF 3.00, RR(q=2) 5.00입니다."},
    ], snapshots)
    events, text = await _turn([_code(ran)])
    # The third batch is past the two-hop cap but is code: it runs instead of being refused.
    assert len(ran) == 3
    tool_messages = [m for m in snapshots[-1][0] if m.get("role") == "tool"]
    assert all("한도에 닿아" not in str(m["content"]) for m in tool_messages)
    assert snapshots[-1][1] == []
    assert "코드가 출력하지 않은 값을 손으로" in str(snapshots[-1][0][-1]["content"])
    assert text == "FCFS 4.75, SRTF 3.00, RR(q=2) 5.00입니다."


@pytest.mark.asyncio
async def test_hand_worked_values_are_sent_back_to_be_computed(monkeypatch):
    ran, snapshots = [], []
    _stream(monkeypatch, [
        {"calls": [_call(1)]},
        {"text": "FCFS 4.75, SRTF 2.25, 우선순위 1.75입니다. SRTF는 코드가 실행 한도에 막혀 "
                 "수동 계산했습니다."},
        {"calls": [_call(2)]},
        {"text": "FCFS 4.75, SRTF 3.00, RR(q=2) 5.00입니다."},
    ], snapshots, hops=1)
    events, text = await _turn([_code(ran)])
    asked = str(snapshots[2][0][-1]["content"])
    assert "2.25" in asked and "1.75" in asked and "4.75" not in asked
    # Granted one tool hop past the cap for it, then closed.
    assert snapshots[2][1] == ["run_code"] and len(ran) == 2 and snapshots[3][1] == []
    assert text == "FCFS 4.75, SRTF 3.00, RR(q=2) 5.00입니다."


@pytest.mark.asyncio
async def test_a_limit_claim_goes_and_values_still_unchecked_are_named(monkeypatch):
    ran, snapshots = [], []
    hand = ("FCFS 4.75, SRTF 2.25, 우선순위 1.75입니다. SRTF와 우선순위는 코드가 실행 한도에 "
            "막혀 실행되지 않아 수동 계산으로 구한 값입니다.")
    _stream(monkeypatch, [{"calls": [_call(1)]}, {"text": hand}, {"text": hand}], snapshots,
            hops=3)
    events, text = await _turn([_code(ran)])
    assert "한도" not in text
    assert text.startswith("FCFS 4.75, SRTF 2.25, 우선순위 1.75입니다.")
    # the unprinted values are named in the steps, never as a footnote under the answer
    assert "코드로 확인하지 못했습니다" not in text
    step = next(e for e in events if e.get("id") == "code-unprinted")
    assert step["detail"] == "2.25, 1.75"


def test_known_values_rounding_and_percentages_are_not_flagged():
    known = "평균 = 4.75\n비율 = 0.297\n합계 = 1,234.5"
    assert agent._unverified_figures("평균 4.8, 비율 29.7%, 합계 1234.50, 새 값 6.50", known) == [
        "6.50"]
    claimed = "값은 4.75입니다. 코드가 호출 한도에 걸려 손으로 셈했습니다.\n| 코드 | 한도에 막혀 |"
    assert agent.strip_limit_claims(claimed) == "값은 4.75입니다.\n| 코드 | 한도에 막혀 |"
