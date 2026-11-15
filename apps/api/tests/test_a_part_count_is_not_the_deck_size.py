"""A count sizing one part (「퀴즈 한 장」) beside the deck's stated range is not the deck size."""

from __future__ import annotations

import pytest

from app.services import deck, report

_ASK = (
    "13강 클라우드 보안과 IAM 내용으로 수업용 슬라이드를 10장 안팎으로 만들어 줘. "
    "마지막에 퀴즈 한 장."
)


def test_a_quiz_slide_beside_an_approximate_size_leaves_the_deck_at_that_size():
    # walk 11-14: 「한 장」 was read as the total, the outline could not be cut to one slide
    # and the turn ended with no deck
    assert deck.requested_slides(_ASK) is None
    assert deck.slide_range(_ASK) == (8, 12)
    assert deck.about_slides(_ASK) == 10


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("8~10장 발표, 표지 한 장과 요약 한 장", None),
        ("10장 안팎으로, 마지막에 퀴즈 1장", None),
        # a firm total still wins, even below the range said first
        ("10장 안팎으로 만들어 줘\n\n덧붙인 조건:\n- 장수는 6장으로 맞춘다", 6),
        ("10장 안팎, 총 12장", 12),
        # no range beside it: still the total
        ("한 장으로 핵심만", 1),
        ("5장", 5),
    ],
)
def test_only_a_firm_or_lone_count_is_the_total(prompt, expected):
    assert deck.requested_slides(prompt) == expected


def test_a_report_part_count_beside_its_range_is_not_the_section_count():
    assert report.requested_sections("4~6개 섹션으로, 결론은 한 절") is None


def _plan(*titles: str, closing: bool = True) -> list[dict]:
    plan = [{"title": "표지", "layout": "title"}, {"title": "목차", "layout": "agenda"}]
    plan += [{"title": t, "layout": "bullets"} for t in titles]
    return plan + ([{"title": "마무리", "layout": "closing"}] if closing else [])


def test_an_asked_quiz_the_planner_left_out_gets_its_slide_before_the_closing():
    # walk 11-14 rerun: two of three 27b plans had no quiz slide although one was asked
    plan = _plan("IAM", "최소 권한", "공유 책임", "데이터 보호", "제로 트러스트", "점검")
    out = deck.keep_asked_quiz(plan, _ASK, 12)
    assert [s["title"] for s in out][-2:] == ["퀴즈", "마무리"]
    assert len(out) == len(plan) + 1


def test_a_full_deck_makes_room_for_the_quiz_from_the_agenda_not_the_content():
    plan = _plan("IAM", "최소 권한", "공유 책임", "데이터 보호", "제로 트러스트", "점검")
    out = deck.keep_asked_quiz(plan, _ASK, len(plan))
    assert len(out) == len(plan) and "퀴즈" in [s["title"] for s in out]
    assert "목차" not in [s["title"] for s in out] and "점검" in [s["title"] for s in out]
    # no divider or agenda left to give up: content is never dropped for it
    bare = [s for s in plan if s["layout"] != "agenda"]
    assert deck.keep_asked_quiz(bare, _ASK, len(bare)) == bare


def test_a_planned_quiz_or_no_quiz_asked_leaves_the_plan_alone():
    planned = _plan("IAM", "확인 퀴즈")
    assert deck.keep_asked_quiz(planned, _ASK, 12) == planned
    plain = _plan("IAM", "정책")
    assert deck.keep_asked_quiz(plain, "IAM 수업 슬라이드 10장 안팎", 12) == plain


def test_a_count_inside_the_asked_range_needs_no_note():
    assert deck.count_note(_ASK, 12) == "" and deck.count_note(_ASK, 9) == ""
    assert "13장" in deck.count_note(_ASK, 13)
