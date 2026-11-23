"""The planner's picture prompt may quote Korean text only if the request contains it."""

from app.services import imagegen

REQUEST = "봄 도서관 행사 포스터, 파스텔 톤, 큰 글씨로 「벚꽃 책 축제」"


def test_a_example_word_copied_from_the_planner_prompt_is_dropped():
    planned = (
        'A poster for a spring library event.\n\n'
        'Center: the title "벚꽃 책 축제" in large rounded sans-serif. '
        'Top-left and bottom-right: small captions "취수" and "슬러지 배출".\n\n'
        'Style: pastel, white background.'
    )
    out = imagegen.strip_foreign_labels(planned, REQUEST)
    assert "벚꽃 책 축제" in out
    assert "취수" not in out and "슬러지" not in out
    assert out.endswith("Style: pastel, white background.")


def test_a_label_the_person_wrote_stays_even_with_different_spacing():
    planned = 'Left: a box ("벚꽃책축제"). Style: flat.'
    assert imagegen.strip_foreign_labels(planned, REQUEST) == planned


def test_a_prompt_without_korean_quotes_is_unchanged():
    planned = "A photograph of a reading room at golden hour.\n\nStyle: warm light."
    assert imagegen.strip_foreign_labels(planned, "도서관 열람실 사진") == planned


def test_the_label_rule_sent_with_the_prompt_carries_no_example_word():
    for labels in ("ko", "en", "none", "auto"):
        sent = imagegen.compose_prompt("A poster.", aspect="16:9", style="auto", labels=labels)
        assert "취수" not in sent and "Intake" not in sent
