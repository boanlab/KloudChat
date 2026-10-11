"""Turn routing: target pipeline for a message."""

from __future__ import annotations

from typing import Any

from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context

#: target → record label
TARGETS = {
    "page": "페이지 쓰기",
    "handover": "파일로 건네기",
    "revise": "문서 고치기",
    "report": "보고서 쓰기",
    "deck": "슬라이드 쓰기",
    "chat": "대화로 답하기",
}


def decide(facts: dict[str, Any]) -> str:
    """Turn target from the router's settled facts."""
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
    """Names the turn's pipeline (`plan.route`)."""

    name = "route"
    kind = "compute"
    reads = ("request",)
    writes = ("plan.route",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        bundle.plan.route = decide(bundle.request.turn.get("route") or {})


registry.register(Route())

__all__ = ["TARGETS", "decide"]
