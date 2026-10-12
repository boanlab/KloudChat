"""Japanese kana slipped into Korean prose (deck notes included) is read back or dropped."""

from app.services import deck, hangul


def test_a_kana_ending_is_read_as_korean():
    slip = "두 가지를 함께 받는 것은できません. 하나만 고릅니다."
    text, changed = hangul.repair_mixed_script(slip)
    assert text == "두 가지를 함께 받는 것은 할 수 없습니다. 하나만 고릅니다."
    assert changed


def test_an_unknown_kana_sentence_is_dropped_and_japanese_material_kept():
    slip = "신청은 학과 사무실에서 합니다. 書類はこちらへ出してね. 끝."
    text, _ = hangul.repair_mixed_script(slip)
    assert "書類" not in text and "ね" not in text
    assert text.startswith("신청은 학과 사무실에서 합니다.") and text.endswith("끝.")
    same = "일본어 인사 「こんにちは」를 배웁니다."
    assert hangul.repair_mixed_script(same, "こんにちは 수업 자료")[0] == same


def test_deck_notes_are_checked_for_kana():
    slides = [{"title": "선택", "notes": "두 가지를 함께 받는 것은できません."}]
    assert deck.mend_mixed_script(slides, "") == [0]
    assert slides[0]["notes"] == "두 가지를 함께 받는 것은 할 수 없습니다."


def test_the_last_slide_hands_over_to_no_next_slide():
    slides = [{"title": "1", "notes": "다음으로 결과를 살펴보겠습니다."},
              {"title": "결론", "notes": "세 가지를 정리했습니다. 다음으로 한계를 살펴보겠습니다."}]
    assert deck.end_without_next(slides) == [1]
    assert slides[1]["notes"] == "세 가지를 정리했습니다."
    assert slides[0]["notes"] == "다음으로 결과를 살펴보겠습니다."
    assert deck.end_without_next(slides) == []
