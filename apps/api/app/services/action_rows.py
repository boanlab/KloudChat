"""Align a document's action and plan rows (「다음 주 계획」, 「실행 항목」) with the source action
items.

Reads the sources' action items (pipe table, or a table flattened to one cell per line), matches
each document row to the item it restates by task words, and sets the owner (「[담당자]」 if the
source names none), the due day (「[기한]」 if none), and adds an owner column where missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.services import placeholders

_TASK_HEADER = re.compile(r"항목|할\s?일|업무|과제|내용|안건|task|action", re.I)
_OWNER_HEADER = re.compile(r"담당|책임|owner", re.I)
_DUE_HEADER = re.compile(r"기한|마감|완료일|납기|due|deadline", re.I)
_DAY = re.compile(r"(?<![\d.])(?:(?P<y>20\d{2})\s{0,2}(?:년|[-./])\s{0,2})?(?P<m>\d{1,2})\s{0,2}"
                  r"(?:월|[-./])\s{0,2}(?P<d>\d{1,2})(?!\d)(?:\s{0,2}일)?")
_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
#: an owner cell that names no one
_NO_ONE = re.compile(
    r"^[\s(（\[]*(?:미정|없음|해당\s{0,2}없음|확인\s{0,2}필요|-|—|–|tbd)?[\s)）\]]*$", re.I)
_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")


@dataclass(frozen=True, slots=True)
class Item:
    task: str
    #: the owner as written; "" when the source names none
    owner: str
    #: the due day as written; "" when the source gives none
    due: str
    source: str = ""

    @property
    def day(self) -> tuple[int, int] | None:
        found = _DAY.search(self.due)
        return (int(found.group("m")), int(found.group("d"))) if found else None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _owner(cell: str) -> str:
    cell = cell.strip(" *`")
    return "" if _NO_ONE.match(cell) else cell


def _due(cell: str) -> str:
    return cell.strip(" *`") if _DAY.search(cell) else ""


def _columns(header: list[str]) -> tuple[int | None, int | None, int | None]:
    owner = next((i for i, h in enumerate(header) if _OWNER_HEADER.search(h)), None)
    due = next((i for i, h in enumerate(header) if _DUE_HEADER.search(h)), None)
    task = next((i for i, h in enumerate(header)
                 if i not in (owner, due) and _TASK_HEADER.search(h)), None)
    return task, owner, due


def _pipe_items(name: str, lines: list[str]) -> list[Item]:
    out: list[Item] = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and _RULE.match(lines[i + 1]):
            header = _cells(lines[i])
            task, owner, due = _columns(header)
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = _cells(lines[i])
                if task is not None and owner is not None and len(row) == len(header):
                    out.append(Item(row[task], _owner(row[owner]),
                                    _due(row[due]) if due is not None else "", name))
                i += 1
            continue
        i += 1
    return out


def _flat_items(name: str, lines: list[str]) -> list[Item]:
    """Table flattened to one cell per line: header labels, then rows of as many lines."""
    out: list[Item] = []
    i = 0
    while i < len(lines):
        for width in (4, 3):
            header = [ln.strip() for ln in lines[i:i + width]]
            if len(header) < width:
                continue
            task, owner, due = _columns(header)
            if task is None or owner is None or due is None:
                continue
            k = i + width
            while k + width <= len(lines):
                row = [ln.strip() for ln in lines[k:k + width]]
                if len(row[owner]) > 16 or not (_DAY.search(row[due]) or _NO_ONE.match(row[due])
                                                or len(row[due]) <= 6):
                    break
                out.append(Item(row[task], _owner(row[owner]), _due(row[due]), name))
                k += width
            if k > i + width:
                i = k - 1
                break
        i += 1
    return out


def items_in(files: list[tuple[str, str]]) -> list[Item]:
    """Every action item of the source files."""
    out: list[Item] = []
    for name, text in files:
        lines = [ln for ln in (text or "").split("\n") if ln.strip()]
        out += _pipe_items(name, lines) + _flat_items(name, lines)
    return [item for item in out if len(_WORD.findall(item.task)) >= 2]


@lru_cache(maxsize=8)
def _cached(blocks: tuple[str, ...]) -> tuple[Item, ...]:
    from app.services import source_conflicts

    return tuple(items_in(source_conflicts.source_files(list(blocks))))


def items_of(blocks: list[str]) -> list[Item]:
    return list(_cached(tuple(blocks)))


def _same(word: str, other: str) -> bool:
    if word == other:
        return True
    return (len(word) >= 3 and len(other) >= 3 and abs(len(word) - len(other)) <= 2
            and (word.startswith(other) or other.startswith(word)))


def restates(task: str, text: str) -> bool:
    """Whether a row's text says the item's task: at least two of its words and three
    quarters of them, a word being the same through a short ending (「마케팅」 「마케팅팀」)."""
    words = {w.lower() for w in _WORD.findall(task)}
    have = {w.lower() for w in _WORD.findall(text)}
    hit = sum(any(_same(w, h) for h in have) for w in words)
    return hit >= 2 and hit >= 0.75 * len(words)


def _matches(row_text: str, items: list[Item]) -> list[Item]:
    return [item for item in items if restates(item.task, row_text)]


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _name(cell: str) -> str:
    """The person or team of an owner cell, without its role in parentheses."""
    return _flat(re.sub(r"[(（][^)）]*[)）]", "", cell))


def _owner_cell(cell: str, found: list[Item], given: str) -> str:
    """Owner cell for a matched row: empty takes the items' single owner (「[담당자]」 if none or
    disagreeing); an unsourced name is replaced; 「미정」 stays if no item names one."""
    named = {_name(i.owner): i.owner for i in found if i.owner}
    sole = next(iter(named.values())) if len(named) == 1 else ""
    written = _name(cell)
    if placeholders.is_blank(cell):
        return cell
    if not written:
        return sole or "[담당자]"
    if _NO_ONE.match(cell):
        return sole or cell
    if written in _flat(given) or not named:
        return cell
    if any(n in written or written in n for n in named):
        return cell
    return sole or "[담당자]"


def _due_cell(cell: str, found: list[Item], given: str) -> str:
    """The day cell a matched row should hold: the cell when its day is one of the items' or
    the request's; else the item's own day, marked, or 「[기한]」 where the items give none."""
    day = _DAY.search(cell)
    if day is None or len(_DAY.findall(cell)) > 1:
        return cell
    mine = (int(day.group("m")), int(day.group("d")))
    if any((int(m.group("m")), int(m.group("d"))) == mine for m in _DAY.finditer(given)):
        return cell
    own = [i for i in found if i.day]
    if any(i.day == mine for i in own):
        return cell
    if not own:
        return "[기한]"
    if len({i.day for i in own}) == 1:
        return f"{own[0].due}(원 기한, 확인 필요)"
    return cell


def _is_day_cell(cell: str) -> bool:
    """A cell that is only a day (with its weekday): 「11월 17일(화)」, 「2026-11-17」."""
    if not _DAY.search(cell):
        return False
    plain = _DAY.sub("", cell)
    return not re.sub(r"[()（）\s]|[월화수목금토일]요일|[월화수목금토일]", "", plain)


def fix(text: str, items: list[Item], given: str = "") -> str:
    """`text` with rows restating a source item given its owner and due day; adds an owner column
    where missing."""
    if not items or "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not (lines[i].lstrip().startswith("|") and i + 1 < len(lines)
                and _RULE.match(lines[i + 1])):
            out.append(lines[i])
            i += 1
            continue
        end = i + 2
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            end += 1
        out.extend(_table(lines[i:end], items, given))
        i = end
    return "\n".join(out)


def _table(block: list[str], items: list[Item], given: str) -> list[str]:
    header = _cells(block[0])
    owner = next((k for k, h in enumerate(header) if _OWNER_HEADER.search(h)), None)
    rows = [(n, _cells(line)) for n, line in enumerate(block[2:], 2)
            if len(_cells(line)) == len(header)]

    def day_cell(k: int, row: list[str]) -> bool:
        return k != owner and (_is_day_cell(row[k]) or (
            bool(_DUE_HEADER.search(header[k])) and bool(_DAY.search(row[k]))))

    matched: dict[int, list[Item]] = {}
    for n, row in rows:
        task = " ".join(c for k, c in enumerate(row) if k != owner and not day_cell(k, row))
        if found := _matches(task, items):
            matched[n] = found
    if not matched:
        return block
    out = list(block)
    adds_owner = owner is None and len(rows) >= 2 and any(
        i.owner for found in matched.values() for i in found)
    for n, row in rows:
        found = matched.get(n)
        cells = list(row)
        if found:
            if owner is not None:
                cells[owner] = _owner_cell(cells[owner], found, given)
            for k in range(len(cells)):
                if day_cell(k, row):
                    cells[k] = _due_cell(cells[k], found, given)
        if adds_owner:
            cells.append(_owner_cell("", found, given) if found else "-")
        if cells != row:
            lead = block[n][: len(block[n]) - len(block[n].lstrip())]
            out[n] = lead + "| " + " | ".join(cells) + " |"
    if adds_owner:
        out[0] = block[0].rstrip().rstrip("|").rstrip() + " | 담당 |"
        out[1] = block[1].rstrip().rstrip("|").rstrip() + " | --- |"
    return out


__all__ = ["Item", "fix", "items_in", "items_of", "restates"]
