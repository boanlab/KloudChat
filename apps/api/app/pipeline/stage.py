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
outline, a question card); any other exception is recorded as a failed stage and raised to
the caller, exactly as the code before the move let it propagate.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

#: Stage kinds; a check stage runs a check skill's check → fix → check.
KINDS = ("compute", "model", "check", "io")
#: Fields every stage may write.
ALWAYS_WRITABLE = ("usage", "steps")


class Stop(Exception):  # noqa: N818 — a signal, not an error
    """Ends the pipeline after this stage; the stages after it are recorded as not run."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class StepRecord:
    """What one stage did, kept on the bundle and in the log."""

    stage: str
    kind: str
    #: done · skipped · stopped · failed · cancelled
    status: str
    reason: str = ""
    #: Declared fields that changed.
    wrote: list[str] = field(default_factory=list)
    events: int = 0
    ms: int = 0

    def wire(self) -> dict[str, Any]:
        """The record as a surface `step` event (status `done` or `error`), for a caller
        that shows the pipeline; the surface reads `category` to group it."""
        label = self.stage if self.status == "done" else f"{self.stage}: {self.status}"
        return {
            "type": "step",
            "category": "pipeline",
            "id": f"stage:{self.stage}",
            "label": label,
            "status": "error" if self.status == "failed" else "done",
            **({"detail": self.reason[:300]} if self.reason else {}),
        }

    def as_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "kind": self.kind, "status": self.status,
                "reason": self.reason, "wrote": list(self.wrote), "events": self.events,
                "ms": self.ms}


@dataclass(slots=True)
class Context:
    """What a stage needs that is not work: the model call and the clock.

    `complete` is looked up at call time by default, so a test that replaces the writer's
    model call (`report._complete`) reaches every stage."""

    complete: Callable[..., Awaitable[tuple[str, dict]]] | None = None
    #: Free-form options the pipeline file sets (`[options]`).
    options: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)


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


__all__ = ["ALWAYS_WRITABLE", "KINDS", "Base", "Context", "Stage", "StepRecord", "Stop"]
