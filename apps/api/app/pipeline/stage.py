"""Stage contract: named step over declared bundle fields; `Stop`/`Wait` end a pipeline."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

#: stage kinds; check = check → fix → check
KINDS = ("compute", "model", "check", "io")
#: always writable
ALWAYS_WRITABLE = ("usage", "steps", "wire")


class Stop(Exception):  # noqa: N818 — a signal, not an error
    """Ends the pipeline; later stages recorded as not run."""

    #: record status for this stop
    status = "stopped"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Wait(Stop):
    """Ends the pipeline to wait on a question card; `evidence` = the card's questions."""

    status = "waiting"

    def __init__(self, reason: str, *, summary: str = "",
                 evidence: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.summary = summary
        self.evidence = dict(evidence or {})


#: `step` label suffix per non-done status
STATUS_WORDS = {"skipped": "해당 없음", "stopped": "멈춤", "failed": "실패", "waiting": "답 기다림",
                "cancelled": "중단"}


@dataclass(slots=True)
class StepRecord:
    """One stage's outcome: bundle, log and run record."""

    stage: str
    kind: str
    #: done · skipped · stopped · failed · waiting · cancelled
    status: str
    reason: str = ""
    wrote: list[str] = field(default_factory=list)
    events: int = 0
    ms: int = 0
    label: str = ""
    summary: str = ""
    #: inputs/changes (`runs.evidence`)
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    attempt: int = 1
    run_id: str = ""
    #: snapshot kept before this stage (rerunnable from here)
    rerunnable: bool = False
    #: tokens spent, by usage key
    tokens: dict[str, int] = field(default_factory=dict)

    @property
    def record_id(self) -> str:
        return f"{self.run_id or 'run'}:{self.stage}:{self.attempt}"

    def stage_wire(self, *, evidence: bool = True) -> dict[str, Any]:
        """Record as the surface reads it."""
        out = {
            "id": self.record_id, "runId": self.run_id, "name": self.stage,
            "label": self.label or self.stage, "kind": self.kind, "status": self.status,
            "reason": self.reason[:300], "summary": self.summary[:300],
            "startedAt": self.started_at, "finishedAt": self.finished_at, "ms": self.ms,
            "attempt": self.attempt, "rerunnable": self.rerunnable,
        }
        if self.tokens:
            out["tokens"] = dict(self.tokens)
        if evidence and self.evidence:
            out["evidence"] = self.evidence
        return out

    def wire(self) -> dict[str, Any]:
        """Record as a surface `step` event; `stage` = whole record."""
        name = self.label or self.stage
        if self.status == "done":
            label = f"{name} · {self.summary}" if self.summary else name
        else:
            label = f"{name} · {STATUS_WORDS.get(self.status, self.status)}"
        detail = self.reason or self.summary
        return {
            "type": "step",
            "category": "pipeline",
            "id": f"stage:{self.stage}",
            "label": label[:200],
            "status": "error" if self.status == "failed" else "done",
            **({"detail": detail[:300]} if detail else {}),
            "stage": self.stage_wire(),
        }

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "kind": self.kind, "status": self.status,
                "reason": self.reason, "wrote": list(self.wrote), "events": self.events,
                "ms": self.ms, "label": self.label, "summary": self.summary,
                "evidence": dict(self.evidence), "startedAt": self.started_at,
                "finishedAt": self.finished_at, "attempt": self.attempt, "runId": self.run_id,
                "rerunnable": self.rerunnable, "tokens": dict(self.tokens)}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> StepRecord:
        return cls(stage=str(row.get("stage") or ""), kind=str(row.get("kind") or ""),
                   status=str(row.get("status") or ""), reason=str(row.get("reason") or ""),
                   wrote=list(row.get("wrote") or []), events=int(row.get("events") or 0),
                   ms=int(row.get("ms") or 0), label=str(row.get("label") or ""),
                   summary=str(row.get("summary") or ""),
                   evidence=dict(row.get("evidence") or {}),
                   started_at=str(row.get("startedAt") or ""),
                   finished_at=str(row.get("finishedAt") or ""),
                   attempt=int(row.get("attempt") or 1), run_id=str(row.get("runId") or ""),
                   rerunnable=bool(row.get("rerunnable")),
                   tokens={str(k): int(v) for k, v in (row.get("tokens") or {}).items()
                           if isinstance(v, int)})


@dataclass(slots=True)
class Context:
    """Model call and clock; `complete` resolved at call time so test patches apply."""

    complete: Callable[..., Awaitable[tuple[str, dict]]] | None = None
    #: pipeline file `[options]`
    options: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    #: owning run (`runs.Recorder`), for record ids
    run_id: str = ""


class Stage(Protocol):
    name: str
    kind: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]

    def skip(self, bundle: Any) -> str: ...

    def run(self, bundle: Any, ctx: Context) -> AsyncIterator[dict[str, Any]]: ...


class Base:
    """Stage defaults: always applies, declares nothing; override `apply` or `run`."""

    name = ""
    kind = "compute"
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()

    def skip(self, bundle: Any) -> str:
        return ""

    async def apply(self, bundle: Any, ctx: Context) -> None:
        return None

    async def run(self, bundle: Any, ctx: Context) -> AsyncIterator[dict[str, Any]]:
        await self.apply(bundle, ctx)
        return
        yield  # an async generator that emits nothing


__all__ = ["ALWAYS_WRITABLE", "KINDS", "STATUS_WORDS", "Base", "Context", "Stage", "StepRecord",
           "Stop", "Wait"]
