"""Every writer call sends `reasoning: {"enabled": false}`; chat does not."""

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
    """The `thinking.starved` re-ask also disables reasoning rather than capping it."""
    from app.services import thinking

    assert thinking.NO_REASONING == {"enabled": False}
    for writer in (deck._complete, report._complete, page._complete):
        source = inspect.getsource(writer)
        # Once for the first call and once for the re-ask.
        assert source.count("thinking.NO_REASONING") >= 2, writer.__module__
        assert "REASONING_CAP" not in source, writer.__module__


def test_starved_still_recognises_the_shape_it_was_written_for() -> None:
    """`thinking.starved` still detects an answer starved by reasoning."""
    from app.services import thinking

    payload = {
        "choices": [{"finish_reason": "length", "message": {"content": None}}],
        "usage": {
            "completion_tokens": 600,
            "completion_tokens_details": {"reasoning_tokens": 586},
        },
    }
    assert thinking.starved(payload, 600) > 600
    # An ordinary answer is left alone.
    done = {"choices": [{"finish_reason": "stop", "message": {"content": "가"}}]}
    assert thinking.starved(done, 600) == 0
