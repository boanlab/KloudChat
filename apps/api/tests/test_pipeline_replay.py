"""Recorded acceptance turns replay to the same events.

Each case under `fixtures/replay/` is one lineup acceptance run (minutes, 개조식, client
proposal, comparison, career history; the data-analysis and career-docs chat turns) with its
model calls recorded. The current code, answered from those calls, must emit exactly the
events and sections stored when they were recorded: a stage moved or rewritten without
changing behaviour leaves this green, and one that changed a prompt or an outcome fails here
with the first event that differs. See `replay_build.py` to rebuild a case on purpose.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
import replay_harness as harness

CASES = harness.cases()


@pytest.fixture
def replay(monkeypatch):
    from app.services import settings_store

    async def litellm_config():
        return "http://replay-litellm", "unused"

    monkeypatch.setattr(settings_store, "litellm_config", litellm_config)
    real = settings_store.tools_config

    async def without_search():
        return dataclasses.replace(await real(), search="")

    monkeypatch.setattr(settings_store, "tools_config", without_search)
    return monkeypatch


def _first_difference(got, want) -> str:
    got_events = got["events"] if isinstance(got, dict) else got
    want_events = want["events"] if isinstance(want, dict) else want
    for i, (a, b) in enumerate(zip(got_events, want_events, strict=False)):
        if a != b:
            return (f"event {i} differs:\n got  {json.dumps(a, ensure_ascii=False)[:1500]}\n"
                    f" want {json.dumps(b, ensure_ascii=False)[:1500]}")
    if len(got_events) != len(want_events):
        return f"{len(got_events)} events, recorded {len(want_events)}"
    return "stored message differs"


@pytest.mark.parametrize("name", CASES)
async def test_a_recorded_turn_replays_to_the_same_events(replay, name):
    case, calls, golden = harness.load(name)
    gateway = harness.Gateway(calls=calls)
    gateway.install(replay.setattr)
    harness.freeze(replay.setattr, case["now"])
    if case["kind"] == "report":
        got = await harness.run_report(case)
    elif case["kind"] == "revise":
        got = await harness.run_revise(case, replay.setattr)
    else:
        got = await harness.run_chat(case, replay.setattr)
    got = json.loads(json.dumps(got, ensure_ascii=False))
    assert not gateway.misses, f"prompts with no recorded reply: {gateway.misses[:2]}"
    assert got == golden, _first_difference(got, golden)
    # Every recorded call was made, by the stage that made it when recorded: a stage that
    # stopped calling the model, or a call that moved to another stage, is a change too.
    made = sorted((c["key"], c.get("stage", "")) for c in gateway.made)
    assert made == sorted((c["key"], c.get("stage", "")) for c in calls)


def test_every_shipped_format_has_a_recorded_turn():
    assert {"minutes", "gaejo", "client_proposal", "comparison", "career_history",
            "data_analysis_chat", "career_docs_chat", "revise_comparison",
            "revise_proposal"} <= set(CASES)
