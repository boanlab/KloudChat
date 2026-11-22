"""A short digest of long source material, for a call that plans and does not write.

Keeps each source's opening, captions, table heads and figure-bearing sentences up to a budget, so
the planning call (the upper model in staged routing) reads a fraction of the text. Code only: no
model call, no phrase list.
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


def _stems(focus: str) -> list[str]:
    return [w for w in re.findall(r"[가-힣A-Za-z]{2,}", focus or "")]


def digest_one(text: str, focus: str = "") -> str:
    """One source's digest: opening, captions, table heads, figure sentences. With `focus` (the
    words of the slide or section the digest serves) the figure sentences and tables that share
    a word with it come first."""
    text = (text or "").strip()
    if len(text) <= WHOLE_UNDER:
        return text
    words = _stems(focus)

    def near(line: str) -> int:
        return sum(w in line for w in words)
    parts = [text[:OPENING_CHARS].rstrip()]
    for block in sorted(_table_blocks(text), key=lambda b: -near(" ".join(b[:TABLE_LINES]))):
        parts.append("\n".join(block[:TABLE_LINES]))
    for line in text.splitlines():
        if _CAPTION.match(line):
            parts.append(line.strip()[:200])
    figures = [s.strip() for s in _SENTENCE.split(text[OPENING_CHARS:])
               if 25 <= len(s.strip()) <= 260 and re.search(r"\d", s) and _FIGURE.search(s)]
    # the sentences about the focus first, the rest in their order
    figures.sort(key=lambda s: -near(s))
    parts.extend(figures[:SENTENCES_PER_SOURCE])
    return "\n".join(dict.fromkeys(p for p in parts if p))


def digest(sources: list[str], *, total: int = TOTAL_CHARS, focus: str = "") -> list[str]:
    """`sources` as digests within `total` characters; every source keeps a share."""
    live = [s for s in sources if (s or "").strip()]
    if sum(len(s) for s in live) <= total:
        return live
    out = [digest_one(s, focus) for s in live]
    over = sum(len(s) for s in out) - total
    if over > 0 and out:
        share = max(400, total // len(out))
        out = [s if len(s) <= share else s[:share].rstrip() for s in out]
    return out
