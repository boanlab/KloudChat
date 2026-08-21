"""Shared helpers for the document-surface tests.

In a conftest so both the outline, design-system and template suites reach the
same one: three copies of a two-pass driver would be three places to forget
when the second pass changes.
"""

from __future__ import annotations

from typing import Any


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
