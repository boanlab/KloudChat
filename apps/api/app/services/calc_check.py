"""Slide numbers corrected against code-computed values; only unambiguous claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: slide number; letters in 「P1」「q=2」 excluded
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])-?\d{1,15}(?:\.\d{1,10})?(?![\d])")
#: counts of other things: 「2배」「30%」「3장」
_OTHER_QUANTITY = re.compile(r"\s{0,2}(?:배|%|퍼센트|장|개|번|명|차|회|위|등|단계|가지|줄|쪽)")
_PAREN = re.compile(r"[(（][^()（）]{0,80}[)）]")
#: allowed between a name's letters: spaces, brackets, 「=」
_GAP = r"[^0-9A-Za-z가-힣]{0,3}"


@dataclass(frozen=True, slots=True)
class Stated:
    """One printed value: 「FCFS | 평균 대기시간 = 4.75」 → row, label, 4.75."""

    row: str
    label: str
    text: str
    value: float


def stated_values(block: str) -> list[Stated]:
    """Single-number values of a computed block, by row and label."""
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
    """name with optional inner spacing/brackets, not inside a longer word."""
    if len(name) < 2:
        return None
    body = _GAP.join(re.escape(ch) for ch in name)
    head = r"(?<![A-Za-z])" if re.match(r"[a-z]", name) else ""
    tail = r"(?![A-Za-z0-9])" if re.search(r"[a-z0-9]$", name) else ""
    return re.compile(head + body + tail, re.I)


class _Names:
    """Slide spellings of each row and label."""

    def __init__(self, stated: list[Stated]) -> None:
        rows = list(dict.fromkeys(s.row for s in stated if s.row))
        cores = [_norm(_PAREN.sub("", r)) for r in rows]
        self.rows: dict[str, list[re.Pattern[str]]] = {}
        for row, core in zip(rows, cores, strict=True):
            names = {_norm(row)}
            # 「SJF(비선점)」 → 「SJF」 unless ambiguous (RR(q=2)/RR(q=4))
            if core and cores.count(core) == 1:
                names.add(core)
            self.rows[row] = [p for n in sorted(names, key=len, reverse=True)
                              if (p := _pattern(n))]
        self.labels: dict[tuple[str, str], list[re.Pattern[str]]] = {}
        for s in stated:
            full = _norm(s.label)
            names = {full}
            # prefix alias (「평균 대기」) when unique in the row
            prefix = full[:4]
            if len(full) > 4 and re.fullmatch(r"[가-힣]{4}", prefix) and sum(
                1 for o in stated if o.row == s.row and _norm(o.label)[:4] == prefix
            ) == 1:
                names.add(prefix)
            self.labels[(s.row, s.label)] = [p for n in names if (p := _pattern(n))]

    def rows_in(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, row) of rows named in text, overlaps dropped."""
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
    """Whether written equals value at its shown precision."""
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
    """Numbers in text that differ from the computed value they name."""
    hits = names.rows_in(text)
    if hits:
        segments = [(h[1], hits[i + 1][0] if i + 1 < len(hits) else len(text), h[2])
                    for i, h in enumerate(hits)]
    else:
        # no row named: use the title's row
        in_title = {row for _, _, row in names.rows_in(context)}
        if len(in_title) == 1:
            segments = [(0, len(text), in_title.pop())]
        else:
            # row-less values matched by label
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
                # value before its label
                number = _first_number(text, start, start + m.start())
        else:
            # one label for the whole line
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


#: sentence end in notes; not a decimal point
_SENTENCE_END = re.compile(r"(?<=[.!?。])(\s{1,8})")


def _check_text(text: str, context: str, names: _Names, stated: list[Stated],
                found: list[str]) -> str:
    out = []
    for line in (text or "").splitlines(keepends=True):
        # split keeping separators (odd items) to rejoin whole
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
    """Table with computed rows/labels as headers."""
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
                # 「최소」 in place of the value
                if cells[j].strip():
                    found.append(f"「{cells[j]}」 → {s.text} ({s.row} {s.label})")
                    fixed[j] = s.text
            elif not _same(number.group(0), s.value):
                found.append(f"「{number.group(0)}」 → {s.text} ({s.row} {s.label})")
                fixed[j] = cells[j][: number.start()] + s.text + cells[j][number.end():]
        out.append(fixed)
    return out


#: word fields that aren't the slide's claims
_SKIP = frozenset({"id", "layout", "title", "accent", "image", "diagram", "textScale", "number",
                   "rows", "notes"})


def check_slides(slides: list[dict[str, Any]], block: str) -> tuple[list[int], list[str]]:
    """Mend slide numbers that differ from computed values: (touched indices, corrections)."""
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
                # pair halves read each other as context
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
