"""Shared helpers for the document-surface tests."""

from __future__ import annotations

import os

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
