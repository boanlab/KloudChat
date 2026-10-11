"""Sanitising outside text for log lines."""

from __future__ import annotations

_MAX = 200

#: C0 controls + DEL; newlines forge entries.
_STRIP = {chr(c) for c in range(0x20)} | {chr(0x7F)}


def safe(value: object, limit: int = _MAX) -> str:
    """Control chars → space, capped at `limit`."""
    # explicit newline `.replace` recognised by static analysis as sanitiser
    text = str(value).replace("\r", " ").replace("\n", " ")
    cleaned = "".join(" " if ch in _STRIP else ch for ch in text)
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")
