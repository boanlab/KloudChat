"""Lexical retrieval over an agent's own documents, used by the `search_knowledge` tool.

Scoring is term containment plus character bigrams (bigrams carry Korean, where
whitespace tokens include particles). Runs with no backend; `merge()` combines
this ranking with the vector index's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Characters per chunk.
_CHUNK = 900
#: Characters each chunk repeats of the previous one.
_OVERLAP = 150
#: Terms shorter than this match everything.
_MIN_TERM = 2


@dataclass(slots=True)
class Passage:
    """One retrieved chunk, with enough to cite it."""

    document: str
    #: 1-based.
    index: int
    text: str
    score: float
    source_url: str | None = None


def chunk(text: str) -> list[str]:
    """Overlapping windows, cut at a paragraph or sentence break where one is near."""
    body = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not body:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + _CHUNK, len(body))
        if end < len(body):
            window = body[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("다.\n"))
            # Back half only, so a heading alone never becomes a chunk.
            if cut > _CHUNK // 2:
                end = start + cut
        piece = body[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(body):
            break
        start = max(end - _OVERLAP, start + 1)
    return chunks


def _terms(text: str) -> set[str]:
    """Whitespace tokens, lowercased, punctuation stripped."""
    return {t for t in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if len(t) >= _MIN_TERM}


def _bigrams(text: str) -> set[str]:
    """Character bigrams over letters only."""
    letters = re.sub(r"[^0-9a-z가-힣]+", "", text.lower())
    return {letters[i : i + 2] for i in range(len(letters) - 1)}


def _flatten(text: str) -> str:
    """Letters and digits only, lowercased."""
    return re.sub(r"[^0-9a-z가-힣]+", "", text.lower())


def score(query: str, passage: str) -> float:
    """0–1: term containment and bigram overlap, measured against the query.

    Containment rather than token equality, because Korean particles attach to words.
    """
    q_terms, q_grams = _terms(query), _bigrams(query)
    if not q_terms and not q_grams:
        return 0.0
    flat = _flatten(passage)
    term_hit = (
        sum(1 for term in q_terms if _flatten(term) in flat) / len(q_terms) if q_terms else 0.0
    )
    gram_hit = len(q_grams & _bigrams(passage)) / len(q_grams) if q_grams else 0.0
    return round(0.65 * term_hit + 0.35 * gram_hit, 4)


#: Minimum score for a passage to be returned.
_FLOOR = 0.12


def search(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> list[Passage]:
    """Best passages across `(name, text, source_url)` documents, at most two per document."""
    scored: list[Passage] = []
    for name, text, url in documents:
        pieces = chunk(text)
        ranked = sorted(
            (
                Passage(
                    document=name,
                    index=i + 1,
                    text=piece,
                    score=score(query, piece),
                    source_url=url,
                )
                for i, piece in enumerate(pieces)
            ),
            key=lambda p: p.score,
            reverse=True,
        )
        scored.extend(p for p in ranked[:2] if p.score >= _FLOOR)
    scored.sort(key=lambda p: p.score, reverse=True)
    return scored[:limit]


#: Total characters below which every document is returned whole, unranked.
_WHOLE_SHELF = 12_000


def gather(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> tuple[list[Passage], bool]:
    """`(passages, ranked)`: whole shelf if small, best passages otherwise."""
    total = sum(len(text) for _, text, _ in documents)
    if total <= _WHOLE_SHELF:
        return [
            Passage(document=name, index=1, text=text.strip(), score=1.0, source_url=url)
            for name, text, url in documents
            if text.strip()
        ], False
    return search(documents, query, limit=limit), True


def render(passages: list[Passage]) -> str:
    """Tool output, cited by document."""
    if not passages:
        return ""
    return "\n\n".join(
        f"[{p.document} · {p.index}번째 조각]"
        + (f"\n출처: {p.source_url}" if p.source_url else "")
        + f"\n{p.text}"
        for p in passages
    )


#: Vector share of the merged score.
_VECTOR_WEIGHT = 0.6


def merge(vector: list[dict], lexical: list[Passage], *, limit: int = 4) -> list[Passage]:
    """One ranked list from both retrievers, matched on `(document, index)`."""
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
