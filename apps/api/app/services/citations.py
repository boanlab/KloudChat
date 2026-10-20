"""Report citations: only cited sources kept, numbered by first citation."""

from __future__ import annotations

import re

#: 「[1]」「[2, 5]」「[3-4]」「[7~9]」「[2; 5]」「［2］」; not link text or footnotes
_CITATION = re.compile(
    r"(?<!\[)[\[［]\s{0,3}(\d{1,3}(?:\s{0,3}[,，;\-–~～]\s{0,3}\d{1,3}){0,9})\s{0,3}[\]］](?!\()")
_REFERENCE_HEADING = re.compile(r"참고\s{0,3}문헌|references|bibliography|인용\s{0,3}문헌", re.I)
_FENCE = "```"


def _numbers(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s{0,3}[,，;]\s{0,3}", spec.strip()):
        if re.fullmatch(r"\d+\s{0,3}[\-–~～]\s{0,3}\d+", part):
            lo, hi = (int(x) for x in re.split(r"\s{0,3}[\-–~～]\s{0,3}", part))
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


#: the app's prose blocks (```callout, ```kpi, ```steps, ```cards): their text cites too
_PROSE_FENCE = re.compile(r"(?:callout|kpi|steps|cards)(?![A-Za-z0-9])", re.I)


def _outside_code(text: str, fn) -> str:
    pieces = text.split(_FENCE)
    for i, piece in enumerate(pieces):
        if i % 2 == 0 or _PROSE_FENCE.match(piece.lstrip(" \t")):
            pieces[i] = fn(piece)
    return _FENCE.join(pieces)


def cited(sections: list[dict]) -> list[int]:
    """Cited numbers in first-cited order, once each."""
    seen: list[int] = []
    for section in sections:
        if not _prose(section):
            continue
        content = str(section.get("content") or "")
        for i, piece in enumerate(content.split(_FENCE)):
            if i % 2 and not _PROSE_FENCE.match(piece.lstrip(" \t")):
                continue
            for m in _CITATION.finditer(piece):
                for n in _numbers(m.group(1)):
                    if n not in seen:
                        seen.append(n)
    return seen


#: a DOI in a link or a field: 「https://doi.org/10.3390/app151910447」
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>?#]+", re.I)


def _identity(source: dict) -> set[str]:
    """What names one work across search results: its DOI, its link, its title."""
    keys: set[str] = set()
    url = str(source.get("url") or "").strip()
    for text in (url, str(source.get("doi") or "")):
        if m := _DOI.search(text):
            keys.add("doi:" + m.group(0).lower().rstrip(".)"))
    if url:
        keys.add("url:" + re.sub(r"^https?://(?:www\.)?", "", url.lower()).rstrip("/"))
    if len(title := _key(str(source.get("title") or ""))) >= 16:
        keys.add("title:" + title)
    return keys


def same_works(sources: list[dict]) -> dict[int, int]:
    """Source number → the earlier number of the same work (one paper found twice, as its DOI
    and its publisher page); attached files stand alone."""
    alias: dict[int, int] = {}
    seen: dict[str, int] = {}
    for index, source in enumerate(sources, 1):
        number = int(source.get("ordinal") or index)
        if source.get("attached"):
            continue
        keys = _identity(source)
        first = next((seen[k] for k in keys if k in seen), None)
        if first is not None and first != number:
            alias[number] = first
        for k in keys:
            seen.setdefault(k, first if first is not None else number)
    return alias


def cite_order(sections: list[dict], sources: list[dict] | None
               ) -> tuple[list[dict], list[dict]]:
    """Uncited sources dropped, one work listed once, rest renumbered by first citation; a
    number no source has is dropped from the text."""
    sources = list(sources or [])
    if not sources:
        return sections, sources
    if any(_REFERENCE_HEADING.search(str(s.get("heading") or "")) for s in sections):
        return sections, sources
    alias = same_works(sources)
    by_number: dict[int, dict] = {}
    for index, source in enumerate(sources, 1):
        number = int(source.get("ordinal") or index)
        if number not in alias:
            by_number.setdefault(number, source)
    written = cited(sections)
    order = list(dict.fromkeys(alias.get(n, n) for n in written if alias.get(n, n) in by_number))
    if not order:
        return sections, sources
    # an attached file is the document's own material: listed even when never cited
    order += [n for n, s in by_number.items() if s.get("attached") and n not in order]
    mapping = {old: new for new, old in enumerate(order, 1)}
    if (all(old == new for old, new in mapping.items()) and len(order) == len(sources)
            and all(n in by_number for n in written)):
        return sections, sources

    def renumber(piece: str) -> str:
        def one(m: re.Match[str]) -> str:
            wanted = [mapping[alias.get(n, n)] for n in _numbers(m.group(1))
                      if alias.get(n, n) in mapping]
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


#: 「[4] Pradhan A., Ortan A. (2025).」: the entry's own number and its first surname
_NUMBERED_ENTRY = re.compile(r"^\s{0,3}\[(\d{1,3})\]")


def _first_surname(source: dict) -> str:
    author = str(source.get("author") or "").strip()
    first = re.split(r",|;| and | 외", author)[0].strip() if author else ""
    if not first or re.search(r"[가-힣]", first):
        return first
    # 「M Malekzadeh」, 「Malekzadeh, M.」: the longest word
    return max(first.split(), key=len) if first.split() else ""


def _restates(entry: str, source: dict) -> bool:
    """Entry naming `source` by its first author and year, its title missing or cut."""
    surname, year = _first_surname(source), str(source.get("year") or "")
    return len(surname) >= 2 and bool(year) and surname.lower() in entry.lower() and year in entry


def drop_restated_references(sections: list[dict], sources: list[dict] | None) -> list[dict]:
    """Writer's 「참고문헌」 entries restating server sources removed (by title, by the
    server's own 「[n]」, or by first author and year); empty section dropped."""
    titles = [k[:30] for k in (_key(str(src.get("title") or "")) for src in sources or [])
              if len(k) >= 8]
    numbers = {int(src.get("ordinal") or i) for i, src in enumerate(sources or [], 1)}
    if not titles and not numbers:
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

        def restated(entry: list[str]) -> bool:
            text = " ".join(entry)
            numbered = _NUMBERED_ENTRY.match(entry[0])
            return (any(t in _key(text) for t in titles)
                    or (numbered is not None and int(numbered.group(1)) in numbers)
                    or any(_restates(text, src) for src in sources or []))

        kept = [e for e in entries if "".join(e).strip() and not restated(e)]
        if not any(_ENTRY.match(e[0]) for e in kept):
            continue
        out.append({**section, "content": "\n".join(line for e in kept for line in e).strip()})
    return out


#: figures that identify a result: 「38.2%」, 「0.71」, 「1,250」, 「41%」; years and small
#: counts left out
_TELLING = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+\s?[%％])(?!\d|[.,]\d)")
#: a sentence end before a citation: 「…했다. 」, a line break; not a decimal point
_SENTENCE_END = re.compile(r"(?<!\bal)[.!?。](?=\s)|\n")


def _has_figure(text: str, figure: str) -> bool:
    """`figure` in `text`; a bare percent 「41%」 only as a percent, never as any 「41」."""
    if figure[-1] in "%％":
        number = figure.rstrip("%％ ")
        tail = r"\s?(?:[%％]|percent|퍼센트)"
        return re.search(rf"(?<![\d.,]){number}{tail}", text, re.I) is not None
    # a comma after it ends a list (「80.53, UAT」), never a thousands group
    return re.search(r"(?<![\d.,])" + re.escape(figure) + r"(?!\d|[.,]\d)", text) is not None


def _distinct(figure: str) -> bool:
    """A figure no second page shares by chance: a decimal or a thousands figure."""
    return figure[-1] not in "%％" and ("." in figure or "," in figure)


#: a work named by author and year: 「Kim & Park(2025)」, 「(Kim & Park, 2025)」,
#: 「Ryu et al.(2025)」, 「Kim과 Park(2025)」; the first surname and the year
_NAMED_WORK = re.compile(
    r"(?<![A-Za-z])(?P<name>[A-Z][a-z][A-Za-z'’-]{0,30})"
    r"(?:\s?(?:&|and|와|과|및|·)\s?[A-Z][a-z][A-Za-z'’-]{0,30})?"
    r"(?:\s?et\s?al\.?|\s?외|\s?등)?\s?(?:\(|,\s?)\s?(?P<year>(?:19|20)\d{2})")


def _names(text: str, surname: str) -> bool:
    found = re.search(rf"(?<![A-Za-z]){re.escape(surname)}(?![A-Za-z])", text or "", re.I)
    return found is not None


def _by_work(source: dict, head: str, surname: str, year: str) -> bool:
    """Whether `source` is the work 「surname (year)」: its author field, else the head of what
    it gave the writer (an attached paper's byline)."""
    author = str(source.get("author") or "")
    if author:
        said_year = str(source.get("year") or "")
        return _names(author, surname) and (not said_year or said_year == year)
    return _names(head[:600], surname) and year in head[:600]


#: a technical term in Korean prose: 「citation-forcing」, 「BM25」, 「multilingual-e5」, 「7B」
_TERM = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)*"
                   r"|\d{1,3}[BM](?![A-Za-z]))(?![A-Za-z0-9])")
#: terms only the attachment has, for a sentence to move to it
_TERMS_TO_MOVE = 3


def _has_term(text: str, term: str) -> bool:
    """`term` as a word in `text`; its hyphens optional (「multilingual-e5large」 in a PDF)."""
    pattern = r"-?\s?".join(re.escape(part) for part in term.split("-"))
    return re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", text, re.I) is not None


def _term_holder(sentence: str, cited: str, attached: dict[int, str]) -> int | None:
    """The attached file a Korean sentence's Latin terms come from: at least three of them
    in it and none only in the cited page (a method taken from the attached paper)."""
    hangul = len(re.findall(r"[가-힣]", sentence))
    if hangul * 3 < len(re.findall(r"[A-Za-z]", sentence)):
        return None
    terms = {t.lower() for t in _TERM.findall(sentence)}
    if any(_has_term(cited, t) and not any(_has_term(x, t) for x in attached.values())
           for t in terms):
        return None
    scored = [(sum(_has_term(text, t) and not _has_term(cited, t) for t in terms), n)
              for n, text in attached.items()]
    best = max(scored, default=(0, None))
    return best[1] if best[0] >= _TERMS_TO_MOVE else None


#: a work's own name, its title's head: 「Face4Rag: Factual Consistency…」 → 「Face4Rag」
_WORK_NAME = re.compile(r"^\s{0,3}([A-Za-z][A-Za-z0-9-]{2,30})\s{0,3}:")


def _work_name(source: dict) -> str:
    m = _WORK_NAME.match(str(source.get("title") or ""))
    return m.group(1) if m and re.search(r"[A-Z].{0,30}[A-Z0-9]", m.group(1)) else ""


def credit_attachments(sections: list[dict], sources: list[dict] | None,
                       texts: dict[int, str]) -> list[dict]:
    """A sentence citing a source that is not the work it names (「Kim & Park(2025)…[1]」 with
    [1] another paper, or 「Face4RAG…[11]」 with [11] not Face4Rag) cites the source that is;
    one citing a web result for figures, or for technical terms, only an attached file holds
    cites that file; one citing a page without its decimal figures cites the one other page
    with them. `texts`: what each source number gave the writer (attachment text, page
    excerpt)."""
    by_number = {int(s.get("ordinal") or i): s for i, s in enumerate(sources or [], 1)}
    attached = [n for n, s in by_number.items() if s.get("attached") and texts.get(n)]
    if not by_number:
        return sections

    def named(sentence: str, number: int) -> int | None:
        """The source the sentence names: the cited one when it is that work, else the one
        other source that is."""
        works = [(m.group("name"), m.group("year")) for m in _NAMED_WORK.finditer(sentence)]
        if not works or number not in by_number:
            return None
        if any(_by_work(by_number[number], texts.get(number, ""), *w) for w in works):
            return number
        if len(works) != 1:
            return None
        holders = [n for n, s in by_number.items() if n != number and (
            (s.get("author") or n in attached) and _by_work(s, texts.get(n, ""), *works[0]))]
        return holders[0] if len(holders) == 1 else None

    def titled(segment: str, number: int) -> int | None:
        """The source whose own name (its title's head, 「Face4Rag」) the words before the
        citation give: the cited one when its name or its page says it, else the one other."""
        said = {n for n, s in by_number.items()
                if (name := _work_name(s)) and _has_term(segment, name)}
        if not said:
            return None
        cited = by_number.get(number)
        if number in said or any(_has_term(texts.get(number, ""), _work_name(by_number[n]))
                                 for n in said) or cited is None:
            return number
        return next(iter(said)) if len(said) == 1 else None

    def fix(piece: str) -> str:
        out, last = [], 0
        for m in _CITATION.finditer(piece):
            numbers = _numbers(m.group(1))
            start = max((e.end() for e in _SENTENCE_END.finditer(piece, last, m.start())),
                        default=last)
            out.append(piece[last:m.start()])
            last = m.end()
            if len(numbers) != 1:
                out.append(m.group(0))
                continue
            if (work := named(piece[start:m.start()], numbers[0])) is not None:
                # the named work, never moved off by a figure it shares with the attachment
                out.append(m.group(0) if work == numbers[0] else f"[{work}]")
                continue
            sentence = piece[start:m.start()]
            if (work := titled(sentence, numbers[0])) is not None:
                out.append(m.group(0) if work == numbers[0] else f"[{work}]")
                continue
            figures = _TELLING.findall(sentence)
            cited = texts.get(numbers[0], "")
            if any(_has_figure(cited, f) for f in figures):
                out.append(m.group(0))
                continue
            holder = None
            if attached and numbers[0] not in attached:
                holder = next((a for a in attached
                               if any(_has_figure(texts[a], f) for f in figures)), None)
                if holder is None and cited:
                    holder = _term_holder(sentence, cited, {a: texts[a] for a in attached})
            if holder is None and cited and (marks := [f for f in figures if _distinct(f)]):
                # a decimal or thousands figure the cited page lacks, held by one other page
                holders = [n for n, text in texts.items()
                           if n != numbers[0] and n in by_number
                           and any(_has_figure(text, f) for f in marks)]
                holder = holders[0] if len(holders) == 1 else None
            out.append(f"[{holder}]" if holder is not None else m.group(0))
        return "".join(out) + piece[last:]

    return [
        {**s, "content": _outside_code(str(s.get("content") or ""), fix)} if _prose(s) else s
        for s in sections
    ]


#: one numbered page in the research block: 「[3] 제목」 up to the next
_SHELF_ENTRY = re.compile(r"^\[(\d{1,3})\] [^\n]*\n출처: ", re.M)


def source_texts(sources: list[dict] | None, research_context: str,
                 attachments: list[str]) -> dict[int, str]:
    """What each source number gave the writer: research pages by their 「[n]」, attached files
    by their 「## name」 block."""
    out: dict[int, str] = {}
    starts = list(_SHELF_ENTRY.finditer(research_context or ""))
    for k, m in enumerate(starts):
        end = starts[k + 1].start() if k + 1 < len(starts) else len(research_context)
        out.setdefault(int(m.group(1)), research_context[m.start():end])
    joined = "\n".join(attachments)
    for i, source in enumerate(sources or [], 1):
        if not source.get("attached"):
            continue
        name = str(source.get("title") or "")
        at = joined.find(f"## {name}\n") if name else -1
        if at < 0:
            continue
        end = joined.find("\n## ", at + 1)
        out[int(source.get("ordinal") or i)] = joined[at:end if end > 0 else len(joined)]
    return out


def tidy(sections: list[dict], sources: list[dict] | None
         ) -> tuple[list[dict], list[dict]]:
    """Drop restated references, name works, order citations."""
    sections = drop_restated_references(sections, sources)
    return cite_order(name_works(sections, sources), list(sources or []))


__all__ = ["cite_order", "cited", "credit_attachments", "drop_restated_references",
           "name_works", "same_works", "source_texts", "tidy"]
