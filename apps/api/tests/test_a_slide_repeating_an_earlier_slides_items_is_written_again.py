"""A content slide whose items an earlier slide already shows is written again for its own
title; a recap (정리·요약·핵심, takeaways) may repeat.

Round 3 (11-21) decks: r3d01 KC-SL slides 7 (핵심 강점) and 8 (공공 설계 대응 방식) held the
same three items; r3d04 KC-SA slides 8 and 9 both said 「재고 6만 장 미정·소재 전환」."""

from __future__ import annotations

from app.services import deck

SLIDES = [
    {"layout": "title", "title": "회사 소개"},
    {"layout": "bullets", "title": "핵심 강점",
     "bullets": ["이용자 참여 설계 워크숍", "BIM 전 과정 적용", "에너지 효율 1++ 인증 3건"]},
    {"layout": "bullets", "title": "공공 설계 대응 방식",
     "bullets": ["이용자 참여 설계 워크숍", "BIM 전 과정 적용", "에너지 효율 1++ 인증 3건"]},
    {"layout": "takeaways", "title": "기억할 세 가지",
     "items": [["이용자 참여 설계 워크숍", ""], ["BIM 전 과정 적용", ""]]},
]


def test_the_later_repeat_is_found_and_the_recap_is_not():
    assert deck.repeated_slides(SLIDES) == [(2, 1)]


def test_slides_sharing_one_item_are_not_repeats():
    other = {"layout": "bullets", "title": "다른 장",
             "bullets": ["BIM 전 과정 적용", "공공청사 리모델링 4건", "학교 증축 2건"]}
    slides = [SLIDES[1], other]
    assert deck.repeated_slides(slides) == []
