"""Truncated tool-call arguments (output limit, early stop) read back as far as they are whole.

`repair` keeps everything up to the last complete value at a shallow depth (a whole slide, not
half) and closes open brackets; a tail cut inside a string is closed too. Nothing is invented."""

from __future__ import annotations

import json
from typing import Any

#: the deepest container stack a cut may fall back to: {"slides": [ ... ]} is two deep, so a
#: whole slide is the finest piece kept
_KEEP_DEPTH = 2


def repair(raw: str) -> tuple[dict[str, Any], bool] | None:
    """`(arguments, cut)` from JSON text that may be cut off, or None when no object can be
    read from it. `cut` is True when text had to be dropped."""
    text = (raw or "").strip()
    if not text.startswith("{"):
        return None
    try:
        whole = json.loads(text)
    except ValueError:
        pass
    else:
        return (whole, False) if isinstance(whole, dict) else None
    stack: list[str] = []
    in_string = escaped = False
    #: (end offset, stack at that point) where a value just ended inside a container
    safe: list[tuple[int, str]] = []
    for at, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                safe.append((at + 1, "".join(stack)))
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                break
            stack.pop()
            safe.append((at + 1, "".join(stack)))
        elif ch.isdigit() or ch in "-.eE" or ch in "truefalsn":
            safe.append((at + 1, "".join(stack)))
    # the latest cut point shallow enough, whose closed text reads as JSON
    for end, closers in reversed(safe):
        if len(closers) > _KEEP_DEPTH:
            continue
        head = text[:end].rstrip().rstrip(",")
        closing = "".join(reversed(closers))
        try:
            value = json.loads(head + closing)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value, True
    return None
