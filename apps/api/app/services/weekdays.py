"""Weekdays written next to dates, checked against the calendar and set right by code.

A model counts weekdays in its head and misses (「2026년 10월 15일(수)」 for a Thursday).
Every date the text writes with a weekday — 「2026년 10월 15일(목)」, 「2026-10-15(목)」,
「10월 15일(목)」, 「10/15(목)」, 「15일(목)」 after a month, 「10월 15일 목요일」 — gets the
calendar's weekday.

The year comes from the text itself first (the last date or year above the day, so
「2027년 4월 13일(화)~19일(월)」 reads both days in 2027 and 「2027학년도 학사일정」 sets 2027 for
March to December and 2028 for January and February), then from what the person gave (the
year the request and material name most), then from today: the year that puts the day nearest
to today. A day far from today whose written weekday is right in the other near year is left
alone, because the year is the guess there, not the weekday.

The person's words win over the guess: a weekday the request or material gives for the same day
(「3월 2일 화요일」) is kept, and so is a weekday that is right in a year the person names.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass

WEEKDAYS = "월화수목금토일"


def _wd(name: str) -> str:
    """A weekday after a date: 「(목)」, 「(목요일)」, or 「 목요일」 (not 「목요일마다」)."""
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

#: Years the person's words name: 「2027년」, 「2027학년도」, 「2027-03-02」.
_YEAR = re.compile(r"(?<!\d)(20\d{2})\s{0,2}(?:년|학년도|[.\-/]\s{0,2}\d{1,2}\s{0,2}[.\-/])")
#: A year the text sets for what follows: 「2027학년도」, 「2027년」 (also the head of a full date).
_ANCHOR = re.compile(r"(?<!\d)(20\d{2})\s{0,2}(학년도|년)")
#: The weekday inside a matched date, with what stands on either side of it.
_WRITTEN = re.compile(rf"(\(\s{{0,2}}|\s)[{WEEKDAYS}]((?:요일)?\s{{0,2}}\)|요일$)")
_FENCE = re.compile(r"```[\s\S]*?```")

#: Days from today within which the nearest year is taken without a second thought.
_SURE_DAYS = 120


@dataclass(slots=True)
class Fix:
    text: str
    fixed: str
    why: str


def context_year(context: str) -> int | None:
    """The year the person's words name most (the later one on a tie), or None."""
    years = Counter(int(y) for y in _YEAR.findall(context or ""))
    if not years:
        return None
    return max(years, key=lambda y: (years[y], y))


def context_years(context: str) -> set[int]:
    """Every year the person's words name."""
    return {int(y) for y in _YEAR.findall(context or "")}


def _stated(context: str) -> dict[tuple[int | None, int, int], set[str]]:
    """The weekdays the person's words give each day: 「3월 2일 화요일」 → {(None, 3, 2): {화}}."""
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
    """The year the last 「NNNN학년도」/「NNNN년」 before `at` gives `month`, with its position."""
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
    """The day in the year nearest to today, and whether that choice is clear."""
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


def fix_weekdays(text: str, *, context: str = "",
                 today: dt.date | None = None) -> tuple[str, list[Fix]]:
    """`text` with every written weekday matching its date, and what was changed."""
    if not text or not re.search(rf"[{WEEKDAYS}]", text):
        return text, []
    today = today or dt.date.today()
    given_year = context_year(context)
    named = context_years(context)
    stated = _stated(context)
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
                    # A heading or a year after the last full date (「## 2027학년도 학사일정」).
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
        # The person gave this day's weekday: theirs stands.
        if wd in stated.get((day.year if explicit else None, day.month, day.day), set()) or (
                not explicit and wd in stated.get((day.year, day.month, day.day), set())):
            continue
        # A day with no year of its own whose weekday is right in a year the person names.
        if not explicit and any(_weekday_in(y, day.month, day.day) == wd for y in named):
            continue
        if not sure:
            # Far from today with no year anywhere: the weekday may be right for the other year.
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
        return text, []
    out.append(text[last:])
    return "".join(out), fixes


__all__ = ["Fix", "context_year", "context_years", "fix_weekdays"]
