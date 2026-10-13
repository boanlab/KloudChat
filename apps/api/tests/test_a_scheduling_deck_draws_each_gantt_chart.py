"""r3 A09 T2 (CPU scheduling lecture): Claude gave every algorithm a Gantt chart; KloudChat's
27B wrote the intervals as a line of text (none for priority), flash wrote none. Each
algorithm's chart now comes from the code-run schedule as a table, the agenda follows the
deck's final slides, and a comparison table's 「Priority」 row is read against the computed
priority rows. Slide strings are the ones the runs gave."""

from __future__ import annotations

import json

import pytest

from app.services import calc_check, deck, scheduling

#: tasks/A09/T2/prompt.md as sent
LECTURE = (
    "운영체제(학부 3학년) 50분 강의용 슬라이드 만들어 줘. 주제는 CPU 스케줄링 — FCFS, SJF(비선점), "
    "SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위 스케줄링. 12장 안팎.\n"
    "아래 예제 프로세스 하나로 모든 알고리즘의 간트 차트와 평균 대기시간을 계산해서 비교해 주고, "
    "마지막에 비교 표와 확인 퀴즈 2문제를 넣어 줘.\n\n"
    "예제 (도착 시간, 실행 시간, 우선순위 — 숫자가 작을수록 높음)\n"
    "P1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2"
)


def _27b_deck() -> list[dict]:
    """out3/A09/T2/final.md, the algorithm slides and their neighbours."""
    return [
        {"layout": "title", "title": "CPU 스케줄링 알고리즘 비교"},
        {"layout": "agenda", "title": "목차",
         "bullets": ["스케줄링 기본 용어와 예제 프로세스", "FCFS 스케줄링", "FCFS 평균 대기시간",
                     "라운드 로빈(타임 퀀텀 2) 스케줄링", "라운드 로빈 평균 대기시간",
                     "우선순위 스케줄링", "네 가지 알고리즘의 평균 대기시간 비교", "확인 퀴즈"]},
        {"layout": "process", "title": "스케줄링 기본 용어와 예제 프로세스",
         "bullets": ["도착 시간, 실행 시간, 우선순위"]},
        {"layout": "bullets", "title": "FCFS 스케줄링",
         "bullets": ["도착 순서대로 순차 실행", "P1(0~7) → P2(7~11) → P3(11~12) → P4(12~16)",
                     "최소 대기: P1, 최대 대기: P3·P4"],
         "notes": "FCFS는 먼저 도착한 프로세스가 먼저 실행되는 방식입니다."},
        {"layout": "metrics", "title": "FCFS 평균 대기시간",
         "metrics": [["0", "P1 대기"], ["5", "P2 대기"], ["7", "P3 대기"], ["7", "P4 대기"]],
         "notes": "P1은 7-0-7=0, P2는 11-2-4=5, P3는 12-4-1=7, P4는 16-5-4=7입니다."},
        {"layout": "bullets", "title": "라운드 로빈(타임 퀀텀 2) 스케줄링",
         "bullets": ["타임 퀀텀 q=2로 돌아가며 실행", "최종 완료 시각 16"]},
        {"layout": "metrics", "title": "라운드 로빈 평균 대기시간",
         "metrics": [["5", "평균 대기시간"], ["9", "P1 대기"], ["6", "P4 대기"]]},
        {"layout": "bullets", "title": "우선순위 스케줄링",
         "bullets": ["비선점 — P1 → P2 → P4 → P3", "선점 — P2(2) → P4(5)가 P1을 선점",
                     "최대 대기 — P3 (11)"],
         "notes": "우선순위가 같으면 도착 순서가 결정합니다."},
        {"layout": "table", "title": "네 가지 알고리즘의 평균 대기시간 비교",
         "rows": [["알고리즘", "평균 대기", "평균 반환"], ["FCFS", "4.75", "8.75"],
                  ["SJF", "4", "8"], ["SRTF", "3", "7"], ["RR(q=2)", "5", "9"]]},
        {"layout": "bullets", "title": "확인 퀴즈",
         "bullets": ["Q1: SRTF에서 P3의 대기시간은?", "Q2: RR(q=2)에서 평균 반환시간은?"]},
    ]


def _cells(slide: dict) -> str:
    return "\n".join(" | ".join(row) for row in slide.get("rows") or [])


def test_each_algorithm_s_result_slide_becomes_its_gantt_chart():
    slides = _27b_deck()
    assert deck.place_gantt_charts(slides, LECTURE) == 3
    titles = [s["title"] for s in slides]
    fcfs = slides[titles.index("FCFS 평균 대기시간")]
    assert fcfs["layout"] == "table" and "metrics" not in fcfs
    assert fcfs["rows"] == [
        ["구간", "실행", "남은 시간 · 대기"],
        ["0–7", "P1", "완료, 대기 7−0−7 = 0"], ["7–11", "P2", "완료, 대기 11−2−4 = 5"],
        ["11–12", "P3", "완료, 대기 12−4−1 = 7"], ["12–16", "P4", "완료, 대기 16−5−4 = 7"],
        ["평균 대기", "", "(0 + 5 + 7 + 7) / 4 = 4.75"]]
    rr = slides[titles.index("라운드 로빈 평균 대기시간")]
    assert [r[:2] for r in rr["rows"][1:-1]] == [
        ["0–2", "P1"], ["2–4", "P2"], ["4–6", "P1"], ["6–7", "P3"], ["7–9", "P2"],
        ["9–11", "P4"], ["11–13", "P1"], ["13–15", "P4"], ["15–16", "P1"]]
    assert rr["rows"][-1][-1] == "(9 + 3 + 2 + 6) / 4 = 5"
    # the explanation slide keeps its points
    assert slides[titles.index("FCFS 스케줄링")]["layout"] == "bullets"
    # priority had one bullets slide: its chart follows it, both readings
    at = titles.index("우선순위 스케줄링")
    assert slides[at]["layout"] == "bullets"
    chart = slides[at + 1]
    assert chart["title"] == "우선순위 스케줄링 간트 차트"
    assert chart["rows"] == [
        ["방식", "간트 차트", "평균 대기"],
        ["비선점", "P1 0–7 → P2 7–11 → P4 11–15 → P3 15–16", "5.5"],
        ["선점", "P1 0–2 → P2 2–6 → P4 6–10 → P1 10–15 → P3 15–16", "5"]]
    assert chart["notes"] == ("비선점 방식의 평균 대기시간은 5.5입니다. "
                              "선점 방식의 평균 대기시간은 5입니다.")
    # nothing else moved: the comparison table and the quiz stand
    assert titles[-2:] == ["네 가지 알고리즘의 평균 대기시간 비교", "확인 퀴즈"]
    assert slides[-2]["rows"][1] == ["FCFS", "4.75", "8.75"]


def test_flash_s_wait_tables_and_a_two_algorithm_slide_get_their_charts():
    # out3/A09/T2/flash/final.md: 「…과 간트 차트」 slides holding only the waiting times, and
    # SRTF only named beside RR
    slides = [
        {"layout": "table", "title": "FCFS 스케줄링과 간트 차트",
         "rows": [["항목 (시간(ms))", "P1", "P2", "P3", "P4"],
                  ["대기시간", "0.0", "5.0", "7.0", "7.0"]]},
        {"layout": "bullets", "title": "SRTF와 RR 스케줄링",
         "bullets": ["SRTF 대기시간 평균 3", "P1 선점으로 9까지 대기", "RR(q=2) 대기시간 평균 5"]},
        {"layout": "bullets", "title": "RR 타임 퀀텀과 문맥 전환 오버헤드",
         "bullets": ["**작은 퀀텀의 단점** 문맥 전환 빈번 · 유휴 시간 낭비 가능"]},
    ]
    deck.place_gantt_charts(slides, LECTURE)
    titles = [s["title"] for s in slides]
    assert titles == ["FCFS 스케줄링과 간트 차트", "SRTF와 RR 스케줄링", "SRTF 간트 차트",
                      "RR 타임 퀀텀과 문맥 전환 오버헤드", "라운드 로빈(q=2) 간트 차트"]
    assert slides[0]["rows"][1] == ["0–7", "P1", "완료, 대기 7−0−7 = 0"]
    assert [r[:2] for r in slides[2]["rows"][1:-1]] == [
        ["0–2", "P1"], ["2–4", "P2"], ["4–5", "P3"], ["5–7", "P2"], ["7–11", "P4"],
        ["11–16", "P1"]]
    assert slides[2]["notes"] == "완료 순서는 P3 → P2 → P4 → P1입니다. 평균 대기시간은 3입니다."


def test_a_chart_already_on_a_table_stands_and_an_exact_count_adds_no_slide():
    shown = {"layout": "table", "title": "FCFS 간트 차트",
             "rows": [["구간", "실행"], ["0~7", "P1"], ["7~11", "P2"], ["11~12", "P3"],
                      ["12~16", "P4"]]}
    lone = {"layout": "bullets", "title": "SJF(비선점) 스케줄링",
            "bullets": ["도착한 프로세스 중 실행 시간이 가장 짧은 것 선택"],
            "notes": "선점은 없습니다."}
    request = LECTURE.replace("12장 안팎", "12장").replace(
        "SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위 스케줄링", "SJF")
    slides = [dict(shown), dict(lone)]
    assert deck.place_gantt_charts(slides, request, may_add=False) == 1
    assert slides[0] == shown
    assert len(slides) == 2 and slides[1]["layout"] == "table"
    assert slides[1]["notes"] == ("도착한 프로세스 중 실행 시간이 가장 짧은 것 선택. "
                                  "선점은 없습니다.")
    # no Gantt asked, nothing drawn
    plain = [dict(lone)]
    assert deck.place_gantt_charts(plain, request.replace("간트 차트와 ", "")) == 0


def test_the_agenda_follows_the_slides_that_ship():
    slides = _27b_deck()
    before = list(slides)
    deck.place_gantt_charts(slides, LECTURE)
    # 「확인 퀴즈」 still empty after its rewrite: dropped
    slides[:] = [s for s in slides if s["title"] != "확인 퀴즈"]
    deck.refresh_agenda(slides, before)
    agenda = slides[1]["bullets"]
    assert "확인 퀴즈" not in agenda and "우선순위 스케줄링 간트 차트" in agenda
    # a written agenda (the time split) only loses the dropped line
    timed = [{"layout": "agenda", "title": "목차", "bullets": ["도입 5분", "확인 퀴즈"]},
             {"layout": "bullets", "title": "확인 퀴즈", "bullets": ["Q1"]}]
    deck.refresh_agenda(timed[:1], timed)
    assert timed[0]["bullets"] == ["도입 5분"]


def test_a_priority_row_in_english_is_read_as_the_computed_priority_rows():
    # out2/A09/T2/final.md: 「| Priority | 2.0 |」 under the averages (5.5 or 5 by code)
    block = scheduling.computed_block(LECTURE)
    slides = [{"layout": "table", "title": "알고리즘 간 평균 대기시간 비교",
               "rows": [["알고리즘", "평균 대기시간"], ["FCFS", "6.0"], ["SJF", "4.0"],
                        ["SRTF", "1.25"], ["RR (q=2)", "10.0"], ["Priority", "2.0"]]}]
    touched, corrected = calc_check.check_slides(slides, block)
    assert touched == [0]
    assert slides[0]["rows"][1:] == [["FCFS", "4.75"], ["SJF", "4.0"], ["SRTF", "3"],
                                     ["RR (q=2)", "5"], ["우선순위(비선점)", "5.5"]]
    assert "1장 「Priority 2.0」 → 우선순위(비선점) 5.5 (평균 대기시간)" in corrected


@pytest.mark.parametrize(("row", "kept"), [
    # flash r3: 「| 우선순위 | 5.5 | 9.5 |」 is the non-preemptive reading, right as it is
    (["우선순위", "5.5", "9.5"], ["우선순위", "5.5", "9.5"]),
    (["Priority (preemptive)", "5.0", "9.0"], ["Priority (preemptive)", "5.0", "9.0"]),
    # the reading the row names is the one it is held to
    (["우선순위 (선점)", "5.5", "9"], ["우선순위 (선점)", "5", "9"]),
    # most values agree with the preemptive reading: its average is set, not the other's
    (["우선순위", "6", "9"], ["우선순위(선점)", "5", "9"]),
])
def test_a_bare_priority_row_keeps_the_reading_its_values_hold(row, kept):
    slides = [{"layout": "table", "title": "비교",
               "rows": [["알고리즘", "평균 대기시간", "평균 반환시간"], row]}]
    calc_check.check_slides(slides, scheduling.computed_block(LECTURE))
    assert slides[0]["rows"][1] == kept


@pytest.mark.asyncio
async def test_the_written_deck_ships_the_charts(monkeypatch):
    draft = {"slides": [
        {"title": "CPU 스케줄링", "layout": "title"},
        {"title": "목차", "layout": "agenda"},
        {"title": "FCFS 스케줄링", "layout": "bullets",
         "bullets": ["도착 순서대로 순차 실행", "P1(0~7) → P2(7~11) → P3(11~12) → P4(12~16)"],
         "notes": "먼저 도착한 프로세스가 먼저 실행됩니다. 긴 P1 뒤에서 P3가 오래 기다립니다."},
        {"title": "FCFS 평균 대기시간", "layout": "metrics",
         "metrics": [["4.75", "평균 대기시간"], ["8.75", "평균 반환시간"]],
         "notes": "P3와 P4가 가장 오래 기다립니다. 평균은 4.75입니다."},
    ]}

    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    request = LECTURE.replace(
        "FCFS, SJF(비선점), SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위 스케줄링", "FCFS")
    events = [e async for e in deck._write_slides(
        plan=[{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]],
        title="CPU 스케줄링", subtitle="", accent="", request=request, model="m", api_key="k",
        trusted_context=[], untrusted_context=[], computed=scheduling.computed_block(request),
        usage={"inputTokens": 0, "outputTokens": 0})]
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    result = next(s for s in slides if s["title"] == "FCFS 평균 대기시간")
    assert result["layout"] == "table" and result["rows"][-1][-1].endswith("= 4.75")
    assert slides[1]["bullets"] == ["FCFS 스케줄링", "FCFS 평균 대기시간"]
