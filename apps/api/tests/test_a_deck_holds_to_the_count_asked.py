"""A deck aims at the count asked (「5장 정도」 at 5) and, when it ends with another count, the
reply says so instead of repeating the request's number."""

from __future__ import annotations

from app.services import deck


def test_an_approximate_count_is_aimed_at():
    assert deck.about_slides("5개 장 정도로 만들어 줘") == 5
    assert deck.about_slides("6장으로 만들어 줘") is None


def test_the_reply_says_the_real_count():
    assert deck.count_note("6장짜리 발표 자료", 8) == (
        "요청하신 6장과 달리 8장입니다. 더하거나 뺄 슬라이드를 말씀해 주시면 맞추겠습니다.")
    assert deck.count_note("6장짜리 발표 자료", 6) == ""
    assert deck.count_note("5장 정도", 6) == ""
    assert deck.count_note("5장 정도", 8).startswith("요청하신 5장과 달리 8장")
    assert "this deck has 8" in deck.count_note("6 slides, 6장", 8, english=True)


def test_a_report_says_its_real_section_count():
    from app.pipeline.save_stages import section_count_note

    assert section_count_note("5개 절로 써 줘", 6).startswith("요청하신 5개 절과 달리 6개 절")
    assert section_count_note("5개 절로 써 줘", 5) == ""
