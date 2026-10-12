"""Dates a request's own rules settle by code (「개시 30일 전」, 「개강일부터 5일간(수업일 기준)」,
「개강 후 8주차 월요일부터 3일간」, 「오늘부터 며칠」), before writing and for checking after."""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.services import skill_scripts

log = logging.getLogger("kchat")

#: 「오늘부터 며칠」 「며칠 남았는지」 「D-day」
_DAYS_LEFT = re.compile(r"오늘\s{0,2}(?:부터|로부터|기준)?[^.\n]{0,24}며칠"
                        r"|며칠\s{0,2}(?:이\s{0,2})?남"
                        r"|(?<![A-Za-z])D\s{0,2}-\s{0,2}day", re.I)


@dataclass(frozen=True, slots=True)
class Settled:
    #: what the line names: 「수강신청 정정」, 「학기 개시 30일 전」
    label: str
    start: dt.date
    end: dt.date
    #: the rule as written, with its base
    rule: str
    class_days: bool = False


def settled(context: str, today: dt.date | None = None) -> list[Settled]:
    """Dates the text's rules give, each counted from a date the text states."""
    return list(_settled(context or "", today or dt.date.today()))


@lru_cache(maxsize=32)
def _settled(context: str, today: dt.date) -> tuple[Settled, ...]:
    try:
        dates = skill_scripts.dates()
        rows = dates.scan(context or "", today)
        runs = dates.spans(context or "", today)
    except Exception:  # noqa: BLE001 — no settled dates; the text stays as written
        log.exception("date rules could not be read")
        return ()
    out: list[Settled] = []
    for row in rows:
        if row.get("kind") != "offset" or row.get("status") != "ok" or not row.get("anchor"):
            continue
        day = dt.date.fromisoformat(row["date"])
        out.append(Settled(f"{row['anchor']} {row['text']}", day, day,
                           f"{row['anchor']} {row['text']}"))
    for row in runs:
        out.append(Settled(row["label"] or row["text"], dt.date.fromisoformat(row["start"]),
                           dt.date.fromisoformat(row["end"]), row["text"], row["class_days"]))
    return tuple(out)


def expected(context: str, today: dt.date | None = None) -> set[dt.date]:
    """Every day the rules settle (a run's first and last day)."""
    return {d for s in settled(context, today) for d in (s.start, s.end)}


def _shown(day: dt.date) -> str:
    return f"{day.year}. {day.month}. {day.day}.({'월화수목금토일'[day.weekday()]})"


def block(request: str, material: str, today: dt.date | None = None) -> str:
    """The settled dates for the writer, or ''."""
    today = today or dt.date.today()
    rows = settled(f"{request}\n{material}", today)
    if not rows:
        return ""
    lines = []
    for s in rows:
        when = _shown(s.start) if s.start == s.end else f"{_shown(s.start)} ~ {_shown(s.end)}"
        basis = " (주말·공휴일 제외 수업일)" if s.class_days else ""
        lines.append(f"- {s.label}: {when} — 「{s.rule}」{basis}")
    if _DAYS_LEFT.search(request or ""):
        for s in rows:
            left = (s.end - today).days
            if left >= 0:
                lines.append(f"- 오늘({_shown(today)})부터 {s.label} 마지막 날 "
                             f"{_shown(s.end)}까지: {left}일")
    return (
        "# 확정 날짜(코드 계산)\nThe code worked out these dates from the dates and rules in the "
        "request and material. Use them as they are, written in the document's date style "
        "(e.g. 2027. 3. 2.(화)); do not recalculate them, do not move them, and do not change "
        "their weekdays.\n" + "\n".join(lines)
    )


__all__ = ["Settled", "block", "expected", "settled"]
