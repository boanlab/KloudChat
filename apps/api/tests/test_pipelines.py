"""The flows are pipelines declared as data, and the runner records every stage.

The pipeline files (`app/pipelines/*.toml`) replaced the format if-chains of the report
writer: which pre-pass steps, check families, judged checks and stages a format runs. The
first tests hold every format to what those chains did; the rest pin the runner's contract.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Request
from app.pipeline.runner import ContractError, Runner
from app.pipeline.stage import Base, Context, Stop
from app.services import doc_formats, report_checks

AGENTS = Path(__file__).resolve().parents[1] / "app" / "agents"

# What the if-chains in `report.write`, `report_checks.prepare`, `checks` and `model_checks`
# did, by format: the pipelines must give the same.
DATE = {"minutes", "event_plan", "client_proposal", "gaejo", "result_report"}
QUOTE = {"client_proposal", "comparison", "gaejo"}
PREPASS = DATE | QUOTE | {"comparison", "career_history"}
PAGES = {"gaejo", "client_proposal"}


def _old_prepass(f: str) -> list[str]:
    steps = []
    if f in DATE:
        steps.append("date-calc")
    if f == "client_proposal":
        steps.append("proposal-rfp")
    if f in QUOTE:
        steps += ["quote-calc", "staffing"]
    if f == "comparison":
        steps += ["compare-cost", "compare-check"]
    if f == "career_history":
        steps.append("career-periods")
    return steps


def _old_checks(f: str) -> list[str]:
    out = []
    if f == "client_proposal":
        out += ["quote-totals", "proposal-schedule"]
    if f == "gaejo":
        out.append("gaejo-style")
    out.append("lead-dates")
    if f == "minutes":
        out.append("minutes-rows")
    if f == "comparison":
        out.append("compare-cells")
    if f == "career_history":
        out.append("career-numbers")
    if f in QUOTE:
        out.append("staff-counts")
    if f in PREPASS:
        out.append("material-blanks")
    return out


def _old_judged(f: str) -> list[str]:
    return {"minutes": ["judge-owners"], "client_proposal": ["judge-bindings"],
            "comparison": ["judge-bindings"], "career_history": ["judge-repeats"]}.get(f, [])


FORMAT_IDS = sorted([*doc_formats.BY_ID, "event_plan", "result_report", ""])


@pytest.mark.parametrize("fmt", FORMAT_IDS)
def test_each_format_runs_what_the_if_chains_ran(fmt):
    pipe = registry.for_report(fmt)
    write = pipe.flow("write")
    assert list(registry.prepass_for(fmt)) == (_old_prepass(fmt) if fmt else [])
    assert list(registry.checks_for(fmt)) == _old_checks(fmt)
    assert list(registry.judged_for(fmt)) == _old_judged(fmt)
    assert ("prepass" in write) == (fmt in PREPASS)
    assert ("page-limit" in write) == (fmt in PAGES)
    assert ("minutes-repair" in write) == ("minutes-reground" in write) == (fmt == "minutes")
    assert ("career-periods-tidy" in write) == (fmt == "career_history")
    assert (pipe.options.get("search", True) is False) == (fmt == "career_history")
    gaejo = fmt == "gaejo"
    assert (pipe.options.get("fixed_outline") == "gaejo") == gaejo
    assert (pipe.options.get("draws") == "when-asked") == gaejo
    assert bool(pipe.options.get("prepass_always")) == bool(pipe.options.get("prepass_quiet")) \
        == gaejo


def test_every_named_stage_step_and_check_exists():
    names = set(registry.stages())
    for pipe in registry.pipelines().values():
        for flow in ("pre", "plan", "write", "finish", "revise"):
            missing = [n for n in pipe.flow(flow) if n not in names]
            assert not missing, (pipe.name, flow, missing)
        assert set(pipe.flow("prepass")) <= set(report_checks.PREPASS_STEPS), pipe.name
        assert set(pipe.flow("checks")) <= set(report_checks.CHECK_FAMILIES), pipe.name
        assert set(pipe.flow("judged")) <= set(report_checks.JUDGED_CHECKS), pipe.name
        # A pipeline that writes also plans and asks first.
        if pipe.flow("write"):
            assert pipe.flow("plan") and pipe.flow("pre")


def test_a_format_or_an_agent_is_claimed_once():
    formats = [f for p in registry.pipelines().values() for f in p.formats]
    agents = [a for p in registry.pipelines().values() for a in p.agents]
    assert len(formats) == len(set(formats)) and len(agents) == len(set(agents))


@pytest.mark.parametrize("path", sorted(AGENTS.glob("*.toml")), ids=lambda p: p.stem)
def test_each_shipped_agent_has_its_pipeline(path):
    agent = tomllib.loads(path.read_text())
    pipe = next(p for p in registry.pipelines().values() if agent["key"] in p.agents)
    if agent.get("doc_format"):
        assert registry.for_report(agent["doc_format"]) is pipe
    if "chat" in agent.get("kinds", []):
        assert registry.for_chat(agent["key"]) is pipe
    assert registry.for_chat("no-such-agent").name == "chat"
    assert registry.for_report("no-such-format").name == "report"


def test_the_report_flows_are_the_same_but_for_the_format_stages():
    generic = {"prepass", "page-limit", "minutes-repair", "minutes-reground",
               "career-periods-tidy"}
    base = registry.pipeline("report").flow("write")
    for pipe in registry.pipelines().values():
        if pipe.flow("write"):
            assert [s for s in pipe.flow("write") if s not in generic] == list(base), pipe.name
            assert pipe.flow("plan") == registry.pipeline("report").flow("plan")
        if pipe.flow("finish"):
            assert pipe.flow("finish") == registry.pipeline("chat").flow("finish")
        if pipe.flow("write"):
            assert pipe.flow("revise") == registry.pipeline("deck").flow("revise")


# ── the runner ──────────────────────────────────────────────────────────


class _Stage(Base):
    def __init__(self, name, *, writes=(), skip="", stop=False, fail=False, touch=None):
        self.name, self.writes, self._skip = name, writes, skip
        self.stop, self.fail, self.touch = stop, fail, touch

    def skip(self, bundle):
        return self._skip

    async def run(self, bundle, ctx):
        yield {"type": "step", "id": self.name, "status": "done"}
        if self.touch:
            self.touch(bundle)
        if self.fail:
            raise ValueError("broken")
        if self.stop:
            raise Stop("asked")


def _bundle() -> Bundle:
    return Bundle(request=Request(text="t", model="m", api_key="k"))


async def _run(runner, bundle, **kwargs):
    return [e async for e in runner.run(bundle, Context(), **kwargs)]


async def test_every_stage_is_recorded_run_skipped_or_stopped():
    bundle = _bundle()
    runner = Runner([_Stage("a"), _Stage("b", skip="nothing to do"), _Stage("c", stop=True),
                     _Stage("d")], name="t")
    events = await _run(runner, bundle)
    assert [e["id"] for e in events] == ["a", "c"]
    assert [(r["stage"], r["status"]) for r in bundle.records] == [
        ("a", "done"), ("b", "skipped"), ("c", "stopped"), ("d", "skipped")]
    assert bundle.records[1]["reason"] == "nothing to do"
    assert bundle.records[3]["reason"] == "stopped at c"


async def test_a_failed_stage_is_recorded_and_raised():
    bundle = _bundle()
    with pytest.raises(ValueError):
        await _run(Runner([_Stage("a", fail=True), _Stage("b")]), bundle)
    assert bundle.records[0]["status"] == "failed" and "ValueError" in bundle.records[0]["reason"]


async def test_a_run_resumes_from_a_stage():
    bundle = _bundle()
    events = await _run(Runner([_Stage("a"), _Stage("b"), _Stage("c")]), bundle, start="b")
    assert [e["id"] for e in events] == ["b", "c"]
    assert bundle.records[0] == {**bundle.records[0], "status": "skipped",
                                 "reason": "resumed from b"}


async def test_strict_mode_refuses_an_undeclared_write():
    def write_title(bundle):
        bundle.plan.title = "x"

    ok = Runner([_Stage("a", writes=("plan.title",), touch=write_title)], strict=True)
    await _run(ok, _bundle())
    bad = Runner([_Stage("a", writes=("plan.headings",), touch=write_title)], strict=True)
    with pytest.raises(ContractError):
        await _run(bad, _bundle())


async def test_records_can_be_shown_as_steps():
    bundle = _bundle()
    events = await _run(Runner([_Stage("a"), _Stage("b", skip="no")], emit_records=True),
                        bundle)
    shown = [e for e in events if e.get("category") == "pipeline"]
    assert [e["id"] for e in shown] == ["stage:a", "stage:b"]
    assert all(e["type"] == "step" and e["status"] == "done" for e in shown)


async def test_report_write_records_every_stage_of_its_flow(monkeypatch):
    from app.services import report

    async def complete(_model, messages, _api_key, max_tokens, **_kwargs):
        return ('{"title": "주간 보고", "sections": ["개요", "진행", "다음 주"]}',
                {"inputTokens": 1, "outputTokens": 1})

    monkeypatch.setattr(report, "_complete", complete)
    trace: list[dict] = []
    events = [e async for e in report.write(request="이번 주 업무 보고서를 써 줘", model="m",
                                            api_key="k", web_search=False, trace=trace)]
    assert any(e["type"] == "proposal" for e in events)
    plan = registry.for_report("").flow("plan")
    assert [r["stage"] for r in trace] == list(plan)
    assert trace[-1]["status"] == "stopped"
