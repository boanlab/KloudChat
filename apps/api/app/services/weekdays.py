"""Weekdays beside dates corrected to the calendar."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass

WEEKDAYS = "월화수목금토일"


def _wd(name: str) -> str:
    """Weekday after a date: 「(목)」, 「(목요일)」, 「 목요일」."""
    return (rf"(?:\(\s{{0,2}}(?P<{name}>[{WEEKDAYS}])(?:요일)?\s{{0,2}}\)"
            rf"|\s{{1,2}}(?P<{name}2>[{WEEKDAYS}])요일(?!마다))")


_DATE = re.compile(
    # 2026년 10월 15일 · 2026-10-15 · 2026. 10. 15.
    r"(?<!\d)(?P<y>20\d{2})\s{0,2}(?:년\s{0,2}|[.\-/]\s{0,2})(?P<m>\d{1,2})\s{0,2}(?:월\s{0,2}|[.\-/]\s{0,2})"
    r"(?P<d>\d{1,2})(?!\d)\s{0,2}(?:일|\.)?\s{0,2}" + _wd("a_wd")
    # 10월 15일
    + r"|(?<![\d.])(?P<m2>\d{1,2})\s{0,2}월\s{0,2}(?P<d2>\d{1,2})\s{0,2}일\s{0,2}" + _wd("b_wd")
    # 10/15 · 10.15
    + r"|(?<![\d./:\-])(?P<m3>\d{1,2})[/.](?P<d3>\d{1,2})\.?\s{0,1}" + _wd("c_wd")
    # 15일, a month already said
    + r"|(?<![\d월/.])(?P<d4>\d{1,2})\s{0,2}일\s{0,2}" + _wd("d_wd")
)

#: years named by the person: 「2027년」, 「2027학년도」, 「2027-03-02」
_YEAR = re.compile(r"(?<!\d)(20\d{2})\s{0,2}(?:년|학년도|[.\-/]\s{0,2}\d{1,2}\s{0,2}[.\-/])")
#: year set for following text: 「2027학년도」, 「2027년」
_ANCHOR = re.compile(r"(?<!\d)(20\d{2})\s{0,2}(학년도|년)")
#: weekday in a matched date, with surroundings
_WRITTEN = re.compile(rf"(\(\s{{0,2}}|\s)[{WEEKDAYS}]((?:요일)?\s{{0,2}}\)|요일$)")
_FENCE = re.compile(r"```[\s\S]*?```")

#: days from today where the nearest year is certain
_SURE_DAYS = 120


@dataclass(slots=True)
class Fix:
    text: str
    fixed: str
    why: str


def context_year(context: str) -> int | None:
    """Year the person names most (later on tie), or None."""
    years = Counter(int(y) for y in _YEAR.findall(context or ""))
    if not years:
        return None
    return max(years, key=lambda y: (years[y], y))


def context_years(context: str) -> set[int]:
    """Every year the person names."""
    return {int(y) for y in _YEAR.findall(context or "")}


def _stated(context: str) -> dict[tuple[int | None, int, int], set[str]]:
    """Weekdays the person gives per day: 「3월 2일 화요일」 → {(None, 3, 2): {화}}."""
    out: dict[tuple[int | None, int, int], set[str]] = {}
    for m in _DATE.finditer(context or ""):
        g = m.groupdict()
        if g["y"]:
            key, wd = (int(g["y"]), int(g["m"]), int(g["d"])), g["a_wd"] or g["a_wd2"]
        elif g["m2"]:
            key, wd = (None, int(g["m2"]), int(g["d2"])), g["b_wd"] or g["b_wd2"]
        elif g["m3"]:
            key, wd = (None, int(g["m3"]), int(g["d3"])), g["c_wd"] or g["c_wd2"]
        else:
            continue
        if wd:
            out.setdefault(key, set()).add(wd)
            out.setdefault((None, key[1], key[2]), set()).add(wd)
    return out


def _anchor_year(text: str, at: int, month: int) -> tuple[int, int] | None:
    """Year from the last 「NNNN학년도」/「NNNN년」 before `at` for `month`, with position."""
    found = None
    for m in _ANCHOR.finditer(text, 0, at):
        found = m
    if found is None:
        return None
    year = int(found.group(1))
    if found.group(2) == "학년도" and month <= 2:
        year += 1
    return year, found.start()


def _nearest(month: int, day: int, today: dt.date) -> tuple[dt.date, bool] | None:
    """Day in the year nearest today, and whether the choice is clear."""
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(dt.date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda d: abs((d - today).days))
    best = candidates[0]
    return best, abs((best - today).days) <= _SURE_DAYS


def _weekday_in(year: int, month: int, day: int) -> str:
    try:
        return WEEKDAYS[dt.date(year, month, day).weekday()]
    except ValueError:
        return ""


def _redated(written: str, new: dt.date, kind: str, month: int | None) -> str:
    """`written` (「2027. 2. 1.(일)」, 「2월 1일(일)」, 「1일(일)」) naming `new`, weekday kept."""
    numbers = list(re.finditer(r"\d+", written))
    if kind == "y":
        values = [str(new.year), str(new.month), str(new.day)]
    elif kind == "md":
        values = [str(new.month), str(new.day)]
    elif new.month == month:
        values = [str(new.day)]
    else:
        values = [f"{new.month}월 {new.day}"]
    out, last = [], 0
    for m, value in zip(numbers, values, strict=False):
        out.append(written[last:m.start()] + value)
        last = m.end()
    return "".join(out) + written[last:]


def _rule_day(day: dt.date, wd: str, rules: set[dt.date]) -> dt.date | None:
    """The one rule-settled day near `day` that has the written weekday, when `day` is none."""
    if day in rules:
        return None
    near = [d for d in rules if 1 <= abs((d - day).days) <= 3 and WEEKDAYS[d.weekday()] == wd]
    return near[0] if len(near) == 1 else None


def fix_weekdays(text: str, *, context: str = "",
                 today: dt.date | None = None) -> tuple[str, list[Fix]]:
    """`text` with weekdays matching their dates, plus changes. A date off by a few days from
    one the request's rules settle, written with that day's weekday, is the slip: the date
    is set to the rule's and the weekday kept. 요일 columns are read against their row's date."""
    if not text or not re.search(rf"[{WEEKDAYS}]", text):
        return text, []
    from app.services import date_rules

    today = today or dt.date.today()
    given_year = context_year(context)
    named = context_years(context)
    stated = _stated(context)
    ruled = date_rules.settled(context, today) if context else []
    rules = {d for r in ruled for d in (r.start, r.end)}
    text, column_fixes = _fix_weekday_columns(text, given_year, today, ruled)
    fences = [m.span() for m in _FENCE.finditer(text)]
    fixes: list[Fix] = []
    out: list[str] = []
    last = 0
    year: int | None = None
    year_at = -1
    month: int | None = None

    for m in _DATE.finditer(text):
        if any(a <= m.start() < b for a, b in fences):
            continue
        g = m.groupdict()
        sure = True
        try:
            explicit = bool(g["y"])
            if g["y"]:
                year, month, year_at = int(g["y"]), int(g["m"]), m.start()
                day = dt.date(year, month, int(g["d"]))
                wd = g["a_wd"] or g["a_wd2"]
            else:
                if g["m2"]:
                    month, dom, wd = int(g["m2"]), int(g["d2"]), g["b_wd"] or g["b_wd2"]
                elif g["m3"]:
                    month, dom, wd = int(g["m3"]), int(g["d3"]), g["c_wd"] or g["c_wd2"]
                else:
                    if month is None:
                        continue
                    dom, wd = int(g["d4"]), g["d_wd"] or g["d_wd2"]
                anchored = _anchor_year(text, m.start(), month)
                if anchored is not None and anchored[1] > year_at:
                    # heading/year after the last full date
                    day = dt.date(anchored[0], month, dom)
                elif year is not None or given_year is not None:
                    day = dt.date(year or given_year, month, dom)
                else:
                    near = _nearest(month, dom, today)
                    if near is None:
                        continue
                    day, sure = near
        except (TypeError, ValueError):
            continue
        real = WEEKDAYS[day.weekday()]
        if not wd or wd == real:
            continue
        if rules and (ruled := _rule_day(day, wd, rules)) is not None:
            # 「2. 1.(일)」 for the rule's 1/31(일): the date slipped, not the weekday
            written = m.group(0)
            kind = "y" if explicit else "md" if (g["m2"] or g["m3"]) else "d"
            fixed = _redated(written, ruled, kind, day.month)
            out.append(text[last:m.start()])
            out.append(fixed)
            last = m.end()
            fixes.append(Fix(written.strip(), fixed.strip(),
                             f"규칙으로 정한 날짜 {ruled.isoformat()}({wd})"))
            continue
        # person's weekday stands
        if wd in stated.get((day.year if explicit else None, day.month, day.day), set()) or (
                not explicit and wd in stated.get((day.year, day.month, day.day), set())):
            continue
        # right in a year the person names
        if not explicit and any(_weekday_in(y, day.month, day.day) == wd for y in named):
            continue
        if not sure:
            # far from today, no year: may be right for the other year
            others = []
            for shift in (-1, 1):
                try:
                    others.append(day.replace(year=day.year + shift))
                except ValueError:
                    continue
            if any(WEEKDAYS[d.weekday()] == wd for d in others):
                continue
        written = m.group(0)
        fixed = _WRITTEN.sub(rf"\g<1>{real}\g<2>", written, count=1)
        if fixed == written:
            continue
        out.append(text[last:m.start()])
        out.append(fixed)
        last = m.end()
        fixes.append(Fix(written.strip(), fixed.strip(), f"{day.isoformat()}은 {real}요일"))
    if not fixes:
        return text, column_fixes
    out.append(text[last:])
    return "".join(out), column_fixes + fixes


#: a date in a table cell, the year optional: 「2027. 2. 15.」 「2. 18.」 「3월 2일」 「2027-03-02」
_CELL_DATE = re.compile(
    r"(?<![\d.])(?:(?P<y>20\d{2})\s{0,2}(?:년|[.\-/])\s{0,2})?(?P<m>\d{1,2})\s{0,2}(?:월|[.\-/])"
    r"\s{0,2}(?P<d>\d{1,2})(?!\d)(?:\s{0,2}일|\.)?")
#: the weekday a 요일 cell opens with: 「화」 「월~목」 「토요일 ~ 금요일」
_CELL_WEEKDAY = re.compile(
    rf"^\s{{0,2}}(?P<a>[{WEEKDAYS}])(?:요일)?(?:\s{{0,2}}[~∼\-–]\s{{0,2}}(?P<b>[{WEEKDAYS}])(?:요일)?)?"
    rf"(?![가-힣])")
_DATE_HEADER = re.compile(r"날짜|일자|일시|기간|일정|날|시기|기한")


def _cells(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def _cell_days(cell: str, year: int) -> list[dt.date] | None:
    """The dates a cell writes, a missing year from the one before; None when one is no date."""
    out = []
    for m in _CELL_DATE.finditer(cell):
        year = int(m.group("y") or year)
        try:
            out.append(dt.date(year, int(m.group("m")), int(m.group("d"))))
        except ValueError:
            return None
    return out


def _label(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")


def _period(days: list[dt.date]) -> str:
    first = f"{days[0].year}. {days[0].month}. {days[0].day}."
    if len(days) == 1 or days[1] == days[0]:
        return first
    last = days[1]
    return first + " ~ " + (f"{last.year}. " if last.year != days[0].year else "") + (
        f"{last.month}. {last.day}.")


def _fix_weekday_columns(text: str, year: int | None, today: dt.date,
                         ruled: list | None = None) -> tuple[str, list[Fix]]:
    """요일 cells set to the weekdays of the dates in their row's date cell; a row named like a
    rule's line (「수강신청 정정」) gets the rule's dates."""
    if "|" not in text or not (re.search(r"\|\s{0,2}\**요일", text) or ruled):
        return text, []
    by_label = {}
    for r in ruled or []:
        by_label.setdefault(_label(r.label), []).append(r)
    lines = text.split("\n")
    fixes: list[Fix] = []
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            i += 1
        header = [c.strip() for c in _cells(lines[start])]
        weekday_col = next((j for j, c in enumerate(header)
                            if re.fullmatch(r"\**요일\**(?:\s{0,2}\(.{0,10}\))?", c)), None)
        date_col = next((j for j, c in enumerate(header)
                         if j not in (0, weekday_col) and _DATE_HEADER.search(c)), None)
        if date_col is None:
            continue
        for k in range(start + 1, i):
            cells = _cells(lines[k])
            if len(cells) != len(header) or all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in cells):
                continue
            rule = by_label.get(_label(cells[0]), [])
            if len(rule) == 1:
                # the rule's period for its own row: 「수강신청 정정 | 2027. 2. 28. ~ 3. 5.」
                want = [rule[0].start] + ([rule[0].end] if rule[0].end != rule[0].start else [])
                if _cell_days(cells[date_col], want[0].year) != want:
                    shown = _period(want)
                    fixes.append(Fix(cells[date_col].strip(), shown,
                                     f"「{rule[0].rule}」로 정한 기간"))
                    cells[date_col] = f" {shown} "
                    if weekday_col is not None and not _CELL_WEEKDAY.match(cells[weekday_col]):
                        # 「[요일 미상]」
                        cells[weekday_col] = " " + "~".join(
                            WEEKDAYS[d.weekday()] for d in want) + " "
                    lead = lines[k][: len(lines[k]) - len(lines[k].lstrip())]
                    closed = lines[k].rstrip().endswith("|")
                    lines[k] = lead + "|" + "|".join(cells) + ("|" if closed else "")
            if weekday_col is None:
                continue
            days: list[dt.date] = []
            last_year = year
            for m in _CELL_DATE.finditer(cells[date_col]):
                y = int(m.group("y")) if m.group("y") else last_year
                if y is None:
                    near = _nearest(int(m.group("m")), int(m.group("d")), today)
                    if near is None or not near[1]:
                        days = []
                        break
                    y = near[0].year
                try:
                    days.append(dt.date(y, int(m.group("m")), int(m.group("d"))))
                except ValueError:
                    days = []
                    break
                last_year = y
            cell = cells[weekday_col]
            wm = _CELL_WEEKDAY.match(cell)
            if not days or len(days) > 2 or wm is None:
                continue
            written = [wm.group("a")] + ([wm.group("b")] if wm.group("b") else [])
            if len(written) != len(days):
                continue
            real = [WEEKDAYS[d.weekday()] for d in days]
            if written == real:
                continue
            fixed_cell = cell
            for group, wd in zip(("a", "b"), real, strict=False):
                at = wm.start(group) + (len(fixed_cell) - len(cell))
                fixed_cell = fixed_cell[:at] + wd + fixed_cell[at + 1:]
            cells[weekday_col] = fixed_cell
            lead = lines[k][: len(lines[k]) - len(lines[k].lstrip())]
            closed = lines[k].rstrip().endswith("|")
            lines[k] = lead + "|" + "|".join(cells) + ("|" if closed else "")
            why = " ~ ".join(f"{d.isoformat()}은 {WEEKDAYS[d.weekday()]}요일" for d in days)
            fixes.append(Fix(cell.strip(), fixed_cell.strip(), why))
    return "\n".join(lines), fixes


__all__ = ["Fix", "context_year", "context_years", "fix_weekdays"]
