"""Shared helpers for the document-surface tests."""

from __future__ import annotations

import os
import tempfile

# Flows under test use fake catalogues without strict-local twins; the rule that keeps
# internal material strict-local is exercised by its own tests with the switch set.
os.environ.setdefault("INTERNAL_DATA_STRICT_LOCAL_DEFAULT", "false")
# Tests pin routing themselves; a bare policy routes nothing and plans with the writer.
os.environ.setdefault("ADAPTIVE_ROUTING_DEFAULT", "false")
os.environ.setdefault("ADAPTIVE_CLASSIFIER_MODEL_DEFAULT", "")
os.environ.setdefault("ADAPTIVE_ECONOMY_MODELS_DEFAULT", "")
os.environ.setdefault("ADAPTIVE_QUALITY_DEFAULT", "false")
os.environ.setdefault("ADAPTIVE_QUALITY_MODELS_DEFAULT", "")
os.environ.setdefault("OUTLINE_MODEL_DEFAULT", "")
# Every pipeline stage is held to the bundle fields it declares (`app.pipeline.runner`).
os.environ.setdefault("KLOUDCHAT_PIPELINE_STRICT", "1")
# Stage records off: the recorded streams carry none; tests that read them turn them on.
os.environ.setdefault("KLOUDCHAT_PIPELINE_STEPS", "0")
# Uploads go to a scratch directory, never the image's /srv/data (a CI runner is no root);
# tests that look at files set their own `tmp_path`.
os.environ.setdefault("FILE_STORAGE_DIR", tempfile.mkdtemp(prefix="kchat-files-"))

import select
import signal
import subprocess
import sys
import warnings
from collections.abc import Callable
from typing import Any

import pytest


async def both_passes(service, **kwargs) -> list[dict[str, Any]]:
    """Drives a document through planning and then writing; returns both passes' events in order."""
    events: list[dict[str, Any]] = []
    plan: dict[str, Any] | None = None
    async for event in service.write(**kwargs):
        events.append(event)
        if event["type"] == "proposal":
            plan = event["plan"]
    if plan is None:
        return events
    async for event in service.write(**kwargs, approved_plan=plan):
        events.append(event)
    return events


@pytest.fixture(autouse=True)
def _no_ambient_search(monkeypatch):
    """Search is off unless a test patches `research.run` itself."""
    import dataclasses

    from app.services import settings_store

    real = settings_store.tools_config

    async def without_search():
        return dataclasses.replace(await real(), search="")

    monkeypatch.setattr(settings_store, "tools_config", without_search)


#: the least of three ratios: a busy runner only ever adds time, so a spike inflates one
#: ratio, never all three; a quadratic scan's least ratio is still about 16
_MEASURE = """
import time as _clock
def _cost(run, size):
    best = None
    for _ in range(2):
        started = _clock.process_time()
        run(size)
        spent = _clock.process_time() - started
        best = spent if best is None else min(best, spent)
    return max(best, 1e-4)
def _least_ratio(run, n):
    return min(_cost(run, 4 * n) / _cost(run, n) for _ in range(3))
"""
_measure: dict[str, Any] = {}
exec(_MEASURE, _measure)


def _growth(script: str, n: int, *, hang_sec: float = 300.0) -> float:
    """How a scan's CPU time grows when its input grows fourfold: `script` defines `run(n)`;
    a child process times `run(n)` and `run(4 * n)` (best of two each, CPU time) and this
    returns the ratio, about 4 for a linear scan and about 16 for a quadratic one. Wall time
    on a busy runner (and even CPU time across runners) is not the scan's cost, so the bound
    is on the growth; the child's `hang_sec` timeout still stops a scan that never ends."""
    wrapped = f"{script}\n{_MEASURE}\nprint(_least_ratio(run, {n}))\n"
    done = subprocess.run(
        [sys.executable, "-c", wrapped], check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, timeout=hang_sec,
    )
    return float(done.stdout.strip().splitlines()[-1])


def _growth_of(run: Callable[[int], object], n: int, *, hang_sec: float = 120.0) -> float:
    """`_growth` for a function of this process: a forked copy measures it (CPU time is then
    the scan's alone, and an import costs nothing), and a copy that has not answered within
    `hang_sec` is killed, so a scan that never ends fails instead of stalling the suite.
    Without `fork` (Windows) it is measured here, unguarded."""
    if not hasattr(os, "fork"):
        return _measure["_least_ratio"](run, n)
    read_end, write_end = os.pipe()
    with warnings.catch_warnings():
        # pytest may hold idle threads; the child only runs `run` and leaves with `_exit`
        warnings.simplefilter("ignore", DeprecationWarning)
        pid = os.fork()
    if pid == 0:  # the copy
        os.close(read_end)
        try:
            answer = repr(_measure["_least_ratio"](run, n))
        except BaseException as error:  # noqa: BLE001 - the parent reports it
            answer = f"error: {type(error).__name__}: {error}"
        os.write(write_end, answer.encode())
        os._exit(0)
    os.close(write_end)
    try:
        ready, _, _ = select.select([read_end], [], [], hang_sec)
        if not ready:
            os.kill(pid, signal.SIGKILL)
            raise AssertionError(f"the scan did not finish in {hang_sec:.0f}s")
        chunks = []
        while chunk := os.read(read_end, 4096):
            chunks.append(chunk)
    finally:
        os.close(read_end)
        os.waitpid(pid, 0)
    answer = b"".join(chunks).decode()
    if answer.startswith("error: ") or not answer:
        raise AssertionError(f"the scan failed: {answer[7:] or 'no answer'}")
    return float(answer)


#: below quadratic growth (16) with room for noise above linear (4)
LINEAR_GROWTH = 8.0


@pytest.fixture
def linear_scan():
    """`linear_scan(run, n)`: fails unless `run` grows linearly from n to 4n (`_growth` under
    `LINEAR_GROWTH`). `run` is a function of the input size, or a script that defines one
    (`def run(n): ...`) for a child process of its own. A bound on CPU-time growth, not on
    seconds: a slow or busy runner stretches both runs alike."""

    def check(run: str | Callable[[int], object], n: int) -> None:
        ratio = _growth(run, n) if isinstance(run, str) else _growth_of(run, n)
        assert ratio < LINEAR_GROWTH, f"CPU time grew {ratio:.1f}x for 4x the input"

    return check
