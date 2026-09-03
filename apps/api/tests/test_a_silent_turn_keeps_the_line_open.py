"""A model thinking is not a connection dying.

A 30-slide outline on a local model produced no event for 65 seconds. The proxy
in front of the deployment closes a response that has sent no byte for sixty,
so the browser's stream ended with a network error a moment before the plan
arrived, and the screen read 「문서를 만들지 못했습니다」 over a turn the server
finished, stored and offered for approval. `_heartbeat` puts an SSE comment on
the wire whenever the turn has said nothing for a while: a byte the proxy
counts, a line the parser skips.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app.routers import sessions


async def _silent_then(events: list[str], quiet: float):
    for event in events:
        await asyncio.sleep(quiet)
        yield event


@pytest.mark.asyncio
async def test_silence_is_filled_with_comments_and_the_events_still_arrive(monkeypatch) -> None:
    monkeypatch.setattr(sessions, "HEARTBEAT_SEC", 0.02)
    turn = _silent_then(["data: a\n\n", "data: b\n\n"], 0.07)
    out = [line async for line in sessions._heartbeat(turn)]
    assert [line for line in out if line.startswith("data:")] == ["data: a\n\n", "data: b\n\n"]
    # Something was said during each wait, and it is a comment — no `data:` a
    # client could mistake for an event.
    beats = [line for line in out if not line.startswith("data:")]
    assert len(beats) >= 2
    assert all(line.startswith(":") and line.endswith("\n\n") for line in beats)
    # The events come out in order, with the heartbeat between and not inside.
    assert out.index("data: a\n\n") < out.index("data: b\n\n")


@pytest.mark.asyncio
async def test_a_turn_that_keeps_talking_is_not_interrupted(monkeypatch) -> None:
    monkeypatch.setattr(sessions, "HEARTBEAT_SEC", 0.2)
    out = [line async for line in sessions._heartbeat(_silent_then(["data: 1\n\n"] * 5, 0.001))]
    assert out == ["data: 1\n\n"] * 5


@pytest.mark.asyncio
async def test_a_reader_leaving_still_closes_the_turn_beneath(monkeypatch) -> None:
    """Closing the relay must unwind the generator under it, as before."""
    monkeypatch.setattr(sessions, "HEARTBEAT_SEC", 0.01)
    closed = asyncio.Event()

    async def turn():
        try:
            yield "data: first\n\n"
            await asyncio.sleep(10)
            yield "data: never\n\n"
        finally:
            closed.set()

    relay = sessions._heartbeat(turn())
    assert await anext(relay) == "data: first\n\n"
    assert (await anext(relay)).startswith(":")
    await relay.aclose()
    assert closed.is_set()


def test_every_streamed_route_beats() -> None:
    """The wrapper every route serves through carries the heartbeat."""
    assert "_heartbeat(" in inspect.getsource(sessions._survive_disconnect)
    # The comparison route is not detached on purpose (stop is the client
    # leaving), so it takes the heartbeat directly.
    source = inspect.getsource(sessions.compare_models)
    where = source.find("_run_comparison(")
    assert source[:where].rstrip().endswith("_heartbeat(")
    # Under the shortest common proxy idle timeout, with room to spare.
    assert sessions.HEARTBEAT_SEC <= 30
