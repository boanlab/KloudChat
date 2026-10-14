"""A chat turn that ran code keeps the values the code printed (verify2 S08 handoff): an answer
that calls the code's output wrong and 「corrects」 it by hand is sent back once to rerun the
code; without a new run its hand values give way to what the code printed. A number naming a
printed quantity is set to it, and the deck built from such a conversation is read against
the computed values, its zero-only chart rows dropped."""

from __future__ import annotations

import copy

import pytest

from app.services import agent, calc_check, deck
from app.services.tools.base import Tool, ToolContext, ToolResult

#: What the S08 simulation printed (the averages a simulation gives for the four processes).
_PRINTED = (
    "stdout:\n"
    "동점 규칙: 먼저 도착한 프로세스 먼저\n"
    "=== FCFS ===\n"
    "Gantt: P1(0-7) P2(7-11) P3(11-12) P4(12-16)\n"
    "Average waiting time: 4.75\n"
    "=== SRTF ===\n"
    "Gantt: P1(0-2) P2(2-4) P3(4-5) P2(5-7) P4(7-11) P1(11-16)\n"
    "Average waiting time: 3.00\n"
    "=== RR (q=2) ===\n"
    "Average waiting time: 5.00\n"
    "=== RR (q=4) ===\n"
    "Average waiting time: 4.50"
)

#: The S08 chat answer's shape (qf/scenarios/08_lecture_slides/handoff/chat.md): a section per
#: algorithm with hand-traced tables, a claim the code was wrong, and a summary table.
_ANSWER = """# CPU 스케줄링 시뮬레이션 결과

## 1. FCFS (First-Come, First-Served)

| 구간 | 프로세스 |
|------|----------|
| 0–7 | P1 |
| 7–11 | P2 |

**평균 대기시간: 4.75**

## 3. SRTF (Shortest Remaining Time First, 선점)

| 프로세스 | 도착 | 시작 | 대기시간 |
|----------|------|------|----------|
| P1 | 0 | 0 | 0 |
| P4 | 5 | 7 | 2 |

**평균 대기시간: 0.50**

> 위 간트 차트와 대기시간은 코드 로직을 따라 수동으로 추적한 값입니다.

## 4. Round Robin (타임 퀀텀 = 2)

**평균 대기시간: 2.50**

> 코드 출력의 P1 대기시간 2는 P1이 두 번째로 큐에서 나온 시점을 시작으로 잘못 잡은 결과입니다.
> 위 표는 수동 추적으로 수정한 값입니다.

## 전체 평균 대기시간 비교

| 알고리즘 | 평균 대기시간 |
|----------|---------------|
| FCFS | 4.75 |
| SRTF | 0.50 |
| RR (q=2) | 2.50 |
| RR (q=4) | 3.00 |

RR은 타임 퀀텀이 커질수록(2→4) 컨텍스트 스위칭은 줄어듭니다."""


def test_printed_values_are_read_by_section_and_a_later_run_wins():
    stated = calc_check.printed_values([agent.printed_text(_PRINTED)])
    got = {(s.row, s.label): s.text for s in stated}
    assert got[("SRTF", "Average waiting time")] == "3.00"
    # 「RR (q=2)」 is a section name, not 「RR (q」 = 2.
    assert got[("RR (q=2)", "Average waiting time")] == "5.00"
    assert got[("RR (q=4)", "Average waiting time")] == "4.50"
    # The measure word splits a row out of a one-line value; a rerun's value replaces the first.
    later = calc_check.printed_values([
        agent.printed_text(_PRINTED), "SRTF 평균 대기시간: 2.75\nAverage waiting time (FCFS): 4.5"])
    got = {(s.row, s.label): s.text for s in later}
    assert got[("SRTF", "평균 대기시간")] == "2.75"
    assert got[("FCFS", "Average waiting time")] == "4.5"


def test_the_s08_answer_numbers_naming_a_printed_value_are_set_to_it():
    checked, fixes = agent.code_checked(_ANSWER, [_PRINTED])
    assert "**평균 대기시간: 3.00**" in checked and "**평균 대기시간: 5.00**" in checked
    assert "| SRTF | 3.00 |" in checked and "| RR (q=2) | 5.00 |" in checked
    assert "| RR (q=4) | 4.50 |" in checked
    assert "0.50" not in checked and "2.50" not in checked
    # Values the code did not print for that row stay: FCFS, the per-process table, 「2→4」.
    assert "**평균 대기시간: 4.75**" in checked and "| P4 | 5 | 7 | 2 |" in checked
    assert "(2→4)" in checked
    assert len(fixes) == 5 and any("「0.50」 → 3.00" in f for f in fixes)


def test_claims_that_set_the_code_aside_are_found_and_agreeing_checks_are_not():
    claims = agent.override_claims(_ANSWER)
    assert len(claims) == 3
    assert any("수동으로 추적한 값" in c for c in claims)
    assert any("잘못 잡은 결과" in c for c in claims)
    for fine in (
        "손으로 검산해도 4.75로 코드 출력과 같습니다.",
        "코드 실행 결과 오류 없이 4.75가 나왔습니다.",
        "첫 실행에서 오류가 나 코드를 고쳐 다시 실행했습니다.",
        "```python\n# 수동으로 추적한 값 대신 코드로\n```",
    ):
        assert agent.override_claims(fine) == [], fine
    assert agent.override_claims("The code output is wrong, so I traced it by hand.")


def _stream(monkeypatch, steps, snapshots, hops=1):
    async def completion(_model, messages, tools, *_args, **kwargs):
        snapshots.append((copy.deepcopy(messages), [tool.name for tool in tools]))
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


def _code(ran, outputs):
    async def run(arguments):
        ran.append(arguments)
        return ToolResult(content=outputs[min(len(ran) - 1, len(outputs) - 1)])

    return Tool(name="run_code", description="code", parameters={"type": "object"}, run=run,
                label="코드 실행")


def _call(n):
    return {"id": f"c{n}", "name": "run_code", "arguments": f'{{"code": "print({n})"}}'}


async def _turn(tools):
    text = ""
    async for event in agent.run_turn(
        "synthetic/qwen",
        [{"role": "user", "content": (
            "P1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2 로 FCFS·SRTF·RR 평균 대기시간을 "
            "코드로 시뮬레이션해 줘")}],
        tools,
        ToolContext(user_id="u", session_id="s"),
    ):
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            at = text.rfind(event["text"])
            text = text[:at] + text[at + len(event["text"]):] if at >= 0 else text
    return text


@pytest.mark.asyncio
async def test_a_hand_correction_at_the_cap_is_sent_back_and_without_a_rerun_the_code_stands(
    monkeypatch,
):
    ran, snapshots = [], []
    # Two code hops on a one-hop cap (the second still runs), then the closing answer sets the
    # code aside by hand — twice, never rerunning.
    _stream(monkeypatch, [{"calls": [_call(1)]}, {"calls": [_call(2)]}, {"text": _ANSWER}],
            snapshots)
    text = await _turn([_code(ran, [_PRINTED])])
    assert len(ran) == 2
    # The closing answer was sent back once with the tools on, asking to rerun or keep the code.
    asked = [str(m[0][-1]["content"]) for m in snapshots]
    assert any("고친 코드를 run_code 로 다시 실행" in a for a in asked)
    assert snapshots[3][1] == ["run_code"]
    # No new run: the hand-worked answer is taken back and the printed values shown, said so.
    assert text.startswith("_이 답은 코드 실행 출력값을 틀렸다며 손으로 셈한 값으로 바꿨지만")
    assert "Average waiting time: 3.00" in text and "Average waiting time: 5.00" in text
    assert "0.50" not in text and "2.50" not in text and "수동" not in text


@pytest.mark.asyncio
async def test_a_correction_backed_by_a_rerun_keeps_the_rerun_values(monkeypatch):
    ran, snapshots = [], []
    first = "stdout:\n=== SRTF ===\nAverage waiting time: 0.50"
    rerun = "stdout:\n=== SRTF ===\nAverage waiting time: 3.00"
    corrected = (
        "## SRTF\n\n**평균 대기시간: 3.00**\n\n처음 코드의 출력이 잘못되어 고친 코드를 다시 "
        "실행했습니다.")
    _stream(monkeypatch, [
        {"calls": [_call(1)]},
        {"text": "## SRTF\n\n**평균 대기시간: 3.00**\n\n코드 출력이 틀려 수동으로 추적한 "
                 "값입니다."},
        {"calls": [_call(2)]},
        {"text": corrected},
    ], snapshots, hops=3)
    text = await _turn([_code(ran, [first, rerun])])
    assert len(ran) == 2
    # The rerun printed 3.00 for SRTF after the first run's 0.50: the later run stands.
    assert text == corrected


@pytest.mark.asyncio
async def test_a_number_naming_a_printed_value_is_set_to_it_and_said(monkeypatch):
    ran, snapshots = [], []
    _stream(monkeypatch, [
        {"calls": [_call(1)]},
        {"text": "## 3. SRTF (선점)\n\n**평균 대기시간: 0.50**\n\nFCFS 평균 대기시간은 "
                 "4.75입니다."},
    ], snapshots, hops=3)
    text = await _turn([_code(ran, [_PRINTED])])
    assert text.startswith(
        "## 3. SRTF (선점)\n\n**평균 대기시간: 3.00**\n\nFCFS 평균 대기시간은 4.75")
    # said as a step, not a footer on the answer (R2W-04)
    assert "바로잡았습니다" not in text


#: The computed block the deck's calc writes (row names in the request's words).
_COMPUTED = "# 코드로 계산한 값\n설명.\n\n" + "\n".join([
    "FCFS | 평균 대기시간 = 4.75",
    "SJF(비선점) | 평균 대기시간 = 4",
    "SRTF | 평균 대기시간 = 3",
    "우선순위(비선점) | 평균 대기시간 = 5.5",
])


def test_the_s08_handoff_deck_reads_its_slides_against_the_computed_values():
    # Slides as the S08 handoff deck shipped them (artifact_deck.json slides 10, 13, 15).
    slides = [
        {"layout": "bullets", "title": "알고리즘별 평균 대기시간",
         "bullets": ["SRTF: 0.50 (최단)", "우선순위(비선점): 5.50 (최장)"]},
        {"layout": "takeaways", "title": "알고리즘별 핵심 특징 요약",
         "items": [["SRTF가 평균 대기시간 0.50으로 가장 우수", "단일 알고리즘 비교 최단"]]},
        {"layout": "closing", "title": "마무리 및 핵심 요점",
         "bullets": ["SRTF 평균 대기시간 0.50", "선점형이 대기시간 단축"]},
        {"layout": "chart", "title": "알고리즘별 평균 대기시간",
         "chart": {"kind": "bar", "unit": "", "categories": ["FCFS", "SJF", "SRTF"],
                   "series": [{"name": "평균 대기시간", "values": [4.75, 4, 0.5]}]}},
    ]
    touched, corrected = calc_check.check_slides(slides, _COMPUTED)
    assert touched == [0, 1, 2, 3]
    # The row named in the line, its label in the title.
    assert slides[0]["bullets"] == ["SRTF: 3 (최단)", "우선순위(비선점): 5.50 (최장)"]
    assert slides[1]["items"][0][0] == "SRTF가 평균 대기시간 3으로 가장 우수"
    assert slides[2]["bullets"][0] == "SRTF 평균 대기시간 3"
    assert slides[3]["chart"]["series"][0]["values"] == [4.75, 4, 3.0]
    assert any("「0.5」 → 3" in c for c in corrected)


def test_a_chart_row_of_zeros_is_dropped_and_categories_keep_their_brackets():
    # S08 handoff slide 6: 「SRTF | 0.0 | 0.0 | …」 over the gantt times.
    zeros = {"kind": "bar", "unit": "", "categories": ["0", "2", "4", "5", "7", "11", "16"],
             "series": [{"name": "SRTF", "values": [0, 0, 0, 0, 0, 0, 0]}]}
    assert deck._clean_chart(zeros) is None
    mixed = {"kind": "bar", "categories": ["FCFS", "SRTF"],
             "series": [{"name": "평균", "values": [4.75, 3]}, {"name": "빈", "values": [0, 0]}]}
    assert [s["name"] for s in deck._clean_chart(mixed)["series"]] == ["평균"]
    # S08 slide 9: both round-robin columns were cut to 「라운드 로빈 (q=」; r7 A09-T1: kept
    # whole, the renderers wrap them at their spaces
    named = {"kind": "bar", "categories": ["라운드 로빈 (q=2)", "라운드 로빈 (q=4)",
                                           "라운드 로빈(타임 퀀텀 2)", "SJF (비선점)"],
             "series": [{"name": "평균 대기시간", "values": [5, 4.5, 5, 4]}]}
    assert deck._clean_chart(named)["categories"] == [
        "라운드 로빈 (q=2)", "라운드 로빈 (q=4)", "라운드 로빈(타임 퀀텀 2)", "SJF (비선점)"]


def test_grouped_amounts_keep_their_commas_and_a_value_with_no_row_pins_nothing():
    printed = "stdout:\n=== 인건비 ===\n합계: 78000\n총계: 152760"
    answer = "## 인건비\n\n합계는 78,500원입니다.\n\n## 운영비\n\n합계는 74,760원입니다."
    checked, fixes = agent.code_checked(answer, [printed])
    assert checked == "## 인건비\n\n합계는 78,000원입니다.\n\n## 운영비\n\n합계는 74,760원입니다."
    assert fixes == ["「78,500」 → 78000 (인건비 합계)"]
    # 「합계: 152760」 printed with no row could be any table's total: nothing is changed.
    assert agent.code_checked("합계는 150,000원입니다.", ["stdout:\n합계: 152760"]) == (
        "합계는 150,000원입니다.", [])
