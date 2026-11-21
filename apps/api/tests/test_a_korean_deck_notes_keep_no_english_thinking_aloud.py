"""A Korean deck's notes re-ask keeps only Korean sentences.

The model answered the notes re-ask by thinking aloud in English first, and the whole reply went
into the speaker notes: 「PSQI 점수가 5를 초과하면 … We need to respond to user's specific request:
They want two sentences …」."""

from __future__ import annotations

from app.services import deck

LEAK = ("PSQI 점수가 5를 초과하면 수면의 질이 낮은 것으로 분류했습니다. "
        "We need to respond to user's specific request: They want two sentences the presenter "
        "will actually say aloud on this slide. Previous slide: 연구 배경과 질문. "
        "Need output only sentences in Korean. 이 기준으로 응답자를 두 집단으로 나눠 "
        "비교했습니다.")


def test_english_thinking_aloud_is_dropped_from_korean_notes():
    kept = deck._korean_sentences(LEAK)
    assert "We need" not in kept and "Need output" not in kept
    assert kept.startswith("PSQI 점수가 5를 초과하면")
    assert kept.endswith("두 집단으로 나눠 비교했습니다.")


def test_a_korean_sentence_with_english_terms_stays():
    text = "OKR과 KPI는 목적이 다릅니다. CSAT 82%를 기준으로 삼습니다."
    assert deck._korean_sentences(text) == text
