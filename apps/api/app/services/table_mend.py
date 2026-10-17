"""Broken Markdown tables rebuilt by code, and tables the text never names pointed at.

A writer's table often comes out half-formed: a header and rule with no leading pipe
(「할 일 | 담당 | 기한」 / 「---|---|---」), a row ending in a bare 「|」, rows split off by a
blank line or a caption. Later code reads only whole tables (leading pipe, header, rule), so
such a table would be taken apart further — its first row made a header, the rest left as
text. The cells are all there; `rebuild` puts them back under one header. `flatten` is the
fallback for pipe lines no table can take, `cite` points the text at a table it never names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_RULE = re.compile(r"^\s{0,8}\|?\s{0,8}:?-{2,}:?\s{0,8}(?:\|\s{0,8}:?-{2,}:?\s{0,8})*\|?\s*$")
_CAPTION = re.compile(r"^\s{0,3}\*\*표\s?\d{1,3}(?:\.[^*\n]{0,200})?\*\*\s*$")
#: a stray 「표: …」 / 「표 2: …」 label line
_LABEL = re.compile(r"^\s{0,3}표\s?\d{0,3}\s?[:：]\s?\S")
_NOT_ROW = ("#", ">", "!", "```", "**표", "*그림")
_LIST = re.compile(r"^(?:[-*•·]|\d{1,2}[.)])\s+")


def _split(line: str) -> list[str]:
    """Cells of a pipe line; the empty piece an edge pipe leaves is not a cell."""
    text = line.strip()
    parts = [c.strip() for c in text.split("|")]
    if text.startswith("|"):
        parts = parts[1:]
    if text.endswith("|") and parts:
        parts = parts[:-1]
    return parts


def _bare(line: str) -> list[str] | None:
    """Cells of a 「a | b | c」 line without a leading pipe, or None."""
    text = line.strip()
    if not text or text.startswith(_NOT_ROW) or text.startswith("|") or "|" not in text:
        return None
    if " | " not in text and not text.endswith("|"):
        return None
    cells = _split(_LIST.sub("", text))
    return cells if len(cells) >= 2 and sum(1 for c in cells if c) >= 2 else None


def _row(line: str) -> list[str] | None:
    text = line.strip()
    if text.startswith("|"):
        return None if _RULE.match(text) else _split(text)
    return _bare(line)


@dataclass
class _Block:
    """A table (header + rule + rows) or a loose run of pipe rows, by line span."""

    start: int
    end: int
    header: list[str] | None
    rows: list[list[str]] = field(default_factory=list)
    #: written whole (leading pipes, a rule under the header)
    clean: bool = True

    @property
    def width(self) -> int:
        return len(self.header) if self.header is not None else max(len(r) for r in self.rows)


def _blocks(lines: list[str]) -> list[_Block]:
    out: list[_Block] = []
    i, fenced = 0, False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            fenced = not fenced
            i += 1
            continue
        cells = None if fenced else _row(lines[i])
        if cells is None:
            i += 1
            continue
        has_rule = i + 1 < len(lines) and bool(_RULE.match(lines[i + 1]))
        block = _Block(i, i + 1, cells if has_rule else None, [] if has_rule else [cells],
                       clean=has_rule and lines[i].strip().startswith("|"))
        j = i + 2 if has_rule else i + 1
        while j < len(lines):
            row = _row(lines[j])
            if row is None or (j + 1 < len(lines) and _RULE.match(lines[j + 1])):
                break
            if not lines[j].strip().startswith("|"):
                block.clean = False
            block.rows.append(row)
            j += 1
        block.end = j
        out.append(block)
        i = j
    return out


def _fits(rows: list[list[str]], width: int) -> bool:
    """Rows a `width`-column header can hold: none wider, bar empty tail cells."""
    return all(len(r) <= width or not any(r[width:]) for r in rows)


def _line(cells: list[str], width: int) -> str:
    return "| " + " | ".join((cells + [""] * width)[:width]) + " |"


def _between(lines: list[str], a: int, b: int) -> tuple[bool, list[int], list[int]]:
    """Whether only blank, caption and label lines stand in `[a, b)`; their caption and label
    line numbers."""
    captions, labels = [], []
    for k in range(a, b):
        if not lines[k].strip():
            continue
        if _CAPTION.match(lines[k]):
            captions.append(k)
        elif _LABEL.match(lines[k]):
            labels.append(k)
        else:
            return False, [], []
    return True, captions, labels


def _joins(lines: list[str], group: list[_Block], block: _Block) -> bool:
    """Whether `block` belongs to the table `group` started."""
    head = group[0]
    ok, captions, _ = _between(lines, group[-1].end, block.start)
    if not ok or head.header is None:
        return False
    width = head.width
    if block.header is None:
        # loose rows after a table are its rows, unless a caption starts another table
        return not captions and _fits(block.rows, width)
    # a header with no rows, then a same-width table: that table's 「header」 is a row
    return (not any(b.rows for b in group) and len(block.header) == width
            and len(captions) <= 1 and _fits(block.rows, width))


def rebuild(body: str) -> str:
    """`body` with its broken pipe tables rebuilt: a bare header/rule made a table, a header
    with no rows given the same-width rows that follow it (across a caption or blank lines), a
    table's split-off rows put back under it, every row padded to the header's width. Whole
    tables stay as written; a loose run with no header is left to `flatten`."""
    lines = (body or "").split("\n")
    groups: list[list[_Block]] = []
    for block in _blocks(lines):
        if groups and _joins(lines, groups[-1], block):
            groups[-1].append(block)
        else:
            groups.append([block])
    edits: list[tuple[int, int, list[str]]] = []
    for group in groups:
        head = group[0]
        if head.header is None:
            continue
        width = head.width
        if len(group) == 1 and head.clean and all(len(r) == width for r in head.rows):
            continue
        rows = [*head.rows]
        captions: list[int] = []
        labels: list[int] = []
        for before, block in zip(group, group[1:], strict=False):
            _, found, named = _between(lines, before.end, block.start)
            captions += found
            labels += named
            if block.header is not None:
                rows.append(block.header)
            rows.extend(block.rows)
        # a caption (or else a 「표: …」 label) from between the parts goes above the table,
        # unless one stands there already
        k = head.start - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        captioned = k >= 0 and bool(_CAPTION.match(lines[k]))
        labelled = k >= 0 and bool(_LABEL.match(lines[k]))
        lead: list[str] = []
        if captions and not captioned:
            lead = [lines[captions[0]].strip(), ""]
        elif labels and not (captioned or labelled):
            lead = [lines[labels[0]].strip()]
        table = [_line(head.header, width), "|" + "---|" * width,
                 *(_line(r, width) for r in rows)]
        edits.append((head.start, group[-1].end, [*lead, *table]))
    if not edits:
        return body
    out: list[str] = []
    at = 0
    for start, end, new in edits:
        out.extend(lines[at:start])
        out.extend(new)
        at = end
    out.extend(lines[at:])
    return drop_spent_labels(re.sub(r"\n{3,}", "\n\n", "\n".join(out)))


def drop_spent_labels(body: str) -> str:
    """A 「표: …」 label line right above a caption: the caption names the table already."""
    lines = (body or "").split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        if _LABEL.match(line):
            k = i + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and _CAPTION.match(lines[k]):
                continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def flatten(body: str) -> str:
    """Pipe lines no table holds, written as one line of 「 · 」-joined items (the cells kept,
    readable as text): the fallback when no header could take them."""
    lines = (body or "").split("\n")
    out: list[str] = []
    fenced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
        cells = None if fenced else _bare(line)
        near = any(lines[k].strip().startswith("|") or _RULE.match(lines[k])
                   for k in (i - 1, i + 1) if 0 <= k < len(lines))
        if cells is None or near:
            out.append(line)
            continue
        marker = m.group(0) if (m := _LIST.match(line.strip())) else "- "
        out.append(marker + " · ".join(c for c in cells if c))
    return "\n".join(out)


def _closed(word: str) -> bool | None:
    """Whether the last syllable of `word` has a final consonant; None when not Hangul."""
    word = re.sub(r"[\s)\]」』*_.]+$", "", word)
    if not word or not "가" <= word[-1] <= "힣":
        return None
    return (ord(word[-1]) - 0xAC00) % 28 != 0


#: digits whose Korean reading ends closed (일·삼·육·칠·팔·십)
_CLOSED_DIGITS = set("013678")


def _anchor(lines: list[str], name: str) -> tuple[int, str] | None:
    """Line where `name`'s block starts (a table's caption above it, a picture, a diagram's
    opening fence) and the block's title."""
    for i, line in enumerate(lines):
        m = re.match(rf"^\s{{0,3}}(?:\*\*|!\[|\*){re.escape(name)}(?!\d)"
                     r"(?:\.\s?(?P<t>[^*\]\n]{0,80}))?", line)
        if not m:
            continue
        title = (m.group("t") or "").strip()
        if line.lstrip().startswith("*") and not line.lstrip().startswith("**"):
            # a diagram's caption under its fence: the block starts at the opening fence
            k = i - 1
            while k >= 0 and not lines[k].strip():
                k -= 1
            if k >= 0 and lines[k].strip().startswith("```"):
                k -= 1
                while k >= 0 and not lines[k].strip().startswith("```"):
                    k -= 1
                if k >= 0:
                    return k, title
            return i, title
        return i, title
    return None


def cite(body: str, name: str, heading: str = "", *, gaejo: bool = False) -> str:
    """`body` naming `name` (「표 2」, 「그림 1」): 「(표 2)」 at the end of the line right above
    the block when that is a sentence or list item, else a short lead line before it
    (「할 일 목록은 표 2와 같습니다.」; 「…같음」 in a 개조식 document). Unchanged when the block
    is not in `body`."""
    lines = (body or "").split("\n")
    found = _anchor(lines, name)
    if found is None:
        return body
    at, title = found
    k = at - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    above = lines[k].rstrip() if k >= 0 else ""
    if above and not re.match(r"^\s*(?:\||#|>|!\[|\*\*표|\*그림|```|※)", above) \
            and not _RULE.match(above) and not above.endswith((":", "：")):
        m = re.search(r"([.!?。])$", above)
        lines[k] = f"{above[:m.start()]}({name}){m.group(1)}" if m else f"{above}({name})"
        return "\n".join(lines)
    title = re.sub(r"\s?[(（][^)）]{0,40}[)）]\s*$", "", title).strip(" .:")
    title = title or re.sub(r"^\s*(?:[\dⅠ-Ⅻ]{1,3}[.)]|[가-하][.)])\s*", "",
                            heading or "").strip(" *#")
    with_ = "과" if name[-1] in _CLOSED_DIGITS else "와"
    closed = _closed(title)
    topic = f"{title}{'은' if closed else '는'}" if closed is not None else f"{title}은(는)"
    if gaejo:
        lead = f"{topic} {name}{with_} 같음" if title else f"{name} 참조"
    else:
        lead = f"{topic} {name}{with_} 같습니다." if title else f"{name}에 정리했습니다."
    lines[at:at] = [lead, ""]
    return "\n".join(lines)


__all__ = ["cite", "drop_spent_labels", "flatten", "rebuild"]
