"""Background work of 지식 서가: a guide or studio output is written after the request
returns (a strict-local writer can take minutes), on a DB session of its own, and its row
says `running` until it is done. A row left `running` by a replica that stopped counts as
failed once it is older than `STALE`, so the person can ask again."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable
from datetime import timedelta
from typing import Any

from app.models.user import utcnow

log = logging.getLogger(__name__)

STALE = timedelta(minutes=20)
#: tasks in flight, kept so they are not collected mid-run
_PENDING: set[asyncio.Task] = set()


def spawn(work: Awaitable[Any]) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(work)  # type: ignore[arg-type]
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


async def drain() -> None:
    """Wait for the work in flight (tests)."""
    while _PENDING:
        await asyncio.gather(*list(_PENDING), return_exceptions=True)


def stale(row: Any) -> bool:
    """A `running` row nobody is working on any more."""
    at = getattr(row, "updated_at", None)
    if getattr(row, "state", "") != "running" or at is None:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=utcnow().tzinfo)
    return utcnow() - at > STALE


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(answer: str) -> dict[str, Any] | None:
    """The answer's JSON object: a fenced block, else the outermost braces."""
    text = answer or ""
    # a reasoning model may think aloud first
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    if "{" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for block in candidates:
        # a trailing comma before a closing bracket is the commonest slip
        for text in (block, re.sub(r",\s*([}\]])", r"\1", block)):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return None


def clip(value: Any, cap: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def strings(value: Any, most: int, cap: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = clip(item, cap)
        if text and text not in out:
            out.append(text)
        if len(out) >= most:
            break
    return out


__all__ = ["STALE", "clip", "drain", "parse_json", "spawn", "stale", "strings"]
