"""Tables of a revised document: one copy each, captions over tables, no block tokens left."""

from __future__ import annotations

import re

#: table, also one indented under a list item
TABLE = re.compile(
    r"(?m)^[ \t]{0,12}\|.+\|[ \t]*\n[ \t]{0,12}\|[\s:|-]+\|[ \t]*\n"
    r"(?:^[ \t]{0,12}\|.+\|[ \t]*(?:\n|$))+"
)
#: caption line: 「표: …」, 「**표 3. …**」, 「- 표: …」, 「<표 2> …」
CAPTION = re.compile(r"^\s{0,12}(?:[-*+]\s{1,4})?(?:\*\*)?\s?<?\s?표\s?\d{0,3}\s?[.:：>]")
#: block token the reader cannot resolve: 〔kpi〕, 〔cards〕, 〔callout〕
_BLOCK_TOKEN = re.compile(r"〔\s?[A-Za-z][A-Za-z ]{0,15}\s?〕")
#: drawn figure in a part: picture, mermaid, figure mark
_FIGURE = re.compile(r"!\[|```mermaid|\[\[\s*그림")
_SEPARATOR = re.compile(r"^\s{0,12}\|?\s{0,4}:?-{2,}")


def _key(cell: str) -> str:
    text = re.sub(r"[\s*\[\]()（）,%]", "", cell)
    return re.sub(r"(?<=\d)(?:천원|만원|원|명|점|건|개|회|시간)$", "", text)


def _rows(table: str) -> list[list[str]]:
    lines = [ln.strip() for ln in table.strip().split("\n") if ln.strip()]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in lines
            if not _SEPARATOR.match(ln)]


def cells(table: str) -> set[tuple[str, str]]:
    """`(row label, cell)` pairs of the body rows, normalised."""
    out: set[tuple[str, str]] = set()
    for row in _rows(table)[1:]:
        label = _key(row[0]) if row else ""
        for cell in row[1:]:
            if (value := _key(cell)) and value not in {"-", "—"}:
                out.add((label, value))
    return out


def _labels(table: str) -> set[str]:
    return {_key(row[0]) for row in _rows(table)[1:] if row and _key(row[0])}


def _header(table: str) -> str:
    rows = _rows(table)
    return "|".join(_key(c) for c in rows[0]) if rows else ""


def _share(a: set, b: set) -> float:
    return len(a & b) / len(a) if a else 0.0


def same_table(a: str, b: str) -> bool:
    """Two copies of one table: one's cells inside the other's, or the same header and rows
    with most cells alike (a placeholder respelt, a cell shortened)."""
    ca, cb = cells(a), cells(b)
    if len(ca) < 2 or len(cb) < 2:
        return False
    if _share(ca, cb) >= 0.8 or _share(cb, ca) >= 0.8:
        return True
    la, lb = _labels(a), _labels(b)
    return (_header(a) == _header(b) and len(la) >= 2 and _share(la, lb) >= 0.8
            and _share(lb, la) >= 0.8 and _share(ca, cb) >= 0.5)


def _blocks(text: str) -> list[tuple[int, int]]:
    """Table spans with the caption line above, when there is one."""
    spans = []
    for m in TABLE.finditer(text):
        start = m.start()
        before = text[:start].rstrip("\n")
        line_start = before.rfind("\n") + 1
        if before and CAPTION.match(before[line_start:]):
            start = line_start
        spans.append((start, m.end()))
    return spans


def _same_rows(a: str, b: str) -> bool:
    return _rows(a) == _rows(b)


def drop_superseded(sections: list[dict], rank: dict[int, int],
                    before: list[str] | None = None) -> list[dict]:
    """One copy of each table: the fuller one, then the higher-ranked section's (revised
    parts above the rest), then the first. The others go with their captions. Two tables
    both in the document `before` the edit stay: only a copy the edit made goes."""
    old = [text[s:e] for text in before or [] for s, e in _blocks(text)]

    def kept_before(table: str) -> bool:
        return any(_same_rows(table, t) for t in old)

    found: list[tuple[int, int, int, str]] = []  # (section, start, end, table)
    for index, sec in enumerate(sections):
        text = str(sec.get("content") or "")
        for start, end in _blocks(text):
            found.append((index, start, end, text[start:end]))
    drop: set[tuple[int, int]] = set()
    for i, (si, ai, _, ta) in enumerate(found):
        for j, (sj, aj, _, tb) in enumerate(found):
            if i == j or (sj, aj) in drop or not same_table(ta, tb):
                continue
            if kept_before(ta) and kept_before(tb):
                continue  # neither is this edit's: the person named neither
            ca, cb = cells(ta), cells(tb)
            if _share(ca, cb) >= 0.8 and len(cb) > len(ca):
                loses = True  # the other is this one plus more
            elif _share(cb, ca) >= 0.8 and len(ca) > len(cb):
                loses = False
            else:
                loses = (rank.get(sj, 0), -j) > (rank.get(si, 0), -i)
            if loses:
                drop.add((si, ai))
                break
    if not drop:
        return sections
    out = []
    for index, sec in enumerate(sections):
        text = str(sec.get("content") or "")
        spans = [(s, e) for si, s, e, _ in found if si == index and (si, s) in drop]
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + text[end:]
        out.append({**sec, "content": re.sub(r"\n{3,}", "\n\n", text).strip("\n") + "\n"}
                   if spans else sec)
    return out


def drop_orphan_captions(body: str) -> str:
    """Caption line with no table under it removed."""
    lines = (body or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        if CAPTION.match(line) and not line.strip().startswith("|"):
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines) or not lines[k].strip().startswith("|"):
                continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def drop_block_tokens(body: str) -> str:
    """Sentence or list item naming a block by its token (「〔kpi〕로 요약함」) removed; so is
    one pointing at 〔표〕 or 〔그림〕 in a part that has no table or figure."""
    tokens = [_BLOCK_TOKEN.pattern]
    if "〔표〕" in (body or "") and not TABLE.search(body):
        tokens.append("〔표〕")
    if "〔그림〕" in (body or "") and not _FIGURE.search(body):
        tokens.append("〔그림〕")
    rx = re.compile("|".join(tokens))
    if not rx.search(body or ""):
        return body
    from app.services import units

    out = []
    for line in body.split("\n"):
        if not rx.search(line) or line.lstrip().startswith(("|", "```")):
            out.append(line)
            continue
        lead = re.match(r"\s{0,24}(?:[-*+]\s{1,4})?", line).group(0)
        kept = [s for s in units.SENTENCE_BREAK.split(line[len(lead):])
                if s.strip() and not rx.search(s)]
        if kept:
            out.append(lead + " ".join(k.strip() for k in kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


_ITEM_CAPTION = re.compile(r"^\s{0,24}[-*+]\s{1,4}((?:\*\*)?\s?표\s?\d{0,3}\s?[.:：].*)$")


def outdent_tables(body: str) -> str:
    """Table indented under a list item set flush, with its caption, between blank lines."""
    lines = (body or "").split("\n")
    out: list[str] = []
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if fenced or not (line.strip().startswith("|") and line[:1] in " \t"):
            out.append(line)
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        block = [row.strip() for row in lines[i:j]]
        if len(block) < 2 or not _SEPARATOR.match(block[1]):
            out.extend(lines[i:j])
            i = j
            continue
        k = len(out) - 1
        while k >= 0 and not out[k].strip():
            k -= 1
        if k >= 0 and (caption := _ITEM_CAPTION.match(out[k])):
            out[k] = caption.group(1).strip()
        if out and out[-1].strip():
            out.append("")
        out.extend(block)
        if j < len(lines) and lines[j].strip():
            out.append("")
        i = j
    return "\n".join(out)


def tidy(sections: list[dict], rank: dict[int, int],
         before: list[str] | None = None) -> list[dict]:
    """A revised document's tables: flush, one copy each (none of two the document had
    `before` the edit), every caption over a table, no block token left in the prose."""
    out = []
    for sec in sections:
        if str(sec.get("format") or "markdown") != "markdown":
            out.append(sec)
            continue
        out.append({**sec, "content": outdent_tables(str(sec.get("content") or ""))})
    out = drop_superseded(out, rank, before)
    return [
        sec if str(sec.get("format") or "markdown") != "markdown"
        else {**sec, "content": drop_block_tokens(
            drop_orphan_captions(str(sec.get("content") or "")))}
        for sec in out
    ]
