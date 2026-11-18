"""A short digest of long source material, for a call that plans and does not write.

The outline or storyline of a deck needs to know what the sources are about and what they hold
(their titles, the tables and figures they carry, the figures they report), not every
sentence. A digest keeps each source's opening, its captions and table heads, and the
sentences that carry figures, up to a budget, so the planning call (the upper model in
staged routing) reads a fraction of the text. Code only: no model call, no phrase list.
"""

from __future__ import annotations

import re

#: a source at or under this many characters goes in whole
WHOLE_UNDER = 3_000
#: characters kept of each source's opening (its title, authors, abstract's start)
OPENING_CHARS = 700
#: figure-carrying sentences kept per source
SENTENCES_PER_SOURCE = 10
#: characters kept of a table (its head and first rows)
TABLE_LINES = 4
#: the whole digest
TOTAL_CHARS = 7_000

_CAPTION = re.compile(r"^\s*(?:표|그림|Table|Figure|Fig\.)\s?\d+[.:)]?\s", re.I)
_FIGURE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:%|×|x\b|배|억|만|원|명|건|ms|s\b|GB|TB|[A-Za-z]{2,5}\b)?")
_SENTENCE = re.compile(r"(?<=[.!?。])\s+|\n+")


def _table_blocks(text: str) -> list[list[str]]:
    """Runs of three or more lines with the same count of commas or pipes (a table)."""
    blocks: list[list[str]] = []
    run: list[str] = []
    mark = -1
    for line in [*text.splitlines(), ""]:
        count = max(line.count("|"), line.count(",") if line.count(",") >= 2 else 0)
        if count >= 2 and (count == mark or not run):
            run.append(line.strip())
            mark = count
            continue
        if len(run) >= 3:
            blocks.append(run)
        run = [line.strip()] if count >= 2 else []
        mark = count if count >= 2 else -1
    return blocks


def digest_one(text: str) -> str:
    """One source's digest: opening, captions, table heads, figure sentences."""
    text = (text or "").strip()
    if len(text) <= WHOLE_UNDER:
        return text
    parts = [text[:OPENING_CHARS].rstrip()]
    for block in _table_blocks(text):
        parts.append("\n".join(block[:TABLE_LINES]))
    for line in text.splitlines():
        if _CAPTION.match(line):
            parts.append(line.strip()[:200])
    seen = 0
    for sentence in _SENTENCE.split(text[OPENING_CHARS:]):
        sentence = sentence.strip()
        if 25 <= len(sentence) <= 260 and re.search(r"\d", sentence) and _FIGURE.search(sentence):
            parts.append(sentence)
            seen += 1
            if seen >= SENTENCES_PER_SOURCE:
                break
    return "\n".join(dict.fromkeys(p for p in parts if p))


def digest(sources: list[str], *, total: int = TOTAL_CHARS) -> list[str]:
    """`sources` as digests within `total` characters; every source keeps a share."""
    live = [s for s in sources if (s or "").strip()]
    if sum(len(s) for s in live) <= total:
        return live
    out = [digest_one(s) for s in live]
    over = sum(len(s) for s in out) - total
    if over > 0 and out:
        share = max(400, total // len(out))
        out = [s if len(s) <= share else s[:share].rstrip() for s in out]
    return out
