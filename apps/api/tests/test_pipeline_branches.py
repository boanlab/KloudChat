# ruff: noqa: F811 — fixtures imported from test_user_pipelines are parameters here.
"""Branching user pipelines: stable step ids, edges, condition steps, per-step run records."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlmodel import select

# The `engine`, `wired` and `writers` fixtures are shared with the pipeline tests.
from test_user_pipelines import (  # noqa: F401
    _AGENTS,
    _JUDGE,
    _people,
    _pipeline,
    _run,
    engine,
    wired,
    writers,
)

from app.models.chat import ChatSession, PipelineRun, SessionKind
from app.models.workspace import Agent
from app.pipeline import compose
from app.pipeline.bundle import Bundle, Request
from app.pipeline.stage import Context
from app.routers import user_pipelines as up
from app.services import report


def _graph(*rows, edges=None) -> tuple[list[dict], list[dict] | None]:
    steps = compose.normalize([dict(r) for r in rows])
    return steps, compose.normalize_edges(edges, steps)


def _codes(verdict: compose.Verdict) -> list[tuple[str, str]]:
    return [(p.step_id, p.code) for p in verdict.problems]


_RULE = {"id": "if", "kind": "condition", "name": "장이 있나?", "mode": "rule",
         "rule": {"step": "w", "value": "sections", "op": "gt", "target": "0"}}
_BRANCHED = [{"from": "w", "to": "if"}, {"from": "if", "to": "chk", "branch": "yes"},
             {"from": "if", "to": "rev", "branch": "no"}]


def _branched_steps(rule: dict | None = None) -> list[dict]:
    return [{"id": "w", "kind": "agent", "ref": "cmp"}, {**_RULE, **(rule or {})},
            {"id": "chk", "kind": "check", "ref": "grounding-check"},
            {"id": "rev", "kind": "review", "settings": {"model": _JUDGE["id"]}}]


# ── shape and validation ───────────────────────────────────────────────


def test_an_old_linear_pipeline_runs_in_its_stored_order_as_before():
    rows = [{"kind": "agent", "ref": "data"}, {"kind": "check", "ref": "copy-check"},
            {"kind": "agent", "ref": "cmp"}, {"kind": "export", "settings": {"formats": ["pdf"]}}]
    steps = compose.normalize(rows)
    # Ids were always given by position; edges that only repeat the order are no graph.
    assert [s["id"] for s in steps] == ["s1", "s2", "s3", "s4"]
    chain = [{"from": a, "to": b} for a, b in (("s1", "s2"), ("s2", "s3"), ("s3", "s4"))]
    assert compose.flow_of(steps, None) is None and compose.flow_of(steps, chain) is None
    plain = compose.compile_steps(steps, _AGENTS)
    assert [s.name for s in plain] == ["1:agent", "2:check", "3:agent", "save", "4:export"]
    assert [s.name for s in compose.compile_steps(steps, _AGENTS, chain)] == [
        s.name for s in plain]
    assert all(s.flow is None for s in plain if isinstance(s, compose.Step))
    assert compose.validate(steps, agents=_AGENTS).wire() == compose.validate(
        steps, agents=_AGENTS, edges=chain).wire()


def test_a_rule_condition_always_has_yes_and_no_and_keeps_its_labels():
    steps, _ = _graph({**_RULE, "branches": [{"key": "yes", "label": "있음"},
                                             {"key": "maybe", "label": "?"}]})
    assert steps[0]["branches"] == [{"key": "yes", "label": "있음"},
                                    {"key": "no", "label": "아니오"}]
    assert steps[0]["rule"] == {"step": "w", "value": "sections", "op": "gt", "target": "0"}
    assert compose.step_title(steps[0], {}) == "장이 있나?"


def test_a_branching_pipeline_with_every_branch_ending_is_valid():
    steps, edges = _graph(*_branched_steps(), edges=_BRANCHED)
    verdict = compose.validate(steps, agents=_AGENTS, edges=edges,
                               allowed_models={_JUDGE["id"]})
    assert verdict.ok, verdict.problems
    assert verdict.surface == "report"


def test_a_loop_back_to_an_earlier_step_is_refused():
    steps, edges = _graph(*_branched_steps(), edges=[*_BRANCHED, {"from": "chk", "to": "w"}])
    verdict = compose.validate(steps, agents=_AGENTS, edges=edges)
    assert _codes(verdict) == [("w", "cycle")]
    assert verdict.wire()["problems"][0]["stepId"] == "w"
    assert verdict.problems[0].step == 1


def test_a_branch_with_nothing_after_it_is_a_dead_end():
    steps, edges = _graph(*_branched_steps()[:3], edges=_BRANCHED[:2])
    verdict = compose.validate(steps, agents=_AGENTS, edges=edges)
    assert _codes(verdict) == [("if", "branch_dead_end")]
    assert "「아니오」 갈래 뒤에 이어질 단계가 없습니다" in verdict.problems[0].message


def test_a_step_no_line_reaches_is_unreachable():
    steps, edges = _graph(*_branched_steps(),
                          {"id": "lost", "kind": "check", "ref": "copy-check"}, edges=_BRANCHED)
    verdict = compose.validate(steps, agents=_AGENTS, edges=edges,
                               allowed_models={_JUDGE["id"]})
    assert _codes(verdict) == [("lost", "unreachable_step")]


def test_an_incomplete_condition_is_named_with_what_it_lacks():
    cases = {
        "규칙이 읽을 값을 이 조건 앞 단계에서 골라 주세요.": {"step": "chk"},
        "그 단계가 남기지 않는 값입니다. 다른 값을 골라 주세요.": {"value": "mood"},
        "비교 방법과 기준을 채워 주세요.": {"target": ""},
        "「보다 크면」은 숫자 값과 숫자 기준에만 쓸 수 있습니다.": {"target": "많이"},
    }
    for said, change in cases.items():
        rule = {**_RULE["rule"], **change}
        steps, edges = _graph(*_branched_steps({"rule": rule}), edges=_BRANCHED)
        verdict = compose.validate(steps, agents=_AGENTS, edges=edges,
                                   allowed_models={_JUDGE["id"]})
        assert _codes(verdict) == [("if", "condition_incomplete")], said
        assert verdict.problems[0].message.endswith(said)
    # An AI condition needs its question; a condition in a pipeline without lines has no branches.
    ask = {"id": "if", "kind": "condition", "mode": "ai", "question": "",
           "branches": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]}
    steps, edges = _graph(_branched_steps()[0], ask, *_branched_steps()[2:], edges=[
        {"from": "w", "to": "if"}, {"from": "if", "to": "chk", "branch": "a"},
        {"from": "if", "to": "rev", "branch": "b"}])
    assert _codes(compose.validate(steps, agents=_AGENTS, edges=edges,
                                   allowed_models={_JUDGE["id"]})) == [
        ("if", "condition_incomplete")]
    steps, _ = _graph(*_branched_steps())
    assert ("if", "condition_incomplete") in _codes(compose.validate(steps, agents=_AGENTS))


def test_branches_that_end_in_a_document_and_an_answer_cannot_be_saved():
    steps, edges = _graph(_branched_steps()[0], _RULE,
                          {"id": "chk", "kind": "agent", "ref": "data"},
                          {"id": "rev", "kind": "check", "ref": "grounding-check"},
                          edges=_BRANCHED)
    verdict = compose.validate(steps, agents=_AGENTS, edges=edges)
    assert [p.code for p in verdict.problems] == ["branch_surface"]


def test_only_a_condition_may_lead_to_two_steps():
    steps, edges = _graph({"id": "w", "kind": "agent", "ref": "cmp"},
                          {"id": "a", "kind": "check", "ref": "grounding-check"},
                          {"id": "b", "kind": "check", "ref": "generic-language-killer"},
                          edges=[{"from": "w", "to": "a"}, {"from": "w", "to": "b"}])
    assert _codes(compose.validate(steps, agents=_AGENTS, edges=edges)) == [
        ("w", "parallel_next")]


def test_the_catalogue_lists_only_values_steps_leave_behind():
    values = compose.catalogue()["condition_values"]
    assert {"step_kind": "check", "key": "found", "label": "찾은 곳 수",
            "type": "number"} in values
    assert {v["step_kind"] for v in values} == {"request", "agent", "skill", "check", "review",
                                                "human"}


# ── deciding ───────────────────────────────────────────────────────────


def test_a_rule_compares_numbers_text_and_emptiness():
    assert compose.rule_holds({"op": "gt", "target": "2"}, 3)
    assert not compose.rule_holds({"op": "gt", "target": "3"}, 3)
    assert compose.rule_holds({"op": "eq", "target": "document"}, "Document")
    assert compose.rule_holds({"op": "contains", "target": "PDF"}, "docx, pdf")
    assert compose.rule_holds({"op": "empty", "target": ""}, 0)
    assert not compose.rule_holds({"op": "empty", "target": ""}, "있음")


def _condition_bundle(**turn) -> Bundle:
    work = Bundle(request=Request(text="회의 메모 정리", model="writer", api_key="k",
                                  outline_model="planner", turn=turn))
    work.wire["product"] = {"kind": "answer"}
    work.document.answer = "실행 과제: 견적 회신(김 과장)"
    return work


async def test_a_rule_reading_a_value_no_step_left_takes_the_no_branch():
    spec = compose.normalize([{**_RULE, "rule": {**_RULE["rule"], "step": "skipped"}}])[0]
    work = _condition_bundle()
    out = [e async for e in compose.ConditionStep(1, spec, "조건").run(work, Context())]
    assert work.wire["branches"] == {"if": "no"}
    assert out[-1]["branch"]["missing"] is True
    assert "읽을 값이 없어" in out[-1]["branch"]["reason"]


async def test_an_ai_condition_asks_the_judge_and_keeps_its_reason(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append((model, messages[-1]["content"]))
        return json.dumps({"branch": "mail", "reason": "실행 과제가 하나 있습니다."}), {
            "inputTokens": 30, "outputTokens": 6}

    monkeypatch.setattr(report, "_complete", complete)
    spec = compose.normalize([{"id": "ask", "kind": "condition", "mode": "ai",
                               "question": "실행 과제가 있나?",
                               "branches": [{"key": "mail", "label": "메일 초안"},
                                            {"key": "keep", "label": "회의록만"}]}])[0]
    work = _condition_bundle()
    out = [e async for e in compose.ConditionStep(1, spec, "조건").run(work, Context())]
    assert work.wire["branches"] == {"ask": "mail"}
    assert out[-1]["branch"] == {"key": "mail", "label": "메일 초안",
                                 "reason": "실행 과제가 하나 있습니다.", "missing": False}
    model, prompt = calls[0]
    assert model == "planner"  # the judge (planner) model
    assert "Question: 실행 과제가 있나?" in prompt and "견적 회신" in prompt
    # Tokens are counted with the run like any judge call.
    assert (work.usage["inputTokens"], work.usage["outputTokens"]) == (30, 6)

    # A strict-local run never sends the question to a judge outside the boundary.
    strict = {"dataBoundary": "self_hosted", "strictLocal": True}
    work = _condition_bundle(model=strict, outline_model={"dataBoundary": "external"})
    _ = [e async for e in compose.ConditionStep(1, spec, "조건").run(work, Context())]
    assert calls[-1][0] == "writer"

    # A reply naming no branch takes the last one, said so.
    async def vague(model, messages, api_key, max_tokens):
        return "글쎄요", {}

    monkeypatch.setattr(report, "_complete", vague)
    work = _condition_bundle()
    out = [e async for e in compose.ConditionStep(1, spec, "조건").run(work, Context())]
    assert work.wire["branches"] == {"ask": "keep"} and out[-1]["branch"]["missing"] is True


# ── running and the records ────────────────────────────────────────────


async def _branched_row(db, p, rule: dict | None = None):
    rows = _branched_steps(rule)
    rows[0] = {**rows[0], "ref": p.cmp.id}
    return await _pipeline(db, p.me, "갈래", rows, edges=_BRANCHED)


async def test_a_run_follows_only_the_branch_its_rule_picks(wired, writers):
    p = await _people(wired.local)
    async with wired.local() as db:
        yes = await _branched_row(db, p)
        no = await _branched_row(db, p, {"rule": {**_RULE["rule"], "target": "5"}})
        session = ChatSession(user_id=p.me.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
    events = await _run(wired.local, p.me, session, yes)
    records = [e["stage"] for e in events if e.get("category") == "pipeline"]
    # The writer wrote one section: more than 0 → 예 → the check; the review is skipped.
    assert [(r["name"], r["status"]) for r in records] == [
        ("1:agent", "done"), ("2:condition", "done"), ("3:check", "done"),
        ("4:review", "skipped"), ("save", "done")]
    assert records[1]["summary"] == "「예」 갈래로"
    assert records[3]["reason"] == compose.OTHER_BRANCH
    assert not [c for c in writers["complete"] if "independent reviewer" in c[1]]

    events = await _run(wired.local, p.me, session, no)
    records = [e["stage"] for e in events if e.get("category") == "pipeline"]
    assert [(r["name"], r["status"]) for r in records] == [
        ("1:agent", "done"), ("2:condition", "done"), ("3:check", "skipped"),
        ("4:review", "done"), ("save", "done")]
    assert records[1]["summary"] == "「아니오」 갈래로"

    # The owner reads each run per step; the stored records say which branch was taken.
    async with wired.local() as db:
        runs = await up.list_runs(yes.id, p.me, db)
        assert len(runs) == 1
        run = runs[0]
        assert (run["status"], run["session_id"], run["pipeline_id"]) == (
            "done", session.id, yes.id)
        assert set(run) >= {"id", "status", "started_at", "ended_at", "session_id", "steps"}
        steps = {s["step_id"]: s for s in run["steps"]}
        assert list(steps) == ["w", "if", "chk", "rev"]
        assert steps["w"]["input_summary"] == "요청"
        assert steps["w"]["output_summary"] == "절 1개 작성"
        assert steps["if"]["branch_taken"] == "yes" and steps["if"]["status"] == "done"
        assert steps["if"]["input_summary"] == "「비교 보고서」 절 1개 작성"
        assert steps["chk"]["status"] == "done" and steps["chk"]["branch_taken"] is None
        assert (steps["rev"]["status"], steps["rev"]["note"]) == ("skipped", "다른 갈래라 건너뜀")
        assert set(steps["w"]) >= {"step_id", "status", "started_at", "ended_at",
                                   "input_summary", "output_summary", "note", "branch_taken"}
        one = await up.get_run(run["id"], p.me, db)
        assert one == run
        # Nobody else reads them.
        for call in (up.list_runs(yes.id, p.other, db), up.get_run(run["id"], p.other, db)):
            with pytest.raises(HTTPException) as caught:
                await call
            assert caught.value.status_code == 404
        stored = (await db.exec(select(PipelineRun).where(
            PipelineRun.pipeline == f"user:{yes.id}"))).one()
        assert stored.stages[1]["evidence"]["branch"]["key"] == "yes"


async def test_an_agents_process_saves_and_loads_its_edges(wired):
    p = await _people(wired.local)
    rows = [{"id": "a", "kind": "agent", "ref": p.data.id},
            {"id": "if", "kind": "condition", "mode": "rule",
             "rule": {"step": "a", "value": "chars", "op": "gt", "target": "100"}},
            {"id": "long", "kind": "check", "ref": "grounding-check"},
            {"id": "short", "kind": "check", "ref": "copy-check"}]
    edges = [{"from": "a", "to": "if"}, {"from": "if", "to": "long", "branch": "yes"},
             {"from": "if", "to": "short", "branch": "no"}]
    async with wired.local() as db:
        # A dead-end branch cannot be saved.
        with pytest.raises(HTTPException) as caught:
            await up.put_process(p.memo.id, up.ProcessIn(mode="custom", stages=rows,
                                                         edges=edges[:2]), p.me, db)
        assert caught.value.status_code == 422
        assert [(x["code"], x.get("stepId")) for x in caught.value.detail["problems"]] == [
            ("branch_dead_end", "if"), ("unreachable_step", "short")]
        checked = await up.validate_draft(up.ValidateIn(stages=rows, edges=edges,
                                                        agent_id=p.memo.id), p.me, db)
        assert checked["ok"] and checked["surface"] == "chat"
        out = await up.put_process(p.memo.id, up.ProcessIn(mode="custom", stages=rows,
                                                           edges=edges), p.me, db)
        assert out["edges"] == edges
        assert [s["id"] for s in out["steps"]] == ["a", "if", "long", "short"]
        agent = await db.get(Agent, p.memo.id)
        loaded = await up.process_out(db, p.me, agent)
        assert loaded["edges"] == edges and loaded["pipelineId"] == agent.pipeline_id
        # Changing only the lines is an edit: a new version.
        version = (await db.get(up.UserPipeline, agent.pipeline_id)).version
        swapped = [edges[0], {**edges[1], "to": "short"}, {**edges[2], "to": "long"}]
        await up.put_process(p.memo.id, up.ProcessIn(mode="custom", stages=rows,
                                                     edges=swapped), p.me, db)
        assert (await db.get(up.UserPipeline, agent.pipeline_id)).version == version + 1


def test_a_bundle_cannot_hold_a_condition():
    steps = compose.normalize([_RULE])
    assert [x["message"] for x in up._bundle_problems(steps, {}, set())] == [
        "1번: 묶음에는 조건 단계를 넣을 수 없습니다."]


# ── the migration ──────────────────────────────────────────────────────


def test_the_migration_gives_stored_steps_the_ids_they_were_shown_under():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0077_pipeline_branches.py"
    spec = importlib.util.spec_from_file_location("pipeline_branches_0077", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0077", "0076")
    engine = sa.create_engine("sqlite://")
    stored = [{"kind": "agent", "ref": "x"}, {"id": "s1", "kind": "check", "ref": "copy-check"},
              {"id": "mine", "kind": "export"}]
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE user_pipelines (id VARCHAR PRIMARY KEY, stages JSON)"))
        conn.execute(sa.text("INSERT INTO user_pipelines VALUES ('p', :s), ('q', NULL)"),
                     {"s": json.dumps(stored)})
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert "edges" in {c["name"] for c in sa.inspect(conn).get_columns("user_pipelines")}
        kept = json.loads(conn.execute(sa.text(
            "SELECT stages FROM user_pipelines WHERE id = 'p'")).scalar_one())
        assert [s["id"] for s in kept] == [s["id"] for s in compose.normalize(stored)]
        assert [s["id"] for s in kept] == ["s1", "s1x", "mine"]
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "edges" not in {c["name"] for c in sa.inspect(conn).get_columns("user_pipelines")}
