"""Lexical retrieval over an agent's own documents.

Chunk, score against the question, return the few that matter. Reached through
the `search_knowledge` tool, so retrieval happens when the model asks for it
rather than on every turn. Distinct from project knowledge, which is injected
whole inside a character budget.

Scoring is term containment plus character bigrams. The bigrams carry Korean,
where whitespace tokens include particles ("보안을"/"보안이"/"보안은" are three
tokens and one word).

The limit: this matches *words*, so 접근 통제 stays hidden from "access
control". Meaning is `index_client`'s job — a vector index in the model stack —
and `merge()` combines the two rankings.

This half is the floor and runs with no backend. The index is across a network
boundary and may be down or absent; an agent reporting an empty shelf while its
documents sit readable in the database would be wrong about itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Characters per chunk. Long enough that a passage answers something on its
#: own, short enough that several fit in a tool result.
_CHUNK = 900
#: How much each chunk repeats of the previous one, so a sentence split across
#: the boundary is still whole somewhere.
_OVERLAP = 150
#: Terms shorter than this match everything and rank nothing.
_MIN_TERM = 2


@dataclass(slots=True)
class Passage:
    """One retrieved chunk, with enough to cite it."""

    document: str
    #: 1-based, so "3번째 조각" means something to a person reading the trace.
    index: int
    text: str
    score: float
    source_url: str | None = None


def chunk(text: str) -> list[str]:
    """Overlapping windows ending at a paragraph or sentence break where near.

    Hard cut when a document has neither — extracted tables, HWP text.
    """
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
            # Back half only: a break at character 20 yields a heading alone.
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
    return {
        t
        for t in re.split(r"[^0-9A-Za-z가-힣]+", text.lower())
        if len(t) >= _MIN_TERM
    }


def _bigrams(text: str) -> set[str]:
    """Character bigrams over letters only — particle-insensitive Korean match."""
    letters = re.sub(r"[^0-9a-z가-힣]+", "", text.lower())
    return {letters[i : i + 2] for i in range(len(letters) - 1)}


def _flatten(text: str) -> str:
    """Letters and digits only, lowercased — the surface a term is sought in."""
    return re.sub(r"[^0-9a-z가-힣]+", "", text.lower())


def score(query: str, passage: str) -> float:
    """0–1. Term containment and bigram overlap, measured against the query.

    Against the query, not symmetrically: length is not evidence either way.

    Containment, not token equality. Korean glues grammar onto words — "교체한다"
    holds no token "교체" — so equality scores a matching passage at zero.
    """
    q_terms, q_grams = _terms(query), _bigrams(query)
    if not q_terms and not q_grams:
        return 0.0
    flat = _flatten(passage)
    term_hit = (
        sum(1 for term in q_terms if _flatten(term) in flat) / len(q_terms)
        if q_terms
        else 0.0
    )
    gram_hit = len(q_grams & _bigrams(passage)) / len(q_grams) if q_grams else 0.0
    # Terms weigh more: a word match beats bigrams two Korean words share.
    return round(0.65 * term_hit + 0.35 * gram_hit, 4)


#: Floor. Below it a passage shares almost nothing with the question.
_FLOOR = 0.12


def search(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> list[Passage]:
    """Best passages across `(name, text, source_url)` documents.

    Two per document at most, so one long file cannot crowd out a short one.
    """
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


#: Shelf size below which the whole thing is returned unranked. Ranking a small
#: corpus is where a lexical scorer misses: vocabulary mismatch reads as silence
#: about a document the person can see in the list.
_WHOLE_SHELF = 12_000


def gather(
    documents: list[tuple[str, str, str | None]],
    query: str,
    *,
    limit: int = 4,
) -> tuple[list[Passage], bool]:
    """`(passages, ranked)` — whole shelf if small, best parts if not.

    `ranked` lets the caller distinguish "read all 3 documents" from "found 4
    passages"; only the second can have missed something.
    """
    total = sum(len(text) for _, text, _ in documents)
    if total <= _WHOLE_SHELF:
        return [
            Passage(document=name, index=1, text=text.strip(), score=1.0, source_url=url)
            for name, text, url in documents
            if text.strip()
        ], False
    return search(documents, query, limit=limit), True


def render(passages: list[Passage]) -> str:
    """Tool output, cited by document so the model can attribute."""
    if not passages:
        return ""
    return "\n\n".join(
        f"[{p.document} · {p.index}번째 조각]"
        + (f"\n출처: {p.source_url}" if p.source_url else "")
        + f"\n{p.text}"
        for p in passages
    )


#: Vector share of the merged score. Weighted towards meaning, but not far
#: enough to bury a literal match — exact article numbers and figures come back
#: from the lexical side alone.
_VECTOR_WEIGHT = 0.6


def merge(vector: list[dict], lexical: list[Passage], *, limit: int = 4) -> list[Passage]:
    """One ranked list from both retrievers.

    Matched on `(document, index)`: a chunk both sides found scores the sum.
    The two scales are uncalibrated against each other, so the ranking is the
    output and the absolute number is not.
    """
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
