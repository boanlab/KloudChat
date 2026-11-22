"""Answers served by a LiteLLM fallback: which model answered when the requested one failed over.

The proxy sets `x-litellm-attempted-fallbacks` (> 0) and `x-litellm-model-group`; the body's
`model` is then the fallback's own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FALLBACKS_HEADER = "x-litellm-attempted-fallbacks"
GROUP_HEADER = "x-litellm-model-group"


def attempts(headers: Mapping[str, str] | None) -> int:
    """Fallbacks the proxy tried before this response; 0 when none or unreadable."""
    try:
        return max(0, int(str((headers or {}).get(FALLBACKS_HEADER) or "0").strip()))
    except ValueError:
        return 0


def served_by(headers: Mapping[str, str] | None, requested: str, reported: str | None) -> str:
    """Model that answered a fallback response: the group header when it names another group,
    else the body's model; empty when no fallback happened."""
    if attempts(headers) <= 0:
        return ""
    group = str((headers or {}).get(GROUP_HEADER) or "").strip()
    if group and group != requested:
        return group
    return (reported or "").strip() or group or requested


def record(requested: dict[str, Any], served: str, catalogue: list[dict[str, Any]],
           tries: int = 1) -> dict[str, Any]:
    """`routing.fallback` for a message: asked vs answering model and whether the latter is
    outside the institution (`dataBoundary`; `unknown` when unlisted)."""
    row = next((m for m in catalogue if m.get("id") == served), None)
    boundary = str((row or {}).get("dataBoundary") or "unknown")
    return {
        "requestedModel": str(requested.get("id") or ""),
        "requestedBoundary": str(requested.get("dataBoundary") or "unknown"),
        "servedBy": served,
        "dataBoundary": boundary,
        # self-hosted answered → not external; external or unlisted → external
        "external": boundary != "self_hosted",
        "attempts": max(1, tries),
    }


__all__ = ["FALLBACKS_HEADER", "GROUP_HEADER", "attempts", "record", "served_by"]
