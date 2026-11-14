"""No-thinking requests (`thinking.switch`) on every writer call, not on chat. Report and deck
writers ask through `thinking.writer_switch`, which is `switch` unless the turn's reasoning
chip asked for reasoning on that model."""

from __future__ import annotations

import inspect

import pytest

from app.services import deck, page, report


@pytest.mark.parametrize(
    "writer",
    # deck and page: the call under the dropped-connection retry
    [deck._complete_once, report._complete, page._complete_once],
    ids=["deck", "report", "page"],
)
def test_the_writers_ask_for_no_thinking(writer) -> None:
    source = inspect.getsource(writer)
    assert "thinking.switch(model)" in source or "thinking.writer_switch(model)" in source


def test_a_writer_switch_without_the_chip_is_no_thinking() -> None:
    from app.services import thinking

    assert thinking.TURN_REASONING.get() is None
    for model in ("deepseek/x", "local/qwen3.8-flash", "strict-local/qwen3.8-27b"):
        assert thinking.writer_switch(model) == thinking.switch(model)
        assert thinking.writer_room(model, 900) == thinking.room(model, 900)


def test_the_re_ask_asks_for_no_thinking_either() -> None:
    """The `thinking.starved` re-ask also disables reasoning rather than capping it."""
    from app.services import thinking

    assert thinking.NO_REASONING == {"enabled": False}
    for writer in (deck._complete_once, report._complete, page._complete_once):
        source = inspect.getsource(writer)
        # Once for the first call and once for the re-ask.
        asks = (source.count("thinking.switch(model)")
                + source.count("thinking.writer_switch(model)"))
        assert asks >= 2, writer.__module__
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
