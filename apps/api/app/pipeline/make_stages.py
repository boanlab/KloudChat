"""The coarse stages of the writers that are still one service call each: a deck and an HTML
page.

`deck.py` and `page.py` plan and write a document in one generator; each is a `model` stage
here (`deck-write`, `page-write`), followed by what the router did after it — a deck's
carried slides (`deck-carry`) and the store (`deck-save`, `page-save`).
Their `make` flow (`app/pipelines/deck.toml`, `page.toml`) is recorded
and rerun like any other; the work itself is the router's (`sessions._deck_write` …),
called through the module so a test that replaces a helper there reaches the stage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context


def _host():
    # Late: the router imports the pipeline.
    from app.routers import sessions

    return sessions


class Hosted(Base):
    """A stage whose work is a router function over the bundle (an async generator of wire
    events)."""

    def __init__(self, name: str, kind: str, work: str, *, reads: tuple[str, ...],
                 writes: tuple[str, ...], needs: str = "") -> None:
        self.name, self.kind, self.work = name, kind, work
        self.reads, self.writes = reads, writes
        #: A `Request.turn` key the stage needs; without it the stage does not apply.
        self.needs = needs

    def skip(self, bundle: Bundle) -> str:
        if self.needs and not bundle.request.turn.get(self.needs):
            return f"no {self.needs} on this turn"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        async for event in getattr(_host(), self.work)(bundle):
            yield event


_WRITE = ("document.sections", "document.data", "plan.proposal", "findings.questions")

for _stage in (
    Hosted("deck-write", "model", "_deck_write", reads=("request",),
           writes=(*_WRITE, "computed.values"), needs="writer"),
    Hosted("deck-carry", "compute", "_deck_carry", reads=("request", "document.sections"),
           writes=("document.sections",), needs="carry_from"),
    Hosted("deck-save", "io", "_deck_save", reads=("request", "document", "usage", "wire"),
           writes=("document.saved",), needs="user_id"),
    Hosted("page-write", "model", "_page_write", reads=("request",), writes=_WRITE,
           needs="writer"),
    Hosted("page-save", "io", "_page_save", reads=("request", "document", "usage", "wire"),
           writes=("document.saved",), needs="user_id"),
):
    registry.register(_stage)
