"""The routing of a turn: which pipeline a message goes to.

A message on a document surface either asks for a new document (the format's `plan` /
`write`), works on the one on screen (`revise`), or only asks for it as a file (`export`);
a page 서식 goes to the page writer and a chat message to the agent loop. `route` reads the
facts the router settled (`Request.turn["route"]`) and names the target; the router
dispatches on it. A revision the planner reads as a change of skeleton is planned again as
a new document (`restructure`), which the router adds to the record once it knows.
"""

from __future__ import annotations

from typing import Any

from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context

#: Targets, and what the record says for each.
TARGETS = {
    "page": "페이지 쓰기",
    "handover": "파일로 건네기",
    "revise": "문서 고치기",
    "report": "보고서 쓰기",
    "deck": "슬라이드 쓰기",
    "chat": "대화 답",
}


def decide(facts: dict[str, Any]) -> str:
    """The target of a turn from what the router settled about it."""
    kind = str(facts.get("kind") or "")
    documents = kind in ("report", "slides")
    if facts.get("page_template"):
        return "page"
    if facts.get("revising") and documents and facts.get("files_only"):
        return "handover"
    if facts.get("revising") and documents:
        return "revise"
    if kind == "report":
        return "report"
    if kind == "slides":
        return "deck"
    return "chat"


class Route(Base):
    """Names the pipeline the turn goes to (`plan.route`)."""

    name = "route"
    kind = "compute"
    reads = ("request",)
    writes = ("plan.route",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        bundle.plan.route = decide(bundle.request.turn.get("route") or {})


registry.register(Route())

__all__ = ["TARGETS", "decide"]
