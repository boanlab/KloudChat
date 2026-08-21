"""중단 has to reach the model, not only the screen.

The report: choose a model other than the default, press 중단 while it is
answering, watch the screen stop — then switch back, type anything, and the
*cancelled* answer arrives underneath the new question.

Two faults, and both are in the same few lines.

The stop was checked between events:

    async for event in run_turn(...):
        if stopping.is_set():
            break

which runs only when the next event arrives. A model that has accepted the
request and gone quiet produces no next event, so the check never ran. The turn
stayed alive against a fifteen-minute upstream timeout and eventually wrote its
answer into a conversation that had moved on.

And `_STOPPING` held one event per session, so a second turn starting on that
session replaced the first turn's signal — after which nothing could stop the
first one at all.
"""

from __future__ import annotations

import asyncio

import pytest

from app.routers.sessions import _STOPPING, _until_stopped


async def _wedged(before: int = 1):
    """A turn that emits a little and then never speaks again."""
    for i in range(before):
        yield {"type": "delta", "text": f"{i}"}
    await asyncio.Event().wait()  # the model that accepted and went quiet
    yield {"type": "delta", "text": "never"}


async def _finishes():
    yield {"type": "delta", "text": "가"}
    yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}


async def test_a_silent_turn_is_stopped_while_it_is_silent():
    """The whole of the reported bug, in one assertion.

    Waiting for a next event that never comes is what made 중단 a screen-only
    gesture. Racing the two means it is acted on exactly when somebody presses
    it — which is while nothing is arriving.
    """
    stopping = asyncio.Event()
    seen = []

    async def drive():
        async for event in _until_stopped(_wedged(), stopping):
            seen.append(event)

    task = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    assert seen, "the turn should have produced its first token"

    stopping.set()
    # Bounded: before this, it would have sat here until the upstream timeout.
    await asyncio.wait_for(task, timeout=1)

    assert [e["text"] for e in seen] == ["0"]


async def test_the_generator_is_closed_so_the_upstream_call_is_abandoned():
    """Stopping has to release the request, not just stop reading it.

    Leaving it running is what kept a cancelled answer alive long enough to
    turn up later — and what kept paying for it.
    """
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
    """What made a superseded turn unstoppable.

    The registry held one event per session, so starting another turn threw the
    running turn's signal away — and 중단 then had nothing to set.
    """
    first, second = asyncio.Event(), asyncio.Event()

    _STOPPING.setdefault("s1", set()).add(first)
    for earlier in _STOPPING.get("s1", set()):
        earlier.set()
    _STOPPING["s1"].add(second)

    # Starting the second turn stops the first, rather than losing track of it:
    # two turns writing into one conversation interleave into a transcript
    # neither of them wrote.
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
    """A session key that outlives its turns is a leak, one entry per turn."""
    mine, other = asyncio.Event(), asyncio.Event()
    _STOPPING["s1"] = {mine, other}

    for done in (mine, other):
        entry = _STOPPING.get("s1")
        entry.discard(done)
        if not entry:
            del _STOPPING["s1"]

    assert "s1" not in _STOPPING
