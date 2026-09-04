"""Sanitising outside text for log lines."""

from __future__ import annotations

#: Cap on one value in a log line.
_MAX = 200

#: C0 controls plus DEL. Newlines forge entries; the rest hide what follows them.
_STRIP = {chr(c) for c in range(0x20)} | {chr(0x7F)}


def safe(value: object, limit: int = _MAX) -> str:
    """Control characters → space (so `a\\nb` stays two words), then capped at `limit`."""
    # Explicit `.replace` of newlines is what static analysis recognises as the sanitiser.
    text = str(value).replace("\r", " ").replace("\n", " ")
    cleaned = "".join(" " if ch in _STRIP else ch for ch in text)
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")
