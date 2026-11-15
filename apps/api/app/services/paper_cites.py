"""Project papers a request asks to cite, and which of them a document never cites.

「프로젝트 논문 중 해당되는 것들을 인용하고 참고문헌도 달아 줘」 asks for every project paper that
belongs to the topic, not for the two the writer happened to read first. The planner and the
writer are told to give each relevant paper its place (`rule`); this module is the code check
after them: which papers the request names, the document discusses or the topic evidently
reaches (`required`), and which of those no `[n]` cites (`uncited`). Relevance is read from the
papers' own text (titles, abstracts) against the request and the document, never from a list
of subjects.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from app.services import citations, source_focus

#: a request that asks to cite project papers: a project/attached source word beside a cite word
_SOURCE_WORD = (
    r"(?:프로젝트|첨부|업로드|올린|제공|자료|폴더|서가)[^\n.]{0,12}?(?:논문|문헌|paper|article)"
    r"|(?:논문|문헌)[^\n.]{0,6}?(?:중|가운데|에서)")
_CITE_WORD = r"인용|참고\s{0,2}문헌|출처|reference|cite|citation"
_ASKS = re.compile(
    rf"(?:{_SOURCE_WORD})[^\n]{{0,60}}?(?:{_CITE_WORD})"
    rf"|(?:{_CITE_WORD})[^\n]{{0,40}}?(?:{_SOURCE_WORD})"
    r"|\bcite\b[^\n]{0,40}?\b(?:project|attached|uploaded|provided)\b[^\n]{0,20}?\b(?:papers?|articles?|sources?)\b"
    r"|\b(?:project|attached|uploaded|provided)\b[^\n]{0,20}?\b(?:papers?|articles?)\b[^\n]{0,40}?\bcite\b",
    re.I)

#: words a title never identifies a paper by
_STOP = frozenset(
    "the and for with from via into over under using use based toward towards through "
    "on in of to an a at by as or is are".split())
#: a title word that names a system: 「PagedAttention」, 「DistServe」, 「H2O」, 「BadNets」
_NAMELIKE = re.compile(r"^(?:[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Z]{1,3}[a-z][A-Za-z0-9]*[A-Z]\w*"
                       r"|[A-Za-z]+\d[A-Za-z0-9]*|\d[A-Za-z]+[A-Za-z0-9]*)$")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def asks_project_papers(request: str) -> bool:
    """Whether the request asks to cite the project's (or attached) papers."""
    return bool(_ASKS.search(request or ""))


@dataclass(frozen=True, slots=True)
class Paper:
    """A project paper as the writer cites it."""

    ordinal: int
    title: str
    name: str = ""
    #: normalized phrases that identify it in prose: its short name, name-like title words,
    #: title word pairs no other project paper's title has
    marks: frozenset[str] = field(default_factory=frozenset)

    @property
    def shown(self) -> str:
        return self.title or self.name


def _plain(title: str) -> str:
    """A title as plain words: 「H$_2$O」 → 「H2O」."""
    title = re.sub(r"\$\s*_?\{?(\w+)\}?\s*\$", r"\1", title or "")
    return re.sub(r"[${}\\]", "", title)


def _flat(text: str) -> str:
    """Lower-cased words joined by single spaces: 「Heavy-Hitter」 → 「heavy hitter」."""
    return " ".join(re.findall(r"[a-z0-9가-힣]+", (text or "").lower()))


def _pairs(title: str) -> set[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", _plain(title)) if w.lower() not in _STOP]
    return {f"{a.lower()} {b.lower()}" for a, b in zip(words, words[1:], strict=False)
            if len(a) >= 3 and len(b) >= 3}


def _marks(title: str, others: list[str]) -> frozenset[str]:
    plain = _plain(title)
    out: set[str] = set()
    head, _, rest = plain.partition(":")
    if rest and len(head.split()) <= 2 and len(re.sub(r"\W", "", head)) >= 3:
        out.add(_flat(head))
    for word in re.findall(r"[A-Za-z0-9\-]+", plain):
        if _NAMELIKE.match(word):
            out.add(_flat(word))
    taken: set[str] = set()
    for other in others:
        taken |= _pairs(other)
    out |= {pair for pair in _pairs(plain) if pair not in taken}
    return frozenset(m for m in out if len(m) >= 3)


def papers_of(project_sources: list[dict] | None, sources: list[dict] | None) -> list[Paper]:
    """The project and attached papers the writer holds, with their citation numbers."""
    ordinals = {str(s.get("id") or ""): int(s.get("ordinal") or 0) for s in sources or []}
    titles = {int(s.get("ordinal") or 0): str(s.get("title") or "") for s in sources or []}
    rows: list[tuple[int, str, str]] = []
    for item in project_sources or []:
        if item.get("state") not in ("included", "truncated") or item.get("listed"):
            continue
        name = str(item.get("name") or "")
        paper = item.get("paper") or {}
        if not paper and not name.lower().endswith(".pdf"):
            continue
        ordinal = ordinals.get(str(item.get("id") or ""), 0)
        if not ordinal:
            continue
        rows.append((ordinal, str(paper.get("title") or titles.get(ordinal) or name), name))
    all_titles = [t for _, t, _ in rows]
    out = []
    for ordinal, title, name in rows:
        out.append(Paper(ordinal, title, name,
                         _marks(title, [t for t in all_titles if t != title])))
    return out


def names(text: str, paper: Paper) -> bool:
    """Whether `text` names the paper: its short name, a name-like title word, a title phrase
    no other paper's title has, its arXiv id or its file name."""
    flat = f" {_flat(text)} "
    if any(f" {mark} " in flat for mark in paper.marks):
        return True
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", paper.name)
    return bool(len(stem) >= 6 and stem.lower() in (text or "").lower())


def cited_numbers(sections: list[dict]) -> set[int]:
    return set(citations.cited(sections))


def body_of(sections: list[dict], reference_heading: re.Pattern[str]) -> str:
    """The prose of every section but the reference list."""
    return "\n".join(f"{s.get('heading') or ''}\n{s.get('content') or ''}" for s in sections
                     if not reference_heading.search(str(s.get("heading") or "")))


def texts_of(material: list[str], papers: list[Paper], most: int = 4000) -> dict[int, str]:
    """Each paper's own opening text (title, abstract, introduction) from the material blocks
    (「## <file name>」 parts)."""
    by_name = {p.name: p for p in papers if p.name}
    out: dict[int, str] = {}
    for block in material:
        for part in source_focus.file_parts(block, list(by_name)):
            paper = by_name.get(part.name) or next(
                (p for n, p in by_name.items() if part.name.startswith(n)), None)
            if paper is not None and paper.ordinal not in out:
                out[paper.ordinal] = re.sub(r"[ \t]+", " ", part.text)[:most]
    return out


def _terms(text: str) -> Counter[str]:
    return Counter(w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOP)


def similar(papers: list[Paper], texts: dict[int, str], anchors: set[int]) -> set[int]:
    """Papers whose own text resembles the cited ones' far more than the project's other
    papers' do (cosine of idf-weighted words against the cited papers' sum, over twice the
    median of the uncited): on the same subject as what the document already cites."""
    have = [p for p in papers if texts.get(p.ordinal)]
    if len(have) < 4 or not anchors & {p.ordinal for p in have}:
        return set()
    counts = {p.ordinal: _terms(texts[p.ordinal]) for p in have}
    df: Counter[str] = Counter()
    for c in counts.values():
        df.update(c.keys())
    n = len(have)
    idf = {w: math.log((n + 1) / (k + 0.5)) for w, k in df.items()}

    def vec(c: Counter[str]) -> dict[str, float]:
        return {w: (1 + math.log(k)) * idf[w] for w, k in c.items()}

    vectors = {o: vec(c) for o, c in counts.items()}
    centroid: Counter[str] = Counter()
    for o in anchors & set(vectors):
        for w, x in vectors[o].items():
            centroid[w] += x
    norm_c = math.sqrt(sum(x * x for x in centroid.values())) or 1.0

    def cosine(v: dict[str, float]) -> float:
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return sum(x * centroid.get(w, 0.0) for w, x in v.items()) / (norm * norm_c)

    scores = {o: cosine(v) for o, v in vectors.items() if o not in anchors}
    if len(scores) < 3:
        return set()
    ordered = sorted(scores.values())
    median = ordered[len(ordered) // 2]
    return {o for o, s in scores.items() if s >= 0.08 and s >= 2 * median}


def required(papers: list[Paper], sections: list[dict], request: str, material: list[str],
             reference_heading: re.Pattern[str]) -> tuple[list[Paper], list[Paper]]:
    """`(certain, maybe)`: uncited papers the document must cite — the request names them, the
    document discusses them, or their text is plainly on the cited ones' subject — and the
    other uncited ones, whose relevance only the writer can judge."""
    have = cited_numbers(sections)
    left = [p for p in papers if p.ordinal not in have]
    if not left:
        return [], []
    body = body_of(sections, reference_heading)
    texts = texts_of(material, papers)
    close = similar(papers, texts, have)
    certain = [p for p in left if names(request, p) or names(body, p) or p.ordinal in close]
    return certain, [p for p in left if p not in certain]


def rule(papers: list[Paper]) -> str:
    """Planner and writer rule for a request that cites the project's papers on a topic."""
    if len(papers) < 2:
        return ""
    listing = "\n".join(f"  [{p.ordinal}] {p.shown}" for p in papers)
    return (
        "\n- The request asks to cite the project's papers that belong to its topic. The "
        "papers on file:\n" + listing + "\n  Judge each one from its title and abstract: is "
        "its main subject within the topic? Every paper that is gets its own place — a "
        "section of its own, or its own paragraph under a sub-category heading that groups "
        "papers addressing the same kind of problem — and is cited there with its number "
        "[n]. Do not leave a relevant paper out because another one covers a similar point, "
        "and do not cite a paper whose subject lies outside the topic. "
        "관련 있는 논문마다 한 문단 이상을 두고 본문에 [n]으로 인용한다.")


__all__ = ["Paper", "asks_project_papers", "body_of", "cited_numbers", "names", "papers_of",
           "required", "rule", "similar", "texts_of"]
