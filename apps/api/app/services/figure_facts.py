"""A drawn figure checked against the words it was drawn from, before it is kept.

A comparison figure names each option's values in its boxes (「중복 방지: 충족」 under 안1). The
model that draws it reads a one-line description, not the section's table, and can say what
the table does not: 안1 「충족」 where the table says 「중복 예약 월 6건 발생」, 안2 「자료 없음」
where the table says 「120만 원」. Code reads the section's Markdown tables, finds the cell for
the box's option and criterion, and calls the figure contradicted when the two disagree; the
caller then leaves the figure out. A highlighted box (`:::hot`) on an option other than the one
the document recommends loses its highlight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: An option's name: 「안1」 「1안」 「A안」 「대안 2」 「제2안」.
_OPTION = re.compile(r"(?:대안\s{0,2}|안\s{0,2})(\d{1,2})(?!\d)"
                     r"|제?\s{0,2}(\d{1,2}|[A-D])\s{0,2}안(?![가-힣])")
_SUBGRAPH = re.compile(r'^\s*subgraph\s+(\w+)\s*(?:\[\s*"?([^"\]]*)"?\s*\])?', re.M)
_NODE = re.compile(r"\b([a-z][\w]*)\s*(?:\[\(|\(\[|\[|\(|\{)\s*\"?([^\]\)\}\"]{1,80})\"?\s*"
                   r"(?:\)\]|\]\)|\]|\)|\})(:::hot)?")
_VERDICT_YES = re.compile(r"^(?:충족|만족|가능|있음|예|O|○|◯|✓|✔|해당|적합)$", re.I)
_UNKNOWN = re.compile(r"자료\s{0,2}없음|미정|확인\s{0,2}필요|알\s{0,2}수\s{0,2}없음"
                      r"|정보\s{0,2}없음|N/?A|-$|—$")
_NEGATIVE = re.compile(r"발생|없음|미흡|불가|부족|미충족|불충족|검토\s{0,2}필요|확인\s{0,2}필요"
                       r"|자료\s{0,2}없음|위험|X$|×")
_NUMBER = re.compile(r"\d[\d,.]*")
_RECOMMEND = re.compile(
    r"((?:대안\s{0,2}|안\s{0,2})\d{1,2}|제?\s{0,2}(?:\d{1,2}|[A-D])\s{0,2}안)\s{0,2}(?:을|를|이|가)?\s{0,2}"
    r"(?:권고|추천|선택|채택|권장)")


class Contradicted(ValueError):
    """A figure that says what the words it was drawn from do not."""


@dataclass(slots=True)
class Check:
    source: str
    problems: list[str]


def option_of(text: str) -> str:
    """The option a label or title names, normalised (「안2」), or ''."""
    m = _OPTION.search(text or "")
    if not m:
        return ""
    return f"안{m.group(1) or m.group(2)}"


def _flat(text: str) -> str:
    return re.sub(r"[\s()（）\[\]·*_`]", "", text or "")


def _tables(text: str) -> list[list[list[str]]]:
    out: list[list[list[str]]] = []
    rows: list[list[str]] = []
    for line in (text or "").split("\n") + [""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append(cells)
            continue
        if len(rows) >= 2:
            out.append(rows)
        rows = []
    return out


def table_cells(text: str) -> dict[tuple[str, str], str]:
    """(option, criterion) → cell, from every table whose header or first column names options."""
    cells: dict[tuple[str, str], str] = {}
    for rows in _tables(text):
        head = rows[0]
        options = [option_of(c) for c in head]
        if sum(1 for o in options if o) >= 2:
            # Options across the top, criteria down the first column.
            for row in rows[1:]:
                if not row:
                    continue
                criterion = _flat(row[0])
                for j, option in enumerate(options):
                    if option and j < len(row) and criterion:
                        cells[(option, criterion)] = row[j]
            continue
        firsts = [option_of(r[0]) if r else "" for r in rows[1:]]
        if sum(1 for o in firsts if o) >= 2:
            criteria = [_flat(c) for c in head]
            for row, option in zip(rows[1:], firsts, strict=False):
                for j, criterion in enumerate(criteria):
                    if option and j and j < len(row) and criterion:
                        cells[(option, criterion)] = row[j]
    return cells


def _cell_for(cells: dict[tuple[str, str], str], option: str, criterion: str) -> str | None:
    want = _flat(criterion)
    if len(want) < 2:
        return None
    for (o, c), value in cells.items():
        if o == option and (want in c or c in want):
            return value
    return None


def _contradicts(said: str, cell: str) -> bool:
    said, cell = said.strip(), cell.strip()
    if not said or not cell or _flat(said) == _flat(cell) or _flat(said) in _flat(cell):
        return False
    if _UNKNOWN.search(said):
        # 「자료 없음」 in the box where the table gives a value.
        return not _UNKNOWN.search(cell)
    if _VERDICT_YES.match(said):
        return bool(_NEGATIVE.search(cell))
    numbers_said = {n.replace(",", "") for n in _NUMBER.findall(said)}
    numbers_cell = {n.replace(",", "") for n in _NUMBER.findall(cell)}
    if numbers_said and numbers_cell:
        return not numbers_said <= numbers_cell
    if numbers_said and _UNKNOWN.search(cell):
        return True
    return False


def recommended(document: str) -> str:
    """The option the document recommends (「안2를 권고합니다」), or ''."""
    found = {option_of(m.group(1)) for m in _RECOMMEND.finditer(document or "")}
    found.discard("")
    return found.pop() if len(found) == 1 else ""


def check(source: str, *, context: str, document: str = "") -> Check:
    """Problems with `source` against the tables in `context`; the source with a highlight on
    a non-recommended option taken off."""
    problems: list[str] = []
    titles = {m.group(1): (m.group(2) or m.group(1)) for m in _SUBGRAPH.finditer(source or "")}
    # Which subgraph each line sits in.
    owner: dict[str, str] = {}
    stack: list[str] = []
    for line in (source or "").split("\n"):
        sub = _SUBGRAPH.match(line)
        if sub:
            stack.append(sub.group(1))
            continue
        if re.match(r"^\s*end\s*$", line):
            if stack:
                stack.pop()
            continue
        for node in _NODE.finditer(line):
            if stack and node.group(1) not in titles:
                owner.setdefault(node.group(1), stack[-1])
    cells = table_cells(context)
    hot_options: list[str] = []
    for node in _NODE.finditer(source or ""):
        node_id, label, hot = node.group(1), node.group(2).strip(), node.group(3)
        if node_id in titles:
            continue
        option = option_of(label) or option_of(titles.get(owner.get(node_id, ""), ""))
        if hot and option:
            hot_options.append(option)
        if not option or ":" not in label:
            continue
        criterion, _, said = label.partition(":")
        cell = _cell_for(cells, option, criterion)
        if cell is not None and _contradicts(said, cell):
            problems.append(f"{option} {criterion.strip()}: 그림 「{said.strip()}」, 표 「{cell}」")
    chosen = recommended(document)
    if chosen and any(o != chosen for o in hot_options):
        source = source.replace(":::hot", "")
    return Check(source, problems)


__all__ = ["Check", "Contradicted", "check", "option_of", "recommended", "table_cells"]
