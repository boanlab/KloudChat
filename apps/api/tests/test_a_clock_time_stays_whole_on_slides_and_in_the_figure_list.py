"""A clock time stays whole (「12:00 점심」, 「09:30」, 「12:00~13:00」) on slides and in the
writers'
figure list."""

from __future__ import annotations

from app.services import deck, report

ASK = ("사내 교육 데이 진행 자료를 6장으로 만들어 줘. "
       "09:30 시작, 점심 12:00~13:00, 17:30 종료, 4개 조.")


def test_a_time_led_bullet_is_not_split_at_its_colon():
    slide = {"layout": "checklist", "title": "진행 체크"}
    deck._fill_pattern(
        slide, {"bullets": ["12:00 점심시간 안내", "13:00 오후 세션 시작", "17:30 마무리"]})
    assert all(not (pair[0].isdigit() and pair[1].startswith(("00", "30")))
               for pair in slide.get("items") or [])


def test_a_label_colon_still_splits():
    assert deck._HEAD_LINE.match("정확도: 매칭 92% 달성").group("head") == "정확도"


def test_the_figure_lists_keep_clock_times_whole():
    line = deck._facts_line(ASK)
    assert "09:30" in line and "12:00~13:00" in line and ", 00," not in line
    rline = report._facts_line(ASK, [])
    assert "09:30" in rline and "17:30" in rline
