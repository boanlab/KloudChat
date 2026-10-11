"""Weekdays written next to dates, checked against the calendar and set right by code.

A model counts weekdays in its head and misses (「2026년 10월 15일(수)」 for a Thursday).
Every date the text writes with a weekday — 「2026년 10월 15일(목)」, 「2026-10-15(목)」,
「10월 15일(목)」, 「10/15(목)」, 「15일(목)」 after a month, 「10월 15일 목요일」 — gets the
calendar's weekday.

The year comes from the text itself first (the last date that names one, so 「2027년 4월 13일(화)~
19일(월)」 reads both days in 2027), then from what the person gave (the year the request and
material name most), then from today: the year that puts the day nearest to today. A day far from
today whose written weekday is right in the other near year is left alone, because the year
is the guess there, not the weekday.
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


def fix_weekdays(text: str, *, context: str = "",
                 today: dt.date | None = None) -> tuple[str, list[Fix]]:
    """`text` with every written weekday matching its date, and what was changed."""
    if not text or not re.search(rf"[{WEEKDAYS}]", text):
        return text, []
    today = today or dt.date.today()
    given_year = context_year(context)
    fences = [m.span() for m in _FENCE.finditer(text)]
    fixes: list[Fix] = []
    out: list[str] = []
    last = 0
    year: int | None = None
    month: int | None = None

    for m in _DATE.finditer(text):
        if any(a <= m.start() < b for a, b in fences):
            continue
        g = m.groupdict()
        sure = True
        try:
            if g["y"]:
                year, month = int(g["y"]), int(g["m"])
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
                if year is not None or given_year is not None:
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


__all__ = ["Fix", "context_year", "fix_weekdays"]
