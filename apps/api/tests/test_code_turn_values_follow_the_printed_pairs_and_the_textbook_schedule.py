"""Round-2 R2W-04: a code turn's answer keeps the value each printed 「label = value」 pair
names (「RR TQ2 avg wait=5.25 avg resp=4.75」 is two values, not one), leaves signed and
percent figures alone, says its corrections as a step rather than a footer, never narrates a
code tool's failure, and a CPU scheduling exercise is read against the textbook run of the
request's own process table (sc/a08_calc, sc/n08_ts)."""

from __future__ import annotations

import copy

import pytest

from app.services import agent, calc_check, internal_words, scheduling
from app.services.tools.base import Tool, ToolContext, ToolResult

#: the verify2 S08 prompt as the professor sent it (sc/a08_calc/spec.json)
_REQUEST = (
    "아래 예제 프로세스로 FCFS, SJF(비선점), SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위(비선점, "
    "선점) 스케줄링을 코드로 시뮬레이션해서, 알고리즘마다 간트 차트 구간(시작–끝, 프로세스)과 "
    "프로세스별 대기시간·평균 대기시간을 표로 정리해 줘. 퀴즈용으로 라운드 로빈 타임 퀀텀 4일 때 "
    "평균 대기시간도 같이. 같은 조건이면 먼저 도착한 프로세스가 먼저야.\n\n"
    "예제 (도착 시간, 실행 시간, 우선순위 — 숫자가 작을수록 높음)\n"
    "P1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2"
)

#: the shipped table's rows (sc/a08_calc/chat.md)
_TABLE = """**프로세스별 대기시간·응답시간, 평균**

| 알고리즘 | P1 대기 | P2 대기 | P3 대기 | P4 대기 | 평균 대기 | P1 응답 | 평균 응답 |
|---|---:|---:|---:|---:|---:|---:|---:|
| FCFS | 0 | 5 | 7 | 7 | 4.75 | 0 | 4.75 |
| SJF(비선점) | 0 | 6 | 3 | 7 | 4.0 | 0 | 4.0 |
| SRTF | 1 | 0 | 0 | 2 | 0.75 | 1 | 0.75 |
| RR(TQ2) | 2 | 5 | 7 | 7 | 5.25 | 0 | 4.75 |
| RR(TQ4) | 4 | 5 | 7 | 7 | 5.75 | 0 | 4.75 |
| 우선순위(비선점) | 0 | 5 | 11 | 6 | 5.5 | 0 | 5.5 |
| 우선순위(선점) | 1 | 0 | 11 | 1 | 3.25 | 1 | 3.25 |

SRTF는 선점 덕분에 이 예제에서 평균 대기시간 0.75로 가장 짧습니다."""


def test_a_line_of_several_printed_pairs_is_several_values_under_the_first_pairs_row():
    stated = calc_check.printed_values(["RR TQ2 avg wait=5.2500 avg resp=4.7500",
                                        "SRTF: wait=3.00, turnaround=7.25"])
    got = {(s.row, s.label): s.text for s in stated}
    assert got == {("RR TQ2", "avg wait"): "5.2500", ("RR TQ2", "avg resp"): "4.7500",
                   ("SRTF", "wait"): "3.00", ("SRTF", "turnaround"): "7.25"}


def test_the_printed_waiting_time_is_not_replaced_by_the_response_time_after_it():
    printed = ("stdout:\nRR TQ2 avg wait=5.2500 avg resp=4.7500\n"
               "RR TQ4 avg wait=5.7500 avg resp=4.7500")
    answer = "RR TQ2 평균 대기시간(avg wait)은 5.25, RR TQ4 avg wait는 5.75입니다."
    checked, fixes = agent.code_checked(answer, [printed])
    assert checked == answer and fixes == []


def test_signed_and_percent_figures_in_a_table_are_left_as_written():
    printed = ("stdout:\n(2) 증감률 (2026 vs 2025, 1~6월):\n출입자수: 9.4\n대출권수: -15.8\n"
               "전자자료_다운로드: 27.0")
    answer = ("## (2) 증감률 (2026 vs 2025, 1~6월)\n\n| 항목 | 증감률 |\n|---|---:|\n"
              "| 출입자수 | +9.4% |\n| 대출권수 | -15.8% |\n| 전자자료_다운로드 | +27.0% |")
    checked, fixes = agent.code_checked(answer, [printed])
    assert checked == answer and fixes == []


def test_the_textbook_run_of_the_s08_table_gives_the_expected_averages():
    averages = {}
    for readings in scheduling.reference(_REQUEST):
        first = readings[0][0]
        averages[first.row] = first.value
    assert averages == {"FCFS": 4.75, "SJF": 4.0, "SRTF": 3.0, "RR(q=2)": 5.0, "RR(q=4)": 4.5,
                        "우선순위(비선점)": 5.5, "우선순위(선점)": 5.0}


def test_the_shipped_s08_table_is_set_to_the_textbook_waiting_times():
    checked, fixes = scheduling.check(_TABLE, _REQUEST)
    rows = {line.split("|")[1].strip(): [c.strip() for c in line.split("|")[2:7]]
            for line in checked.splitlines() if line.startswith("| ") and "---" not in line}
    assert rows["FCFS"] == ["0", "5", "7", "7", "4.75"]
    assert rows["SRTF"] == ["9", "1", "0", "2", "3"]
    assert rows["RR(TQ2)"] == ["9", "3", "2", "6", "5"]
    assert rows["RR(TQ4)"] == ["5", "2", "4", "7", "4.5"]
    assert rows["우선순위(선점)"] == ["8", "0", "11", "1", "5"]
    # the response columns are not waiting times and stay
    assert "| SRTF | 9 | 1 | 0 | 2 | 3 | 1 | 0.75 |" in checked
    # the particle follows the new figure: 「0.75로」 → 「3으로」
    assert "평균 대기시간 3으로 가장 짧습니다" in checked
    assert len(fixes) >= 10


def test_round_robin_read_the_other_queueing_way_stands_and_other_setups_are_not_judged():
    other = "| 알고리즘 | 평균 대기 |\n|---|---:|\n| RR(q=2) | 6.25 |\n| RR(q=4) | 5.0 |"
    assert scheduling.check(other, _REQUEST) == (other, [])
    assert scheduling.reference(_REQUEST + "\n문맥 교환 시간은 1이다.") == []
    assert scheduling.reference("FCFS 평균 대기시간을 계산해 줘") == []


def test_a_sentence_telling_a_code_tool_run_by_name_is_dropped():
    text = ("직전 `run_code`는 코드 전달이 잘려 syntax error로 실패했고, 실행 결과가 없습니다. "
            "FCFS 평균 대기시간은 4.75입니다.\n\n```python\nrun_code()\n```")
    assert internal_words.strip_tool_apologies(text) == (
        "FCFS 평균 대기시간은 4.75입니다.\n\n```python\nrun_code()\n```")


def _stream(monkeypatch, steps, snapshots):
    async def completion(_model, messages, tools, *_args, **kwargs):
        snapshots.append(copy.deepcopy(messages))
        step = steps[min(len(snapshots) - 1, len(steps) - 1)]
        acc = agent._Accumulator()
        acc.calls = dict(enumerate(step.get("calls", [])))
        acc.content = [step["text"]] if step.get("text") else []
        acc.usage = {"inputTokens": 1, "outputTokens": 1}
        for text in acc.content:
            yield "delta", text
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)
    monkeypatch.setattr(agent.settings, "max_tool_hops", 3)


async def _turn(outputs, ran):
    async def run(arguments):
        ran.append(arguments)
        return ToolResult(content=outputs[min(len(ran) - 1, len(outputs) - 1)])

    tool = Tool(name="run_code", description="code", parameters={"type": "object"}, run=run,
                label="코드 실행")
    text, steps = "", []
    async for event in agent.run_turn("synthetic/qwen", [{"role": "user", "content": _REQUEST}],
                                      [tool], ToolContext(user_id="u", session_id="s")):
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            at = text.rfind(event["text"])
            text = text[:at] + text[at + len(event["text"]):] if at >= 0 else text
        elif event["type"] == "step":
            steps.append(event)
    return text, steps


def _call(n):
    return {"id": f"c{n}", "name": "run_code", "arguments": f'{{"task": "run {n}"}}'}


@pytest.mark.asyncio
async def test_a_wrong_simulation_is_sent_back_once_then_its_values_are_set_and_said_as_a_step(
    monkeypatch,
):
    snapshots, ran = [], []
    wrong = "stdout:\n=== SRTF ===\n평균 대기시간: 0.75"
    _stream(monkeypatch, [
        {"calls": [_call(1)]},
        {"text": "## SRTF\n\n**평균 대기시간: 0.75**"},
        {"text": "## SRTF\n\n**평균 대기시간: 0.75**"},
    ], snapshots)
    text, steps = await _turn([wrong], ran)
    asked = [str(m[-1]["content"]) for m in snapshots]
    assert sum("교과서 정의" in a for a in asked) == 1
    assert any("「0.75」 → 3 (SRTF 평균 대기시간)" in a for a in asked)
    assert text == "## SRTF\n\n**평균 대기시간: 3**"
    assert "바로잡았습니다" not in text
    assert any(s["id"] == "schedule-values" and s["detail"] == "값 1개 맞춤" for s in steps)


@pytest.mark.asyncio
async def test_a_code_value_correction_is_a_step_not_a_footer(monkeypatch):
    snapshots, ran = [], []
    printed = "stdout:\n=== FCFS ===\nAverage waiting time: 4.75"
    _stream(monkeypatch, [{"calls": [_call(1)]}, {"text": "FCFS 평균 대기시간은 4.70입니다."}],
            snapshots)
    text, steps = await _turn([printed], ran)
    assert text == "FCFS 평균 대기시간은 4.75입니다."
    assert any(s["id"] == "code-values" for s in steps)
