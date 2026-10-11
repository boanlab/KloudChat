"""A written report's numbered citations put in order: the reference list holds only what the
body cites, numbered 1, 2, 3 … in the order the body first cites it.

`cite_order` runs once on a finished report before it is stored, so the stored sections and
sources agree, and everything read from them — the review panel's 「이 자료를 쓴 절」, the
IEEE numbers, the DOCX/HWPX/PDF reference list — follows. `name_works` puts the work's own
name where the writer named a venue as if it were the author (「MDPI 연구(2026) [9]」).
"""

from __future__ import annotations

import re

#: 「[1]」「[2, 5]」「[3-4]」「［2］」; not a link's text 「[1](…)」 nor a footnote 「[^1]」.
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
    """`[1, 2, 3, 5]` → 「[1-3, 5]」; two in a row stay 「[1, 2]」."""
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
    """The numbers the body cites, each once, in the order first cited."""
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
    """`sections` and `sources` with the uncited sources dropped and the rest numbered by first
    citation, every 「[n]」 in the body following its source.

    Left as they are: a report with no sources, one whose body cites none (a document that
    does not cite by number keeps its list), and one with a reference list of its own written
    into the text (its numbering is the writer's). A number no source carries is dropped from
    the body when it would now name another source, else left for the panel to point out."""
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
                    wanted.append(n)  # points at nothing before or after: the panel says so
            return _written(wanted) if wanted else ""

        piece = _CITATION.sub(one, piece)
        # 「[1][1]」 after a merge is one citation; 「… 있다 .」 left by a dropped marker.
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


#: Venues a writer may put where a work's author goes.
_VENUE_WORDS = (
    "MDPI", "arXiv", "ACM", "IEEE", "ACL Anthology", "ACL", "NeurIPS", "OpenReview",
    "Springer", "Elsevier", "PMLR", "AAAI", "KCI", "DBpia", "RISS", "KISS", "ScienceON",
    "KoreaScience", "earticle", "Nature", "ScienceDirect",
)


def _work_label(source: dict) -> str:
    """How the text names a work: its first author (「Wang 외」), else its title's head."""
    author = str(source.get("author") or "").strip()
    if author:
        names = [a.strip() for a in re.split(r",|;| and | 외", author) if a.strip()]
        first = names[0] if names else author
        # A Latin name by its surname; a Korean name whole.
        surname = first if re.search(r"[가-힣]", first) else first.split()[-1]
        many = len(names) > 1 or author.endswith("외")
        return f"{surname} 외" if many else surname
    title = str(source.get("title") or "").strip()
    head = title.split(":")[0].strip() if ":" in title[3:] else title
    words = head.split()
    return " ".join(words[:5]) if len(words) > 5 else head


def name_works(sections: list[dict], sources: list[dict] | None) -> list[dict]:
    """「MDPI 연구(2026) [9]」 → 「Atomic Contrastive Verification 연구(2026) [9]」: a venue or
    a site is not the author; the work's author (or its title) is named instead."""
    by_number = {int(s.get("ordinal") or i): s for i, s in enumerate(sources or [], 1)}
    if not by_number:
        return sections
    venues = {v.lower() for v in _VENUE_WORDS}
    for source in by_number.values():
        # A site (「mdpi.com」) too; an institute named as publisher may well be the author.
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


#: A reference entry's own start: 「[3]」, 「3.」, 「- 」.
_ENTRY = re.compile(r"^\s{0,3}(?:\[\d{1,3}\]|\d{1,3}[.)]|[-*·])\s")


def drop_restated_references(sections: list[dict], sources: list[dict] | None) -> list[dict]:
    """The writer's own 「참고문헌」 entries that restate a source the server lists anyway,
    taken out; the section goes when nothing else is in it. A research plan carried both
    lists, the writer's half without authors or years (verify1 R-08)."""
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
    """In order: the writer's restated reference list dropped, the works named, then the
    citations put in order."""
    sections = drop_restated_references(sections, sources)
    return cite_order(name_works(sections, sources), list(sources or []))


__all__ = ["cite_order", "cited", "drop_restated_references", "name_works", "tidy"]
