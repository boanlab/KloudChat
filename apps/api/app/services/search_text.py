"""Search text and match excerpts; pure functions, caller's own rows only."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

#: stored text cap per result (~40-page report)
TEXT_LIMIT = 20_000

#: settings/ids/styling/machine keys, not content
SKIP_KEYS = frozenset({
    "id", "templateId", "status", "format", "level", "layout", "kind", "language", "design",
    "lint", "research", "diagrams", "factCheck", "marks", "src", "url", "href", "image",
    "logo", "accent", "font", "visualStyle", "theme", "citationStyle", "wordCount",
    "chartType", "storageKey", "sourceFile", "request", "mime", "aspect", "voice", "model",
    "style", "css", "svg", "png", "dataUrl", "thumbnail", "provider", "jobId",
})

#: code/drawing elements, not prose
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
    """Words of an HTML fragment, no tags/styles/scripts."""
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
    """Result text as one line, capped at `TEXT_LIMIT`."""
    out: list[str] = []
    _strings(data, out, [TEXT_LIMIT])
    return " ".join(out)[:TEXT_LIMIT]


#: query bounds
MAX_TERMS = 6
MAX_TERM = 40
MAX_QUERY = 200
#: chars around a match
SNIPPET = 140


def terms(query: str) -> list[str]:
    """Query words: lower-cased, deduped, bounded."""
    seen: list[str] = []
    for word in (query or "")[:MAX_QUERY].split():
        word = word[:MAX_TERM].lower()
        if word and word not in seen:
            seen.append(word)
        if len(seen) == MAX_TERMS:
            break
    return seen


def like_pattern(term: str) -> str:
    r"""`%term%` with LIKE wildcards escaped (`\`)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _spans(text: str, words: list[str]) -> list[tuple[int, int]]:
    """Case-insensitive match spans, merged, in order."""
    lowered = text.lower()
    if len(lowered) != len(text):
        # length-changing case mapping would misplace spans
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
    """`text` as `{"text", "hit"}` runs."""
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
    """~`SNIPPET`-char window around the first match, as marked runs."""
    flat = " ".join((text or "").split())
    if not flat:
        return []
    lowered = flat.lower()
    hits = [i for i in (lowered.find(w) for w in words) if i != -1] if len(lowered) == len(flat) \
        else []
    first = min(hits) if hits else 0
    start = max(0, first - SNIPPET // 3)
    if start:
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
