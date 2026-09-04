"""`_survive_disconnect` wraps every streamed turn: a closed tab cancels the relay, not the work."""

from __future__ import annotations

import inspect

import pytest

from app.routers import sessions


@pytest.mark.parametrize(
    "runner",
    ["_run_turn", "_run_report", "_run_deck", "_run_page", "_revise_document"],
)
def test_every_streamed_turn_is_detached(runner: str) -> None:
    """Every streamed route wraps its runner in `_survive_disconnect`."""
    source = inspect.getsource(sessions.send_message)
    where = source.find(f"{runner}(")
    assert where > 0, f"{runner} 가 이 라우트에서 호출되지 않습니다"
    before = source[:where]
    # The wrapper opens immediately above the runner it is wrapping.
    assert before.rstrip().endswith("_survive_disconnect("), f"{runner} 가 연결이 끊기면 사라집니다"


def test_the_survivor_keeps_the_work_and_drops_only_the_relay() -> None:
    """Cancelling the response cancels the relay only; the task runs to completion."""
    source = inspect.getsource(sessions._detached)
    assert "asyncio.create_task" in source
    assert "_detached(" in inspect.getsource(sessions._survive_disconnect)
    # A strong reference, or the loop may garbage-collect the task mid-turn.
    assert "_DETACHED" in source
