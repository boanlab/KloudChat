"""The last slide written as a closing (「감사합니다」) whose title is a part of the content the
request asked for — the quiz, the checklist — is a content slide again.

Round-3 r3d02 and r3d09 (11-21, KC-SL): 「퀴즈 3문제」 and
「첫 달 체크리스트와 도움받을 곳」 came out as closing slides reading 「감사합니다」, the
checklist with nothing else on it."""

from __future__ import annotations

from app.services import deck


def test_a_quiz_in_closing_layout_becomes_a_content_slide():
    slides = [{"layout": "title", "title": "물의 순환"},
              {"layout": "closing", "title": "퀴즈 3문제", "body": "감사합니다",
               "bullets": ["증발이란?", "구름은?", "침투란?"]}]
    assert deck.closing_with_content(slides) == [1]
    assert slides[1]["layout"] == "bullets" and "body" not in slides[1]


def test_a_real_closing_stays():
    for title in ("마무리", "감사합니다", "오늘의 핵심 정리", "Closing: next steps", "Q&A"):
        slides = [{"layout": "title", "title": "x"},
                  {"layout": "closing", "title": title, "body": "감사합니다"}]
        assert deck.closing_with_content(slides) == []
        assert slides[1]["layout"] == "closing"
