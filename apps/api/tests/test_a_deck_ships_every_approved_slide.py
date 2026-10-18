"""Approved slide count, 「10장 내외」 as a count, and whole bullets."""

from __future__ import annotations

import pytest

from app.services import deck


@pytest.mark.parametrize(
    ("prompt", "count", "span"),
    [
        ("랩 세미나 발표 자료, 10장 내외로", None, (8, 12)),
        ("12장 안팎", None, (10, 14)),
        ("8장 정도로 만들어 줘", None, (6, 10)),
        ("총 8장 안팎으로", None, (6, 10)),
        ("5장 남짓", None, (4, 6)),
        ("8~10장", None, (8, 10)),
        ("10장으로", 10, None),
    ],
)
def test_an_approximate_count_is_a_range_around_it(prompt, count, span):
    assert deck.requested_slides(prompt) == count
    assert deck.slide_range(prompt) == span


def test_a_long_bullet_is_never_cut_inside_a_word():
    line = ("ClaimCheck의 Kendall tau는 0.61로, ROUGE-L의 0.42와 BERTScore의 0.47보다 인간 판단과 "
            "더 잘 맞았습니다")
    assert len(line) > 80
    assert deck._clean_bullets([line]) == [line]
    long = "가나다 " * 60
    kept = deck._clean_bullets([long])[0]
    assert len(kept) <= 160 and long.startswith(kept) and kept.endswith("가나다")


def test_a_bullet_the_rewrite_left_long_is_cut_at_a_clause_and_kept_whole_in_the_notes():
    line = ("주장 추출은 8B 프롬프트 모델이 담당하고, 검증은 DeBERTa-v3-large를 "
            "40k 주장-증거 쌍으로 학습해 수행합니다")
    slides = [{"layout": "bullets", "title": "ClaimCheck", "bullets": [line, "짧은 줄"],
               "notes": "설명."}]
    assert deck.long_bullets(slides) == {0: [line]}
    assert deck.fit_long_bullets(slides) == [0]
    assert slides[0]["bullets"] == ["주장 추출은 8B 프롬프트 모델이 담당하고", "짧은 줄"]
    assert line.rstrip(".") in slides[0]["notes"]
