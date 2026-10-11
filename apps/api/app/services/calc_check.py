"""Slides read against the values code computed for them (`calc.computed_values`).

The lecture deck of the quality lane showed 「FCFS 평균 대기시간 = 2」 and 「RR(q=2) 평균 9」 while
the procedure gives 4.75 and 5.0, and its quality step said 「문제 없음」. Here a slide's number is
compared with the computed value it names — the row (「FCFS」) from the line itself or from the
slide's title, the value (「평균 대기시간」) from the line — and a different one is set back.
A line that names two rows, or no value the code printed, is left alone: only an unambiguous
claim is corrected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: A number as a slide writes it; 「P1」 and 「q=2」's letters are not numbers.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])-?\d{1,15}(?:\.\d{1,10})?(?![\d])")
#: A number counting something else: 「2배」, 「30%」, 「3장」, 「2번」.
_OTHER_QUANTITY = re.compile(r"\s{0,2}(?:배|%|퍼센트|장|개|번|명|차|회|위|등|단계|가지|줄|쪽)")
_PAREN = re.compile(r"[(（][^()（）]{0,80}[)）]")
#: What may sit between the letters of a name as written: spaces, brackets, 「=」.
_GAP = r"[^0-9A-Za-z가-힣]{0,3}"


@dataclass(frozen=True, slots=True)
class Stated:
    """One value a program printed: 「FCFS | 평균 대기시간 = 4.75」 → row FCFS, label, 4.75."""

    row: str
    label: str
    text: str
    value: float


def stated_values(block: str) -> list[Stated]:
    """The single-number values of a computed block, by row and label."""
    out: list[Stated] = []
    for line in (block or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            row, named = cells[0], [c for c in cells[1:] if "=" in c]
        else:
            row, named = "", [line]
        for cell in named:
            label, value = cell.split("=", 1)
            numbers = _NUMBER.findall(value)
            if len(numbers) == 1 and label.strip():
                out.append(Stated(row, label.strip(), numbers[0], float(numbers[0])))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def _pattern(name: str) -> re.Pattern[str] | None:
    """`name` as written with any spacing or brackets inside it, not inside a longer word."""
    if len(name) < 2:
        return None
    body = _GAP.join(re.escape(ch) for ch in name)
    head = r"(?<![A-Za-z])" if re.match(r"[a-z]", name) else ""
    tail = r"(?![A-Za-z0-9])" if re.search(r"[a-z0-9]$", name) else ""
    return re.compile(head + body + tail, re.I)


class _Names:
    """How each row and each label of a computed block may be written on a slide."""

    def __init__(self, stated: list[Stated]) -> None:
        rows = list(dict.fromkeys(s.row for s in stated if s.row))
        cores = [_norm(_PAREN.sub("", r)) for r in rows]
        self.rows: dict[str, list[re.Pattern[str]]] = {}
        for row, core in zip(rows, cores, strict=True):
            names = {_norm(row)}
            # 「SJF(비선점)」 is also written 「SJF」 — unless 「RR(q=2)」 and 「RR(q=4)」 share it.
            if core and cores.count(core) == 1:
                names.add(core)
            self.rows[row] = [p for n in sorted(names, key=len, reverse=True)
                              if (p := _pattern(n))]
        self.labels: dict[tuple[str, str], list[re.Pattern[str]]] = {}
        for s in stated:
            full = _norm(s.label)
            names = {full}
            # 「평균 대기」 for 「평균 대기시간」, when no other value of the row starts alike.
            prefix = full[:4]
            if len(full) > 4 and re.fullmatch(r"[가-힣]{4}", prefix) and sum(
                1 for o in stated if o.row == s.row and _norm(o.label)[:4] == prefix
            ) == 1:
                names.add(prefix)
            self.labels[(s.row, s.label)] = [p for n in names if (p := _pattern(n))]

    def rows_in(self, text: str) -> list[tuple[int, int, str]]:
        """`(start, end, row)` of each row named in `text`, left to right, overlaps dropped."""
        hits = sorted(
            ((m.start(), m.end(), row) for row, patterns in self.rows.items()
             for p in patterns for m in p.finditer(text)),
            key=lambda h: (h[0], -(h[1] - h[0])),
        )
        out: list[tuple[int, int, str]] = []
        for hit in hits:
            if out and hit[0] < out[-1][1]:
                continue
            out.append(hit)
        return out

    def label_at(self, row: str, label: str, text: str) -> re.Match[str] | None:
        for pattern in self.labels.get((row, label), []):
            if m := pattern.search(text):
                return m
        return None


def _same(written: str, value: float) -> bool:
    """`written` is `value` as rounded to the places it shows (「4.8」 for 4.75, 「1592」 for
    1591.5). A small value is not rounded to a whole number: 「5」 is not 4.5."""
    places = len(written.split(".", 1)[1]) if "." in written else 0
    if not places and abs(value) < 100:
        return abs(float(written) - value) < 1e-9
    return abs(float(written) - value) <= 0.5 * 10 ** -places + 1e-9


def _first_number(text: str, start: int, end: int) -> re.Match[str] | None:
    for m in _NUMBER.finditer(text, start, end):
        if not _OTHER_QUANTITY.match(text, m.end()):
            return m
    return None


@dataclass(slots=True)
class Clash:
    start: int
    end: int
    written: str
    stated: Stated


def _clashes(text: str, context: str, names: _Names, stated: list[Stated]) -> list[Clash]:
    """Numbers in `text` that differ from the computed value they name."""
    hits = names.rows_in(text)
    if hits:
        segments = [(h[1], hits[i + 1][0] if i + 1 < len(hits) else len(text), h[2])
                    for i, h in enumerate(hits)]
    else:
        # A line without a row name speaks of the slide's row, when its title names one.
        in_title = {row for _, _, row in names.rows_in(context)}
        if len(in_title) == 1:
            segments = [(0, len(text), in_title.pop())]
        else:
            # Values with no row (「평균 = 4.75」) are named by their label alone.
            segments = [(0, len(text), "")] if any(not s.row for s in stated) else []
    out: list[Clash] = []
    for start, end, row in segments:
        mine = [s for s in stated if s.row == row]
        piece = text[start:end]
        found = [(m, s) for s in mine if (m := names.label_at(row, s.label, piece))]
        if found:
            m, s = min(found, key=lambda pair: pair[0].start())
            following = [other.start() for other, _ in found if other.start() > m.start()]
            number = _first_number(text, start + m.end(),
                                   start + min(following, default=len(piece)))
            if number is None:
                # 「… 퀀텀 4) 4.5가 평균 대기시간입니다」: the value before its label.
                number = _first_number(text, start, start + m.start())
        else:
            # 「FCFS 4.75, SJF 4.00 … 평균 대기시간입니다」: the label once for the whole line.
            named = [s for s in mine if names.label_at(row, s.label, text)]
            if len(named) != 1:
                continue
            s, number = named[0], _first_number(text, start, end)
        if number and not _same(number.group(0), s.value):
            out.append(Clash(number.start(), number.end(), number.group(0), s))
    return out


def _mend(text: str, clashes: list[Clash]) -> str:
    for clash in sorted(clashes, key=lambda c: c.start, reverse=True):
        text = text[: clash.start] + clash.stated.text + text[clash.end :]
    return text


#: Where a sentence of the speaker notes ends; a decimal point is not one.
_SENTENCE_END = re.compile(r"(?<=[.!?。])(\s{1,8})")


def _check_text(text: str, context: str, names: _Names, stated: list[Stated],
                found: list[str]) -> str:
    out = []
    for line in (text or "").splitlines(keepends=True):
        # Split keeping the separators (odd items), so the line comes back whole.
        for i, piece in enumerate(_SENTENCE_END.split(line)):
            if i % 2 or not piece:
                out.append(piece)
                continue
            clashes = _clashes(piece, context, names, stated)
            found.extend(f"「{c.written}」 → {c.stated.text} ({c.stated.row} {c.stated.label})"
                         for c in clashes)
            out.append(_mend(piece, clashes))
    return "".join(out)


def _check_rows(rows: list[list[Any]], names: _Names, stated: list[Stated],
                found: list[str]) -> list[list[Any]]:
    """A table with rows or columns named for the computed rows, values for its labels."""
    if len(rows) < 2:
        return rows
    header = [str(c) for c in rows[0]]
    out = [list(rows[0])]

    def one_row(cell: str) -> str | None:
        named = {row for _, _, row in names.rows_in(cell)}
        return named.pop() if len(named) == 1 else None

    def label_of(row: str, cell: str) -> Stated | None:
        matched = [s for s in stated if s.row == row and names.label_at(row, s.label, cell)]
        return matched[0] if len(matched) == 1 else None

    by_column = [one_row(h) for h in header]
    for raw in rows[1:]:
        cells = [str(c) for c in raw]
        own = one_row(cells[0]) if cells else None
        fixed = list(raw)
        for j in range(1, len(cells)):
            if own is not None:
                s = label_of(own, header[j]) if j < len(header) else None
            elif j < len(by_column) and by_column[j] is not None:
                s = label_of(by_column[j], cells[0])
            else:
                s = None
            if s is None:
                continue
            number = _first_number(cells[j], 0, len(cells[j]))
            if number is None:
                # 「최소」 where the code printed the value.
                if cells[j].strip():
                    found.append(f"「{cells[j]}」 → {s.text} ({s.row} {s.label})")
                    fixed[j] = s.text
            elif not _same(number.group(0), s.value):
                found.append(f"「{number.group(0)}」 → {s.text} ({s.row} {s.label})")
                fixed[j] = cells[j][: number.start()] + s.text + cells[j][number.end():]
        out.append(fixed)
    return out


#: Slide fields that hold words but are not the slide's own claims.
_SKIP = frozenset({"id", "layout", "title", "accent", "image", "diagram", "textScale", "number",
                   "rows", "notes"})


def check_slides(slides: list[dict[str, Any]], block: str) -> tuple[list[int], list[str]]:
    """Mends every slide number that differs from the computed value it names.

    Returns `(touched slide indices, what was corrected)`."""
    stated = stated_values(block)
    if not stated:
        return [], []
    names = _Names(stated)
    touched: list[int] = []
    corrected: list[str] = []
    for index, slide in enumerate(slides):
        found: list[str] = []
        title = str(slide.get("title") or "")

        def walk(value: Any, context: str, inner: bool = False,
                 found: list[str] = found) -> Any:
            if isinstance(value, str):
                return _check_text(value, context, names, stated, found)
            if isinstance(value, list):
                if not inner:
                    return [walk(v, context, True) for v in value]
                # A pair (「카드 제목」, 「설명」): each half reads the other as context.
                return [walk(v, " ".join([context, *(str(o) for j, o in enumerate(value)
                                                    if j != i and isinstance(o, str))]), True)
                        for i, v in enumerate(value)]
            if isinstance(value, dict):
                return {k: walk(v, context, True) for k, v in value.items()}
            return value

        mended = dict(slide)
        for key, value in slide.items():
            if key not in _SKIP:
                mended[key] = walk(value, title)
        if isinstance(slide.get("notes"), str):
            mended["notes"] = _check_text(slide["notes"], title, names, stated, found)
        if isinstance(slide.get("rows"), list) and all(isinstance(r, list) for r in slide["rows"]):
            mended["rows"] = _check_rows(slide["rows"], names, stated, found)
        if found:
            slides[index] = mended
            touched.append(index)
            corrected.extend(f"{index + 1}장 {item}" for item in found)
    return touched, corrected
