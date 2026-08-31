"""Shared helpers for the document-surface tests.

In a conftest so both the outline, design-system and template suites reach the
same one: three copies of a two-pass driver would be three places to forget
when the second pass changes.
"""

from __future__ import annotations

from typing import Any

import pytest


async def both_passes(service, **kwargs) -> list[dict[str, Any]]:
    """Drives a document through planning and then through writing.

    A document surface no longer produces a document from one request. It plans,
    offers the outline, and waits — so a test that wants the finished thing has
    to do what a person does: read what was proposed and say yes.

    Returns the events from both passes in order, which is what these tests were
    reading back when one call did both halves. A run that asked a question
    instead of planning returns the first pass alone, because there is nothing
    to approve.
    """
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
    """No search backend unless a test asks for one.

    `research.run` reads the address out of `settings_store.tools_config`, which
    falls back to the environment — so on a machine where the stack is
    configured (which is every machine anyone develops on) twenty-two tests
    about outlines and templates started reaching the real SearXNG. They did
    not fail on an assertion; they failed because the `httpx` double each one
    installs implements `post` and research calls `get`.

    A test asserting something about a document's outline must not depend on
    whether the machine running it can reach the internet. Research is off by
    default here and the suites that are about research turn it back on by
    patching `research.run` themselves, which is the honest arrangement: a test
    that wants a search says so.
    """
    import dataclasses

    from app.services import settings_store

    real = settings_store.tools_config

    async def without_search():
        return dataclasses.replace(await real(), search="")

    monkeypatch.setattr(settings_store, "tools_config", without_search)
