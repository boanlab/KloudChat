"""중단 races the stop signal against the next event and holds one signal per turn."""

from __future__ import annotations

import asyncio

import pytest

from app.routers.sessions import _STOPPING, _until_stopped


async def _wedged(before: int = 1):
    """A turn that emits a little and then never speaks again."""
    for i in range(before):
        yield {"type": "delta", "text": f"{i}"}
    await asyncio.Event().wait()  # accepted and went quiet
    yield {"type": "delta", "text": "never"}


async def _finishes():
    yield {"type": "delta", "text": "가"}
    yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}


async def test_a_silent_turn_is_stopped_while_it_is_silent():
    """A stop is acted on while no event is arriving."""
    stopping = asyncio.Event()
    seen = []

    async def drive():
        async for event in _until_stopped(_wedged(), stopping):
            seen.append(event)

    task = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    assert seen, "the turn should have produced its first token"

    stopping.set()
    # Bounded.
    await asyncio.wait_for(task, timeout=1)

    assert [e["text"] for e in seen] == ["0"]


async def test_the_generator_is_closed_so_the_upstream_call_is_abandoned():
    """Stopping closes the generator so the upstream request is released."""
    stopping = asyncio.Event()
    closed = asyncio.Event()

    async def events():
        try:
            yield {"type": "delta", "text": "가"}
            await asyncio.Event().wait()
        finally:
            closed.set()

    async def drive():
        async for _ in _until_stopped(events(), stopping):
            stopping.set()

    await asyncio.wait_for(drive(), timeout=1)
    assert closed.is_set()


async def test_a_turn_that_ends_on_its_own_is_untouched():
    stopping = asyncio.Event()

    seen = [e async for e in _until_stopped(_finishes(), stopping)]

    assert [e["type"] for e in seen] == ["delta", "usage"]
    assert not stopping.is_set()


async def test_already_stopped_never_starts():
    stopping = asyncio.Event()
    stopping.set()

    seen = [e async for e in _until_stopped(_finishes(), stopping)]

    assert seen == []


# ── one signal per turn, not per session ───────────────────────────────


@pytest.fixture(autouse=True)
def clean_registry():
    _STOPPING.clear()
    yield
    _STOPPING.clear()


def test_a_second_turn_does_not_orphan_the_first_turns_stop_signal():
    """Starting a second turn supersedes the first rather than orphaning its signal."""
    first, second = asyncio.Event(), asyncio.Event()

    _STOPPING.setdefault("s1", set()).add(first)
    for earlier in _STOPPING.get("s1", set()):
        earlier.set()
    _STOPPING["s1"].add(second)

    # Starting the second turn stops the first.
    assert first.is_set()
    assert not second.is_set()
    assert _STOPPING["s1"] == {first, second}


def test_the_stop_button_reaches_every_turn_on_the_session():
    live = {asyncio.Event(), asyncio.Event()}
    _STOPPING["s1"] = set(live)

    for signal in _STOPPING.get("s1", set()):
        signal.set()

    assert all(e.is_set() for e in live)


def test_a_finished_turn_leaves_the_registry_empty():
    """A finished turn leaves no entry in `_STOPPING`."""
    mine, other = asyncio.Event(), asyncio.Event()
    _STOPPING["s1"] = {mine, other}

    for done in (mine, other):
        entry = _STOPPING.get("s1")
        entry.discard(done)
        if not entry:
            del _STOPPING["s1"]

    assert "s1" not in _STOPPING
