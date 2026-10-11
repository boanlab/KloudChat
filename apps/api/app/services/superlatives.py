"""「모든 문항에서 가장 낮다」 read against the table the answer printed, and narrowed by code.

Quality lane Q19: an answer's own table had 교수법 lowest at 공과대학, yet its sentence said
「예술대학은 모든 문항에서 가장 낮은 평균」. The table is right; the sweeping word is not. The
claim is rewritten to the columns where the row really is the lowest (or highest).
"""

from __future__ import annotations

import re

_CELL_NUMBER = re.compile(r"^\**\s{0,2}(-?\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(%|점|명|건)?\**$")
#: Columns that count rather than measure: 「응답 수」, 「인원」, 「건수」.
_COUNT_HEAD = re.compile(r"(?:응답|인원|건|명|사례|표본)\s{0,2}수?$|^\s{0,2}n\s{0,2}$|수$", re.I)
#: 「모든 문항에서」, 「전 문항」, 「모든 항목에서」, 「전체 지표」.
_ALL = re.compile(r"(?:모든|전체|전)\s{0,2}(?P<noun>문항|항목|지표|영역|과목)")
_LOW = re.compile(r"가장\s{0,2}낮|최저")
_HIGH = re.compile(r"가장\s{0,2}높|최고")


def _tables(text: str) -> list[tuple[int, list[str], dict[str, list[float | None]]]]:
    """Each Markdown table as (the line after it, its measured columns, row label → values)."""
    out = []
    lines = text.split("\n")
    k = 0
    while k < len(lines):
        if not lines[k].strip().startswith("|"):
            k += 1
            continue
        block = []
        while k < len(lines) and lines[k].strip().startswith("|"):
            cells = [c.strip() for c in lines[k].strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                block.append(cells)
            k += 1
        if len(block) < 3:
            continue
        head, rows = block[0], block[1:]
        keep = []
        for col in range(1, len(head)):
            values = [_CELL_NUMBER.match(r[col]) if col < len(r) else None for r in rows]
            if all(values) and not _COUNT_HEAD.search(head[col]) and not any(
                    v.group(2) == "%" for v in values):
                keep.append(col)
        if len(keep) < 2:
            continue
        table = {r[0].strip("* "): [float(_CELL_NUMBER.match(r[c]).group(1).replace(",", ""))
                                    for c in keep] for r in rows if r and r[0].strip()}
        out.append((k, [head[c] for c in keep], table))
    return out


def narrow_sweeping_claims(text: str) -> str:
    """A sentence naming a table row and 「모든 문항에서 가장 낮은」 keeps the columns where
    that row is the lowest (highest), named; one true for every column is left as written."""
    if "|" not in (text or "") or not _ALL.search(text or ""):
        return text
    tables = _tables(text)
    if not tables:
        return text
    lines = text.split("\n")
    for at, line in enumerate(lines):
        if line.strip().startswith("|") or not _ALL.search(line):
            continue
        before = [t for t in tables if t[0] <= at]
        if not before:
            continue
        _, columns, table = before[-1]
        for label, values in table.items():
            if label not in line:
                continue
            low, high = _LOW.search(line), _HIGH.search(line)
            if bool(low) == bool(high):
                continue
            pick = min if low else max
            where = [name for i, name in enumerate(columns)
                     if values[i] == pick(row[i] for row in table.values())]
            if not where or len(where) == len(columns):
                continue
            claim = _ALL.search(line[line.index(label):])
            if claim is None:
                continue
            start = line.index(label) + claim.start()
            end = line.index(label) + claim.end()
            noun = claim.group("noun")
            lines[at] = line[:start] + "·".join(w.strip("* ") for w in where) + f" {noun}" \
                + line[end:]
            break
    return "\n".join(lines)


__all__ = ["narrow_sweeping_claims"]
