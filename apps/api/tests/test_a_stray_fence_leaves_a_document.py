"""A fence a writer left in a document section with nothing in it, or only its own unfinished
note (「(이전 생각:」), is dropped; a fence holding code stays."""

from __future__ import annotations

from app.services import internal_words


def _clean(text: str) -> str:
    return internal_words.strip_work_notes(text, request="관련 연구 써 줘", document=True)


def test_an_unfinished_note_in_a_fence_is_dropped():
    text = ("그림 1. LLM 추론 단계 구분\n\n````\n(이전 생각:\n````\n"
            "DistServe는 prefill과 decoding을 서로 다른 GPU로 나눕니다.")
    out = _clean(text)
    assert "이전 생각" not in out and "```" not in out
    assert "DistServe는 prefill과 decoding을" in out


def test_an_empty_fence_is_dropped_and_code_stays():
    assert "```" not in _clean("앞 문단입니다.\n\n```\n```\n\n뒤 문단입니다.")
    code = "예시는 다음과 같습니다.\n\n```python\nx = cache.get(key)\n```\n\n끝."
    assert "x = cache.get(key)" in _clean(code)
