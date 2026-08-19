"""Rules about the *plan* a document is built from, before anything is written.

Both writing tracks — `deck` (JSON slides) and `page` (an HTML artifact from a
template) — ask a model for a list of headings with a layout on each, then
write one call per entry. What is checkable at that point is cheap to check:
the plan is a few dozen tokens, no content exists yet, and a bad plan costs
every call that follows it.

One rule lives here so far, and it is the one that showed up in the output:
a small model reaches for the first layout it is offered and stays there.
Measured over the decks this instance had already made, `bullets` was 77% of
every body slide and 101 of 128 decks ran three or more identical slides in a
row — with four layouts on offer and a renderer behind each. That is not a
rendering problem and no amount of CSS fixes it; the plan was flat before a
word was written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import groupby

#: Three of anything in a row is where a reader stops seeing the shape and
#: starts seeing a list, so it is the run length worth objecting to.
MAX_RUN = 3

#: How many of the offered layouts a plan should actually touch. Capped by how
#: many exist: a template with two body layouts cannot use three.
MIN_DISTINCT = 3


def count(usage: dict[str, int], spent: dict[str, int], *, planned_apart: bool) -> None:
    """Adds one planning call's tokens to the right half of a turn's usage.

    A planner can be a different model from the writer, and a call billed at
    the wrong model's price is a ledger that says the wrong thing about where
    the money went. When the same model does both — the default everywhere —
    the tokens stay where they always were.
    """
    prefix = "outline" if planned_apart else ""
    usage[f"{prefix}InputTokens" if prefix else "inputTokens"] += spent["inputTokens"]
    usage[f"{prefix}OutputTokens" if prefix else "outputTokens"] += spent["outputTokens"]


def flat_layouts(blocks: Sequence[dict], choices: Sequence[str]) -> list[str]:
    """Layouts this plan never reached for — empty when it is varied enough.

    The unused list rather than a bool, because the only useful thing to do
    with a flat plan is to ask again naming what is missing, and this is that
    list. The first block is the cover in both tracks and is not a choice.
    """
    options = list(choices)
    body = [str(block.get("layout") or "") for block in blocks[1:]]
    if len(options) < 2 or len(body) < MAX_RUN:
        return []
    longest = max(len(list(group)) for _, group in groupby(body))
    counted = Counter(layout for layout in body if layout in options)
    if longest < MAX_RUN and len(counted) >= min(MIN_DISTINCT, len(options)):
        return []
    unused = [layout for layout in options if layout not in counted]
    if unused:
        return unused
    # Everything appears and one of them still dominates — a plan that opens
    # with a table and then runs eight bullets. Name the ones it leans away
    # from, so the ask is "more of these" rather than "be different".
    most = max(counted.values())
    return [layout for layout in options if counted[layout] < most]


__all__ = ["MAX_RUN", "MIN_DISTINCT", "count", "flat_layouts"]
