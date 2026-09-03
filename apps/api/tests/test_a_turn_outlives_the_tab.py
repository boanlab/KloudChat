"""Closing the tab must not throw the document away.

`_survive_disconnect` exists for this: the turn produces into a queue and the
response relays it, so a reader leaving cancels the relay and not the work. The
turn still reaches the block that stores what it made, charges for it and names
the conversation. The client documents the same contract — stop aborts the
request, the server keeps what it produced.

It was wrapped around chat and nothing else. A report, a deck, a page and a
document revision all ran undetached, so closing the tab during a three-minute
deck generation cancelled the generator at the `yield` it was sitting on and
lost the slides, the failure record and the charge together. The person came
back to their own question with nothing under it — the state `_run_deck`'s own
comment refuses to leave behind when the model fails, and left behind anyway
when the connection did.
"""

from __future__ import annotations

import inspect

import pytest

from app.routers import sessions


@pytest.mark.parametrize(
    "runner",
    ["_run_turn", "_run_report", "_run_deck", "_run_page", "_revise_document"],
)
def test_every_streamed_turn_is_detached(runner: str) -> None:
    """One of them undetached is the same bug in that one surface.

    Read off the route's source rather than through the app: what has to hold
    is that the wrapper is around the call, and that is a fact about this file.
    """
    source = inspect.getsource(sessions.send_message)
    where = source.find(f"{runner}(")
    assert where > 0, f"{runner} 가 이 라우트에서 호출되지 않습니다"
    before = source[:where]
    # The wrapper opens immediately above the runner it is wrapping.
    assert before.rstrip().endswith("_survive_disconnect("), f"{runner} 가 연결이 끊기면 사라집니다"


def test_the_survivor_keeps_the_work_and_drops_only_the_relay() -> None:
    """The shape is what makes it work: a task that outlives the response.

    A wrapper that merely caught the cancellation would still lose everything
    the turn had not finished — the point is that nothing is cancelled at all.
    """
    source = inspect.getsource(sessions._detached)
    assert "asyncio.create_task" in source
    # And the route actually serves that shape, not a wrapper that lost it.
    assert "_detached(" in inspect.getsource(sessions._survive_disconnect)
    # Held somewhere, or the loop is free to garbage-collect the task mid-turn.
    assert "_DETACHED" in source
