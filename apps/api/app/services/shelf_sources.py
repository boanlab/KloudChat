"""지식 서가 sources: what each project file is, the numbered source text a studio or guide
call reads, and the passage a citation points at.

A project source is a `StoredFile` with a `project_id`: an upload, a web page snapshot, pasted
text, a note made into a source, or a video/recording (its transcript, time-coded
「[HH:MM:SS]」 by the video pipeline). Calls read sources as 「[n] name」 blocks so their
citations (「[n]」) name the file; `locate` finds the passage a cited sentence came from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.services import knowledge

#: 「[00:01:23]」 at a transcript section's start (video pipeline), 「[01:23]」 tolerated
_TIME = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\]")
_PAGE = re.compile(r"\[페이지\s{0,2}(\d{1,5})\]")


def kind_of(stored: Any, notes: set[str] | frozenset[str] = frozenset()) -> str:
    """file | web | text | note | video | audio; `notes`: ids of sources made from notes.

    Pasted text and notes are stored as uploads (the person gave them), so the file box
    lists them; plain text is 「text」 whether pasted or uploaded as `.txt`."""
    mime = str(getattr(stored, "mime", "") or "")
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if str(getattr(stored, "id", "")) in notes:
        return "note"
    if getattr(stored, "source_url", None):
        return "web"
    if mime.startswith("text/plain"):
        return "text"
    return "file"


def digest_of(stored: Any) -> dict[str, Any] | None:
    """The file's per-file digest (`file_digest`, fix/project-coverage) when one is current:
    `{label, title, summary, outline, terms}`; None before that branch, or without one."""
    data = getattr(stored, "digest", None)
    if not isinstance(data, dict) or not (data.get("summary") or data.get("title")):
        return None
    if int(data.get("chars") or -1) != len(getattr(stored, "text", "") or ""):
        return None
    return {
        "label": str(data.get("label") or ""),
        "title": str(data.get("title") or ""),
        "summary": str(data.get("summary") or ""),
        "outline": [str(x) for x in data.get("outline") or []][:8],
        "terms": [str(x) for x in data.get("terms") or []][:8],
    }


def signature(files: list[Any]) -> str:
    """What a guide or output was made from: ids and text lengths, in order."""
    key = "|".join(f"{f.id}:{len(f.text or '')}" for f in files)
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _spread(text: str, budget: int) -> str:
    """`budget` chars of `text`: the head, then windows spread evenly over the rest up to its
    end, so a long file is read end to end rather than cut after its opening."""
    if len(text) <= budget:
        return text
    head = int(budget * 0.4)
    windows = 6
    size = max(200, (budget - head) // windows - 8)
    rest = len(text) - head - size
    parts = [text[:head]]
    for i in range(windows):
        start = head + (rest * i) // (windows - 1)
        parts.append(text[start:start + size])
    return "\n…\n".join(parts)


@dataclass(slots=True)
class Numbered:
    """One source as a call reads it."""

    number: int
    file_id: str
    name: str
    kind: str
    chars: int
    #: chars of it the call read
    read: int


@dataclass(slots=True)
class Bundle:
    text: str
    sources: list[Numbered] = field(default_factory=list)

    @property
    def partial(self) -> list[str]:
        """Names of sources read only in part."""
        return [s.name for s in self.sources if s.read < s.chars]

    def wire(self) -> list[dict[str, Any]]:
        return [{"n": s.number, "fileId": s.file_id, "name": s.name, "kind": s.kind,
                 "chars": s.chars, "read": s.read} for s in self.sources]


def bundle(files: list[Any], budget: int) -> Bundle:
    """Sources numbered 1.. in the given order within `budget` chars: small ones whole, the
    rest share what is left evenly (water-filling), long ones read across their length."""
    readable = [f for f in files if (f.text or "").strip()]
    shares: dict[str, int] = {}
    left = max(budget, 2_000)
    pending = sorted(readable, key=lambda f: len(f.text))
    while pending:
        share = left // len(pending)
        f = pending[0]
        if len(f.text) <= share:
            shares[f.id] = len(f.text)
            left -= len(f.text)
            pending.pop(0)
            continue
        for rest in pending:
            shares[rest.id] = share
        break
    parts: list[str] = []
    numbered: list[Numbered] = []
    for n, f in enumerate(readable, 1):
        take = shares.get(f.id, 0)
        body = _spread(f.text, take)
        digest = digest_of(f)
        lead = ""
        if take < len(f.text):
            lead = f"(전체 {len(f.text):,}자 중 일부를 고르게 읽음)\n"
            if digest and digest.get("summary"):
                lead += f"요약: {digest['summary']}\n"
        parts.append(f"[{n}] {f.name}\n{lead}{body}")
        numbered.append(Numbered(n, f.id, f.name, kind_of(f), len(f.text), min(take, len(f.text))))
    return Bundle("\n\n".join(parts), numbered)


def seconds_of(text: str, at: int) -> float | None:
    """Time code of the transcript section `at` falls in (last 「[HH:MM:SS]」 before it)."""
    found = None
    for m in _TIME.finditer(text, 0, max(0, at) + 1):
        found = m
    if found is None:
        first = _TIME.search(text)
        if first is None or first.start() > at + 900:
            return None
        found = first
    hours = int(found.group(1) or 0)
    return float(hours * 3600 + int(found.group(2)) * 60 + int(found.group(3)))


def _page_before(text: str, at: int) -> int | None:
    found = None
    for m in _PAGE.finditer(text, 0, max(0, at) + 1):
        found = m
    return int(found.group(1)) if found else None


#: citation markers and list bullets do not help find the passage
_NOISE = re.compile(
    r"\[\[?\d{1,3}(?:[,\s\-–]{1,3}\d{1,3}){0,5}\]\]?(?:\([^)\s]{1,500}\))?|[*_#>`|]")


def locate(text: str, quote: str = "", page: int | None = None) -> dict[str, Any]:
    """The passage of `text` a cited sentence (`quote`) came from: its offsets, the page it is
    on (「[페이지 n]」 marks) and, for a transcript, its time code. With no usable quote, the
    page's start (or the beginning)."""
    body = text or ""
    query = _NOISE.sub(" ", quote or "").strip()
    spans = knowledge._spans(body)
    best: tuple[float, int, str] | None = None
    if query and spans:
        for start, piece in spans:
            score = knowledge.score(query, piece)
            if page is not None and _page_before(body, start) == page:
                score += 0.05
            if best is None or score > best[0]:
                best = (score, start, piece)
        if best is not None and best[0] < 0.12:
            best = None
    if best is None and page is not None:
        mark = re.search(rf"\[페이지\s{{0,2}}{int(page)}\]", body)
        if mark:
            piece = body[mark.start(): mark.start() + 900]
            best = (0.0, mark.start(), piece)
    if best is None:
        best = (0.0, 0, body[:900])
    score, start, piece = best
    # the sentence of the passage that shares most with the quote, for the highlight
    focus_at, focus_len = start, len(piece)
    if query:
        sentences = [m for m in re.finditer(r"[^.!?。\n]{8,}[.!?。]?", piece)]
        if sentences:
            top = max(sentences, key=lambda m: knowledge.score(query, m.group(0)))
            if knowledge.score(query, top.group(0)) >= 0.2:
                focus_at, focus_len = start + top.start(), len(top.group(0))
    return {
        "start": start,
        "end": start + len(piece),
        "focusStart": focus_at,
        "focusEnd": focus_at + focus_len,
        "page": _page_before(body, focus_at) if page is None else page,
        "seconds": seconds_of(body, focus_at),
        "score": round(score, 3),
        "matched": score >= 0.12,
    }


async def given_texts(db: Any, items: Any) -> dict[str, str]:
    """Name → text of the project files a turn's context gave (whole or in part), for the
    answer to cite them and for its citations to be checked against them."""
    from sqlmodel import col, select

    from app.models.workspace import StoredFile

    ids = [item.id for item in items or () if item.state != "omitted" and item.id]
    if not ids:
        return {}
    rows = (await db.exec(select(StoredFile.id, StoredFile.name, StoredFile.text)
                          .where(col(StoredFile.id).in_(ids)))).all()
    by_id = {row[0]: (row[1], row[2] or "") for row in rows}
    return {by_id[i][0]: by_id[i][1] for i in ids if i in by_id}


__all__ = ["Bundle", "given_texts", "Numbered", "bundle", "digest_of", "kind_of", "locate",
           "seconds_of", "signature"]
