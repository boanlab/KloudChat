"""Pipelines declared as data (`app/pipelines/*.toml`) and the runner's stage records."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Request
from app.pipeline.runner import ContractError, Runner
from app.pipeline.stage import Base, Context, Stop
from app.services import doc_formats, process_settings, report_checks

AGENTS = Path(__file__).resolve().parents[1] / "app" / "agents"

# The steps each format expects, written out by hand: the pipelines must give the same.
DATE = {"minutes", "event_plan", "client_proposal", "gaejo", "result_report", "agenda"}
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
    if f == "result_report":
        # a remark's count kept with its remark
        out.append("count-labels")
    return out


def _old_judged(f: str) -> list[str]:
    return {"minutes": ["judge-owners"], "client_proposal": ["judge-bindings"],
            "comparison": ["judge-bindings"], "career_history": ["judge-repeats"]}.get(f, [])


FORMAT_IDS = sorted([*doc_formats.BY_ID, "event_plan", "result_report", ""])

#: How each kind of document treated two values for one thing (`key_figures._ADOPT`).
_OLD_ADOPT = {
    "comparison": (
        "In the 「자료와 충돌」 section, write the two values side by side with their source, time "
        "and measurement conditions, and state in one line which value you followed and why "
        "(같은 조건 측정 > 최신 > 소개서). If the conditions differ, adopt neither and leave "
        "「조건 다름」."
    ),
    "minutes": (
        "The value of the later decision or later mail is final. Write the final value and leave "
        "「○○에서 변경」 in 비고."
    ),
    "gaejo": "Use the latest value only for the same item the material explicitly changed, and "
             "record the change in one line.",
    "client_proposal": (
        "State which value you took as the premise, and leave what must be checked in "
        "「전제·제외 범위·위험」 or the question list."
    ),
}
#: What `report_checks.amendments` added by format.
_OLD_AMEND = {"client_proposal": ["requirement-ids", "quote-validity"],
              "comparison": ["option-status", "conflict-values", "unverified-options"]}
#: Formats whose 서식 turns web search off: written from the person's own material.
NO_SEARCH = {"career_history", "gaejo", "minutes", "result_report", "internal_draft", "agenda"}
#: Formats whose 서식 draws figures only when asked.
DRAW_WHEN_ASKED = {"gaejo", "minutes", "comparison", "result_report", "internal_draft",
                   "event_plan", "agenda"}
OPTIONAL = {"prepass", "page-limit", "minutes-repair", "minutes-reground", "career-periods-tidy"}


@pytest.mark.parametrize("fmt", FORMAT_IDS)
def test_each_format_runs_what_the_if_chains_ran(fmt):
    """The settings its 서식 declares give each format what the if-chains (and then the
    per-format pipeline files) ran."""
    settings = process_settings.for_format(fmt)
    assert list(settings.prepass) == (_old_prepass(fmt) if fmt else [])
    assert list(settings.code_checks) == _old_checks(fmt)
    assert list(settings.judged) == _old_judged(fmt)
    assert settings.runs_prepass == (fmt in PREPASS)
    assert settings.has_stage("page-limit") == (fmt in PAGES)
    assert settings.has_stage("minutes-repair") == settings.has_stage("minutes-reground") \
        == (fmt == "minutes")
    assert settings.has_stage("career-periods-tidy") == (fmt == "career_history")
    # Documents written from the person's own material search nothing.
    assert (settings.search is False) == (fmt in NO_SEARCH)
    gaejo = fmt == "gaejo"
    # A 공문 is one untitled 「본문」; a 공모 계획서 the funder's skeleton, no 요약 or 권고안.
    assert list(settings.outline) == (
        list(doc_formats.GAEJO_OUTLINE) if gaejo else ["본문"] if fmt == "official"
        else list(process_settings.FORMAT_DEFAULTS["grant"]["structure"]["sections"])
        if fmt == "grant" else [])
    # Work documents read from their tables and lists draw no figures unless asked; a 공문
    # and a 보도자료 have no shipped 서식, so the format's own default says so.
    assert (settings.draws == "when-asked") == (fmt in DRAW_WHEN_ASKED | {"official", "press"})
    assert settings.prepass_always == settings.prepass_quiet == gaejo
    assert settings.date_base == ("meeting" if fmt == "minutes" else "text")
    assert list(settings.amend) == _OLD_AMEND.get(fmt, [])
    assert settings.structure.summary_lines == (2 if gaejo else 0)
    assert settings.structure.page_limit == (1 if gaejo else 0)
    assert settings.structure.page_limit_rfp == (fmt == "client_proposal")
    assert list(settings.head) == {"minutes": ["meeting-date"], "agenda": ["meeting-date"],
                                   "client_proposal": ["rfp-client"]}.get(fmt, [])
    assert settings.conflict_rule == _OLD_ADOPT.get(fmt, "")
    # Every document is written by one flow that holds every optional stage.
    assert OPTIONAL <= set(registry.for_report(fmt).flow("write"))


def test_every_named_stage_step_and_check_exists():
    names = set(registry.stages())
    for pipe in registry.pipelines().values():
        for flow in ("pre", "plan", "write", "finish", "revise"):
            missing = [n for n in pipe.flow(flow) if n not in names]
            assert not missing, (pipe.name, flow, missing)
        # A pipeline that writes also plans and asks first.
        if pipe.flow("write"):
            assert pipe.flow("plan") and pipe.flow("pre")
    assert set(process_settings.PREPASS) == set(report_checks.PREPASS_STEPS)
    assert set(process_settings.CHECKS) == set(report_checks.CHECK_FAMILIES)
    assert set(process_settings.JUDGED) == set(report_checks.JUDGED_CHECKS)
    assert set(process_settings.STAGES) <= names


def test_a_format_or_an_agent_is_claimed_once():
    formats = [f for p in registry.pipelines().values() for f in p.formats]
    agents = [a for p in registry.pipelines().values() for a in p.agents]
    assert len(formats) == len(set(formats)) and len(agents) == len(set(agents))


@pytest.mark.parametrize("path", sorted(AGENTS.glob("*.toml")), ids=lambda p: p.stem)
def test_each_shipped_agent_has_its_pipeline(path):
    agent = tomllib.loads(path.read_text())
    if agent.get("surface") == "report":
        assert registry.for_report(agent.get("doc_format")).name == "report"
    if "chat" in agent.get("kinds", []):
        assert registry.for_chat(agent["key"]).name == "chat"
    # The steps a person reads for it are its own file's.
    assert set(agent.get("summary") or {}) <= {"material", "question", "compute", "write",
                                               "check"}
    assert registry.for_chat("no-such-agent").name == "chat"
    assert registry.for_report("no-such-format").name == "report"


def test_one_report_flow_and_the_old_names_still_open_their_runs():
    report = registry.pipeline("report")
    assert OPTIONAL <= set(report.flow("write"))
    for pipe in registry.pipelines().values():
        if pipe.flow("finish"):
            assert pipe.flow("finish") == registry.pipeline("chat").flow("finish")
        if pipe.flow("write"):
            assert pipe.flow("revise") == registry.pipeline("deck").flow("revise")
    for name in registry.LEGACY:
        old = registry.pipeline(name)
        assert old.flow("write") == report.flow("write")
        assert old.flow("finish") == registry.pipeline("chat").flow("finish")


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
