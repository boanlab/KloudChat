"""Stages by name, and the pipelines declared as data in `app/pipelines/*.toml`.

A pipeline file names its stages in order and says when it applies:

    name = "report"
    agents = ["document"]          # catalogue agents whose turns it serves
    plan = ["research", "material", "outline"]
    write = ["research", "material", "approved-plan", …]
    [summary]                      # one plain line per step of the drawn chain, for people
    question = "빠진 정보가 있으면 먼저 물어봅니다"

Every report is written by `report` and every chat answer finished by `chat`: what a document
does besides writing (its pre-pass, its 서식 보정 stages, its checks and options) is the 서식's
and the agent's 진행 과정 설정 (`services/process_settings.py`), not a file of its own. The
names of the per-format files that were once here still open the runs stored under them
(`LEGACY`).
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
    #: What each step of the drawn chain (material, question, compute, write, check, export)
    #: does, said for the person. Display only: no stage reads it.
    summary: dict[str, str] = field(default_factory=dict)

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
        summary={str(k): str(v) for k, v in (raw.get("summary") or {}).items()},
    )


@cache
def pipelines() -> dict[str, Pipeline]:
    out = {}
    for path in sorted(PIPELINES.glob("*.toml")):
        pipe = _parse(path)
        out[pipe.name] = pipe
    return out


#: Retired pipeline names a stored run may carry; such a run reruns on `report` (plus
#: `chat`'s finish), whose 진행 과정 설정 hold their stages and options.
LEGACY = ("gaejo-report", "minutes", "comparison-report", "dated", "career-docs",
          "client-proposal", "data-analysis")


@cache
def _legacy(name: str) -> Pipeline:
    report, chat = pipelines()["report"], pipelines()["chat"]
    return Pipeline(name=name, flows={**report.flows, "finish": chat.flow("finish")},
                    summary=dict(report.summary))


def pipeline(name: str) -> Pipeline:
    found = pipelines().get(name)
    if found is None and name in LEGACY:
        return _legacy(name)
    if found is None:
        raise KeyError(name)
    return found


def for_report(format_id: str | None = None) -> Pipeline:
    """The pipeline that writes a report: `report`, whatever the format."""
    for pipe in pipelines().values():
        if format_id and format_id in pipe.formats:
            return pipe
    return pipeline("report")


def for_chat(agent_key: str | None) -> Pipeline:
    """The pipeline that finishes a chat answer of this agent; `chat` when none names it."""
    if agent_key:
        for pipe in pipelines().values():
            if agent_key in pipe.agents and pipe.flow("finish"):
                return pipe
    return pipeline("chat")


__all__ = ["LEGACY", "PIPELINES", "Pipeline", "for_chat", "for_report", "pipeline", "pipelines",
           "register", "stage", "stages"]
