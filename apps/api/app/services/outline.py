"""Checks on a document plan (headings with a layout each) before anything is written.

Used by the `deck` and `page` tracks. Small models tend to reach for one
layout and stay there; `flat_layouts` detects that.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import groupby

#: Longest run of one layout a plan may contain.
MAX_RUN = 3

#: Distinct body layouts a plan should use, capped by how many exist.
MIN_DISTINCT = 3


def count(usage: dict[str, int], spent: dict[str, int], *, planned_apart: bool) -> None:
    """Adds a planning call's tokens to the outline or writer half of a turn's usage."""
    prefix = "outline" if planned_apart else ""
    usage[f"{prefix}InputTokens" if prefix else "inputTokens"] += spent["inputTokens"]
    usage[f"{prefix}OutputTokens" if prefix else "outputTokens"] += spent["outputTokens"]


def flat_layouts(blocks: Sequence[dict], choices: Sequence[str]) -> list[str]:
    """Layouts to ask for more of, or `[]` when the plan is varied enough.

    The first block is the cover and is not a choice.
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
    # Every layout appears but one dominates: name the under-used ones.
    most = max(counted.values())
    return [layout for layout in options if counted[layout] < most]


__all__ = ["MAX_RUN", "MIN_DISTINCT", "count", "flat_layouts"]
