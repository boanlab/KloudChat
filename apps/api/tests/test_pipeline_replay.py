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


# ── per stage: a recorded run restored before any stage and run on from it ──

REPORTS = [c for c in CASES if harness.load(c)[0]["kind"] == "report"]


async def _recorded_write(case, calls, patch):
    """Both passes of the case, the writing pass kept by a recorder."""
    from app.pipeline import runs
    from app.services import report

    gateway = harness.Gateway(calls=calls)
    gateway.install(patch)
    kwargs = dict(case["write"])
    planned = [e async for e in report.write(**kwargs)]
    plan = next(e["plan"] for e in planned if e["type"] == "proposal")
    recorder, built, events = runs.Recorder("replay"), [], []
    async for event in report.write(**kwargs, approved_plan=case.get("approved_plan") or plan,
                                    figures_plan=[], recorder=recorder, bundle_out=built):
        events.append(event)
    return recorder, built[0][0], events


def _stage_of(call: dict) -> str:
    stage = str(call.get("stage") or "")
    return stage if stage.startswith("check:") else stage.split(":")[0]


def _body(events) -> list[tuple[str, str]]:
    sections = next(e["sections"] for e in reversed(events) if e["type"] == "report")
    return [(s.get("heading"), s.get("content")) for s in sections]


@pytest.mark.parametrize("name", REPORTS)
async def test_a_recorded_run_reruns_from_every_stage_to_the_same_document(replay, name):
    from app.pipeline import registry, runs
    from app.pipeline.runner import Runner
    from app.pipeline.stage import Context

    case, calls, _golden = harness.load(name)
    harness.freeze(replay.setattr, case["now"])
    recorder, first, events = await _recorded_write(case, calls, replay.setattr)
    want = _body(events)
    snaps = json.loads(json.dumps(recorder.snapshots(), ensure_ascii=False))
    pipe = registry.for_report(first.spec.format_id)
    flow = list(pipe.flow("write"))
    ran = [r.stage for r in first.steps if r.status == "done"]
    assert ran and set(ran) <= set(runs.rerunnable_from(snaps))
    for start in ran:
        later = set(flow[flow.index(start):])
        # A stage's own steps tag their calls under it (`prepass:quote-calc`).
        gateway = harness.Gateway(calls=[c for c in calls if _stage_of(c) in later])
        gateway.install(replay.setattr)
        bundle = runs.restore(recorder.request, snaps, before=start, api_key="k")
        got = [e async for e in Runner(pipe.stages("write"), name="rerun").run(
            bundle, Context(options=dict(pipe.options)), start=start)]
        assert not gateway.misses, f"{start}: prompts with no recorded reply"
        # Only the stage and those after it ran; the ones before made no call.
        assert {_stage_of(c) for c in gateway.made} <= later, start
        assert _body(got) == want, f"rerun from {start} wrote another document"


@pytest.mark.parametrize("name", REPORTS)
async def test_the_records_ride_the_same_stream(replay, name, monkeypatch):
    """With the records on, the stream is the recorded one with one record per stage."""
    from app.pipeline import registry

    monkeypatch.setenv("KLOUDCHAT_PIPELINE_STEPS", "1")
    case, calls, golden = harness.load(name)
    harness.Gateway(calls=calls).install(replay.setattr)
    harness.freeze(replay.setattr, case["now"])
    got = json.loads(json.dumps(await harness.run_report(case), ensure_ascii=False))
    records = [e for e in got if e.get("category") == "pipeline"]
    assert [e for e in got if e.get("category") != "pipeline"] == golden
    write = registry.for_report(case["write"].get("format_id")).flow("write")
    written = [e["stage"]["name"] for e in records][-len(write):]
    assert written == list(write)
    assert all(e["type"] == "step" and e["status"] in ("done", "error") for e in records)
    save = records[-1]["stage"]
    assert (save["name"], save["status"], save["reason"]) == (
        "report-save", "skipped", "no turn to store")


CHATS = [c for c in CASES if harness.load(c)[0]["kind"] not in ("report", "revise")]


@pytest.mark.parametrize("name", CHATS)
async def test_a_chat_answer_keeps_its_finish_records(replay, name, monkeypatch):
    """The stream's finish records are stored on the answer (`routing.run`), so a reloaded
    transcript lists them beside the tool steps as the stream did."""
    from app.pipeline import registry

    monkeypatch.setenv("KLOUDCHAT_PIPELINE_STEPS", "1")
    case, calls, _golden = harness.load(name)
    harness.Gateway(calls=calls).install(replay.setattr)
    harness.freeze(replay.setattr, case["now"])
    got = json.loads(json.dumps(await harness.run_chat(case, replay.setattr), ensure_ascii=False))
    streamed = [e["stage"]["name"] for e in got["events"] if e.get("category") == "pipeline"]
    run = got["stored"]["run"]
    assert (run["flow"], run["id"]) == ("finish", "")
    finish = list(registry.for_chat(case["turn"].get("agent_key") or "").flow("finish"))
    assert [s["name"] for s in run["stages"]] == finish == streamed[-len(finish):]
    assert all("evidence" not in s for s in run["stages"])


REVISIONS = [c for c in CASES if harness.load(c)[0]["kind"] == "revise"]


@pytest.mark.parametrize("name", REVISIONS)
async def test_a_recorded_revision_reruns_from_every_stage_to_the_same_rows(replay, name):
    """A revision kept by its recorder, run again from each of its stages, stores the same
    sections; the stages before the restart make no call."""
    from types import SimpleNamespace

    from app.pipeline import registry, runs
    from app.routers import sessions

    case, calls, golden = harness.load(name)
    harness.Gateway(calls=calls).install(replay.setattr)
    harness.freeze(replay.setattr, case["now"])
    kept = []

    async def persist(work, recorder, **_kwargs):
        kept.append((work, recorder))

    replay.setattr(sessions, "_persist_run", persist)
    got = await harness.run_revise(case, replay.setattr)
    rows = got[-1]["rows"]

    def stored_sections():
        return [r["sections"] for r in rows if "sections" in r][-1]

    want = [r["sections"] for r in golden[-1]["rows"] if "sections" in r][-1]
    assert stored_sections() == want
    work, recorder = kept[0]
    snaps = json.loads(json.dumps(recorder.snapshots(), ensure_ascii=False))
    pipe = registry.pipeline(recorder.pipeline)
    flow = [s.name for s in sessions._revise_stages(pipe)]
    # Calls a stage's own steps tag with a name of no stage here (a pre-pass run again by
    # `revise-widen`) are offered to every restart.
    orphans = {_stage_of(c) for c in calls} - set(flow)
    parent = SimpleNamespace(id="parent", stages=[r.as_dict() for r in work.steps])
    for start in [r.stage for r in work.steps if r.status == "done"]:
        if start not in flow or start == "revise-save":
            continue  # `revise-load` runs before the pipeline is known
        later = set(flow[flow.index(start):]) | orphans
        gateway = harness.Gateway(calls=[c for c in calls if _stage_of(c) in later])
        gateway.install(replay.setattr)
        bundle = runs.restore(recorder.request, snaps, before=start, api_key="k")
        again = runs.Recorder("again")
        again.pipeline, again.flow = recorder.pipeline, "revise"
        stored = len(rows)
        _events = [c async for c in sessions._revise_pass(
            bundle, pipe, again, session_id="replay-session", user_id="replay-user",
            start=start, carried=runs.carried(snaps, before=start), attempts={},
            parent=parent)]
        assert not gateway.misses, f"{start}: prompts with no recorded reply"
        assert {_stage_of(c) for c in gateway.made} <= later, start
        # Stored again, as a new version: the same document.
        assert len(rows) > stored and stored_sections() == want, start
