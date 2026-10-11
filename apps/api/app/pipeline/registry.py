"""Stage registry and pipelines declared in `app/pipelines/*.toml`."""

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
    # stage modules self-register on import
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
    #: named stage lists: `plan`, `write`, `finish`, `prepass` …
    flows: dict[str, tuple[str, ...]] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    #: per-step plain summary for the drawn chain; display only
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


#: retired pipeline names of stored runs; rerun on `report` + `chat` finish
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
    """Report pipeline: `report`, unless a pipeline claims the format."""
    for pipe in pipelines().values():
        if format_id and format_id in pipe.formats:
            return pipe
    return pipeline("report")


def for_chat(agent_key: str | None) -> Pipeline:
    """Chat finish pipeline for the agent; `chat` by default."""
    if agent_key:
        for pipe in pipelines().values():
            if agent_key in pipe.agents and pipe.flow("finish"):
                return pipe
    return pipeline("chat")


__all__ = ["LEGACY", "PIPELINES", "Pipeline", "for_chat", "for_report", "pipeline", "pipelines",
           "register", "stage", "stages"]
