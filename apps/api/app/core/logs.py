"""Putting somebody else's text into a log line.

A log is read later, often by a machine and often after something has gone
wrong. A value that carries a newline can end the entry it is in and start
another that looks exactly like the ones around it — an attacker's sentence
wearing the server's own timestamp and level. Nothing downstream can tell the
forged line from a real one, because by the time it is written there is no
difference.

So a value that came from outside is trimmed of the characters that structure
a log before it goes in, and capped: an error message is a field in a line,
not a document.
"""

from __future__ import annotations

#: Long enough to identify what failed, short enough that one value cannot push
#: the rest of the entry out of a viewer.
_MAX = 200

#: Everything below space, plus DEL. Newlines and carriage returns are the ones
#: that forge entries; the rest render as nothing and hide what follows them.
_STRIP = {chr(c) for c in range(0x20)} | {chr(0x7F)}


def safe(value: object, limit: int = _MAX) -> str:
    """One outside value, fit to sit in a log line.

    Control characters become a space rather than vanishing, so `a\\nb` reads
    as two words and not as the single word `ab` — what was there should still
    be legible to whoever is reading the line.
    """
    text = str(value)
    cleaned = "".join(" " if ch in _STRIP else ch for ch in text)
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")
