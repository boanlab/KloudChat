"""Report citations: only cited sources kept, numbered by first citation."""

from __future__ import annotations

import re

#: 「[1]」「[2, 5]」「[3-4]」「［2］」; not link text or footnotes
_CITATION = re.compile(r"(?<!\[)[\[［]\s*(\d{1,3}(?:\s*[,，\-–]\s*\d{1,3}){0,9})\s*[\]］](?!\()")
_REFERENCE_HEADING = re.compile(r"참고\s{0,3}문헌|references|bibliography|인용\s{0,3}문헌", re.I)
_FENCE = "```"


def _numbers(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*[,，]\s*", spec.strip()):
        if re.fullmatch(r"\d+\s*[\-–]\s*\d+", part):
            lo, hi = (int(x) for x in re.split(r"\s*[\-–]\s*", part))
            out.extend(range(lo, hi + 1) if lo <= hi <= lo + 20 else [lo, hi])
        elif part.isdigit():
            out.append(int(part))
    return out


def _written(numbers: list[int]) -> str:
    """`[1, 2, 3, 5]` → 「[1-3, 5]」; pairs stay 「[1, 2]」."""
    numbers = sorted(dict.fromkeys(numbers))
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    parts = [f"{r[0]}-{r[-1]}" if len(r) >= 3 else ", ".join(map(str, r)) for r in runs]
    return f"[{', '.join(parts)}]"


def _prose(section: dict) -> bool:
    return (str(section.get("format") or "markdown") == "markdown"
            and not _REFERENCE_HEADING.search(str(section.get("heading") or "")))


def _outside_code(text: str, fn) -> str:
    pieces = text.split(_FENCE)
    for i in range(0, len(pieces), 2):
        pieces[i] = fn(pieces[i])
    return _FENCE.join(pieces)


def cited(sections: list[dict]) -> list[int]:
    """Cited numbers in first-cited order, once each."""
    seen: list[int] = []
    for section in sections:
        if not _prose(section):
            continue
        content = str(section.get("content") or "")
        for i, piece in enumerate(content.split(_FENCE)):
            if i % 2:
                continue
            for m in _CITATION.finditer(piece):
                for n in _numbers(m.group(1)):
                    if n not in seen:
                        seen.append(n)
    return seen


def cite_order(sections: list[dict], sources: list[dict] | None
               ) -> tuple[list[dict], list[dict]]:
    """Uncited sources dropped, rest renumbered by first citation."""
    sources = list(sources or [])
    if not sources:
        return sections, sources
    if any(_REFERENCE_HEADING.search(str(s.get("heading") or "")) for s in sections):
        return sections, sources
    by_number: dict[int, dict] = {}
    for index, source in enumerate(sources, 1):
        by_number.setdefault(int(source.get("ordinal") or index), source)
    order = [n for n in cited(sections) if n in by_number]
    if not order:
        return sections, sources
    mapping = {old: new for new, old in enumerate(order, 1)}
    if all(old == new for old, new in mapping.items()) and len(order) == len(sources):
        return sections, sources

    def renumber(piece: str) -> str:
        def one(m: re.Match[str]) -> str:
            wanted: list[int] = []
            for n in _numbers(m.group(1)):
                if n in mapping:
                    wanted.append(mapping[n])
                elif n not in by_number and n > len(order):
                    wanted.append(n)  # dangling: left for the panel
            return _written(wanted) if wanted else ""

        piece = _CITATION.sub(one, piece)
        # merged duplicates collapsed; stray space before period
        piece = re.sub(r"(\[\d{1,3}\])(?:[ ,]{0,2}\1){1,10}", r"\1", piece)
        return re.sub(r"[ \t]{1,2}([.,])", r"\1", piece)

    out = [
        {**s, "content": _outside_code(str(s.get("content") or ""), renumber)} if _prose(s)
        else s
        for s in sections
    ]
    kept = [{**by_number[old], "ordinal": new} for old, new in mapping.items()]
    kept.sort(key=lambda s: s["ordinal"])
    return out, kept


#: venues written in an author's place
_VENUE_WORDS = (
    "MDPI", "arXiv", "ACM", "IEEE", "ACL Anthology", "ACL", "NeurIPS", "OpenReview",
    "Springer", "Elsevier", "PMLR", "AAAI", "KCI", "DBpia", "RISS", "KISS", "ScienceON",
    "KoreaScience", "earticle", "Nature", "ScienceDirect",
)


def _work_label(source: dict) -> str:
    """Text name of a work: first author (「Wang 외」), else title head."""
    author = str(source.get("author") or "").strip()
    if author:
        names = [a.strip() for a in re.split(r",|;| and | 외", author) if a.strip()]
        first = names[0] if names else author
        # Latin by surname, Korean whole
        surname = first if re.search(r"[가-힣]", first) else first.split()[-1]
        many = len(names) > 1 or author.endswith("외")
        return f"{surname} 외" if many else surname
    title = str(source.get("title") or "").strip()
    head = title.split(":")[0].strip() if ":" in title[3:] else title
    words = head.split()
    return " ".join(words[:5]) if len(words) > 5 else head


def name_works(sections: list[dict], sources: list[dict] | None) -> list[dict]:
    """「MDPI 연구(2026) [9]」 → work's author or title in place of the venue."""
    by_number = {int(s.get("ordinal") or i): s for i, s in enumerate(sources or [], 1)}
    if not by_number:
        return sections
    venues = {v.lower() for v in _VENUE_WORDS}
    for source in by_number.values():
        # sites too; a publisher institute may be the author
        value = str(source.get("publisher") or "").strip()
        if "." in value and len(value) <= 40:
            venues.add(value.lower())
    names = "|".join(re.escape(v) for v in sorted(venues, key=len, reverse=True))
    pattern = re.compile(
        rf"(?<![\w.])({names})(?:의)?\s?(연구|논문|보고서|연구진)(?=[^\[\n]{{0,16}}"
        rf"[\[［]\s*(\d{{1,3}})\s*[\]］])", re.I)

    def fix(piece: str) -> str:
        def one(m: re.Match[str]) -> str:
            source = by_number.get(int(m.group(3)))
            if source is None:
                return m.group(0)
            label = _work_label(source)
            return f"{label} {m.group(2)}" if label else m.group(0)

        return pattern.sub(one, piece)

    return [
        {**s, "content": _outside_code(str(s.get("content") or ""), fix)} if _prose(s) else s
        for s in sections
    ]


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


#: reference entry start: 「[3]」, 「3.」, 「- 」
_ENTRY = re.compile(r"^\s{0,3}(?:\[\d{1,3}\]|\d{1,3}[.)]|[-*·])\s")


def drop_restated_references(sections: list[dict], sources: list[dict] | None) -> list[dict]:
    """Writer's 「참고문헌」 entries restating server sources removed; empty section dropped."""
    titles = [k[:30] for k in (_key(str(src.get("title") or "")) for src in sources or [])
              if len(k) >= 8]
    if not titles:
        return sections
    out = []
    for section in sections:
        if (not _REFERENCE_HEADING.search(str(section.get("heading") or ""))
                or str(section.get("format") or "markdown") != "markdown"):
            out.append(section)
            continue
        entries: list[list[str]] = []
        for line in str(section.get("content") or "").split("\n"):
            if _ENTRY.match(line) or not entries:
                entries.append([line])
            else:
                entries[-1].append(line)
        kept = [e for e in entries if "".join(e).strip()
                and not any(t in _key(" ".join(e)) for t in titles)]
        if not any(_ENTRY.match(e[0]) for e in kept):
            continue
        out.append({**section, "content": "\n".join(line for e in kept for line in e).strip()})
    return out


def tidy(sections: list[dict], sources: list[dict] | None
         ) -> tuple[list[dict], list[dict]]:
    """Drop restated references, name works, order citations."""
    sections = drop_restated_references(sections, sources)
    return cite_order(name_works(sections, sources), list(sources or []))


__all__ = ["cite_order", "cited", "drop_restated_references", "name_works", "tidy"]
