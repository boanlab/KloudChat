"""Complex document turns on a self-hosted model: an optional shared slot cap and a same-turn
fallback.

A slow self-hosted model that chat shares (a single server with few sequences) must not have
all its sequences taken by long document tool loops. With `settings.complex_document_slots` set
(0 by default: the built-in complex row is Qwen3.8 Flash, which needs no cap), at most that many
complex document turns (`super_agent.complexity`) run on it at once, across API replicas: a
slot is a Postgres session-level advisory lock held on a connection of its own, so a replica
that dies lets go of its slots with its connection. A turn that finds none waits, shown as
「대기 중 · n번째」, for at most `settings.complex_document_wait_sec`; then the `document` row's
model writes it instead (`Plan.use_fallback`). Without Postgres (tests, a single-process run)
the cap is per process.

The same `Plan` carries the fallback for the whole-answer calls of the report and deck
pipelines (`run_call`): a refused, dropped or too slow call is asked again on the fallback model,
and the failed model is left out of Super Agent picks for a while (`model_health`). Chat turns
(office files) take the fallback through `agent.run_turn(fallback_model=…)` instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

from app.core.config import settings
from app.services import model_health

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Advisory-lock key of slot 0; slot `i` is `LOCK_BASE + i`.
LOCK_BASE = 7_331_200
#: Seconds between tries for a slot.
POLL_SEC = 2.0


@dataclass(slots=True)
class Plan:
    """One complex document turn's writer and what carries it on."""

    #: the complex row's model, and the `document` row's model behind it
    primary: str
    fallback: str | None
    #: this turn writes on the fallback from the start (no slot in time)
    use_fallback: bool = False
    #: this turn holds a slot (`None`: it needs none or has not got one)
    slot: Slot | None = None
    #: the turn runs on the self-hosted server, so it needs a slot
    queued: bool = False
    #: the turn's surface: `document` (report and deck pipelines) or `chat` (office files)
    surface: str = "document"
    #: the answer's language, for the step lines (`agent._note`)
    korean: bool = True
    #: why the turn writes on the fallback: `queue`, or the error that made it (`busy`, ...)
    reason: str = ""

    def model_for(self, model: str) -> str:
        """The model a call asked for `model` goes to."""
        if self.use_fallback and self.fallback and model == self.primary:
            return self.fallback
        return model


#: the running turn's plan (set before its stream starts, read by the pipelines' calls)
PLAN: ContextVar[Plan | None] = ContextVar("complex_plan", default=None)

#: turns of this process waiting for a slot, oldest first
_waiting: list[object] = []
#: slots held in this process, for the per-process cap and tests
_local_held: set[int] = set()


class Slot:
    """A held slot: released when the turn ends or the process goes."""

    def __init__(self, index: int, conn: Any = None) -> None:
        self.index, self._conn = index, conn

    async def release(self) -> None:
        conn, self._conn = self._conn, None
        _local_held.discard(self.index)
        if conn is not None:
            try:
                await conn.close(timeout=5)
            except Exception:  # noqa: BLE001 — closing the connection frees the lock anyway
                conn.terminate()


def _postgres() -> bool:
    return settings.database_url.startswith("postgresql")


async def _try_once(cap: int) -> Slot | None:
    """A free slot among `cap`, or None. Postgres: advisory locks, shared by every replica."""
    if not _postgres():
        for index in range(cap):
            if index not in _local_held:
                _local_held.add(index)
                return Slot(index)
        return None
    import asyncpg

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
    except Exception as exc:  # noqa: BLE001 — a database blip: the turn goes on without a cap
        log.warning("complex slot connection failed (%s); not capped", type(exc).__name__)
        return Slot(-1)
    try:
        for index in range(cap):
            if await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_BASE + index):
                _local_held.add(index)
                return Slot(index, conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("complex slot lock failed (%s); not capped", type(exc).__name__)
        await conn.close()
        return Slot(-1)
    await conn.close()
    return None


def capped() -> bool:
    return settings.complex_document_slots > 0


def _say(korean: bool, ko: str, en: str) -> str:
    return ko if korean else en


def queue_event(position: int, waited: float, wait: float, korean: bool = True) -> dict[str, Any]:
    """The step line of a turn in line: its place and how long it waits at most."""
    cap = settings.complex_document_slots
    limit = "" if wait == float("inf") else _say(
        korean, f" {int(max(0.0, wait - waited))}초 안에 차례가 오지 않으면 다른 모델이 씁니다.",
        f" If its turn does not come within {int(max(0.0, wait - waited))} s, another model "
        "writes it.")
    return {
        "type": "step", "category": "thinking", "id": "complex-queue",
        "label": f"대기 중 · {position}번째", "queuePosition": position, "status": "running",
        "detail": _say(
            korean, f"복잡한 문서는 자체 서버에서 한 번에 {cap}건만 돌립니다.",
            f"Complex documents run {cap} at a time on the in-house server.") + limit,
    }


async def wait_for_slot(plan: Plan, *, poll: float | None = None) -> AsyncIterator[dict]:
    """Takes a slot for `plan`, yielding the step line while it waits. On return `plan.slot` is
    held, or `plan.use_fallback` says the fallback writes (no slot within the wait)."""
    cap, wait = settings.complex_document_slots, settings.complex_document_wait_sec
    if cap <= 0:
        return
    me = object()
    started = time.monotonic()
    shown = 0
    try:
        while True:
            slot = await _try_once(cap)
            if slot is not None:
                plan.slot = slot
                if shown:
                    yield {"type": "step", "category": "thinking", "id": "complex-queue",
                           "label": "대기 끝", "status": "done"}
                return
            if me not in _waiting:
                _waiting.append(me)
            waited = time.monotonic() - started
            if waited >= wait:
                if plan.fallback:
                    plan.use_fallback, plan.reason = True, "queue"
                    yield {"type": "step", "category": "thinking", "id": "complex-queue",
                           "label": "다른 모델로 쓰기", "status": "done",
                           "detail": _say(plan.korean, f"{plan.fallback} 모델이 씁니다",
                                          f"{plan.fallback} writes it")}
                    return
                wait = float("inf")  # nothing to fall back to: keep waiting
            position = _waiting.index(me) + 1
            if position != shown:
                shown = position
                yield queue_event(position, waited, wait, plan.korean)
            await asyncio.sleep(POLL_SEC if poll is None else poll)
    finally:
        if me in _waiting:
            _waiting.remove(me)


async def release(plan: Plan | None) -> None:
    if plan is not None and plan.slot is not None:
        slot, plan.slot = plan.slot, None
        await slot.release()


def _failure(exc: BaseException) -> bool:
    """A call the model server did not answer: refused, dropped, broken or too slow."""
    return isinstance(exc, (httpx.HTTPError, TimeoutError, asyncio.TimeoutError))


async def run_call(model: str, call: Callable[[str], Awaitable[T]]) -> T:
    """`call(model)` for a whole-answer document call; on the complex row's model, one that
    fails or stays silent is asked again on the plan's fallback, and every later call of the
    turn goes there too."""
    plan = PLAN.get()
    if plan is None or plan.fallback is None or model != plan.primary:
        return await call(model)
    if plan.use_fallback:
        return await call(plan.fallback)
    try:
        done = await asyncio.wait_for(call(model), settings.complex_document_call_sec)
        model_health.mark_ok(model)
        return done
    except Exception as exc:  # noqa: BLE001 — only a server failure hands over; the rest is the call's own
        if not _failure(exc):
            raise
        error = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            # the gateway's reason is in the body (`strict_local_unavailable`)
            error = f"upstream_{exc.response.status_code} {exc.response.text[:200]}"
        skipped = model_health.mark_failed(model, error)
        plan.use_fallback = True
        plan.reason = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else (
            "busy" if model_health.busy_refusal(error) else "error")
        await release(plan)
        log.warning("%s failed (%s: %s); %s carries the document on, %s skipped %ds",
                    model, plan.reason, type(exc).__name__, plan.fallback, model, skipped)
    return await call(plan.fallback)
