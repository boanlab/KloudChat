"""한 덩어리 답을 받는 호출에는 생각을 시키지 않는다.

A reasoning model asked for one JSON object spends its whole token ceiling
thinking and answers with `content: null`. Measured on `qwen3.5-122b` through
OpenRouter, against the real slide prompt:

    ceiling  600 → reasoning   586, content ''
    ceiling 1908 → reasoning 1,677, content ''
    ceiling 4000 → reasoning 3,964, content ''

The ceiling is not the problem — the model fills whatever it is given. So
`thinking.starved` fired, re-asked with more room, and was starved again; a
whole deck came back with every slide reading "이 장을 쓰지 못했습니다.", and
each of those empty answers was charged for. One run cost 3,431 credits and
produced nothing; the same run asked not to think cost 303 and produced six
slides out of seven.

`reasoning: {"enabled": false}` is sent on every writer call. The proxy runs
`drop_params`, so a provider that has never heard of the field never sees it,
and the local model — which does not think — is unaffected either way.

Chat is deliberately not in here. Somebody asking a question may want the model
to work through it; somebody asking for slide four does not.
"""

from __future__ import annotations

import inspect

import pytest

from app.services import deck, page, report


@pytest.mark.parametrize(
    "writer",
    [deck._complete, report._complete, page._complete],
    ids=["deck", "report", "page"],
)
def test_the_writers_ask_for_no_thinking(writer) -> None:
    source = inspect.getsource(writer)
    assert "thinking.NO_REASONING" in source


def test_the_re_ask_asks_for_no_thinking_either() -> None:
    """`thinking.starved` raises the ceiling when an answer was starved. On a
    model that fills any ceiling that is a second empty answer at twice the
    price, so the re-ask carries the same setting.

    It used to send `REASONING_CAP`, which asks for a smaller amount of
    thinking rather than none. That is right for a model that respects a cap
    and wrong for one that does not: `qwen3.5-122b` spent 1,016 tokens against
    a cap of 400 and answered with nothing.
    """
    from app.services import thinking

    assert thinking.NO_REASONING == {"enabled": False}
    for writer in (deck._complete, report._complete, page._complete):
        source = inspect.getsource(writer)
        # Once for the first call and once for the re-ask.
        assert source.count("thinking.NO_REASONING") >= 2, writer.__module__
        assert "REASONING_CAP" not in source, writer.__module__


def test_starved_still_recognises_the_shape_it_was_written_for() -> None:
    """The flag is a fix, not a replacement. A model that runs out of room with
    reasoning to blame is still worth re-asking — the two work together."""
    from app.services import thinking

    payload = {
        "choices": [{"finish_reason": "length", "message": {"content": None}}],
        "usage": {
            "completion_tokens": 600,
            "completion_tokens_details": {"reasoning_tokens": 586},
        },
    }
    assert thinking.starved(payload, 600) > 600
    # And an ordinary answer is left alone.
    done = {"choices": [{"finish_reason": "stop", "message": {"content": "가"}}]}
    assert thinking.starved(done, 600) == 0
