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

import subprocess
import sys
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


def _growth(script: str, n: int, *, hang_sec: float = 300.0) -> float:
    """How a scan's CPU time grows when its input grows fourfold: `script` defines `run(n)`;
    a child process times `run(n)` and `run(4 * n)` (best of two each, CPU time) and this
    returns the ratio, about 4 for a linear scan and about 16 for a quadratic one. Wall time
    on a busy runner (and even CPU time across runners) is not the scan's cost, so the bound
    is on the growth; the child's `hang_sec` timeout still stops a scan that never ends."""
    wrapped = (
        f"{script}\nimport time as _clock\n"
        "def _cost(size):\n"
        "    best = None\n"
        "    for _ in range(2):\n"
        "        started = _clock.process_time()\n"
        "        run(size)\n"
        "        spent = _clock.process_time() - started\n"
        "        best = spent if best is None else min(best, spent)\n"
        "    return max(best, 1e-4)\n"
        # the least of three ratios: a busy runner only ever adds time, so a spike inflates one
        # ratio, never all three; a quadratic scan's least ratio is still about 16
        f"print(min(_cost({4 * n}) / _cost({n}) for _ in range(3)))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", wrapped], check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, timeout=hang_sec,
    )
    return float(done.stdout.strip().splitlines()[-1])


#: below quadratic growth (16) with room for noise above linear (4)
LINEAR_GROWTH = 8.0


@pytest.fixture
def linear_scan():
    """`linear_scan(script, n)`: fails unless the script's `run` grows linearly from n to 4n
    (`_growth` under `LINEAR_GROWTH`)."""

    def check(script: str, n: int) -> None:
        ratio = _growth(script, n)
        assert ratio < LINEAR_GROWTH, f"CPU time grew {ratio:.1f}x for 4x the input"

    return check
