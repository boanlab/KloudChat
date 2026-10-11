"""Figure columns of tables (flush right); mirrors web figureColumns.ts."""

from __future__ import annotations

import re

from app.services import placeholders

#: money/count header; figures flush right
NUMERIC_HEAD = re.compile(
    r"금액|단가|수량|합계|소계|총액|총계|예산|비용|가격|요금|점수|비율|증감|%"
    r"|(?:^|[\s(（/])(?:천|만|백만|억)?원(?:[)）\s]|$)"
)
#: one figure with unit/citation marks: `51,810,000원`, `12.5%`, `17,280,000[2]`
NUMERIC_CELL = re.compile(
    r"^[-+−△▲▼]?\s*\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%p?|원|천원|만원|백만원|억원|명|개|건|회|식|부|대|쪽|MM|M/M|개월|년|일|시간|분|초"
    r"|점|배|kg|km|ms|GB|MB|TB)?"
    r"(?:\s*\[\d+\])*$"
)
#: dates are labels, not amounts
DATE_CELL = re.compile(r"^(?:19|20)\d{2}[.\-/]")
#: no-figure cells; neutral for numeric columns
NO_FIGURE = frozenset(
    {"", "-", "–", "—", "·", "자료 없음", "해당 없음", "미정", "확인 필요", "조건 미확인", "(기입)"}
)

_TAG = re.compile(r"<[^>]{0,2000}>")
_ENTITY = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}


def plain(text: str) -> str:
    """Cell text without Markdown markers and HTML tags."""
    text = _TAG.sub("", text or "")
    for entity, char in _ENTITY.items():
        text = text.replace(entity, char)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    return re.sub(r"`(\S(?:[^`]*\S)?)`", r"\1", text).replace(" ", " ").strip()


def figure_columns(rows: list[list[str]]) -> set[int]:
    """Flush-right columns: money/count header over a figure, or all-figure body."""
    if len(rows) < 2:
        return set()
    head, body = rows[0], rows[1:]
    out: set[int] = set()
    for column in range(max(len(row) for row in rows)):
        cells = [
            plain(row[column])
            for row in body
            if column < len(row)
            and plain(row[column]) not in NO_FIGURE
            and not placeholders.is_blank(row[column])
        ]
        figures = sum(1 for cell in cells if NUMERIC_CELL.match(cell) and not DATE_CELL.match(cell))
        if not figures:
            continue
        named = column < len(head) and NUMERIC_HEAD.search(plain(head[column]))
        if named and figures * 2 >= len(cells):
            out.add(column)
        elif column and figures == len(cells):
            out.add(column)
    return out


#: total row first cell: 「합계」 「총계」 「총액」 「계」
TOTAL_LABEL = re.compile(r"^(?:합\s?계|총\s?계|총\s?액|계)(?:\s*\(.{0,30}\))?$")


def is_total(row: list[str]) -> bool:
    """Whether a body row is the total row."""
    return bool(row) and bool(TOTAL_LABEL.match(plain(row[0])))


_TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.S | re.I)
_CELL = re.compile(r"<(t[hd])\b([^>]*)>(.*?)</\1\s*>", re.S | re.I)
_CLASS = re.compile(r'\bclass="([^"]*)"', re.I)


def _with_class(attrs: str, name: str) -> str:
    found = _CLASS.search(attrs)
    if found:
        classes = found.group(1).split()
        if name in classes:
            return attrs
        joined = " ".join([*classes, name])
        return attrs[: found.start()] + f'class="{joined}"' + attrs[found.end() :]
    return f'{attrs} class="{name}"'


def _mark_table(table: str) -> str:
    rows = [
        [cell.group(3) for cell in _CELL.finditer(row.group(1))] for row in _ROW.finditer(table)
    ]
    # spanned cells and head field tables left as is
    opening = table[: table.index(">") + 1]
    if not rows or "tb-fields" in opening or re.search(r"\b(?:col|row)span=", table, re.I):
        return table
    columns = figure_columns(rows)
    at = iter(range(len(rows)))

    def row(found: re.Match[str]) -> str:
        position = next(at)
        index = iter(range(10_000))

        def cell(match: re.Match[str]) -> str:
            tag, attrs, inner = match.group(1), match.group(2), match.group(3)
            if next(index) in columns:
                attrs = _with_class(attrs, "fig")
            return f"<{tag}{attrs}>{inner}</{tag}>"

        opening = found.group(0)[: found.start(1) - found.start(0)]
        if position and is_total(rows[position]):
            opening = "<tr" + _with_class(opening[3:-1], "total") + ">"
        return (
            opening
            + _CELL.sub(cell, found.group(1))
            + found.group(0)[found.end(1) - found.start(0) :]
        )

    return _ROW.sub(row, table)


def mark_html(html: str) -> str:
    """All tables: figure cells `class="fig"`, total row `class="total"`."""
    return _TABLE.sub(lambda found: _mark_table(found.group(0)), html or "")
