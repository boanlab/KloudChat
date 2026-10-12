"""Lexical retrieval over an agent's documents (`search_knowledge`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: chars per chunk
_CHUNK = 900
#: chars repeated from the previous chunk
_OVERLAP = 150
#: shorter terms match everything
_MIN_TERM = 2


@dataclass(slots=True)
class Passage:
    """Retrieved chunk with citation info."""

    document: str
    #: 1-based
    index: int
    text: str
    score: float
    source_url: str | None = None
    #: article (「제12조(휴학)」) the passage sits under
    section: str = ""
    #: start page from 「[페이지 3]」 marks
    page: int | None = None
    #: 시행일/개정일 as written
    dated: str = ""

    @property
    def label(self) -> str:
        """Citation form: 「학칙 · 제12조(휴학) · 3쪽 · 시행 2025. 3. 1.」."""
        parts = [self.document]
        if self.section:
            parts.append(self.section)
        if self.page:
            parts.append(f"{self.page}쪽")
        if self.dated:
            parts.append(self.dated)
        return " · ".join(parts)


def _body(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").strip())


def _spans(body: str) -> list[tuple[int, str]]:
    """`(start, piece)` overlapping windows, cut near paragraph/sentence breaks."""
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(body):
        end = min(start + _CHUNK, len(body))
        if end < len(body):
            window = body[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("다.\n"))
            # back half only: no heading-only chunk
            if cut > _CHUNK // 2:
                end = start + cut
        raw = body[start:end]
        piece = raw.strip()
        if piece:
            chunks.append((start + (len(raw) - len(raw.lstrip())), piece))
        if end >= len(body):
            break
        start = max(end - _OVERLAP, start + 1)
    return chunks


def chunk(text: str) -> list[str]:
    """Overlapping windows cut near paragraph/sentence breaks."""
    return [piece for _, piece in _spans(_body(text))]


#: Korean regulation article heading
_ARTICLE = re.compile(
    r"(?m)^[ \t]{0,4}(제[ \t]{0,2}\d{1,4}[ \t]{0,2}조(?:의[ \t]{0,2}\d{1,3})?"
    r"(?:[ \t]{0,2}\([^()\n]{1,40}\))?)")
#: PDF page mark (`files._from_pdf`)
_PAGE = re.compile(r"\[페이지 (\d{1,5})\]")
#: 「시행 2025. 3. 1.」「2024.2.28. 개정」「2025년 3월 1일부터 시행한다」
_DATE = r"(\d{4})[ \t]{0,2}(?:\.|년|-)[ \t]{0,2}(\d{1,2})[ \t]{0,2}(?:\.|월|-)[ \t]{0,2}(\d{1,2})"
_DATED = re.compile(
    r"(시행|개정|제정)[ \t]{0,3}:?[ \t]{0,2}" + _DATE
    + r"|" + _DATE + r"[ \t]{0,2}(?:\.|일)?[ \t]{0,3}(?:부터[ \t]{0,2})?(시행|개정|제정)"
)


def _article(heading: str) -> str:
    return re.sub(r"[ \t]+", "", heading.split("(")[0]) + (
        "(" + heading.split("(", 1)[1].strip() if "(" in heading else "")


def _section_at(body: str, start: int, piece: str, query: str = "") -> str:
    """Article of a passage: in force at its start; multi-article passages by best query match."""
    heads = list(_ARTICLE.finditer(piece))
    before = None
    for match in _ARTICLE.finditer(body, 0, start + 1):
        before = match
    current = _article(before.group(1)) if before is not None else ""
    if not heads:
        return current
    parts = [(current, piece[: heads[0].start()])] if heads[0].start() > 0 else []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(piece)
        parts.append((_article(head.group(1)), piece[head.start():end]))
    parts = [(name, text) for name, text in parts if name]
    if not parts:
        return current
    if len(parts) == 1 or not query:
        return parts[0][0]
    return max(parts, key=lambda part: score(query, part[1]))[0]


def _page_of(body: str, start: int, piece: str, section: str) -> int | None:
    """Page of the cited article's part."""
    at = 0
    if section:
        for match in _ARTICLE.finditer(piece):
            if _article(match.group(1)) == section:
                at = match.start()
                break
    return _page_at(body, start + at, piece[at:])


def _page_at(body: str, start: int, piece: str) -> int | None:
    marks = [int(m.group(1)) for m in _PAGE.finditer(body, 0, start + 1)]
    if marks:
        return marks[-1]
    first = _PAGE.search(piece)
    return int(first.group(1)) if first else None


def dated(text: str) -> str:
    """Latest 시행일 (else 개정일, 제정일) as written, or ""."""
    found: dict[str, tuple[int, int, int]] = {}
    for match in _DATED.finditer((text or "")[:200_000]):
        if match.group(1):
            kind, y, m, d = match.group(1), match.group(2), match.group(3), match.group(4)
        else:
            y, m, d, kind = match.group(5), match.group(6), match.group(7), match.group(8)
        try:
            key = (int(y), int(m), int(d))
        except (TypeError, ValueError):
            continue
        if not (1 <= key[1] <= 12 and 1 <= key[2] <= 31):
            continue
        if kind not in found or key > found[kind]:
            found[kind] = key
    for kind in ("시행", "개정", "제정"):
        if kind in found:
            y, m, d = found[kind]
            return f"{kind} {y}. {m}. {d}."
    return ""


def _terms(text: str) -> set[str]:
    """Lowercased tokens, punctuation stripped."""
    return {t for t in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if len(t) >= _MIN_TERM}


def _bigrams(text: str) -> set[str]:
    """Letter-only character bigrams."""
    letters = re.sub(r"[^0-9a-z가-힣]+", "", text.lower())
    return {letters[i : i + 2] for i in range(len(letters) - 1)}


def _flatten(text: str) -> str:
    """Letters and digits only, lowercased."""
    return re.sub(r"[^0-9a-z가-힣]+", "", text.lower())


def score(query: str, passage: str) -> float:
    """0–1 term containment + bigram overlap vs the query (containment: Korean particles)."""
    q_terms, q_grams = _terms(query), _bigrams(query)
    if not q_terms and not q_grams:
        return 0.0
    flat = _flatten(passage)
    term_hit = (
        sum(1 for term in q_terms if _flatten(term) in flat) / len(q_terms) if q_terms else 0.0
    )
    gram_hit = len(q_grams & _bigrams(passage)) / len(q_grams) if q_grams else 0.0
    return round(0.65 * term_hit + 0.35 * gram_hit, 4)


#: min passage score
_FLOOR = 0.12


def search(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> list[Passage]:
    """Best passages over `(name, text, source_url)`, ≤2 per document."""
    scored: list[Passage] = []
    for name, text, url in documents:
        body = _body(text)
        when = dated(body)
        ranked = sorted(
            (
                Passage(
                    document=name,
                    index=i + 1,
                    text=piece,
                    score=score(query, piece),
                    source_url=url,
                    section=(section := _section_at(body, start, piece, query)),
                    page=_page_of(body, start, piece, section),
                    dated=when,
                )
                for i, (start, piece) in enumerate(_spans(body))
            ),
            key=lambda p: p.score,
            reverse=True,
        )
        scored.extend(p for p in ranked[:2] if p.score >= _FLOOR)
    scored.sort(key=lambda p: p.score, reverse=True)
    return scored[:limit]


#: below this total, every document returned whole
_WHOLE_SHELF = 12_000


def gather(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> tuple[list[Passage], bool]:
    """`(passages, ranked)`: whole small shelf (split by article) or best passages."""
    total = sum(len(text) for _, text, _ in documents)
    if total <= _WHOLE_SHELF:
        out: list[Passage] = []
        for name, text, url in documents:
            body = _body(text)
            if body:
                out.extend(_articles(name, body, url))
        return out, False
    return search(documents, query, limit=limit), True


def _articles(name: str, body: str, url: str | None) -> list[Passage]:
    """Whole document, split at article headings when ≥2."""
    when = dated(body)
    heads = list(_ARTICLE.finditer(body))
    if len(heads) < 2:
        return [Passage(document=name, index=1, text=body, score=1.0, source_url=url,
                        dated=when, page=_page_at(body, 0, body))]
    out: list[Passage] = []
    cuts = [0, *(m.start() for m in heads[1:]), len(body)]
    for i, (start, end) in enumerate(zip(cuts, cuts[1:], strict=False)):
        piece = body[start:end].strip()
        if not piece:
            continue
        out.append(Passage(document=name, index=i + 1, text=piece, score=1.0, source_url=url,
                           section=_section_at(body, start, piece),
                           page=_page_at(body, start, piece), dated=when))
    return out


def render(passages: list[Passage]) -> str:
    """Tool output: passages under numbered 「자료:」 lines (`agent._number_sources`)."""
    if not passages:
        return ""
    return "\n\n".join(
        f"[{n}] 자료: {p.label}"
        + (f"\n출처: {p.source_url}" if p.source_url else "")
        + f"\n{p.text}"
        for n, p in enumerate(passages, 1)
    )


#: vector share of merged score
_VECTOR_WEIGHT = 0.6


def merge(vector: list[dict], lexical: list[Passage], *, limit: int = 4) -> list[Passage]:
    """Merged ranking of both retrievers by `(document, index)`."""
    merged: dict[tuple[str, int], Passage] = {}
    for row in vector:
        key = (str(row.get("document") or ""), int(row.get("index") or 0))
        merged[key] = Passage(
            document=key[0],
            index=key[1],
            text=str(row.get("text") or ""),
            score=round(_VECTOR_WEIGHT * float(row.get("score") or 0.0), 4),
            source_url=row.get("source_url"),
        )
    for passage in lexical:
        key = (passage.document, passage.index)
        lexical_part = (1 - _VECTOR_WEIGHT) * passage.score
        if key in merged:
            found = merged[key]
            merged[key] = Passage(
                document=found.document,
                index=found.index,
                text=found.text,
                score=round(found.score + lexical_part, 4),
                source_url=found.source_url,
            )
        else:
            merged[key] = Passage(
                document=passage.document,
                index=passage.index,
                text=passage.text,
                score=round(lexical_part, 4),
                source_url=passage.source_url,
            )
    return sorted(merged.values(), key=lambda p: p.score, reverse=True)[:limit]
