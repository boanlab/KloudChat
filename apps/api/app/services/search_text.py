"""Plain text for search: what a result says, and the excerpt a match is shown in.

Pure functions, no database and no app imports, so the model layer can call them on every
write and the migration can keep a copy in step. Nothing here leaves the server: search
reads the caller's own rows and answers only the caller.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

#: Cap on the stored text of one result. Long enough for a 40-page report's words.
TEXT_LIMIT = 20_000

#: Keys whose values are settings, ids, styling or machine records, never what the result says.
SKIP_KEYS = frozenset({
    "id", "templateId", "status", "format", "level", "layout", "kind", "language", "design",
    "lint", "research", "diagrams", "factCheck", "marks", "src", "url", "href", "image",
    "logo", "accent", "font", "visualStyle", "theme", "citationStyle", "wordCount",
    "chartType", "storageKey", "sourceFile", "request", "mime", "aspect", "voice", "model",
    "style", "css", "svg", "png", "dataUrl", "thumbnail", "provider", "jobId",
})

#: Elements whose text is code or drawing, not prose.
_SILENT = frozenset({"style", "script", "svg", "math", "template", "noscript"})


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.size = 0
        self._silent = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 — HTMLParser's signature
        if tag in _SILENT:
            self._silent += 1
        elif tag in {"p", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SILENT and self._silent:
            self._silent -= 1

    def handle_data(self, data: str) -> None:
        if not self._silent and self.size < TEXT_LIMIT:
            self.parts.append(data)
            self.size += len(data)


def html_text(markup: str) -> str:
    """The words of an HTML fragment, without tags, styles or scripts."""
    if "<" not in markup:
        return markup
    reader = _Text()
    try:
        reader.feed(markup)
        reader.close()
    except Exception:  # noqa: BLE001 — a malformed page still has words
        pass
    return "".join(reader.parts)


def _strings(value: Any, out: list[str], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith(("data:", "http://", "https://")):
            return
        words = " ".join(html_text(text).split())
        if words:
            out.append(words)
            budget[0] -= len(words) + 1
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIP_KEYS:
                _strings(item, out, budget)
    elif isinstance(value, list):
        for item in value:
            _strings(item, out, budget)


def artifact_text(data: Any) -> str:
    """What a result says, as one line of words, capped at `TEXT_LIMIT`."""
    out: list[str] = []
    _strings(data, out, [TEXT_LIMIT])
    return " ".join(out)[:TEXT_LIMIT]


# ── queries and excerpts ──────────────────────────────────────────────

#: Bounds on what one query may ask: a few words, each short.
MAX_TERMS = 6
MAX_TERM = 40
MAX_QUERY = 200
#: Characters shown around a match.
SNIPPET = 140


def terms(query: str) -> list[str]:
    """The words of a query, lower-cased, deduplicated, bounded."""
    seen: list[str] = []
    for word in (query or "")[:MAX_QUERY].split():
        word = word[:MAX_TERM].lower()
        if word and word not in seen:
            seen.append(word)
        if len(seen) == MAX_TERMS:
            break
    return seen


def like_pattern(term: str) -> str:
    """`%term%` with LIKE's own wildcards escaped (escape character `\\`)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    """Where the words occur in `text`, case-insensitively, merged and in order."""
    lowered = text.lower()
    if len(lowered) != len(text):
        # A case mapping that changes length would misplace every span; show it unmarked.
        return []
    found: list[tuple[int, int]] = []
    for word in words:
        start = lowered.find(word)
        while start != -1 and len(found) < 200:
            found.append((start, start + len(word)))
            start = lowered.find(word, start + len(word))
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def parts(text: str, words: list[str]) -> list[dict[str, Any]]:
    """`text` cut into `{"text", "hit"}` runs, the query's words marked."""
    runs: list[dict[str, Any]] = []
    at = 0
    for start, end in _spans(text, words):
        if start > at:
            runs.append({"text": text[at:start], "hit": False})
        runs.append({"text": text[start:end], "hit": True})
        at = end
    if at < len(text):
        runs.append({"text": text[at:], "hit": False})
    return runs


def excerpt(
    text: str, words: list[str], *, cut_before: bool = False, cut_after: bool = False
) -> list[dict[str, Any]]:
    """A window of about `SNIPPET` characters around the first match, as marked runs.

    `cut_before` / `cut_after`: the text handed in is already a window of a longer original.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return []
    lowered = flat.lower()
    hits = [i for i in (lowered.find(w) for w in words) if i != -1] if len(lowered) == len(flat) \
        else []
    first = min(hits) if hits else 0
    start = max(0, first - SNIPPET // 3)
    if start:
        # Start on a word, not inside one.
        space = flat.find(" ", start, first)
        start = space + 1 if space != -1 else start
    end = min(len(flat), start + SNIPPET)
    if end < len(flat):
        space = flat.rfind(" ", first, end)
        end = space if space > first else end
    window = flat[start:end]
    lead = "…" if (start > 0 or cut_before) else ""
    tail = "…" if (end < len(flat) or cut_after) else ""
    runs = parts(window, words)
    if lead:
        runs.insert(0, {"text": lead, "hit": False})
    if tail:
        runs.append({"text": tail, "hit": False})
    return runs
