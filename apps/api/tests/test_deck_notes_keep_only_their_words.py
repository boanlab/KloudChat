"""A slide's note keeps its own words only, and the deck's Markdown shows a chart as a table.

Quality lane Q22: the last slide's note carried the next slides' JSON
(「…기억해 두면 됩니다."}]}, {"title": "다음 시간 예고", …」), and the Markdown export printed a
chart's data as 「- P1: 0 · 7 · 0 · 0 · 0」 bullets.
"""

from __future__ import annotations

from app.services import deck


def test_the_next_slides_json_is_cut_from_a_note():
    note = ('선점 여부를 먼저 묻고 나면 나머지 숫자는 자연스럽게 따라옵니다."}]}, {"title": '
            '"다음 시간 예고", "bullets": ["멀티레벨 큐"], "speaker_notes": "오늘 예제의 P1~P4를"}')
    quoted = '따옴표 \\"예시\\"는 남는다.'
    slides = [{"title": "정리", "notes": note}, {"title": "그대로", "notes": quoted}]
    assert deck.strip_json_residue(slides) == [0, 1]
    assert slides[0]["notes"] == "선점 여부를 먼저 묻고 나면 나머지 숫자는 자연스럽게 따라옵니다."
    assert slides[1]["notes"] == '따옴표 "예시"는 남는다.'


def test_a_chart_reads_as_a_table_in_the_markdown():
    slides = [{"title": "대기 시간", "layout": "chart", "chart": {
        "kind": "bar", "unit": "ms", "categories": ["FCFS", "SJF"],
        "series": [{"name": "평균 대기", "values": [4.75, 4.0]}]}}]
    text = deck.to_markdown("스케줄링", slides)
    assert "| 항목 (ms) | FCFS | SJF |" in text
    assert "| 평균 대기 | 4.75 | 4.0 |" in text
    assert "·" not in text


def test_the_cover_carries_the_requested_title_and_authors_as_written():
    # Quality lane Q21: the cover shortened the title and dropped the authors.
    request = ("다음 주 학회 구두 발표용 슬라이드 만들어 줘. 발표 15분.\n\n"
               "제목: 소형 언어모델을 위한 검색 증강 생성에서의 문서 재순위화 경량화\n"
               "저자: 이서준, 김나래, 최영훈(한빛대학교 컴퓨터공학과)\n\n초록: …")
    shortened = "소형 언어모델 RAG를 위한 경량 재순위화"
    title, subtitle = deck.given_cover(request, shortened, "학회 발표")
    assert title == "소형 언어모델을 위한 검색 증강 생성에서의 문서 재순위화 경량화"
    assert subtitle == "이서준, 김나래, 최영훈(한빛대학교 컴퓨터공학과)"
    # Without those lines the model's cover stands.
    assert deck.given_cover("RAG 발표 슬라이드 만들어 줘", "RAG 개요", "연구실 세미나") == (
        "RAG 개요", "연구실 세미나")
