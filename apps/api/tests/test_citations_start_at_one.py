"""Cited sources renumbered from 1 in reading order (a list once read 「[5] [7] [8] [10]」)."""
from __future__ import annotations

from app.services import agent

SOURCES = [f"https://example{n}.kr/a" for n in range(1, 12)]


def test_cited_numbers_start_at_one_in_reading_order():
    answer = ("KT는 3차원 커버리지를 내세웁니다 [[10]](https://example10.kr/a). "
              "SKT는 AI 네이티브를 말합니다 "
              "[[5]](https://example5.kr/a)[[7]](https://example7.kr/a). "
              "둘 다 AI 인프라입니다 [[10]](https://example10.kr/a).")
    text, cited, sources = agent._renumber_citations(answer, [5, 7, 10], SOURCES)
    assert cited == [1, 2, 3]
    assert sources == ["https://example10.kr/a", "https://example5.kr/a", "https://example7.kr/a"]
    assert "커버리지를 내세웁니다 [[1]](https://example10.kr/a)" in text
    assert "[[2]](https://example5.kr/a)[[3]](https://example7.kr/a)" in text
    assert text.endswith("[[1]](https://example10.kr/a).")


def test_code_blocks_keep_their_brackets():
    answer = "가 [[4]](https://example4.kr/a)\n```\nx = a[4]\n```\n끝 [[3]](https://example3.kr/a)"
    text, cited, sources = agent._renumber_citations(answer, [3, 4], SOURCES)
    assert text.startswith("가 [[1]](https://example4.kr/a)")
    assert "x = a[4]" in text and text.endswith("[[2]](https://example3.kr/a)")
    assert sources == [SOURCES[3], SOURCES[2]] and cited == [1, 2]


def test_already_in_order_is_unchanged():
    answer = "가 [[1]](https://example1.kr/a) 나 [[2]](https://example2.kr/a)"
    assert agent._renumber_citations(answer, [1, 2], SOURCES) == (answer, [1, 2], SOURCES)
