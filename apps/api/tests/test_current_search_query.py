"""A primary-source retrieval cue is not a source or a hardcoded fact."""

import pytest

from app.services.context import search_query


@pytest.mark.parametrize("question", [
    "현재 대한민국 대통령", "현재 Acme CEO", "최신 Python 버전", "오늘 달러 환율",
])
def test_current_korean_query_uses_korean_cue_without_answer_or_domain(question):
    query = search_query(question, prefer_primary=True)
    assert query.endswith("공식")
    assert "official" not in query
    assert "site:" not in query
    assert question in query


def test_english_query_does_not_inject_a_korean_government_domain():
    query = search_query("Current Example Corp CEO", prefer_primary=True)
    assert query.endswith("official")
    assert "go.kr" not in query


def test_explicit_source_constraint_and_default_query_are_preserved():
    question = "latest version site:python.org"
    assert search_query(question, prefer_primary=True) == search_query(question)
    assert search_query("현재 대한민국 대통령") == "현재 대한민국 대통령"


def test_primary_cue_does_not_grow_or_duplicate():
    question = "현재 Python 공식"
    assert search_query(question, prefer_primary=True).count("공식") == 1
    assert len(search_query("가" * 200, prefer_primary=True)) <= 120
