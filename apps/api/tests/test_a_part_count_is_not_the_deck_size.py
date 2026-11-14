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
