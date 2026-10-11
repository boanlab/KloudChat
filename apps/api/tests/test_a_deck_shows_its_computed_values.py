"""A deck shows the values code computed for it, and its quality check reads the slides
against them (a CPU scheduling lecture)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import calc, calc_check, deck
from app.services.tools.base import ToolResult

#: Two independent programs for the CPU scheduling lecture.
_RUN_A = [
    "동점 규칙 = 같은 조건이면 먼저 도착한 프로세스",
    "FCFS | 순서 = P1 0-7, P2 7-11, P3 11-12, P4 12-16",
    "FCFS | 평균 대기시간 = 4.75",
    "SJF(비선점) | 순서 = P1 0-7, P3 7-8, P2 8-12, P4 12-16",
    "SJF(비선점) | 평균 대기시간 = 4",
    "SRTF | 평균 대기시간 = 3",
    "라운드 로빈(타임 퀀텀 2) | 평균 대기시간 = 5",
    "라운드 로빈(타임 퀀텀 4) | 평균 대기시간 = 4.5",
]
_RUN_B = [
    "FCFS | 순서 = P1 0-7, P2 7-11, P3 11-12, P4 12-16",
    "FCFS | 평균 대기시간 = 4.750",
    "SJF(비선점) | 순서 = P1 0-7, P3 7-8, P2 8-12, P4 12-16",
    "SJF(비선점) | 평균 대기시간 = 4.000",
    "SRTF | 평균 대기시간 = 3.000",
    "라운드 로빈(타임 퀀텀 2) | 평균 대기시간 = 5.000",
    "라운드 로빈(타임 퀀텀 4) | 평균 대기시간 = 4.500",
]

LECTURE = (
    "운영체제 50분 강의용 슬라이드 만들어 줘. 주제는 CPU 스케줄링 — FCFS, SJF(비선점), SRTF, "
    "라운드 로빈(타임 퀀텀 2). 10장 내외.\n"
    "아래 예제 프로세스로 평균 대기시간을 계산해서 비교해 줘.\n\n"
    "P1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2"
)


def test_two_runs_of_a_procedure_agree_row_by_row():
    # Each row prints its order and its average on separate lines under one key: two items,
    # not one item 「FCFS」 the two runs could never agree on.
    assert calc.agree(_RUN_A, _RUN_B) and calc.agree(_RUN_B, _RUN_A)
    kept = calc.confirmed(_RUN_A, _RUN_B)
    assert "FCFS | 평균 대기시간 = 4.75" in kept and "SRTF | 평균 대기시간 = 3" in kept
    # Quantum 2 and quantum 4 are two rows, not one.
    names = {name for line in _RUN_A for name, _ in calc._items(line)}
    assert "라운드로빈타임퀀텀2|평균대기시간" in names
    assert "라운드로빈타임퀀텀4|평균대기시간" in names
    wrong = [*_RUN_B[:5], "SRTF | 평균 대기시간 = 2.25",
             "라운드 로빈(타임 퀀텀 2) | 평균 대기시간 = 9",
             "라운드 로빈(타임 퀀텀 4) | 평균 대기시간 = 5"]
    assert not calc.agree(_RUN_A, wrong)


class _Backends:
    exec = "http://sandbox"


@pytest.mark.asyncio
async def test_a_procedure_whose_check_run_runs_late_keeps_the_finished_programs_values(
    monkeypatch,
):
    async def tools_config():
        return _Backends()

    calls = {"n": 0}

    async def complete(model, messages, api_key, max_tokens=0):
        calls["n"] += 1
        if calls["n"] > 1:
            await asyncio.sleep(5)
        return "```python\nprint('x')\n```", {"inputTokens": 1, "outputTokens": 1}

    async def execute_code(args):
        return ToolResult(content="stdout:\n" + "\n".join(_RUN_A))

    from app.services.tools import builtin

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin, "execute_code", execute_code)
    block, _, status = await calc.computed_values(
        LECTURE, [], complete=complete, model="m", api_key="k", deadline=0.3
    )
    assert status == "done" and "FCFS | 평균 대기시간 = 4.75" in block
    # Not a procedure: one program alone is not enough.
    calls["n"] = 0
    block, _, status = await calc.computed_values(
        "아래 측정값으로 이득을 계산해 줘.\n\n---\n1 2 3 4 5 6", [], complete=complete, model="m",
        api_key="k", deadline=0.3,
    )
    assert (block, status) == ("", "timeout")


_BLOCK = "# 코드로 계산한 값\n설명.\n\n" + "\n".join(_RUN_A)


def _v1_deck() -> list[dict]:
    """A drafted deck's slides with hand-worked values that disagree with the computed ones."""
    return [
        {"layout": "bullets", "title": "선입선출(FCFS) 스케줄링",
         "bullets": ["도착 순서대로 실행, 선점 없음", "간트: P1(7) P2(4) P3(1) P4(4)",
                     "평균 대기시간 = 2"],
         "notes": "P1이 0에 도착해 7을 쓰고, P2가 7부터 11까지 실행합니다."},
        {"layout": "bullets", "title": "라운드 로빈(타임 퀀텀 2) 스케줄링",
         "bullets": ["타임 퀀텀 2 단위 로터리 실행", "평균 대기시간 = 9", "전체 완료 시간 16"],
         "notes": "평균 대기시간이 FCFS보다 2배 길어질 수 있습니다."},
        {"layout": "table", "title": "알고리즘 성능 종합 비교",
         "rows": [["지표", "FCFS", "SJF(비선점)", "라운드 로빈(타임 퀀텀 2)"],
                  ["평균 대기", "2", "최소", "9"], ["선점 여부", "없음", "없음", "있음"]]},
        {"layout": "bullets", "title": "핵심 요약",
         "bullets": ["FCFS 평균 대기시간 2, SRTF 2.5", "알고리즘은 상황별 선택"],
         "notes": "FCFS 4.8, SJF(비선점) 3, 라운드 로빈(타임 퀀텀 4) 5가 평균 대기시간입니다."},
    ]


def test_slide_numbers_naming_a_computed_value_are_set_to_it():
    slides = _v1_deck()
    touched, corrected = calc_check.check_slides(slides, _BLOCK)
    assert touched == [0, 1, 2, 3]
    assert slides[0]["bullets"][2] == "평균 대기시간 = 4.75"
    # Numbers that are not the computed value stay: the gantt, the run count, 「2배」.
    assert slides[0]["bullets"][1] == "간트: P1(7) P2(4) P3(1) P4(4)"
    assert slides[1]["bullets"][1:] == ["평균 대기시간 = 5", "전체 완료 시간 16"]
    assert slides[1]["notes"] == "평균 대기시간이 FCFS보다 2배 길어질 수 있습니다."
    assert slides[2]["rows"][1] == ["평균 대기", "4.75", "4", "5"]
    assert slides[2]["rows"][2] == ["선점 여부", "없음", "없음", "있음"]
    assert slides[3]["bullets"][0] == "FCFS 평균 대기시간 4.75, SRTF 3"
    # 4.8 is 4.75 rounded; the rest were wrong.
    assert slides[3]["notes"] == (
        "FCFS 4.8, SJF(비선점) 4, 라운드 로빈(타임 퀀텀 4) 4.5가 평균 대기시간입니다.")
    assert any("「9」 → 5" in c for c in corrected)


def test_a_slide_naming_two_rows_or_no_printed_value_is_left_alone():
    slides = [
        {"layout": "bullets", "title": "SJF와 SRTF",
         "bullets": ["평균 대기시간은 3"], "notes": "반환 시간 8.75."},
        {"layout": "bullets", "title": "개요", "bullets": ["평균 대기시간 = 7"]},
    ]
    before = json.loads(json.dumps(slides))
    assert calc_check.check_slides(slides, _BLOCK) == ([], [])
    assert slides == before


async def _deck_with(monkeypatch, draft: dict, computed: str) -> list[dict]:
    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    plan = [{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]]
    return [
        event
        async for event in deck._write_slides(
            plan=plan, title="CPU 스케줄링", subtitle="", accent="", request=LECTURE,
            model="m", api_key="k", trusted_context=[computed], untrusted_context=[],
            usage={"inputTokens": 0, "outputTokens": 0}, computed=computed,
        )
    ]


@pytest.mark.asyncio
async def test_the_written_deck_shows_the_computed_values_and_its_check_says_so(monkeypatch):
    draft = {"slides": [
        {"title": "CPU 스케줄링", "layout": "title"},
        {"title": "선입선출(FCFS) 스케줄링", "layout": "bullets",
         "bullets": ["도착 순서대로 실행", "평균 대기시간 = 2"],
         "notes": "도착 순서대로 실행합니다. 짧은 작업이 오래 기다릴 수 있습니다."},
        {"title": "라운드 로빈(타임 퀀텀 2)", "layout": "bullets",
         "bullets": ["타임 퀀텀 2 단위로 돌아가며 실행", "평균 대기시간 = 9"],
         "notes": "퀀텀이 끝나면 큐 뒤로 갑니다. 응답이 고르게 빠릅니다."},
    ]}
    events = await _deck_with(monkeypatch, draft, _BLOCK)
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    assert [s["title"] for s in slides] == [s["title"] for s in draft["slides"]]
    assert "평균 대기시간 = 4.75" in slides[1]["bullets"]
    assert "평균 대기시간 = 5" in slides[2]["bullets"]
    quality = next(e for e in events if e.get("id") == "quality")
    assert "계산 값 2곳 바로잡음" in quality["label"]
    assert "「2」 → 4.75" in quality["detail"]
