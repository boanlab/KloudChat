"""「용어는 영어 원문을 병기해 줘」 reaches the slide writer as a rule, with the material's terms:
English originals shown, no coined Korean terms such as 「단문」, 「추록」.
"""

from __future__ import annotations

from app.services.deck import bilingual_terms_rule

PAPER = (
    "ClaimCheck decomposes a summary into atomic claims. Each atomic claim is checked by "
    "Natural Language Inference against the source. The abstract and the atomic claims "
    "are aligned; Natural Language Inference scores are averaged."
)


def test_the_rule_comes_with_the_request_and_names_the_materials_terms():
    request = "랩 세미나 발표 자료 만들어 줘. 용어는 영어 원문을 병기해 줘."
    rule = bilingual_terms_rule(request, [PAPER])
    assert "한국어 용어(English term)" in rule
    assert "atomic claims" in rule and "Natural Language Inference" in rule
    assert bilingual_terms_rule("영문 병기로 부탁해", []) != ""


def test_no_rule_without_the_ask():
    assert bilingual_terms_rule("랩 세미나 발표 자료 만들어 줘.", [PAPER]) == ""
