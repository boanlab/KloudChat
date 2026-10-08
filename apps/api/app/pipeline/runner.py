"""Runs a pipeline's stages in order over one bundle, recording every one.

Each stage is recorded as `done`, `skipped` (with the reason the stage gave), `stopped`
(it ended the pipeline on purpose), `failed` (it raised; the exception goes on to the
caller, as it did before the flows were stages) or `cancelled` (the turn was closed under
it). Stages after a stop are recorded as
skipped with the stage that stopped them, so the record always names every stage.

The wire events a stage yields pass through unchanged. The records themselves stay on the
bundle (`bundle.steps`) and in the log; `emit_records` also yields each as a surface
`step` event, for a caller that shows the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Iterable
from contextvars import ContextVar
from typing import Any

from app.core import logs
from app.pipeline.stage import ALWAYS_WRITABLE, Context, Stage, StepRecord, Stop

log = logging.getLogger(__name__)


#: The stage running now, for whatever wants to attribute work to it (the replay harness
#: tags each recorded model call with it).
CURRENT_STAGE: ContextVar[str] = ContextVar("pipeline_stage", default="")


class ContractError(RuntimeError):
    """A stage changed a bundle field it did not declare."""


def _get(bundle: Any, path: str) -> Any:
    value = bundle
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _fingerprint(value: Any) -> int:
    try:
        return hash(repr(value))
    except Exception:  # noqa: BLE001 — an unprintable value is compared by identity
        return id(value)


def watched_fields(bundle: Any) -> list[str]:
    """Every dotted field the bundle exposes for the contract check."""
    fields = getattr(bundle, "FIELDS", None)
    return list(fields) if fields else []


def strict_default() -> bool:
    """`KLOUDCHAT_PIPELINE_STRICT=1`: refuse a stage that writes a field it did not declare
    (the test suite runs with it; production does not pay for the comparison)."""
    return os.environ.get("KLOUDCHAT_PIPELINE_STRICT", "") == "1"


def records_default() -> bool:
    """`KLOUDCHAT_PIPELINE_STEPS=1`: send each stage record to the surface as a `step`
    (category `pipeline`). Off by default, so the surface shows what it always showed."""
    return os.environ.get("KLOUDCHAT_PIPELINE_STEPS", "") == "1"


class Runner:
    def __init__(self, stages: Iterable[Stage], *, name: str = "", strict: bool | None = None,
                 emit_records: bool | None = None) -> None:
        self.stages = list(stages)
        self.name = name
        self.strict = strict_default() if strict is None else strict
        self.emit_records = records_default() if emit_records is None else emit_records

    async def run(self, bundle: Any, ctx: Context | None = None, *,
                  start: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Runs from the first stage, or from `start` (the stages before it recorded as
        skipped: a resumed run reads what they left on the bundle)."""
        ctx = ctx or Context()
        records: list[StepRecord] = bundle.steps
        names = [s.name for s in self.stages]
        if start is not None and start not in names:
            raise KeyError(f"no stage {start!r} in pipeline {self.name!r}")
        resumed = start is not None
        fields = watched_fields(bundle) if self.strict else []
        for index, stage in enumerate(self.stages):
            if resumed and stage.name != start:
                records.append(StepRecord(stage.name, stage.kind, "skipped",
                                          f"resumed from {start}"))
                continue
            resumed = False
            reason = stage.skip(bundle)
            if reason:
                record = StepRecord(stage.name, stage.kind, "skipped", reason)
                records.append(record)
                if self.emit_records:
                    yield record.wire()
                continue
            before = {f: _fingerprint(_get(bundle, f)) for f in fields}
            began = time.monotonic()
            record = StepRecord(stage.name, stage.kind, "done")
            token = CURRENT_STAGE.set(stage.name)
            try:
                async for event in stage.run(bundle, ctx):
                    record.events += 1
                    yield event
            except Stop as stop:
                record.status, record.reason = "stopped", stop.reason
                record.ms = int((time.monotonic() - began) * 1000)
                records.append(record)
                for later in self.stages[index + 1:]:
                    records.append(StepRecord(later.name, later.kind, "skipped",
                                              f"stopped at {stage.name}"))
                self._log(records)
                if self.emit_records:
                    yield record.wire()
                return
            except (GeneratorExit, asyncio.CancelledError):
                # The turn was closed or cancelled under the stage (a stop, a lost socket).
                record.status, record.reason = "cancelled", "closed while running"
                record.ms = int((time.monotonic() - began) * 1000)
                records.append(record)
                self._log(records)
                raise
            except BaseException as exc:
                record.status, record.reason = "failed", f"{type(exc).__name__}: {exc}"[:300]
                record.ms = int((time.monotonic() - began) * 1000)
                records.append(record)
                self._log(records)
                raise
            finally:
                CURRENT_STAGE.reset(token)
            record.ms = int((time.monotonic() - began) * 1000)
            if fields:
                changed = [f for f in fields if _fingerprint(_get(bundle, f)) != before[f]]
                record.wrote = changed
                allowed = (*stage.writes, *ALWAYS_WRITABLE)
                stray = [f for f in changed
                         if not any(f == w or f.startswith(w + ".") for w in allowed)]
                if stray:
                    raise ContractError(f"stage {stage.name!r} wrote undeclared {stray}")
            records.append(record)
            if self.emit_records:
                yield record.wire()
        self._log(records)

    def _log(self, records: list[StepRecord]) -> None:
        shown = ", ".join(f"{r.stage}:{r.status}" + (f"({r.reason[:60]})" if r.reason and
                                                      r.status != "done" else "")
                          for r in records)
        log.info("pipeline %s: %s", self.name or "?", logs.safe(shown, 2000))


__all__ = ["CURRENT_STAGE", "ContractError", "Runner", "records_default", "strict_default",
           "watched_fields"]
