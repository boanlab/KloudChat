"""Scheduling decks keeping their example slide, slide count, table columns and agenda."""

from __future__ import annotations

from test_a_scheduling_deck_draws_each_gantt_chart import LECTURE

from app.services import deck

_EXAMPLE = [["프로세스", "도착 시간", "실행 시간", "우선순위"], ["P1", "0", "7", "3"],
            ["P2", "2", "4", "1"], ["P3", "4", "1", "4"], ["P4", "5", "4", "2"]]


def _dsp_deck() -> list[dict]:
    """A 14-slide scheduling outline before the Gantt pass."""
    return [
        {"layout": "title", "title": "CPU 스케줄링 알고리즘 비교"},
        {"layout": "agenda", "title": "발표 순서", "bullets": ["평가 기준과 예제"]},
        {"layout": "section", "title": "01. 평가 기준과 예제"},
        {"layout": "bullets", "title": "스케줄링을 판단하는 네 가지 지표",
         "bullets": ["대기시간", "반환시간", "응답시간", "처리량"]},
        {"layout": "table", "title": "예제 프로세스 구성과 우선순위", "rows": _EXAMPLE},
        {"layout": "bullets", "title": "FCFS 도착 순서대로 처리하기",
         "bullets": ["도착 순서대로 준비 큐에 넣는다",
                     "간트 차트: P1 0-7, P2 7-11, P3 11-12, P4 12-16",
                     "평균 대기시간 4.75, 평균 반환시간 8.75"]},
        {"layout": "bullets", "title": "SJF 비선점 방식의 특징",
         "bullets": ["원리 — 실행 시간이 가장 짧은 프로세스를 먼저 실행",
                     "간트 차트 — P1 0-7, P3 7-8, P2 8-12, P4 12-16",
                     "평균 대기시간 — 4.0으로 FCFS보다 짧다"]},
        {"layout": "bullets", "title": "SRTF는 선점형 SJF",
         "bullets": ["남은 실행 시간이 가장 짧은 프로세스가 선점"]},
        {"layout": "bullets", "title": "라운드 로빈과 타임 퀀텀",
         "bullets": ["타임 퀀텀 — 2 단위씩 돌아가며 실행, 남은 시간이 있으면 큐 맨 뒤로",
                     "평균 대기시간 — 5.0, 평균 반환시간 9.0"]},
        {"layout": "bullets", "title": "우선순위 스케줄링의 동작 원리",
         "bullets": ["우선순위 값이 작을수록 높음", "비선점은 실행 중 프로세스를 중단하지 않음",
                     "선점은 더 높은 우선순위가 오면 바로 전환", "낮은 우선순위는 기아 현상 위험"]},
        {"layout": "table", "title": "5개 알고리즘 성능 비교",
         "rows": [["알고리즘", "평균 대기"], ["FCFS", "4.75"], ["SJF", "4"]]},
        {"layout": "chart", "title": "알고리즘별 대기시간 한눈에 보기"},
        {"layout": "bullets", "title": "학습 내용 확인 퀴즈", "bullets": ["Q1", "Q2"]},
        {"layout": "closing", "title": "기억에 남을 핵심 요약"},
    ]


def test_about_twelve_slides_caps_the_deck_at_the_range_top():
    assert deck.slide_range(LECTURE) == (10, 14)
    slides = _dsp_deck()
    assert deck.gantt_room(LECTURE, slides) == 0
    deck.place_gantt_charts(slides, LECTURE, room=deck.gantt_room(LECTURE, slides))
    assert len(slides) == 14


def test_the_example_slide_keeps_its_process_table():
    slides = _dsp_deck()
    deck.place_gantt_charts(slides, LECTURE, room=deck.gantt_room(LECTURE, slides))
    titles = [s["title"] for s in slides]
    assert slides[titles.index("예제 프로세스 구성과 우선순위")]["rows"] == _EXAMPLE
    # the priority chart goes to the priority slide, its points to the notes
    priority = slides[titles.index("우선순위 스케줄링의 동작 원리")]
    assert priority["layout"] == "table"
    assert priority["rows"][1] == ["비선점", "P1 0–7 → P2 7–11 → P4 11–15 → P3 15–16", "5.5"]
    assert "기아 현상 위험" in priority["notes"]
    # FCFS's points listed the intervals: that slide is the chart, no second one
    fcfs = slides[titles.index("FCFS 도착 순서대로 처리하기")]
    assert fcfs["rows"][1] == ["0–7", "P1", "완료, 대기 7−0−7 = 0"]
    assert not any(t.endswith("간트 차트") for t in titles)


def test_with_room_a_chart_slide_is_still_added():
    request = LECTURE.replace(" 12장 안팎.", "")
    slides = _dsp_deck()
    assert deck.gantt_room(request, slides) is None
    deck.place_gantt_charts(slides, request, room=None)
    titles = [s["title"] for s in slides]
    assert "SRTF 간트 차트" in titles
    assert slides[titles.index("예제 프로세스 구성과 우선순위")]["rows"] == _EXAMPLE


def test_a_slide_table_keeps_five_columns_and_whole_names():
    # A results table with a fifth column
    rows = deck._clean_rows([
        ["방법", "EM", "F1", "지연(ms)", "파라미터"],
        ["교차 인코더 (bge-reranker-large)", "72.8", "69.1", "412", "560M"],
        ["LiteRerank (MiniLM)", "71.9", "68.4", "96", "22M"],
    ])
    assert rows[0][-1] == "파라미터"
    assert rows[1] == ["교차 인코더 (bge-reranker-large)", "72.8", "69.1", "412", "560M"]
    # a run that cannot be kept whole never ends inside an open bracket
    assert "(" not in deck._cell("교차 인코더 (bge-reranker-large-v2-multilingual-extra)")


def test_the_agenda_lists_nine_parts():
    slides = [{"layout": "bullets", "title": f"{n}. 내용 {n}"} for n in range(1, 10)]
    lines = deck._agenda_lines(slides)
    assert len(lines) == 9 and lines[-1] == "내용 9"


def test_an_english_gantt_derivation_slide_takes_its_chart():
    # 「FCFS와 Gantt 도출」 as the result slide, with no slides added
    slides = [{"layout": "bullets", "title": "FCFS와 Gantt 도출",
               "bullets": ["도착 순서대로 실행"]}]
    deck.place_gantt_charts(slides, LECTURE, room=None)
    assert len(slides) >= 1 and slides[0]["layout"] == "table"
    assert slides[0]["rows"][1] == ["0–7", "P1", "완료, 대기 7−0−7 = 0"]


def test_a_definition_body_ends_at_a_sentence():
    body = ("비선점 방식이라 실행 중인 프로세스를 빼앗지 않는다. 간트 차트는 도착 순서를 그대로 "
            "이어 붙여 그린다. 먼저 온 P1이 0부터 7까지 실행되고, 그다음 P2가 7부터 11까지, P3가 "
            "11부터 12까지 실행된다.")
    assert len(body) > 80
    cut = deck._within_sentences(body, 80)
    assert cut.endswith("그린다.") and len(cut) <= 80
    assert deck._within_sentences("짧은 문장.", 120) == "짧은 문장."


def test_a_second_lead_on_is_not_joined():
    existing = "지연이 줄어든 이유가 핵심입니다. 다음 장에서 지연과 정확도를 함께 봅니다."
    added = ("이 수치가 중요한 이유는 사용자 대기가 줄기 때문입니다. "
             "다음 장에서는 구성 요소별로 확인합니다.")
    joined = deck._join_notes(existing, added)
    assert joined.count("다음 장") == 1 and "사용자 대기가 줄기" in joined


def test_notes_are_told_which_slide_comes_next():
    slides = [{"title": "경량화 핵심 수치"}, {"title": "RAG 지연의 병목은 재순위화"},
              {"title": "적용 한계"}]
    assert deck.notes_neighbours(slides, 0) == "Next slide: RAG 지연의 병목은 재순위화"
    assert deck.notes_neighbours(slides, 2) == (
        "Previous slide: RAG 지연의 병목은 재순위화\nThis is the deck's last slide.")
    assert "{neighbours}" in deck._NOTES_PROMPT
