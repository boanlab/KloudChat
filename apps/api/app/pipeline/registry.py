"""Stages by name, and the pipelines declared as data in `app/pipelines/*.toml`.

A pipeline file names its stages in order and says when it applies:

    name = "minutes"
    formats = ["minutes"]          # report formats it serves (`doc_formats` ids)
    agents = ["minutes"]           # catalogue agents whose turns it serves
    plan = ["research", "material", "outline"]
    write = ["research", "material", "approved-plan", …]
    prepass = ["date-calc"]        # the pre-pass steps, in order
    [options]                      # read by stages through `ctx.options`
    search = true

`report.toml` and `chat.toml` are the defaults: a report whose format no file names is
written by `report`, a chat turn whose agent no file names is finished by `chat`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from app.pipeline.stage import Stage

PIPELINES = Path(__file__).resolve().parents[1] / "pipelines"

_STAGES: dict[str, Stage] = {}


def register(stage: Stage) -> Stage:
    if not stage.name:
        raise ValueError("a stage needs a name")
    if stage.name in _STAGES and _STAGES[stage.name] is not stage:
        raise ValueError(f"stage {stage.name!r} registered twice")
    _STAGES[stage.name] = stage
    return stage


def stage(name: str) -> Stage:
    _load_stages()
    try:
        return _STAGES[name]
    except KeyError:
        raise KeyError(f"no stage named {name!r}") from None


def stages() -> dict[str, Stage]:
    _load_stages()
    return dict(_STAGES)


def _load_stages() -> None:
    # The stage modules register themselves on import.
    from app.pipeline import (  # noqa: F401
        chat_stages,
        check_stages,
        export_stages,
        make_stages,
        report_stages,
        revise_stages,
        route_stages,
        save_stages,
    )


@dataclass(frozen=True, slots=True)
class Pipeline:
    name: str
    formats: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    #: Named stage lists: `plan`, `write`, `finish`, `prepass` …
    flows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def flow(self, name: str) -> tuple[str, ...]:
        return self.flows.get(name, ())

    def stages(self, flow: str) -> list[Stage]:
        return [stage(n) for n in self.flow(flow)]


_FLOWS = ("pre", "plan", "write", "finish", "prepass", "checks", "judged", "revise", "make")


def _parse(path: Path) -> Pipeline:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return Pipeline(
        name=str(raw.get("name") or path.stem),
        formats=tuple(raw.get("formats") or ()),
        agents=tuple(raw.get("agents") or ()),
        flows={k: tuple(raw[k]) for k in _FLOWS if k in raw},
        options=dict(raw.get("options") or {}),
    )


@cache
def pipelines() -> dict[str, Pipeline]:
    out = {}
    for path in sorted(PIPELINES.glob("*.toml")):
        pipe = _parse(path)
        out[pipe.name] = pipe
    return out


def pipeline(name: str) -> Pipeline:
    return pipelines()[name]


def for_report(format_id: str | None) -> Pipeline:
    """The pipeline that writes a report of this format; `report` when none names it."""
    if format_id:
        for pipe in pipelines().values():
            if format_id in pipe.formats:
                return pipe
    return pipeline("report")


def for_chat(agent_key: str | None) -> Pipeline:
    """The pipeline that finishes a chat answer of this agent; `chat` when none names it."""
    if agent_key:
        for pipe in pipelines().values():
            if agent_key in pipe.agents and pipe.flow("finish"):
                return pipe
    return pipeline("chat")


def prepass_for(format_id: str | None) -> tuple[str, ...]:
    """The pre-pass steps a format runs before writing, in order."""
    return for_report(format_id).flow("prepass") if format_id else ()


def checks_for(format_id: str | None) -> tuple[str, ...]:
    """The format code checks (`report_checks.CHECK_FAMILIES`) a format runs, in order."""
    return for_report(format_id).flow("checks")


def judged_for(format_id: str | None) -> tuple[str, ...]:
    """The judged checks (`report_checks.JUDGED_CHECKS`) a format runs."""
    return for_report(format_id).flow("judged")


__all__ = ["PIPELINES", "Pipeline", "checks_for", "for_chat", "for_report", "judged_for",
           "pipeline", "pipelines", "prepass_for", "register", "stage", "stages"]
