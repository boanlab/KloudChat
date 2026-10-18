"""「모든 문항에서 가장 낮은」-style claims narrowed to what the answer's own table shows."""

from app.services import superlatives

TABLE = (
    "| 단과대학 | 응답 수 | 교과내용 | 교수법 | 시설 | 온라인학습 | 전반만족 | 추천 '예' |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    "| 사회과학대학 | 24 | 4.38 | 3.92 | 3.92 | 3.83 | 4.17 | 75.0% |\n"
    "| 인문대학 | 25 | 4.12 | 3.80 | 4.12 | 3.84 | 3.92 | 64.0% |\n"
    "| 공과대학 | 32 | 3.66 | 3.56 | 3.69 | 3.41 | 3.59 | 68.8% |\n"
    "| 예술대학 | 18 | 3.22 | 3.61 | 2.89 | 3.06 | 3.39 | 50.0% |\n\n"
)


def test_the_sweeping_word_becomes_the_columns_that_hold():
    text = TABLE + "예술대학은 모든 문항에서 가장 낮은 평균을 보였고, 특히 시설이 낮았습니다."
    out = superlatives.narrow_sweeping_claims(text)
    assert "예술대학은 교과내용·시설·온라인학습·전반만족 문항에서 가장 낮은 평균을 보였고" in out
    assert out.startswith(TABLE)


def test_a_highest_claim_narrows_too_and_text_without_a_table_stays():
    # 사회과학대학 is not the highest in 시설 (인문대학's 4.12): narrowed.
    true = TABLE + "사회과학대학은 모든 문항에서 가장 높은 평균입니다."
    narrowed = superlatives.narrow_sweeping_claims(true)
    assert "교과내용·교수법·전반만족 문항에서 가장 높은" in narrowed
    plain = "예술대학은 모든 문항에서 가장 낮았습니다."
    assert superlatives.narrow_sweeping_claims(plain) == plain
    only = TABLE + "공과대학은 전 문항 최저입니다."
    assert "교수법" in superlatives.narrow_sweeping_claims(only)
