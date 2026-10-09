"""The stage contract: one named step of a document or chat flow, reading and writing parts
of the work bundle it declares.

A stage is an object with

- `name` — unique in the registry; what a pipeline file lists;
- `kind` — `compute` (code only), `model` (asks a model), `check` (finds, fixes, finds
  again) or `io` (reads or hands out files); for the record and for composing later;
- `reads` / `writes` — dotted bundle fields (`computed.settled`, `document.sections`). The
  runner compares the bundle before and after and refuses, in strict mode, a stage that
  changed a field it did not declare; `usage` and `steps` are always writable;
- `skip(bundle) -> str` — why it does not apply to this bundle ("" when it does);
- `run(bundle, ctx)` — an async generator: it writes its fields on the bundle and yields
  the wire events (`step`, `section`, …) the surface shows, in the order it always has.

A stage ends the pipeline on purpose by raising `Stop` (a planning pass that offered its
outline) or `Wait` (a question card the run waits on); any other exception is recorded as a
failed stage and raised to the caller, exactly as the code before the move let it propagate.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Stage kinds; a check stage runs a check skill's check → fix → check.
KINDS = ("compute", "model", "check", "io")
#: Fields every stage may write.
ALWAYS_WRITABLE = ("usage", "steps", "wire")


class Stop(Exception):  # noqa: N818 — a signal, not an error
    """Ends the pipeline after this stage; the stages after it are recorded as not run."""

    #: The record's status for a stop of this kind.
    status = "stopped"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Wait(Stop):
    """Ends the pipeline to wait for the person (a question card); the run resumes when the
    answers come. `evidence` goes on the record (the card's questions)."""

    status = "waiting"

    def __init__(self, reason: str, *, summary: str = "",
                 evidence: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.summary = summary
        self.evidence = dict(evidence or {})


#: Words a record's legacy label adds for a status other than done.
STATUS_WORDS = {"skipped": "건너뜀", "stopped": "멈춤", "failed": "실패", "waiting": "답 기다림",
                "cancelled": "중단"}


@dataclass(slots=True)
class StepRecord:
    """What one stage did, kept on the bundle, in the log and in the run record."""

    stage: str
    kind: str
    #: done · skipped · stopped · failed · waiting · cancelled
    status: str
    reason: str = ""
    #: Declared fields that changed.
    wrote: list[str] = field(default_factory=list)
    events: int = 0
    ms: int = 0
    #: The stage's Korean name, and a Korean line of what it did.
    label: str = ""
    summary: str = ""
    #: What the stage worked from and changed (`runs.evidence`): computed values, findings
    #: before and after, text old → new, code and its output, a card's questions.
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    attempt: int = 1
    run_id: str = ""
    #: Whether the run kept a snapshot before this stage (it can be run again from here).
    rerunnable: bool = False
    #: Tokens the stage spent, by usage key (`inputTokens`, `outlineOutputTokens`, …).
    tokens: dict[str, int] = field(default_factory=dict)

    @property
    def record_id(self) -> str:
        return f"{self.run_id or 'run'}:{self.stage}:{self.attempt}"

    def stage_wire(self, *, evidence: bool = True) -> dict[str, Any]:
        """The record as the surface reads it (`stage` on a `step` event, `routing.run`)."""
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
        """The record as a surface `step` event (status `done` or `error`), in the shape the
        surface has always read; `stage` carries the whole record."""
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
    """What a stage needs that is not work: the model call and the clock.

    `complete` is looked up at call time by default, so a test that replaces the writer's
    model call (`report._complete`) reaches every stage."""

    complete: Callable[..., Awaitable[tuple[str, dict]]] | None = None
    #: Free-form options the pipeline file sets (`[options]`).
    options: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    #: The run this pass belongs to (`runs.Recorder`), for the records' ids.
    run_id: str = ""


class Stage(Protocol):
    name: str
    kind: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]

    def skip(self, bundle: Any) -> str: ...

    def run(self, bundle: Any, ctx: Context) -> AsyncIterator[dict[str, Any]]: ...


class Base:
    """Defaults for a stage class: applies always, declares nothing. A stage that shows
    nothing while it works overrides `apply`; one that emits events overrides `run`."""

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
