"""Report pipeline's code work: pre-pass facts before writing, format checks and repairs after."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from app.core import logs
from app.services import key_figures, placeholders, skill_scripts, stated_policy, thinking, units
from app.services.verify import Check, Doc, Finding, model_check

log = logging.getLogger(__name__)

#: steps, checks and amendments per 진행 과정 설정 (`process_settings.py`), on `Prepass.process`

_PRICE_LINE = re.compile(
    r"(?:단가|MM|M/M|견적|월\s{0,2}단가|×|x\s{0,2}\d|\d\s{0,2}(?:명|대|개|식|회|개월|부|권)\s{0,4}[×x*])"
    r"[^\n]{0,80}?\d[\d,]{2,15}\s{0,2}(?:만\s{0,2})?원"
    r"|\d[\d,]{2,15}\s{0,2}(?:만\s{0,2})?원[^\n]{0,40}?(?:부가세|VAT)",
    re.I,
)


def price_line(line: str) -> bool:
    """Whether a line prices something: 「PM 1명 5MM(월 7,000,000원)」."""
    return bool(_PRICE_LINE.search(line or ""))


_WEIGHTS = re.compile(
    r"가중치|중요도|배점|비중\s{0,2}\d|\d{1,3}\s{0,2}[:·/]\s{0,2}\d{1,3}\s{0,2}[:·/]\s{0,2}\d{1,3}"
    # 3+ criteria with bare numbers: 「비용 50, 모바일 앱 30, HR 연동 20」
    r"|(?:[가-힣A-Za-z][가-힣A-Za-z ]{0,10}\s\d{1,3}(?:점|%)?\s{0,2}[,，]\s{0,2}){2,6}"
    r"[가-힣A-Za-z][가-힣A-Za-z ]{0,10}\s\d{1,3}(?:점|%)?(?!\d)")
_FULL_DATE = re.compile(
    r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})"
)
_MEETING = re.compile(r"회의|미팅|모임|위원회|간담회|녹음|전사")
_REQ_ID = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2,4}-\d{2,3})(?![\d])")
_VALIDITY = re.compile(r"(?:견적\s{0,2})?유효\s{0,2}기간\s{0,2}(?:은|는|:|：)?\s{0,2}([^\n,.;]{2,30})")
_TABLE_RULE = re.compile(r"^\s{0,8}\|?\s{0,8}:?-{2,}")


# ── tables ──


@dataclass(slots=True)
class Table:
    section: int
    header: list[str]
    rows: list[tuple[str, list[str]]]
    above: str = ""


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def tables(sections: list[dict]) -> list[Table]:
    found: list[Table] = []
    for index, sec in enumerate(sections):
        lines = str(sec.get("content") or "").split("\n")
        i = 0
        while i < len(lines) - 1:
            if lines[i].lstrip().startswith("|") and _TABLE_RULE.match(lines[i + 1]):
                header = cells(lines[i])
                rows = []
                j = i + 2
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    if j + 1 < len(lines) and _TABLE_RULE.match(lines[j + 1]):
                        # a header and rule glued on: the next table
                        break
                    rows.append((lines[j], cells(lines[j])))
                    j += 1
                above = next((ln for ln in reversed(lines[max(0, i - 3):i]) if ln.strip()), "")
                found.append(Table(index, header, rows, above))
                i = j
                continue
            i += 1
    return found


def _column(header: list[str], pattern: str) -> int | None:
    rx = re.compile(pattern)
    return next((k for k, h in enumerate(header) if rx.search(h)), None)


def _owner_due(header: list[str], due: str = r"기한|마감|완료일|due") -> tuple[int | None, int | None]:
    """Owner and due columns; one combined 「담당·기한」 column is neither."""
    owner, when = _column(header, r"담당|책임|owner"), _column(header, due)
    return (None, None) if owner is not None and owner == when else (owner, when)


def _words(text: str) -> set[str]:
    """2+ char words, particles trimmed: what binds a row to a line."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9○]{2,20}", text or ""):
        word = re.sub(r"(?:까지|에서|으로|에게|님이|님은|님|이가|은|는|이|가|을|를|의|에|로|과|와|도)$", "", word)
        if len(word) >= 2 and not word.isdigit():
            out.add(word)
    return out


# ── before writing ──


@dataclass(slots=True)
class Prepass:
    format_id: str = ""
    process: Any = None
    base_date: str = ""
    #: `base_date` source: 「text」, 「file」 (attachment name), 「said:오늘」 (today/yesterday), or ""
    base_source: str = ""
    dates: list[dict] = field(default_factory=list)
    lead: dict | None = None
    quote_input: dict | None = None
    quote: dict | None = None
    scores: dict | None = None
    periods: dict | None = None
    musts: dict | None = None
    costs: dict | None = None
    #: never-agreeing cost readings: writer gets no totals
    costs_withheld: bool = False
    #: the options split readings still priced alike, with their budget comparison
    costs_partial: dict | None = None
    rfp: dict | None = None
    #: per-role headcount and MM (`staffing`): MM never written as people
    staff: dict[str, dict] = field(default_factory=dict)
    figures: list[key_figures.Figure] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    #: `{"skill", "summary"}` per run; `error` = failed, `skipped` = nothing to do
    runs: list[dict] = field(default_factory=list)

    @property
    def settings(self):
        """Its 진행 과정 설정; older runs without one read the format's."""
        from app.services import process_settings

        if self.process is not None:
            return self.process
        return process_settings.for_format(self.format_id)

    @property
    def counts_from_meeting(self) -> bool:
        """Relative dates from meeting day (`date_base = "meeting"`)."""
        return self.settings.date_base == "meeting"

    @property
    def undated(self) -> bool:
        """Minutes with no known meeting date: stays 「[회의 일자]」."""
        return self.counts_from_meeting and not self.base_source

    @property
    def bound_dates(self) -> list[dict]:
        lead = [d for d in (self.lead or {}).get("items") or [] if d.get("status") == "ok"]
        return [d for d in self.dates if d.get("status") in ("ok", "repeat")] + lead


#: yearless day with weekday: 「10/8(목)」, 「10월 8일(목)」, 「10. 8.(목)」
_DAY_WITH_WEEKDAY = re.compile(
    r"(?<![\d./])(\d{1,2})\s{0,2}(?:[/.]|월)\s{0,2}(\d{1,2})\s{0,2}(?:일|\.)?\s{0,2}\(([월화수목금토일])\)")
#: …weekday first: 「목요일(10/15)」, 「목(10월 15일)」
_WEEKDAY_WITH_DAY = re.compile(
    r"([월화수목금토일])(?:요일)?\s{0,2}\(\s{0,2}(\d{1,2})\s{0,2}(?:[/.]|월)\s{0,2}(\d{1,2})"
    r"\s{0,2}일?\s{0,2}\)")
#: a meeting still to come: 「다음 주 목요일(10/15) … 회의」
_UPCOMING = re.compile(r"다음\s{0,2}주|이번\s{0,2}주|다음\s{0,2}달|내일|모레|예정|열릴|열\s|있을")
#: yearless day without a weekday: 「10/15」, 「10월 15일」
_YEARLESS_DAY = re.compile(r"(?<![\d./])(\d{1,2})\s{0,2}(?:/|월\s{0,2})(\d{1,2})(?![\d/])\s{0,2}일?")
_WEEKDAYS = "월화수목금토일"


def _year_of(month: int, day: int, weekday: str, today: dt.date) -> dt.date | None:
    """Date this year or last (or early next year, within 60 days) matching the weekday;
    None if none or several (ask, don't guess)."""
    found = []
    for year in (today.year, today.year - 1, today.year + 1):
        try:
            date = dt.date(year, month, day)
        except ValueError:
            continue
        if year > today.year and (date - today).days > 60:
            continue
        if _WEEKDAYS[date.weekday()] == weekday:
            found.append(date)
    return found[0] if len(found) == 1 else None


def _upcoming_day(month: int, day: int, today: dt.date) -> dt.date | None:
    """A meeting still to come on a yearless day: the one from today to 120 days ahead."""
    for year in (today.year, today.year + 1):
        try:
            date = dt.date(year, month, day)
        except ValueError:
            continue
        if 0 <= (date - today).days <= 120:
            return date
    return None


def meeting_date(request: str, material: str, today: dt.date | None = None) -> dt.date | None:
    """Meeting date from the person or material header; yearless day resolved by weekday,
    or, for a meeting the request says is still to come, by the next such day."""
    for text in (request, material):
        for line in (text or "").splitlines():
            if (m := _FULL_DATE.search(line)) and _MEETING.search(line):
                try:
                    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
    today = today or dt.date.today()
    for text in (request, material):
        for line in (text or "").splitlines():
            if not _MEETING.search(line):
                continue
            if m := _DAY_WITH_WEEKDAY.search(line):
                month, day, weekday = int(m.group(1)), int(m.group(2)), m.group(3)
            elif m := _WEEKDAY_WITH_DAY.search(line):
                month, day, weekday = int(m.group(2)), int(m.group(3)), m.group(1)
            else:
                continue
            if found := _year_of(month, day, weekday, today):
                return found
    for line in (request or "").splitlines():
        # 「다음 주 목요일(10/15) 14시 주간회의」: a meeting to come is this year's next one
        if _MEETING.search(line) and _UPCOMING.search(line) and (
                m := _YEARLESS_DAY.search(line)):
            if found := _upcoming_day(int(m.group(1)), int(m.group(2)), today):
                return found
    return None


#: attached file heading: 「## 녹음_연구실주간미팅_20261014_1000.mp3」
_FILE_HEADING = re.compile(r"^#{2,3}\s{1,3}(\S[^\n]{0,200}\.[A-Za-z0-9]{2,5})\s{0,3}$")
_FILE_DATE = re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


def file_date(material: str) -> dt.date | None:
    """Date in an attached file's name (20261014, 2026-10-14, 2026_10_14)."""
    for line in (material or "").splitlines():
        if not (head := _FILE_HEADING.match(line.strip())):
            continue
        for m in _FILE_DATE.finditer(head.group(1)):
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


#: 「오늘 회의」, 「어제 한 미팅」 — not 「오늘까지 회의록」 (due) or 「오늘 회의록」 (document)
_SAID_DAY = re.compile(
    r"(?<![가-힣])(오늘|금일|어제|어저께|그저께|그제)(?!\s{0,2}(?:까지|중|안에|내로))"
    r"(?:\s{1,3}[가-힣A-Za-z0-9]{1,10}){0,3}?\s{0,3}(?:회의|미팅|모임|간담회|세미나)(?!록)"
)
#: gap-card answer merged into request: 「- 회의 일시: 10월 7일 오후 2시」
_ANSWERED_DAY = re.compile(r"회의\s{0,2}일시\s{0,2}[:：]\s{0,4}(\d{1,2})\s{0,2}(?:월|[/.])\s{0,2}(\d{1,2})")
#: the same answer giving the year only (「- 회의 일시: 2026」): the memo's day takes it
_ANSWERED_YEAR = re.compile(
    r"회의\s{0,2}일시\s{0,2}[:：]\s{0,4}((?:19|20)\d{2})\s{0,2}년?\s{0,2}$", re.M)
_DAYS_BACK = {"오늘": 0, "금일": 0, "어제": 1, "어저께": 1, "그저께": 2, "그제": 2}


def meeting_base(request: str, material: str, today: dt.date | None,
                 nearest: bool = False) -> tuple[dt.date | None, str]:
    """Relative-date base and source: text, file name, or 'today/yesterday' (KST); never handover
    day. With `nearest` (the year question already asked or skipped), a yearless meeting day takes
    the year nearest today when that is clear."""
    if found := meeting_date(request, material, today):
        return found, "text"
    if found := file_date(material):
        return found, "file"
    if year := _ANSWERED_YEAR.search(request or ""):
        for text in (request, material):
            for line in (text or "").splitlines():
                if _MEETING.search(line) and (m := _YEARLESS_DAY.search(line)):
                    try:
                        return dt.date(int(year.group(1)), int(m.group(1)), int(m.group(2))), "text"
                    except ValueError:
                        break
    if today and (said := _ANSWERED_DAY.search(request or "")):
        # yearless 「회의 일시」 answer: latest such day not after today
        try:
            day = dt.date(today.year, int(said.group(1)), int(said.group(2)))
        except ValueError:
            day = None
        if day is not None:
            if day > today:
                day = day.replace(year=today.year - 1)
            return day, "text"
    if today and (said := _SAID_DAY.search(request or "")):
        return today - dt.timedelta(days=_DAYS_BACK[said.group(1)]), "said:" + said.group(1)
    if nearest and today and (day := _nearest_meeting_day(request, material, today)):
        return day, "near"
    return None, ""


#: the next meeting's clause: 「다음 회의 10/21 화 10시 3층 회의실.」
_NEXT_MEETING = re.compile(
    r"(?:다음|차기)\s{0,2}(?:회의|모임|미팅)[^\n;]*?(?=(?<!\d)\.(?!\s{0,2}\d)|[\n;]|$)")


def _nearest_meeting_day(request: str, material: str, today: dt.date) -> dt.date | None:
    """The first yearless day on a meeting line (the next meeting's clause aside), in the year
    nearest today when that day is within `weekdays._SURE_DAYS`."""
    from app.services import weekdays

    for text in (request, material):
        for line in (text or "").splitlines():
            if not _MEETING.search(line):
                continue
            if m := _YEARLESS_DAY.search(_NEXT_MEETING.sub(" ", line)):
                near = weekdays._nearest(int(m.group(1)), int(m.group(2)), today)
                return near[0] if near and near[1] else None
    return None


def _base_words(source: str) -> str:
    """Base-date source, as step and date block word it."""
    if source == "file":
        return "첨부 파일 이름의 날짜"
    if source == "near":
        return "연도 없는 회의 날짜를 대화 날짜에 가장 가까운 해로 셈"
    if source.startswith("said:"):
        return f"요청의 「{source[5:]}」을 대화 날짜(한국 시간)로 셈"
    return ""


#: date-calc reading with no meeting date: words kept, no date
_NO_BASE = "no_base"


def _undated(rows: list[dict], probe: dt.date) -> list[dict]:
    """Stand-in-base rows with relative dates turned back into words; written-out dates stay."""
    out = []
    for row in rows:
        if row.get("base_kind") == "meeting" and row.get("base_date") == probe.isoformat():
            if row.get("kind") in ("full_date", "month_day"):
                continue
            row = {"text": row["text"], "kind": row.get("kind", ""), "status": _NO_BASE,
                   "source_line": row.get("source_line", ""), "line": row.get("line", 0),
                   "base_date": "", "base_kind": ""}
        out.append(row)
    return out


_SPEAKER = re.compile(r"^\s{0,4}[^\s:|#]{1,12}\s{0,2}:\s")


def _with_context(rows: list[dict], material: str) -> None:
    """Row context: own line, plus the line before in a transcript (names the task the reply dates)."""
    lines = (material or "").splitlines()
    for row in rows:
        number = int(row.get("line") or 0)
        own = lines[number - 1] if 0 < number <= len(lines) else str(row.get("source_line") or "")
        near = [_SPEAKER.sub("", own).strip()]
        if _SPEAKER.match(own):
            before = next((ln for ln in reversed(lines[:max(0, number - 1)]) if ln.strip()), "")
            # speaker labels (「한교수:」) bind nothing
            near.insert(0, _SPEAKER.sub("", before).strip())
        row["context"] = " ".join(near)[:600]


#: date block head when meeting date unknown: nothing computed from today
_UNDATED_HEAD = (
    "회의 일자: not in the material or the request. Write the meeting date in the head field 일시 and "
    "in the body (회의 개요 etc.) as 「[회의 일자]」; do not guess it from today or the day the material "
    "arrived. Do not resolve relative deadlines (다음 주 금요일, 모레) to dates: write 「[기한]」 in the "
    "기한 cell and 「원문 「다음 주 금요일」, 회의 일자 미기재로 날짜 미확정」 in 비고."
)


def _date_block(rows: list[dict], base: str, *, undated: bool = False) -> str:
    relative = [r for r in rows if r.get("kind") not in ("full_date", "month_day", "weekday")
                or r.get("status") in ("unclear", "unresolved", _NO_BASE)]
    if not relative and not undated:
        return ""
    lines = []
    for r in relative[:40]:
        if r["status"] == _NO_BASE:
            lines.append(f"- 「{r['text']}」 → not resolved (no meeting date): 기한 「[기한]」, in 비고 "
                         f"원문 「{r['text']}」 — 출처: 「{r['source_line'][:120]}」")
            continue
        where = f" — 출처: 「{r['source_line'][:120]}」"
        if r.get("base_kind") == "mail":
            where += f"(기준 {r['base_date']}, 메일 발신일)"
        elif r.get("base_date") != base:
            # a base other than the block's head
            where += f"(기준 {r['base_date']})"
        if r["status"] == "ok":
            note = " (주말)" if r.get("weekend") else ""
            note += f" ({r['holiday']})" if r.get("holiday") else ""
            lines.append(f"- 「{r['text']}」 → {r['shown']}{note}{where}")
        elif r["status"] == "repeat":
            every = "격주" if r.get("repeat") == "biweekly" else "매주"
            lines.append(f"- 「{r['text']}」 → {every} {r['weekday']}요일({r['first_date']}부터){where}")
        elif r["status"] == "unclear":
            lines.append(f"- 「{r['text']}」 → inaudible: 기한 「[기한]」, the original wording in 비고{where}")
        else:
            lines.append(f"- 「{r['text']}」 → not one definite date: 기한 「미정」, the original wording in 비고{where}")
    head = f"기준일 {base}." if base else ""
    if undated and not any(r["status"] in ("ok", "repeat") for r in relative):
        return "# 확정 날짜(코드 계산)\n" + _UNDATED_HEAD + ("\n" + "\n".join(lines) if lines else "")
    if undated:
        head = _UNDATED_HEAD + f" Only lines that carry a date, such as mails, were resolved from that date ({base})."
    return (
        "# 확정 날짜(코드 계산)\n" + head + " The code resolved the material's relative dates. In the "
        "기한 cell write the dates below like 「2026-10-23(금)」, the date alone. "
        "Do not recalculate. Do not move a date that falls on a weekend unless the material says "
        "to.\n" + "\n".join(lines)
    )


def _lead_verdict(row: dict) -> str:
    """Lead-time date vs deadline, in words."""
    if "within" not in row:
        return ""
    slack = int(row["slack_days"])
    if row["within"]:
        return f"기한 {row['deadline']} 이내({slack}일 여유)" if slack else f"기한 {row['deadline']} 당일"
    return f"기한 {row['deadline']} 초과({-slack}일 늦음)"


def _lead_block(lead: dict) -> str:
    rows = lead.get("items") or []
    if not rows:
        return ""
    lines = []
    for r in rows[:30]:
        label = f"{r['label']}: " if r.get("label") else ""
        where = f" — 출처: 「{r['source_line'][:120]}」"
        if r["status"] != "ok":
            lines.append(f"- {label}「{r['text']}」 → could not be resolved ({r['reason']}): do not guess "
                         f"a date; keep the original wording{where}")
            continue
        verdict = _lead_verdict(r)
        lines.append(f"- {label}「{r['text']}」 → {r['shown']} ({r['anchor']} {r['start']} + "
                     f"{r['n']}{r['unit']})" + (f" · {verdict}" if verdict else "") + where)
    deadline = (f"\nThe deadline is the material's 「{lead['deadline_line'][:120]}」. Use the within/over "
                f"verdicts above as they are."
                if lead.get("deadline") else "")
    return (
        "# 확정 날짜(코드 계산 — 기준 날짜 + 기간)\nThe code turned periods such as the material's "
        "「발주 후 n주」 into dates by adding them to the base date the material states. For completion "
        "and delivery dates use the dates below as they are; do not recalculate.\n"
        + "\n".join(lines) + deadline
    )


_QUOTE_PROMPT = """Copy the values for the quote calculation from the material below into one JSON object. Do not calculate; copy only the values written in the material.

Schema:
{{"vat_basis": "excl" 또는 "incl",
 "items": [{{"name": "PM", "mm": 1.5, "monthly_price": 7000000}},
           {{"name": "GPU 임차", "qty": 3, "unit": "개월", "unit_price": 1200000}},
           {{"name": "강사료", "qty": [4, 16], "unit": "회×시간", "unit_price": 200000}},
           {{"name": "인증 수수료", "amount": 3300000, "excluded": true, "reason": "발주기관 부담"}}],
 "budget": {{"amount": 48000000, "vat_basis": "excl" 또는 "incl" 또는 ""}},
 "per_head": {{"n": 120}} 또는 null,
 "scenarios": [{{"name": "6개월", "premise": "PM 6MM", "overrides": [{{"name": "PM", "mm": 6}}], "remove": [], "add": []}}],
 "quotes": [{{"vendor": "A사", "amount": 45650000, "vat_basis": "incl"}}]}}

Rules:
- Amounts are integers in won (「900만 원」 → 9000000). Quantities and MM exactly as the material's numbers.
- Copy staff as "mm" and "monthly_price". If the MM is per person (「퍼블리셔 2명 각 4MM」): "mm": 4, "people": 2.
  If the table's MM cell is already the sum over several people (「AI 엔지니어 2명 | 12」), write only "mm": 12 and no people.
- For excluded items, copy the material's unit price and quantity as well.
- If the material does not state the VAT basis, "". Items the client pays for or that are out of scope: "excluded": true.
- Judge for each item whether to include or exclude it against the material's requirements (RFP 요구사항·보안·제약
  조건). Items that conflict with a requirement (e.g. overseas API fees when transfer abroad is
  banned or domestic processing is required), items the material marks as 대체·제외·불가·미포함, and whichever of
  two alternatives fails a requirement get "excluded": true and a "reason" (the requirement number and why, one
  line in Korean). Do not write excluded on items that are included.
- Quotes with only a per-vendor total go in quotes; calculations where an alternative or premise changes go in scenarios. Empty lists if none.
- If there are no items for a quote, {{"items": [], "quotes": []}}.

Material:
{material}

Answer with one JSON object only."""


def _material_numbers(text: str) -> list[float]:
    out = []
    for m in re.finditer(units._TERM, text or ""):
        try:
            out.append(units._ko_number(m.group(0)))
        except ValueError:
            continue
    return out


def _grounded(value: Any, numbers: list[float]) -> bool:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    return any(abs(number - n) < 1e-6 * max(1.0, abs(n)) for n in numbers)


def _item_grounded(item: dict, numbers: list[float]) -> bool:
    keys = [k for k in ("mm", "people", "monthly_price", "unit_price", "amount") if k in item]
    qty = item.get("qty")
    factors = qty if isinstance(qty, list) else ([qty] if qty is not None else [])
    if not keys and not factors:
        return False
    return all(_grounded(item[k], numbers) for k in keys) and all(
        _grounded(f, numbers) for f in factors)


#: in-country processing requirement: 「국외로 이전하지 않으며 …」, 「외부 LLM API 사용 불가」
_DOMESTIC_ONLY = re.compile(
    r"(?:국외|해외)\S{0,4}\s{0,4}(?:이전하지|이전\s{0,2}금지|이전\s{0,2}불가|두지\s{0,2}않)|"
    r"국내에\s{0,2}(?:둔다|두어야|설치|구축)|"
    r"외부\s{0,4}(?:LLM|API|AI|클라우드)\S{0,6}\s{0,4}(?:사용|호출|이용|연동)?\s{0,2}(?:금지|불가|하지\s{0,2}않)"
)
#: item sending data abroad or to an outside model: 「외부 LLM API 사용료」
_OFFSHORE_ITEM = re.compile(r"(?:외부|해외|국외)\s{0,4}(?:LLM|API|AI|리전|클라우드)", re.I)
_REQ_ID = re.compile(r"\b[A-Z]{2,4}-\d{2,3}\b")
#: material setting an item aside: 「(대체)」, 「제외」, 「불가」; bare 「대체」 names a replacement
_SET_ASIDE = re.compile(
    r"\(\s{0,2}(?:대체|제외|불가|미포함)[^()\n]{0,20}\)|제외|불가|미포함|발주기관\s{0,2}부담|"
    r"고객\s{0,2}부담|대체\s{0,2}(?:됨|되어|되므로|대상)"
)

_CLIENT_PAYS = re.compile(
    r"(?:발주\s{0,2}기관|발주처|수요\s{0,2}기관|고객사?)\S{0,2}\s{0,4}(?:별도로?\s{0,2}|직접\s{0,2})?부담"
)


def _ruled_out(item: dict, material: str) -> str:
    """Material's reason to rule out a quote item, or ""; set-aside word must follow the item's price."""
    name = str(item.get("name") or "")
    prices = [item[k] for k in ("monthly_price", "unit_price", "amount") if k in item]
    lines = (material or "").splitlines()
    key = re.sub(r"\s+", "", name)[:20]
    for line in lines:
        if not key or key not in re.sub(r"\s+", "", line):
            continue
        for m in re.finditer(units._TERM, line):
            try:
                value = units._ko_number(m.group(0))
            except ValueError:
                continue
            tail = re.split(r"[.,;·\n]", line[m.end():m.end() + 30], maxsplit=1)[0]
            if any(_grounded(p, [value]) for p in prices) and _SET_ASIDE.search(tail):
                return f"자료: 「{line.strip()[:60]}」"
    words = [w for w in re.split(r"[\s()·/,]+", name) if len(w) >= 2][:6]
    for line in lines:
        # RFP names the item, not its price: 「※ 품질인증 심사 수수료는 발주기관이 별도 부담함」
        matched = [w for w in words if w in line]
        pays = _CLIENT_PAYS.search(line)
        if (pays and len(matched) >= min(2, len(words)) and sum(map(len, matched)) >= 4
                and line.find(matched[0]) < pays.start()):
            return f"자료: 「{line.strip()[:60]}」"
    if _OFFSHORE_ITEM.search(name):
        for line in lines:
            if _DOMESTIC_ONLY.search(line):
                rule = _REQ_ID.search(line)
                head = f"{rule.group(0)} " if rule else ""
                return f"{head}국내 처리 요건과 충돌"
    return ""


_BASIS_MARK = re.compile(r"(?:VAT|부가세|부가가치세)\s{0,2}(별도|포함|제외)", re.I)
_UNIT_PRICE_LINE = re.compile(r"단가|MM|M/M|\d\s{0,2}(?:개월|명|대|식|회)\s{0,4}[×x*]|[×x*]\s{0,2}\d")
_BUDGET_LINE = re.compile(r"예산|기초금액|사업비|추정\s{0,2}가격|사업\s{0,2}금액")


def price_basis(material: str) -> str:
    """「excl」/「incl」 if material price lines state the VAT basis, else ""; budget basis ignored."""
    marks = {
        "incl" if m.group(1) == "포함" else "excl"
        for line in (material or "").splitlines()
        if _UNIT_PRICE_LINE.search(line) and not _BUDGET_LINE.search(line)
        for m in _BASIS_MARK.finditer(line)
    }
    return marks.pop() if len(marks) == 1 else ""


def grounded_quote_input(data: dict, material: str) -> tuple[dict, list[str]]:
    """Quote input minus unstated values; ruled-out items kept as `excluded` with `reason`."""
    numbers = _material_numbers(material)
    dropped: list[str] = []
    items = []
    set_aside = []
    for item in data.get("items") or []:
        if isinstance(item, dict) and item.get("name") and _item_grounded(item, numbers):
            kept = {k: item[k] for k in ("name", "mm", "people", "monthly_price", "qty", "unit",
                                         "unit_price", "amount", "note")
                    if k in item and item[k] is not None}
            if "monthly_price" in kept and "mm" not in kept and "unit_price" not in kept:
                # 「qty: 7, unit: MM, monthly_price」 = unit price
                kept["unit_price"] = kept.pop("monthly_price")
            reason = str(item.get("reason") or "").strip()[:120] if item.get("excluded") else ""
            if item.get("excluded") and not reason:
                reason = str(item.get("note") or "").strip()[:120] or "자료에서 범위 밖"
            reason = reason or _ruled_out(kept, material)
            priced = any(k in kept for k in ("monthly_price", "unit_price", "amount"))
            if not priced:
                # priceless excluded item: set aside, not priced
                if reason:
                    set_aside.append({"name": kept["name"], "excluded": True, "reason": reason})
                else:
                    dropped.append(str(kept["name"]))
                continue
            if reason:
                kept["excluded"] = True
                kept["reason"] = reason
                kept.setdefault("note", reason)
            items.append(kept)
        elif isinstance(item, dict):
            dropped.append(str(item.get("name") or "?"))
    # stated price basis beats model's; unstated = VAT 별도 regardless of budget basis
    stated = price_basis(material)
    basis = stated or "excl"
    out: dict[str, Any] = {"vat_basis": basis, "items": items}
    if not stated:
        out["basis_assumed"] = True
    if set_aside:
        out["set_aside"] = set_aside
    budget = data.get("budget")
    if isinstance(budget, dict) and _grounded(budget.get("amount"), numbers):
        out["budget"] = {"amount": budget["amount"], "vat_basis": budget.get("vat_basis") or ""}
    per_head = data.get("per_head")
    if isinstance(per_head, dict) and _grounded(per_head.get("n"), numbers):
        out["per_head"] = {"n": per_head["n"]}
    scenarios = []
    for spec in data.get("scenarios") or []:
        if not isinstance(spec, dict):
            continue
        changes = [c for c in (spec.get("overrides") or []) + (spec.get("add") or [])
                   if isinstance(c, dict)]
        if all(_item_grounded({k: v for k, v in c.items() if k != "name"} | {"name": c.get("name")},
                              numbers) or set(c) <= {"name", "excluded", "note"} for c in changes) \
                and all(any(k in c for k in ("monthly_price", "unit_price", "amount"))
                        for c in spec.get("add") or [] if isinstance(c, dict)):
            scenarios.append({k: spec[k] for k in ("name", "premise", "overrides", "remove", "add")
                              if spec.get(k)})
        else:
            dropped.append(f"시나리오 {spec.get('name') or '?'}")
    if scenarios:
        out["scenarios"] = scenarios
    quotes = [q for q in data.get("quotes") or []
              if isinstance(q, dict) and _grounded(q.get("amount"), numbers)]
    if quotes:
        out["quotes"] = [{"vendor": q.get("vendor") or "", "amount": q["amount"],
                          "vat_basis": q.get("vat_basis") or ""} for q in quotes]
    return out, dropped


def excluded_items(data: dict | None) -> list[dict]:
    """Quote items left out of the price, with reasons."""
    data = data or {}
    return [*(i for i in data.get("items") or [] if i.get("excluded")),
            *(data.get("set_aside") or [])]


#: material mentions VAT: only then flag a quote with no basis
_VAT_WORD = re.compile(r"부가세|부가가치세|VAT", re.I)


def _quote_block(result: dict, excluded: list[dict] | None = None, assumed: bool = False,
                 material: str = "") -> str:
    lines = ["# 견적 확정 블록(코드 계산)",
             "The code calculated the amounts below from the material's unit prices and quantities (in "
             "won, VAT truncated below one won). For the quote table's 소계·공급가액(VAT 별도)·부가세·합계(VAT "
             "포함) and the amounts against the budget, use only these values; do not recalculate. Do not "
             "change unit prices. Label every amount with its basis, such as 「공급가액(VAT 별도)」 「부가세」 "
             "「합계(VAT 포함)」, never as a bare 「총액」. Do not divide VAT back out to make a different "
             "공급가액."]
    if result.get("lines"):
        lines.append(result["table_markdown"])
        lines.append(f"- 공급가액(VAT 별도): {result['supply_official']}")
        lines.append(f"- 부가세: {result['vat']:,}원")
        lines.append(f"- 합계(VAT 포함): {result['total_official']}")
        if result.get("vat_basis") == "incl":
            lines.append("- The material's unit prices include VAT, so 공급가액 was worked back from the total.")
        elif assumed:
            lines.append("- The material's unit prices state no VAT basis, so they were taken as 부가세 별도. "
                         "State this premise in the quote.")
    for item in excluded or []:
        lines.append(f"- 제외: {item['name']} ({item['reason']}) — not part of the quote amount.")
    budget = result.get("budget")
    if budget and budget.get("vat_basis") in ("excl", "incl"):
        basis = "부가세 별도" if budget["vat_basis"] == "excl" else "부가세 포함"
        word = "여유" if budget["within"] else "초과"
        lines.append(f"- 예산 {budget['amount']:,}원({basis}) 대비 같은 기준 {budget['compared']:,}원: "
                     f"{word} {abs(budget['headroom']):,}원")
    elif budget:
        lines.append(f"- 예산 {budget['amount']:,}원: the material gives no VAT basis — taken as 별도: "
                     f"{'여유' if budget['if_excl']['within'] else '초과'} {abs(budget['if_excl']['headroom']):,}원, "
                     f"taken as 포함: {'여유' if budget['if_incl']['within'] else '초과'} "
                     f"{abs(budget['if_incl']['headroom']):,}원. State which basis is the premise.")
    if head := result.get("per_head"):
        lines.append(f"- 1인당({head['n']}명): 부가세 별도 {head['excl']:,}원, 포함 {head['incl']:,}원")
    for row in result.get("scenarios") or []:
        extra = ""
        if b := row.get("budget"):
            if b.get("vat_basis") in ("excl", "incl"):
                extra = f", 예산 대비 {'여유' if b['within'] else '초과'} {abs(b['headroom']):,}원"
        lines.append(f"- 시나리오 「{row['name']}」({row['premise'] or '전제 없음'}): 공급가액 "
                     f"{row['supply']:,}원(VAT 별도), 부가세 {row['vat']:,}원, 합계(VAT 포함) "
                     f"{row['total']:,}원{extra}")
    for row in result.get("quotes") or []:
        if "incl" in row:
            ok = ""
            if "within_budget" in row:
                ok = f", 예산 {'안' if row['within_budget'] else '초과'}({row['headroom']:,}원)"
            lines.append(f"- 견적 {row['vendor']}: 부가세 별도 {row['excl']:,}원 / 포함 {row['incl']:,}원{ok}")
        elif _VAT_WORD.search(material):
            lines.append(f"- 견적 {row['vendor']}: {row['amount']:,}원(부가세 기준 불분명)")
        else:
            lines.append(f"- 견적 {row['vendor']}: {row['amount']:,}원 (as the material writes it)")
    settled = [row for row in result.get("quotes") or [] if "incl" in row]
    if len(settled) >= 2:
        # the comparison itself, by code: lowest and each gap on the same (VAT-inclusive) basis
        ranked = sorted(settled, key=lambda row: row["incl"])
        low = ranked[0]
        lines.append("- 부가세 포함 기준 낮은 순: " + " < ".join(
            f"{row['vendor']} {row['incl']:,}원" for row in ranked)
            + f" → 가장 낮은 견적: {low['vendor']}; " + ", ".join(
                f"{row['vendor']} +{row['incl'] - low['incl']:,}원" for row in ranked[1:])
            + " (settled facts: state them as given)")
    return "\n".join(lines)


def _quote_figures(result: dict) -> list[key_figures.Figure]:
    if not result.get("lines"):
        return []
    return [
        key_figures.Figure("견적 공급가액(VAT 별도)", f"{result['supply']:,}원", ["공급가액"]),
        key_figures.Figure("견적 부가세", f"{result['vat']:,}원", ["부가세"]),
        key_figures.Figure("견적 합계(VAT 포함)", f"{result['total']:,}원", ["합계(VAT 포함)", "총액"]),
    ]


# career periods: 「2019.04 ~ 2021.01」, 「2023.03~ 현재」, 「2019년 4월 – 2021년 1월」
_YM = r"(?:20|19)?\d{2}\s{0,2}(?:[./-]|년)\s{0,2}\d{1,2}\s{0,2}월?"
_PERIOD = re.compile(
    rf"(?P<start>{_YM})\s{{0,3}}[~∼〜–—-]\s{{0,3}}(?P<end>{_YM}|현재|재직\s{{0,2}}중|진행\s{{0,2}}중)?"
)
_VAGUE_PERIOD = re.compile(r"(?<!\d)\d{2,4}\s{0,2}년\s{0,2}(?:봄|여름|가을|겨울|초|중반|말|쯤)")
_SIDE_WORK = re.compile(r"프리랜서|병행|겸업|부업|사이드|외주")
_AS_OF = re.compile(r"(20\d{2})\s{0,2}(?:년\s{0,2}|[./-])(\d{1,2})\s{0,2}월?\s{0,2}(?:기준|현재)|오늘\s{0,2}\((20\d{2})년\s{0,2}(\d{1,2})월\)")


def career_periods(material: str, request: str, today: dt.date | None = None) -> dict | None:
    dates = skill_scripts.dates()
    periods, vague = [], []
    for line in (material or "").splitlines():
        if _VAGUE_PERIOD.search(line):
            vague.append(line.strip()[:120])
        m = _PERIOD.search(line)
        if not m:
            continue
        label = (line[:m.start()] + " " + line[m.end():]).strip(" -·:|*()[]")
        label = re.sub(r"\s+", " ", label)[:60] or f"기간 {len(periods) + 1}"
        end = m.group("end") or "현재"
        periods.append({"label": label, "start": m.group("start"), "end": end,
                        "side": bool(_SIDE_WORK.search(line))})
    if not periods:
        return None
    as_of = None
    if m := _AS_OF.search(request or ""):
        as_of = f"{m.group(1) or m.group(3)}-{int(m.group(2) or m.group(4)):02d}"
    elif today:
        as_of = f"{today.year}-{today.month:02d}"
    try:
        result = dates.span([
            {"label": p["label"], "start": p["start"], "end": p["end"], "counted": not p["side"]}
            for p in periods
        ], as_of)
    except (ValueError, KeyError) as exc:
        log.info("career periods not counted: %s", exc)
        return None
    result["vague"] = vague
    return result


def _career_block(span: dict) -> str:
    lines = ["# 기간 계산(코드)",
             f"Month counts with both the first and the last month counted (to {span['as_of']} for a current "
             "job). Use only these values for each period and the total career; do not recalculate. Write "
             "periods as 「2019.04 ~ 2021.01」, or 「2023.03 ~ 현재」 for a current job."]
    for p in span["periods"]:
        side = "" if p["counted"] else " — concurrent with another job: not added to the total career"
        years, months = p["years_months"]
        shown = f"{years}년 {months}개월" if years and months else f"{years}년" if years else f"{months}개월"
        lines.append(f"- {p['label']}: {p['start'].replace('-', '.')} ~ "
                     f"{'현재' if p['ongoing'] else p['end'].replace('-', '.')} = {p['months']}개월({shown}){side}")
    if span.get("vague"):
        lines.append("- Some periods are not fixed to the month: " + " / ".join(span["vague"][:3])
                     + " — for the total career, state the assumption (e.g. 4월) or ask for confirmation.")
    else:
        lines.append(f"- 총 경력(겹침 제외): {span['total_months']}개월 = {span['total_text']}")
    for gap in span.get("gaps") or []:
        lines.append(f"- 공백: {gap['start'].replace('-', '.')} ~ {gap['end'].replace('-', '.')} "
                     f"({gap['months']}개월) — do not hide it or stretch periods over it")
    return "\n".join(lines)


_SCORE_PROMPT = """From the material and the request below, copy into one JSON object the values for scoring the alternatives and checking the must-have conditions. Do not calculate.

Schema:
{{"criteria": [{{"name": "비용", "weight": 50, "kind": "cost"}},
              {{"name": "앱", "weight": 30, "kind": "yesno"}},
              {{"name": "성능", "weight": 20, "kind": "benefit"}}],
 "musts": [{{"name": "SSO", "kind": "yesno"}},
           {{"name": "화상회의 인원", "kind": "min", "limit": 60, "unit": "명"}},
           {{"name": "1대 가격", "kind": "max", "limit": 4000000, "unit": "원", "vat_basis": "incl"}}],
 "options": [{{"name": "런큐브", "values": {{"비용": 18000000, "앱": "있음", "성능": null}},
              "musts": {{"SSO": "있음", "화상회의 인원": 100, "1대 가격": {{"value": 4200000, "vat_basis": "excl"}}}}}}]}}

- kind: cost (lower is better), benefit (higher is better), yesno (있음/없음). weight exactly as the number in the request or material.
- musts: only the conditions the request says must be met (필수, 꼭, 안 된다, 이내, 이상). max is 이하·이내, min is 이상. [] if none.
- null when the material has no value. If there are no weights at all, {{"criteria": []}}.
- If an amount's VAT basis differs from the condition's, do not convert it; leave it as {{"value": 값, "vat_basis": "excl" 또는 "incl"}}.

Request:
{request}

Material:
{material}

Answer with one JSON object only."""


def _q(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def scores(data: dict) -> dict | None:
    """Weighted scores, rules S1–S4.
    S1 cost: lowest ÷ own × w. S2 benefit: own ÷ highest × w; 있음/없음: w or 0.
    S3 missing value: (a) dropped and rescaled to 100, (b) scored 0 — both shown. S4 ties reported."""
    criteria = [c for c in data.get("criteria") or [] if isinstance(c, dict) and c.get("name")]
    options = [o for o in data.get("options") or [] if isinstance(o, dict) and o.get("name")]
    if not criteria or len(options) < 2:
        return None
    weights = {c["name"]: Decimal(str(c.get("weight") or 0)) for c in criteria}
    total_weight = sum(weights.values())
    table: dict[str, dict[str, float | None]] = {o["name"]: {} for o in options}
    missing: set[str] = set()
    for c in criteria:
        name, kind, weight = c["name"], str(c.get("kind") or "benefit"), weights[c["name"]]
        raw = {o["name"]: (o.get("values") or {}).get(name) for o in options}
        if kind == "yesno":
            for option, value in raw.items():
                if value is None:
                    missing.add(name)
                    table[option][name] = None
                else:
                    yes = str(value).strip() in ("있음", "예", "O", "o", "true", "True", "1", "충족")
                    table[option][name] = _q(weight if yes else Decimal(0))
            continue
        numbers = {}
        for option, value in raw.items():
            try:
                numbers[option] = Decimal(str(value).replace(",", "")) if value is not None else None
            except ArithmeticError:
                numbers[option] = None
        present = [v for v in numbers.values() if v is not None and v > 0]
        if not present:
            missing.add(name)
            for option in table:
                table[option][name] = None
            continue
        best = min(present) if kind == "cost" else max(present)
        for option, value in numbers.items():
            if value is None or value <= 0:
                missing.add(name)
                table[option][name] = None
            else:
                ratio = best / value if kind == "cost" else value / best
                table[option][name] = _q(ratio * weight)
    out_rows = {}
    for option, row in table.items():
        zero = sum(Decimal(str(v)) for v in row.values() if v is not None)
        kept_weight = sum(w for n, w in weights.items() if n not in missing)
        kept = sum(Decimal(str(v)) for n, v in row.items() if v is not None and n not in missing)
        out_rows[option] = {
            "parts": row,
            "total": _q(zero),
            "renormalized": _q(kept / kept_weight * 100) if kept_weight else None,
        }
    best_total = max(r["total"] for r in out_rows.values())
    leaders = [o for o, r in out_rows.items() if r["total"] == best_total]
    return {"criteria": [{"name": c["name"], "weight": float(weights[c["name"]]),
                          "kind": c.get("kind") or "benefit"} for c in criteria],
            "weight_sum": float(total_weight), "missing": sorted(missing),
            "options": out_rows, "leaders": leaders, "tie": len(leaders) > 1}


def _score_block(result: dict) -> str:
    names = [c["name"] for c in result["criteria"]]
    head = "| 대안 | " + " | ".join(f"{c['name']}({c['weight']:g})" for c in result["criteria"]) + " | 합계 |"
    if result["missing"]:
        head += " 자료 없음 제외 환산 |"
    rows = [head, "|" + "---|" * (len(names) + 2 + bool(result["missing"]))]
    for option, row in result["options"].items():
        parts = " | ".join("자료 없음" if row["parts"].get(n) is None else f"{row['parts'][n]:g}"
                           for n in names)
        line = f"| {option} | {parts} | {row['total']:g} |"
        if result["missing"]:
            line += f" {row['renormalized']:g} |"
        rows.append(line)
    notes = [f"가중치 합 {result['weight_sum']:g}"
             + ("" if abs(result["weight_sum"] - 100) < 1e-9 else " — not 100. State that the request's weights were used as given")]
    if result["missing"]:
        notes.append("자료 없는 기준(" + ", ".join(result["missing"]) + "): show both the total with it scored 0 "
                     "and the value rescaled to 100 without that criterion")
    if result["tie"]:
        notes.append("동점: " + ", ".join(result["leaders"]) + " — do not break the tie by order; write that it is a tie")
    return ("# 점수 계산(코드)\nCost: lowest cost ÷ that alternative's cost × weight; higher-is-better values: "
            "the value ÷ the highest value × weight; 있음/없음: weight or 0. Use only these values in the "
            "score table.\n" + "\n".join(rows) + "\n- "
            + "\n- ".join(notes))


_COST_PROMPT = """From the request and the material below, copy into one JSON object the values for computing each alternative's total cost. Do not calculate; copy only the material's numbers.

Schema (the example comes from other material; take names and numbers from the material below):
{{"period": "3년", "years": 3,
 "options": [
   {{"name": "<대안 1>", "items": [
     {{"name": "구입가", "unit_price": 2500000, "count": 3, "per": "once"}},
     {{"name": "지원금", "unit_price": 500000, "count": 1, "per": "once", "sign": -1}},
     {{"name": "매각액", "unit_price": 2500000, "count": 3, "per": "once", "rate": 0.2, "sign": -1}},
     {{"name": "유지비", "unit_price": 40000, "count": 3, "per": "month", "years": 3}},
     {{"name": "보험", "unit_price": 150000, "count": 3, "per": "year", "years": 3}}]}},
   {{"name": "<대안 2>", "items": [
     {{"name": "임대료", "unit_price": 90000, "count": 3, "per": "month", "months": 36}},
     {{"name": "출력 요금", "unit_price": 10, "count": 3, "per": "장", "usage": 2000, "years": 3}},
     {{"name": "초과 사용료", "unit_price": 15, "count": 3, "per": "장", "usage": 2000,
      "threshold": 1500, "years": 3}}]}}],
 "scenarios": [{{"name": "연 3,000장", "replace": [{{"from": 2000, "to": 3000}}]}}]}}

Fields (the code does the multiplication; do not multiply or add anything yourself):
- unit_price: the unit price exactly as written in the material (won). count: the number of units, people or items the price applies to — only when the material prices each one (「대당」「1인당」「/명」); a price for the whole setup (「A100 4장, 시간당 12,000원」) has count 1.
- per: the unit of the price. "once" (one time), "year" (each year), "month" (each month), otherwise the usage unit
  ("km", "시간", "장", "건" …).
- years: the number of years compared. For a monthly price, "months" if the material gives the number of months, otherwise only years.
- usage: the yearly usage per unit (per person) for a price per usage unit. threshold: when only the usage above this value
  is charged, that threshold (「연 20,000km 초과 시」). Leave it out when there is none.
- rate: only when the price is a ratio of the unit price (35% → 0.35). sign: -1 for items that reduce the cost (보조금·지원금·잔존가치·매각액·환급).
- A discount is its own item with sign -1: the discounted price as unit_price, the same count and per, and the percentage as rate
  (「월 14,000원, 15% 할인」 → {{"name": "할인", "unit_price": 14000, "count": 40, "per": "month", "rate": 0.15, "sign": -1}}).
- from_year: when an item starts in a later year (「2년 차부터 10% 할인」 → 2). Leave it out when it applies from the first year.
- when: when an item applies only under a condition the material states (「연 단위 계약 시」, 「3년 약정 시」), that condition
  in the material's words. The code prices the alternatives with and without it.

Rules:
- Put every alternative in the material in options, none left out, each with its cost items (items).
- A 「연간」 comparison is years 1.
- Do not add separately a cost already included in another item (「보험·정비 포함」). Add costs stated as separate (별도).
- If sources give different values, use the more recent official source's value and its conditions (such as a cap on the number of subsidized units).
- scenarios: write the premise changes the request asks about (mileage, people or period going up) as the numbers that change. [] if none.
- If there are no cost items per alternative, {{"options": []}}.

Request:
{request}

Material:
{material}

Answer with one JSON object only."""


#: units a thing is counted in (「4장」「40명」「3대」), not usage (「2,000시간」「15,000km」)
_COUNTED_IN = r"장|개|대|명|인|석|좌석|부|권|세트|식|계정|카드|노드|copy|units?|seats?|users?"


def _count_alternatives(unit: str) -> str:
    return "명|인" if unit in ("명", "인") else re.escape(unit)


def priced_per_unit(count: Any, text: str) -> bool:
    """Whether a price is multiplied by `count` in the material: a count of things (「4장」)
    needs a per-unit price there (「대당」「1인」「/명」「× 40명」); a hourly price for 「A100
    4장」 is the whole instance's. Counts of usage (「2,000시간」) or unwritten stand."""
    plain = _plain(count)
    try:
        number = float(plain) if plain is not None else 1.0
    except ValueError:
        return True
    if number <= 1 or number != int(number):
        return True
    shown = "|".join(re.escape(f) for f in dict.fromkeys((f"{int(number):,}", str(int(number)))))
    units = {m.group(1) for m in re.finditer(
        rf"(?<![\d.,])(?:{shown})\s{{0,2}}({_COUNTED_IN})(?![가-힣A-Za-z])", text or "")}
    if not units:
        return True
    for unit in units:
        alt = _count_alternatives(unit)
        if re.search(rf"(?:{alt})\s{{0,2}}당|(?<![\d.,])1\s{{0,2}}(?:{alt})(?![가-힣\d])"
                     rf"|/\s{{0,2}}(?:{alt})(?![가-힣])|[×x*]\s{{0,2}}(?:{shown})\s{{0,2}}(?:{alt})"
                     rf"|(?:{shown})\s{{0,2}}(?:{alt})\s{{0,2}}[×x*]", text or ""):
            return True
    return False


def counted_as_written(data: dict, text: str) -> dict:
    """The cost reading with a count the material does not price per unit set to 1."""
    for option in data.get("options") or []:
        for item in (option.get("items") or []) if isinstance(option, dict) else []:
            if isinstance(item, dict) and "count" in item and not priced_per_unit(
                    item.get("count"), text):
                log.info("cost item %r: count %s is not priced per unit in the material",
                         logs.safe(str(item.get("name"))), logs.safe(str(item.get("count"))))
                item["count"] = 1
    return data


_ONCE = ("once", "한 번", "1회", "일시")
_YEAR = ("year", "연", "년", "annual")
_MONTH = ("month", "월", "개월")


def _plain(raw: Any) -> Any:
    """Model's number field: 「15,000」 or 「15,000km」 → 15000."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw
    m = re.match(r"\s{0,4}(\d[\d,]{0,20}(?:\.\d{1,10})?)", str(raw or ""))
    return m.group(1).replace(",", "") if m else None


def explicit_factors(item: dict, years: Any) -> dict | None:
    """Named fields → factors and `over` for code to multiply; `None` if a needed field is missing."""
    price = _plain(item.get("unit_price"))
    if price is None:
        return None
    count = _plain(item.get("count")) or 1
    per = str(item.get("per") or "").strip().lower()
    span = _plain(item.get("years")) or _plain(years)
    rate = _plain(item.get("rate"))
    tail = [rate] if rate is not None else []
    calendar = False
    if per in _ONCE:
        factors = [count, price, *tail]
        over = None
    elif per in _YEAR:
        if span is None:
            return None
        factors, over = [count, price, span, *tail], None
    elif per in _MONTH:
        months = _plain(item.get("months"))
        if months is None and span is None:
            return None
        factors, over = ([count, price, months, *tail] if months is not None
                         else [count, price, span, 12, *tail]), None
        calendar = months is None
    else:
        usage = _plain(item.get("usage"))
        if usage is None or span is None:
            return None
        threshold = _plain(item.get("threshold"))
        if threshold is None:
            factors, over = [count, price, usage, span], None
        else:
            factors, over = [count, price, span], [usage, threshold]
    start = _plain(item.get("from_year"))
    code: list[float] = []
    if start is not None and float(start) > 1 and per not in _ONCE:
        # 「2년 차부터」: remaining years by code (3년 → 2)
        first = int(float(start))
        if per in _MONTH and _plain(item.get("months")) is not None:
            left = float(_plain(item.get("months"))) - 12 * (first - 1)
            factors = [count, price, left, *tail]
        else:
            if span is None:
                return None
            left = float(span) - (first - 1)
            # span is the third factor for yearly, monthly (× 12) or usage prices
            at = 3 if per not in _YEAR and per not in _MONTH and over is None else 2
            factors = [*factors[:at], left, *factors[at + 1:]]
        if left <= 0:
            return None
        code.append(left)
    out = {"name": item.get("name"), "factors": factors}
    if calendar:
        out["calendar"] = True
    if code:
        out["code"] = code
    if over:
        out["over"] = over
    if item.get("sign") in (-1, "-1"):
        out["sign"] = -1
    if str(item.get("when") or "").strip():
        out["when"] = str(item["when"]).strip()[:40]
    if (vat := str(item.get("vat") or "").strip().lower()) in ("incl", "excl"):
        out["vat"] = vat
    out["raw"] = {k: item.get(k) for k in ("unit_price", "count", "per", "months", "usage",
                                           "threshold", "rate", "from_year", "years", "vat")}
    return out


def _vat_added(amount: int) -> int:
    """A VAT-exclusive amount with its 10% VAT (truncated below one won), sign kept."""
    return amount + (abs(amount) // 10) * (1 if amount >= 0 else -1)


#: a VAT basis written right after a price: 「1,320,000원(부가세 별도)」, 「(VAT 포함)」
_VAT_AFTER = re.compile(r"^[^\n\d]{0,6}?\(?\s{0,2}(?:부가세|부가가치세|VAT)\s{0,2}(별도|미포함|제외|포함)",
                        re.I)
#: the budget the request states: 「예산 4,800만원(부가세 포함)」
_BUDGET = re.compile(r"예산\s{0,2}(?:은|는|:|：)?\s{0,2}(\d[\d,.]*\s{0,2}(?:억|천만|백만|만)?)\s{0,2}원"
                     r"(?:\s{0,2}\(?\s{0,2}(?:부가세|부가가치세|VAT)\s{0,2}(별도|미포함|제외|포함))?",
                     re.I)


def _vat_written(price: Any, material: str) -> str:
    """「incl」/「excl」 when the material writes that basis right after this price, else ''."""
    value = _plain(price)
    if value is None or not material:
        return ""
    number = float(value)
    seen = set()
    for m in re.finditer(r"(?<![\d,.])(\d[\d,]*(?:\.\d+)?)\s{0,2}(억|천만|백만|만)?\s{0,2}원", material):
        try:
            written = units._ko_number(m.group(1) + (m.group(2) or ""))
        except ValueError:
            continue
        if abs(written - number) > 1e-6 * max(1.0, number):
            continue
        if said := _VAT_AFTER.match(material[m.end():m.end() + 24]):
            seen.add("incl" if said.group(1) == "포함" else "excl")
    return next(iter(seen)) if len(seen) == 1 else ""


def _budget_of(data: dict, numbers: list[float], material: str = "") -> dict | None:
    """The stated budget `{"amount", "vat"}`: read from the material by code, else the
    reading's when the material gives that amount."""
    if m := _BUDGET.search(material or ""):
        try:
            amount = units._ko_number(m.group(1).replace(" ", ""))
        except ValueError:
            amount = None
        if amount:
            return {"amount": int(amount),
                    "vat": "" if not m.group(2) else "incl" if m.group(2) == "포함" else "excl"}
    budget = data.get("budget")
    if not isinstance(budget, dict):
        return None
    amount = _plain(budget.get("amount"))
    if amount is None or not _grounded(amount, numbers):
        return None
    vat = str(budget.get("vat") or "").strip().lower()
    return {"amount": int(float(amount)), "vat": vat if vat in ("incl", "excl") else ""}


def _judged_against(budget: dict | None, totals: dict[str, dict], basis: str) -> dict | None:
    """Each option's room under the budget on the budget's VAT basis (negative = over)."""
    if not budget:
        return None
    if budget["vat"] == basis:
        compared = {name: e["total"] for name, e in totals.items()}
    elif budget["vat"] == "incl" and basis == "excl":
        compared = {name: _vat_added(e["total"]) for name, e in totals.items()}
    else:
        return None  # one side's VAT basis unknown, or a VAT-exclusive budget: not judged
    return {"amount": budget["amount"], "vat": budget["vat"],
            "room": {name: budget["amount"] - total for name, total in compared.items()},
            "compared": compared}


def year_amount(item: dict, year: int) -> Decimal | None:
    """Cost of `item` in comparison year `year` (1-based); None if no fields to price a year."""
    raw = item.get("raw")
    if not raw:
        return None
    price = _plain(raw.get("unit_price"))
    if price is None:
        return None
    count = Decimal(str(_plain(raw.get("count")) or 1))
    per = str(raw.get("per") or "").strip().lower()
    rate = _plain(raw.get("rate"))
    amount = count * Decimal(str(price)) * (Decimal(str(rate)) if rate is not None else 1)
    start = int(float(_plain(raw.get("from_year")) or 1))
    if per in _ONCE:
        amount = amount if year == 1 else Decimal(0)
    elif year < start:
        amount = Decimal(0)
    elif per in _MONTH:
        months = _plain(raw.get("months"))
        if months is not None and Decimal(str(months)) <= 12:
            # a monthly fee read with the comparison's 12 months recurs the next year too, not
            # 0원
            left = Decimal(str(months))
        else:
            left = Decimal(12) if months is None else max(
                Decimal(0), min(Decimal(12), Decimal(str(months)) - 12 * (year - 1)))
        amount *= left
    elif per not in _YEAR:
        usage = _plain(raw.get("usage"))
        if usage is None:
            return None
        threshold = _plain(raw.get("threshold"))
        amount *= max(Decimal(0), Decimal(str(usage)) - Decimal(str(threshold or 0)))
    amount = amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return -amount if item.get("sign") == -1 else amount


def _cost_item(item: dict, swap: dict[float, Decimal]) -> Decimal:
    def value(raw: Any) -> Decimal:
        number = Decimal(str(raw).replace(",", ""))
        return swap.get(float(number), number)

    amount = Decimal(1)
    for factor in item["factors"]:
        amount *= value(factor)
    if over := item.get("over"):
        amount *= max(Decimal(0), value(over[0]) - value(over[1]))
    return amount.quantize(Decimal(1), rounding=ROUND_HALF_UP) * (-1 if item.get("sign") == -1 else 1)


#: monthly price over years: ×12 is a factor no material writes
_MONTHLY = re.compile(r"월\s{0,2}(?:[\d,]|요금|사용료|임대료|관리비|구독료|이용료)|/\s{0,2}월|매월")
_YEARLY = re.compile(r"\d\s{0,2}년|연간|연\s{0,2}(?:비용|기준|단위|총액)")


def _calendar(text: str) -> list[float]:
    """Allowed calendar factors: 12 when monthly prices meet years."""
    return [12.0] if _MONTHLY.search(text or "") and _YEARLY.search(text or "") else []


def _percent(value: Any, numbers: list[float]) -> bool:
    """「35%」 → 0.35; 「15% 할인」 → 0.85."""
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    return 0 < number < 1 and (_grounded(round(number * 100, 6), numbers)
                               or _grounded(round((1 - number) * 100, 6), numbers))


def option_costs(data: dict, numbers: list[float], why: list[str] | None = None, *,
                 material: str = "") -> dict | None:
    """Each option's total from material-stated factors; `None` (reason in `why`) if < 2 options left.
    `material`: where each price's VAT basis and the budget are read by code."""
    def grounded(item: Any) -> bool:
        if not isinstance(item, dict) or not item.get("name"):
            return False
        factors = item.get("factors")
        over = item.get("over")
        if not isinstance(factors, list) or not factors or len(factors) > 8:
            return False
        if over is not None and not (isinstance(over, list) and len(over) == 2):
            return False
        # factor 1 (unwritten 「1대」) allowed
        allowed = [*numbers, 1.0, *([12.0] if item.get("calendar") else []),
                   *(float(c) for c in item.get("code") or [])]
        return all(_grounded(f, allowed) or _percent(f, numbers) for f in factors) and all(
            _grounded(f, numbers) for f in over or [])

    dropped: list[str] = []
    options = []
    for option in data.get("options") or []:
        if not isinstance(option, dict) or not option.get("name"):
            continue
        items = []
        for item in option.get("items") or []:
            if isinstance(item, dict) and "factors" not in item and "unit_price" in item:
                item = explicit_factors(item, data.get("years")) or {"name": item.get("name")}
            if grounded(item):
                # the basis the material writes beside this price wins over the reading's
                if said := _vat_written((item.get("raw") or {}).get("unit_price"), material):
                    item = {**item, "vat": said}
                items.append(item)
            elif isinstance(item, dict):
                dropped.append(f"{option['name']} {item.get('name') or '?'}")
        if items:
            options.append({"name": str(option["name"])[:40], "items": items})
    if len(options) < 2:
        if why is not None:
            why.append(f"비용을 셀 수 있는 대안 {len(options)}개"
                       + (f" (자료에 없는 값이라 뺀 것: {', '.join(dropped[:4])})" if dropped else ""))
        return None

    conditions = sorted({i["when"] for o in options for i in o["items"] if i.get("when")})
    # a condition the material's own figures meet (「30대 이상 구매 시 5% 할인」 for 30 units)
    # is applied, not offered as a scenario (a dropped scenario leaves the undiscounted total)
    applied: list[str] = []
    for option in options:
        met = [c for c in conditions if any(i.get("when") == c for i in option["items"])
               and condition_met(c, option["items"], data.get("years"))]
        current = [i for i in option["items"] if not i.get("when")]
        for condition in met:
            _, by = conditional_items(
                [*current, *(i for i in option["items"] if i.get("when") == condition)],
                [condition], data.get("years"))
            current = [{k: v for k, v in i.items() if k != "when"} for i in by[condition]]
        option["rest"] = [*current, *(i for i in option["items"]
                                      if i.get("when") and i["when"] not in met)]
        applied += [f"{c} ({option['name']})" for c in met]
    conditions = [c for c in conditions
                  if any(any(i.get("when") == c for i in o["rest"]) for o in options)]
    for option in options:
        option["base"], option["by"] = conditional_items(option.pop("rest"), conditions,
                                                         data.get("years"))

    # VAT-exclusive and VAT-inclusive prices side by side: all put on the inclusive basis
    stated = {i.get("vat") for o in options for i in o["items"]} - {None}
    to_incl = stated == {"incl", "excl"}
    basis = "incl" if to_incl else (next(iter(stated)) if len(stated) == 1 and all(
        i.get("vat") for o in options for i in o["items"]) else "")

    def priced(item: dict, amount: int) -> int:
        return _vat_added(amount) if to_incl and item.get("vat") == "excl" else amount

    def run(swap: dict[float, Decimal], conditions: frozenset[str] = frozenset()) -> dict[str, dict]:
        out = {}
        for option in options:
            # condition scenario (「연 단위 계약 시」) priced with the condition; base without
            items = (option["by"][next(iter(conditions))] if conditions else option["base"])
            rows = [{"name": str(i["name"])[:40], "amount": priced(i, int(_cost_item(i, swap))),
                     "factors": i["factors"], "over": i.get("over"),
                     **({"vat_added": True} if to_incl and i.get("vat") == "excl" else {})}
                    for i in items]
            entry = {"rows": rows, "total": sum(r["amount"] for r in rows)}
            if not swap:
                years = [[year_amount(i, y) for i in items] for y in (1, 2)]
                if all(a is not None for row in years for a in row):
                    first, later = (zip(items, row, strict=True) for row in years)
                    entry["first_year"] = sum(priced(i, int(a)) for i, a in first)
                    entry["next_year"] = sum(priced(i, int(a)) for i, a in later)
            out[option["name"]] = entry
        return out

    scenarios = []
    for spec in data.get("scenarios") or []:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        pairs = [r for r in spec.get("replace") or [] if isinstance(r, dict)]
        if not pairs or not all(_grounded(r.get("from"), numbers) and _grounded(r.get("to"), numbers)
                                for r in pairs):
            dropped.append(f"시나리오 {spec['name']}")
            continue
        swap = {float(str(r["from"]).replace(",", "")): Decimal(str(r["to"]).replace(",", ""))
                for r in pairs}
        scenarios.append({"name": str(spec["name"])[:40], "options": run(swap)})
    for condition in conditions:
        scenarios.insert(0, {"name": condition, "options": run({}, frozenset({condition}))})
    base = run({})
    lowest = min(base, key=lambda name: base[name]["total"])
    out = {"period": str(data.get("period") or "")[:20], "options": base, "lowest": lowest,
           "scenarios": scenarios[:4], "dropped": dropped}
    if applied:
        out["applied"] = applied
    if basis:
        out["vat_basis"] = basis
    budget = _budget_of(data, numbers, material)
    if judged := _judged_against(budget, base, basis):
        out["budget"] = judged
        scenario_budgets(out, budget)
    return out


def scenario_budgets(costs: dict, budget: dict | None) -> None:
    """Each scenario's totals on the budget's basis, beside the base's: an option over the
    budget only without a condition is not out (`must_status`)."""
    if not budget or "budget" not in costs:
        return
    costs["budget"]["scenarios"] = {
        sc["name"]: j["compared"] for sc in costs.get("scenarios") or []
        if (j := _judged_against(budget, sc["options"], costs.get("vat_basis") or ""))}


#: a quantity threshold in a condition: 「30대 이상 구매 시」, 「50명 이상」
_AT_LEAST = re.compile(r"(\d[\d,]*)\s{0,2}(?:" + _COUNTED_IN + r")\s{0,2}이상")
#: a period threshold: 「3년 이상 계약 시」
_YEARS_AT_LEAST = re.compile(r"(\d{1,2})\s{0,2}년\s{0,2}이상")


def condition_met(condition: str, items: list[dict], years: Any) -> bool:
    """Whether the material's own figures meet `condition`: a count threshold the option's
    counted units reach, or a period threshold the compared years reach. A condition only the
    buyer decides (「연 단위 계약 시」) is never met by code."""
    if m := _AT_LEAST.search(condition or ""):
        need = float(m.group(1).replace(",", ""))
        counts = []
        for item in items:
            try:
                counts.append(float(_plain((item.get("raw") or {}).get("count")) or 0))
            except ValueError:
                continue
        return bool(counts) and max(counts) >= need
    if m := _YEARS_AT_LEAST.search(condition or ""):
        try:
            return float(_plain(years) or 0) >= float(m.group(1))
        except ValueError:
            return False
    return False


#: words of a name that say nothing of what is priced
_NAME_NOISE = re.compile(r"\([^()\n]{0,40}\)|[^가-힣A-Za-z]|비용|요금|금액|가격|단가|할인|시|연장|계약|기본|추가|조건")


def _same_item(a: dict, b: dict) -> bool:
    """Whether two items name the same thing: 「구독료」 and 「구독료(연 계약)」 do, 「노트북」 and
    「3년 보증 연장」 do not."""
    words = [set(re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}", _NAME_NOISE.sub(" ", str(i.get("name") or ""))))
             for i in (a, b)]
    raw_a, raw_b = a.get("raw") or {}, b.get("raw") or {}
    same_price = _plain(raw_a.get("unit_price")) is not None and (
        _plain(raw_a.get("unit_price")) == _plain(raw_b.get("unit_price")))
    return same_price or bool(words[0] & words[1]) or not (words[0] and words[1])


def _same_base(a: dict, b: dict) -> bool:
    ra, rb = a.get("raw") or {}, b.get("raw") or {}
    return (str(ra.get("per") or "").strip().lower() == str(rb.get("per") or "").strip().lower()
            and _plain(ra.get("count")) == _plain(rb.get("count")) and _same_item(a, b))


def conditional_items(items: list[dict], conditions: list[str],
                      years: Any) -> tuple[list[dict], dict[str, list[dict]]]:
    """Option items unconditioned and per condition; only discounts withheld from base, tagged costs replace matching untagged ones."""
    base = [i for i in items if not i.get("when")]
    by: dict[str, list[dict]] = {}
    for condition in conditions:
        chosen = list(base)
        for item in items:
            if item.get("when") != condition:
                continue
            if item.get("sign") != -1:
                twin = next((b for b in chosen if not b.get("when") and b.get("sign") != -1
                             and _same_base(b, item)), None)
                if twin is not None:
                    chosen.remove(twin)
            chosen.append(item)
        by[condition] = chosen
    for item in items:
        if not item.get("when") or item.get("sign") == -1:
            continue
        if any(_same_base(b, item) and b.get("sign") != -1 for b in base):
            continue
        raw = dict(item.get("raw") or {})
        rate = _plain(raw.get("rate"))
        if rate is not None and float(rate) < 1:
            # 「연 계약 시 85%」 without a full-price line: full price goes to base
            raw["rate"] = None
            full = explicit_factors({**raw, "name": item.get("name")}, years)
            if full is None:
                continue
            item = full
        base.append({k: v for k, v in item.items() if k != "when"})
    return base, by


def _factor_text(row: dict) -> str:
    parts = [f"{float(str(f).replace(',', '')):,.10g}" for f in row["factors"]]
    if row.get("over"):
        a, b = row["over"]
        parts.append(f"({float(str(a).replace(',', '')):,g} − {float(str(b).replace(',', '')):,g}, 0 이상)")
    return " × ".join(parts) + (" + 부가세 10%" if row.get("vat_added") else "")


def year_lines(label: str, totals: dict[str, dict]) -> list[str]:
    """First-year/next-year amounts per option and total gaps, precomputed for the writer and trace."""
    lines = []
    if all("first_year" in e for e in totals.values()):
        lines.append(f"- {label}첫해(1회 비용 포함): " + ", ".join(
            f"{o} {e['first_year']:,}원" for o, e in totals.items())
            + " / 이듬해(반복 비용만): " + ", ".join(
            f"{o} {e['next_year']:,}원" for o, e in totals.items()))
        first = min(totals, key=lambda n: totals[n]["first_year"])
        later = min(totals, key=lambda n: totals[n]["next_year"])
        # makes 「2년 연속 더 적다」 a block-stated figure
        lines.append(f"- {label}더 적은 대안: 첫해 {first}, 이듬해 {later}"
                     + (f" (첫해·이듬해 2년 연속 {first})" if first == later else ""))
    names = list(totals)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            lines.append(f"- {label}{a}와 {b}의 합계 차이: "
                         f"{abs(totals[a]['total'] - totals[b]['total']):,}원")
    return lines


def _cost_block(result: dict, *, data: bool = False) -> str:
    """The code's totals for the writer. `data`: a strong writer gets them as figures to use,
    with no verdict (the lowest alternative, 「settled facts」) to follow: which condition
    applies and what to recommend stay its judgment (a total stated as settled tilts the
    recommendation)."""
    if data:
        return _cost_data(result)
    period = result["period"] or "비교 기간"
    rows = ["| 대안 | 항목 | 계산 | 금액(원) |", "|---|---|---|---:|"]
    for option, entry in result["options"].items():
        for row in entry["rows"]:
            rows.append(f"| {option} | {row['name']} | {_factor_text(row)} | {row['amount']:,} |")
        rows.append(f"| {option} | 합계 |  | {entry['total']:,} |")
    # VAT basis or budget known: the comparison itself is settled here too
    settled = bool(result.get("vat_basis") or result.get("budget"))
    basis = {"incl": "부가세 포함", "excl": "부가세 별도"}.get(result.get("vat_basis") or "", "")
    lines = [f"# 총비용 계산(코드)\nThe code calculated the {period} total costs from the material's unit prices "
             "and quantities. Use only these values for the total cost table and the amounts in comparison "
             "sentences; do not recalculate."
             + (" The comparisons below (lowest, differences, budget) are settled facts: state them "
                "as given, do not judge them again." if settled else "")
             + (f" Every amount is on one basis, {basis}"
                + (" (VAT-exclusive prices had their 10% VAT added)" if any(
                    r.get("vat_added") for e in result["options"].values() for r in e["rows"])
                   else "") + "; label totals with it." if basis else ""),
             "\n".join(rows)]
    totals = result["options"]
    ranked = sorted(totals, key=lambda n: totals[n]["total"])
    lines.append("- " + ", ".join(f"{o} {e['total']:,}원" for o, e in totals.items())
                 + f" → 가장 적은 대안: {result['lowest']}"
                 + (f" ({basis} 기준)" if basis else ""))
    if settled and len(ranked) > 2:
        lines.append("- 적은 순: " + " < ".join(f"{o} {totals[o]['total']:,}원" for o in ranked))
    lines += year_lines("", result["options"])
    if budget := result.get("budget"):
        said = {"incl": "부가세 포함", "excl": "부가세 별도"}.get(budget["vat"], "")
        within = [o for o, room in budget["room"].items() if room >= 0]
        lines.append(f"- 예산 {budget['amount']:,}원" + (f"({said})" if said else "") + " 대비: "
                     + ", ".join(f"{o} {budget['compared'][o]:,}원 → "
                                 + (f"예산 안(잔액 {room:,}원)" if room >= 0
                                    else f"예산 초과({-room:,}원)")
                                 for o, room in budget["room"].items())
                     + " → " + (f"예산 안: {', '.join(within)}" if within else "예산 안의 대안 없음"))
    lines += _applied_lines(result)
    for spec in result["scenarios"]:
        totals = spec["options"]
        lines.append(f"- 시나리오 「{spec['name']}」: " + ", ".join(
            f"{o} {e['total']:,}원" for o, e in totals.items())
                     + f" → 가장 적은 대안: {min(totals, key=lambda n: totals[n]['total'])}")
        lines += year_lines(f"「{spec['name']}」 ", totals)
    if result["dropped"]:
        lines.append("- Left out of the calculation (values not in the material): " + ", ".join(result["dropped"][:6]))
    return "\n".join(lines)


def _applied_lines(result: dict) -> list[str]:
    """The material's conditions its own figures meet, applied in the totals above."""
    if not result.get("applied"):
        return []
    return ["- 자료의 조건 중 자료의 수량·기간으로 충족되어 위 합계에 넣은 것: "
            + ", ".join(f"「{a}」" for a in result["applied"][:4])]


def _cost_data(result: dict) -> str:
    period = result["period"] or "비교 기간"
    rows = ["| 대안 | 항목 | 계산 | 금액(원) |", "|---|---|---|---:|"]
    for option, entry in result["options"].items():
        for row in entry["rows"]:
            rows.append(f"| {option} | {row['name']} | {_factor_text(row)} | {row['amount']:,} |")
        rows.append(f"| {option} | 합계 |  | {entry['total']:,} |")
    basis = {"incl": "부가세 포함", "excl": "부가세 별도"}.get(result.get("vat_basis") or "", "")
    conditional = bool(result["scenarios"])
    lines = [f"# 총비용 계산(코드)\nThe code worked out the {period} totals from the material's unit "
             "prices and quantities. Use these amounts where you state totals rather than "
             "recalculating them."
             + (" The first table leaves out the material's conditional terms; each scenario "
                "below applies one. Which applies, and what to recommend, is your judgment from "
                "the request: state the condition with the figure." if conditional else
                " What to recommend is your judgment from the request and the material.")
             + (f" Every amount is on one basis, {basis}; label totals with it." if basis else ""),
             "\n".join(rows)]
    totals = result["options"]
    ranked = sorted(totals, key=lambda n: totals[n]["total"])
    lines.append(("- 조건 없이: " if conditional else "- ")
                 + " < ".join(f"{o} {totals[o]['total']:,}원" for o in ranked))
    lines += [line for line in year_lines("", totals) if "더 적은 대안" not in line]
    if budget := result.get("budget"):
        said = {"incl": "부가세 포함", "excl": "부가세 별도"}.get(budget["vat"], "")
        lines.append(f"- 예산 {budget['amount']:,}원" + (f"({said})" if said else "") + " 대비: "
                     + ", ".join(f"{o} {budget['compared'][o]:,}원 → "
                                 + (f"잔액 {room:,}원" if room >= 0 else f"초과 {-room:,}원")
                                 for o, room in budget["room"].items()))
    lines += _applied_lines(result)
    for spec in result["scenarios"]:
        totals = spec["options"]
        ranked = sorted(totals, key=lambda n: totals[n]["total"])
        lines.append(f"- 「{spec['name']}」 적용: "
                     + " < ".join(f"{o} {totals[o]['total']:,}원" for o in ranked))
        lines += [line for line in year_lines(f"「{spec['name']}」 ", totals)
                  if "더 적은 대안" not in line]
    if result["dropped"]:
        lines.append("- Left out of the calculation (values not in the material): "
                     + ", ".join(result["dropped"][:6]))
    return "\n".join(lines)


Complete = Callable[..., Any]

#: thinking settle-read time cap and token budget (Flash+thinking: 150 s median on costs; outlines hit 6,400)
DELIBERATE_SEC = 360.0
DELIBERATE_TOKENS = 12000


def _failure(exc: Exception) -> str:
    """Pre-pass failure reason, user-facing."""
    if isinstance(exc, KeyError):
        return f"값 {exc} 없음"
    if isinstance(exc, (ValueError, ArithmeticError, TypeError)) and str(exc):
        return str(exc)[:120]
    return type(exc).__name__


def _json(text: str) -> dict:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


#: split vote: no totals, placeholders marked
_COST_SPLIT = "총비용 계산 불일치 — 직접 확인 필요"
_COST_WITHHELD = (
    "# 총비용 계산(코드): 확정 못 함\nThe cost items read from the material differed on every reading, so the "
    "code settled no total cost per alternative. Work each total out yourself from the unit prices, "
    "quantities, periods, discounts and one-off costs the material writes, and show the formula beside it "
    "(「14,000원 × 40명 × 12개월 × 0.85 + 3,000,000원 = 8,712,000원」); where the material lacks a figure a "
    "total needs, leave that total as 「[금액]」.")
#: what the writer does with an option the readings did not settle
_UNSETTLED = ("its cost items read differently each time, so the code settled no total for it. Work "
              "its total and yearly totals out yourself from the unit prices, quantities, periods, "
              "discounts and one-off costs the material writes, showing the formula beside each; "
              "leave a total as 「[금액]」 only where the material lacks a figure it needs.")

#: an option's head label: 「A안: 사내 메신저 유지 + …」, 「제2안」, 「안 1」, 「견적 2」
_OPTION_HEAD = re.compile(
    r"^\s*(?:제\s?)?(?:(?P<x>[A-Za-z]|\d{1,2}|[가-하])\s?안|안\s?(?P<y>\d{1,2}))"
    r"(?=$|[\s:：(\-—·,])")


def _option_key(name: str) -> str:
    """Option name for comparison: 「팀링크 (엔터프라이즈)」 → 「팀링크」; a labelled option by its
    label, 「A안: 지금 쓰는 사내 메신저 유지 + …」 → 「A안」, so two wordings are one option."""
    if m := _OPTION_HEAD.match(str(name)):
        return (m.group("x") or "").upper() + "안" if m.group("x") else m.group("y") + "안"
    return re.sub(r"\s+|\(.*?\)|（.*?）", "", str(name))


_DISCOUNT = re.compile(r"(?P<n>\d{1,2}(?:\.\d)?)\s?%\s?(?:할인|감면|인하)|할인(?:율)?\s?(?P<m>\d{1,2})\s?%")


def _missing_discount(options: dict[str, dict], scenarios: list[dict], material: str) -> list[str]:
    """Options whose material names a percent discount no reading priced: neither a row nor a
    condition scenario applies it (「연 단위 계약 시 15% 할인」), so the undiscounted total is
    not settled."""
    names = list(options)
    out = []
    for name in names:
        said = _option_text(name, names, material)
        if not _DISCOUNT.search(said):
            continue
        rows = options[name].get("rows") or []
        priced = any("할인" in str(r.get("name") or "") or int(r.get("amount") or 0) < 0
                     or any(_rate_factor(f) for f in r.get("factors") or [])
                     for r in rows)
        scenario = any(name in (sc.get("options") or {}) and sc["options"][name]["total"]
                       != options[name]["total"] for sc in scenarios)
        if not priced and not scenario:
            out.append(name)
    return out


def _rate_factor(factor: Any) -> bool:
    try:
        value = float(str(factor).replace(",", "").rstrip("%"))
    except ValueError:
        return False
    return 0 < value < 1 or str(factor).strip().endswith("%")


def _names_option(line: str, name: str) -> bool:
    """Whether a material line opens `name`'s part: its label (「A안:」) or its name."""
    head = line.strip()
    key = _option_key(name)
    if _OPTION_HEAD.match(head):
        return _option_key(head) == key
    plain = re.sub(r"\s+", "", head)
    return bool(key) and (key in plain or re.sub(r"\s+", "", name) in plain)


def _option_text(name: str, names: list[str], material: str) -> str:
    """What the material writes under an option: from the line naming it to the line naming
    another (「[견적 2] 다솜시스템」 and the lines under it)."""
    out: list[str] = []
    inside = False
    for line in (material or "").split("\n"):
        if _names_option(line, name):
            inside = True
        elif any(n != name and _names_option(line, n) for n in names):
            inside = False
        if inside:
            out.append(line)
    return "\n".join(out) or _described(name, names, material)


def _same_totals(a: dict[str, dict], b: dict[str, dict]) -> bool:
    """Same options, same totals; names may carry extra words (「법인 카셰어링」 ~ 「카셰어링」)."""
    if len(a) != len(b):
        return False
    left = list(b.items())
    for name, entry in a.items():
        key = _option_key(name)
        match = next((k for k, (other, e) in enumerate(left) if e["total"] == entry["total"]
                      and (key in _option_key(other) or _option_key(other) in key)), None)
        if match is None:
            return False
        left.pop(match)
    return True


def settled_options(readings: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """Options two differing readings price alike, plus the rest's names; one split option doesn't blank all."""
    kept: dict[str, dict] = {}
    unsettled: list[str] = []
    first = readings[0]["options"] if readings else {}
    names = list(dict.fromkeys(n for r in readings for n in r["options"]))
    for name in names:
        key = _option_key(name)
        if any(_option_key(k) == key for k in kept) or any(
                _option_key(u) == key for u in unsettled):
            continue
        seen = [e for r in readings for n, e in r["options"].items()
                if _option_key(n) == key]
        agreed = next((e for i, e in enumerate(seen) for other in seen[i + 1:]
                       if e["total"] == other["total"]), None)
        if agreed is not None:
            kept[next((n for n in first if _option_key(n) == key), name)] = agreed
        else:
            unsettled.append(name)
    return kept, unsettled


def _partial_cost_block(kept: dict[str, dict], unsettled: list[str], period: str) -> str:
    rows = ["| 대안 | 항목 | 계산 | 금액(원) |", "|---|---|---|---:|"]
    for option, entry in kept.items():
        for row in entry["rows"]:
            rows.append(f"| {option} | {row['name']} | {_factor_text(row)} | {row['amount']:,} |")
        rows.append(f"| {option} | 합계 |  | {entry['total']:,} |")
    lines = [f"# 총비용 계산(코드): 일부만 확정\nThe code settled the {period or '비교 기간'} total "
             "costs of the alternatives below (two readings of the material agreed on them). "
             "Use these values as they are; do not recalculate.", "\n".join(rows),
             "- " + ", ".join(f"{o} {e['total']:,}원" for o, e in kept.items())]
    if all("first_year" in e for e in kept.values()):
        lines.append("- 첫해(1회 비용 포함): " + ", ".join(
            f"{o} {e['first_year']:,}원" for o, e in kept.items()) + " / 이듬해(반복 비용만): "
            + ", ".join(f"{o} {e['next_year']:,}원" for o, e in kept.items()))
    if unsettled:
        lines.append(f"- Not settled: {', '.join(unsettled)} — {_UNSETTLED}")
    return "\n".join(lines)


def _partial_budget(kept: dict[str, dict], readings: list[dict]) -> dict | None:
    """`kept` options' budget comparison from a reading that agrees on them."""
    for reading in readings:
        budget = reading.get("budget") or {}
        compared = budget.get("compared") or {}
        if not compared:
            continue
        same = {n: compared[n] for n in kept if n in compared
                and (reading["options"].get(n) or {}).get("total") == kept[n]["total"]}
        if same:
            return {"options": kept, "budget": {**budget, "compared": same,
                                                "room": {n: budget["room"][n] for n in same
                                                         if n in (budget.get("room") or {})}}}
    return None


def _costs_agree(a: dict, b: dict) -> bool:
    """Readings agree: same options, same totals."""
    return _same_totals(a["options"], b["options"])


def _quote_key(result: dict) -> tuple:
    return (result.get("supply"), result.get("total"), tuple(sorted(
        (q.get("amount") or 0, str(q.get("vat_basis") or "")) for q in result.get("quotes") or [])))


async def _vote(extract: Callable[[str], Any], agree: Callable[[Any, Any], bool],
                tiebreak: str, settle: Callable[[], Any] | None = None) -> tuple[Any, str, list]:
    """Two readings, a third (`settle` first, else `tiebreak`) on disagreement; returns `(result, how, outcomes)`."""
    async def read(model: str) -> Any:
        try:
            return await extract(model)
        except Exception as exc:  # noqa: BLE001 — one failed reading is a missing vote
            log.info("pre-pass reading failed: %s", logs.safe(repr(exc)))
            return exc

    outcomes = list(await asyncio.gather(read(""), read("")))

    def found(outcome: Any) -> Any:
        return None if isinstance(outcome, BaseException) else outcome[0]

    def same(i: int, j: int) -> bool:
        a, b = found(outcomes[i]), found(outcomes[j])
        return a is not None and b is not None and agree(a, b)

    if same(0, 1):
        return outcomes[0][0], "agreed", outcomes
    third: Any = None
    if settle is not None:
        try:
            third = await settle()
        except Exception as exc:  # noqa: BLE001 — a timed-out or failed settling reading
            log.info("pre-pass settling reading failed: %s", logs.safe(repr(exc)))
            third = None
        if third is not None and third[0] is None:
            third = None
    outcomes.append(third if third is not None else await read(tiebreak))
    plain = [o for o in outcomes if not isinstance(o, BaseException)]
    for i, j in ((0, 2), (1, 2)):
        if same(i, j):
            return outcomes[j][0], "voted", plain
    if not plain:
        raise outcomes[0]
    return None, "split" if any(found(o) is not None for o in outcomes) else "empty", plain


@dataclass(slots=True)
class PrepassJob:
    """Pre-pass step inputs: request, material, calls."""

    format_id: str
    request: str
    material: str
    complete: Complete
    model: str
    api_key: str
    today: dt.date | None
    tiebreak_model: str
    usage: dict
    #: thinking completion for the split-settling read (`report._complete_thinking`); None = plain third read
    deliberate: Complete | None = None
    #: the writer is a strong model: blocks are data for it, never verdicts to follow
    strong: bool = False

    def count(self, more: dict) -> None:
        for key in ("inputTokens", "outputTokens"):
            self.usage[key] = self.usage.get(key, 0) + int((more or {}).get(key, 0))


async def prepare(
    format_id: str | None, request: str, material: str, *, complete: Complete, model: str,
    api_key: str, usage: dict | None = None, today: dt.date | None = None,
    tiebreak_model: str = "", process: Any = None, deliberate: Complete | None = None,
    strong: bool = False,
) -> Prepass:
    """Pre-pass: code-settled facts before writing; steps from pipeline `prepass`, runs in `Prepass.runs`."""
    from app.pipeline.runner import CURRENT_STAGE
    from app.services import process_settings

    if process is None:
        process = process_settings.for_format(format_id)
    pre = Prepass(format_id=format_id or "", process=process)
    if not (material or "").strip():
        return pre
    job = PrepassJob(format_id or "", request, material, complete, model, api_key, today,
                     tiebreak_model, usage if usage is not None else {}, deliberate, strong)
    for name in process.prepass:
        token = CURRENT_STAGE.set(f"prepass:{name}")
        try:
            await PREPASS_STEPS[name](pre, job)
        finally:
            CURRENT_STAGE.reset(token)
    return pre


async def _date_calc(pre: Prepass, job: PrepassJob) -> None:
    """Relative dates from meeting/memo date; lead times from material's anchor date."""
    request, material, today = job.request, job.material, job.today
    meeting = pre.counts_from_meeting
    # minutes only: RFP/memo relative dates never read from today
    base, source = (meeting_base(request, material, today, nearest=True) if meeting
                    else (meeting_date(request, material, today), "text"))
    scanned = material
    if meeting and not skill_scripts.dates().scan(material, base or today or dt.date.today()):
        # a memo typed as the request itself (the material holds no day): its days are read
        typed = [ln for ln in (request or "").splitlines() if ln.strip() and ln.strip() not in material]
        scanned = "\n".join([*typed, material]).strip()
    if base is None and meeting:
        # meeting date 「[회의 일자]」: relative deadlines stay words
        probe = today or dt.date.today()
        rows = _undated(skill_scripts.dates().scan(scanned, probe), probe)
    else:
        rows = skill_scripts.dates().scan(scanned, base)
    _with_context(rows, scanned)
    pre.dates = rows
    first = next((r["base_date"] for r in rows if r.get("base_date")),
                 base.isoformat() if base else "")
    pre.base_date = first
    pre.base_source = source if base else ""
    undated = meeting and base is None
    if block := _date_block(rows, first, undated=undated):
        pre.blocks.append(block)
    # 「발주 후 8주」: from material's absolute date regardless of meeting date
    lead = skill_scripts.dates().lead_times(material)
    lead_rows = [r for r in lead["items"] if r["status"] == "ok"]
    if lead["items"]:
        pre.lead = lead
        pre.blocks.append(_lead_block(lead))
    relative = [r for r in rows if r.get("kind") not in ("full_date", "month_day")]
    parts = []
    if relative or not lead_rows:
        parts.append(
            f"날짜 {sum(r['status'] in ('ok', 'repeat') for r in relative)}개 풂"
            + (f", 미정·[기한] {sum(r['status'] in ('unresolved', 'unclear') for r in relative)}개"
               if any(r['status'] in ('unresolved', 'unclear') for r in relative) else "")
            + (f", 회의 일자가 없어 날로 풀지 않음 {sum(r['status'] == _NO_BASE for r in relative)}개"
               " → 기한 [기한], 원문은 비고"
               if any(r['status'] == _NO_BASE for r in relative) else "")
            + (f" (기준 {first}{', ' + w if (w := _base_words(pre.base_source)) else ''})"
               if first else " (회의 일자 없음 → [회의 일자])" if undated else " (기준일 없음)"))
    if lead["items"]:
        starts = ", ".join(dict.fromkeys(f"{r['anchor']} {r['start']}" for r in lead_rows))
        late = sum(1 for r in lead_rows if r.get("within") is False)
        parts.append(
            f"기간 {len(lead_rows)}개 → 날짜" + (f"({starts} 기준)" if starts else "")
            + (f", 기한 {lead['deadline']} 초과 {late}개" if lead.get("deadline") else "")
            + (f", 못 풂 {len(lead['items']) - len(lead_rows)}개"
               if len(lead["items"]) > len(lead_rows) else ""))
    pre.runs.append({"skill": "date-calc", "summary": " · ".join(parts)})


async def _proposal_rfp(pre: Prepass, job: PrepassJob) -> None:
    """RFP terms: client names, period, page limit."""
    if terms := rfp_terms(job.material, job.request):
        pre.rfp = terms
        shown = [x for x in (
            "고객사 " + "·".join(terms["clients"][:2]) if terms.get("clients") else "",
            f"사업 종료 {terms['end']}" if terms.get("end") else "",
            f"사업 기간 {terms['months']}개월" if terms.get("months") else "",
            f"본문 {terms['pages']}쪽 이내" if terms.get("pages") else "") if x]
        pre.runs.append({"skill": "proposal-check", "summary": "RFP: " + ", ".join(shown)})


def _quote_empty(result: dict) -> bool:
    """A quote result that settles nothing: lines all left out, or a 0원 total."""
    lines = result.get("lines") or []
    if lines:
        return not [line for line in lines if not line.get("excluded")] \
            or not result.get("total")
    return not result.get("quotes")


async def _quote_calc(pre: Prepass, job: PrepassJob) -> None:
    """Quote lines read twice (third settles split), summed by code."""
    material, api_key = job.material, job.api_key
    settings = pre.settings
    complete, model, tiebreak_model, count = job.complete, job.model, job.tiebreak_model, job.count
    if _PRICE_LINE.search(material):
        async def read_quote(override: str) -> tuple[Any, Any]:
            text, more = await complete(
                override or model,
                [{"role": "user", "content": _QUOTE_PROMPT.format(material=material[:12000])}],
                api_key, 1500,
            )
            count(more)
            data, dropped = grounded_quote_input(_json(text), material)
            if data["items"] or data.get("quotes"):
                return (data, dropped, skill_scripts.quotes().run(data)), dropped
            return None, dropped

        try:
            agreed, how, outcomes = await _vote(
                read_quote, lambda a, b: _quote_key(a[2]) == _quote_key(b[2]),
                tiebreak_model or model)
            dropped = next((o[1] for o in outcomes if o[1]), [])
            if how == "split":
                pre.runs.append({"skill": "quote-calc", "error": True,
                                 "summary": "견적 계산 불일치 — 직접 확인 필요"})
            elif agreed is not None and _quote_empty(agreed[2]):
                # every line left out (or a 0원 sum): no settled block for the writer
                # (「공급가액 0원 … 합계 0원」)
                pre.runs.append({"skill": "quote-calc", "error": True, "skipped": True,
                                 "summary": "견적 계산 보류 — 계산할 항목이 남지 않아 확정값 없음"})
            elif agreed is not None:
                data, dropped, result = agreed
                pre.quote_input, pre.quote = data, result
                pre.staff = staffing(material, data)
                left_out = excluded_items(data)
                pre.blocks.append(_quote_block(result, left_out, bool(data.get("basis_assumed")),
                                               material))
                if "quote-totals" in settings.code_checks:
                    pre.figures.extend(_quote_figures(result))
                counted = [line for line in result.get("lines") or [] if not line.get("excluded")]
                summary = (f"견적 {len(counted)}항목 → 공급가액(VAT 별도) {result['supply']:,}원, "
                           f"합계(VAT 포함) {result['total']:,}원"
                           if result.get("lines") else f"업체 견적 {len(result.get('quotes') or [])}건")
                if left_out:
                    summary += " · " + ", ".join(
                        f"제외: {i['name']} ({i['reason']})" for i in left_out[:4])
                if dropped:
                    summary += f" (자료에 없는 값이라 뺀 것: {', '.join(dropped[:4])})"
                if how == "voted":
                    summary += " · 세 번 읽어 둘이 같은 값"
                pre.runs.append({"skill": "quote-calc", "summary": summary})
            elif dropped:
                pre.runs.append({"skill": "quote-calc", "error": True, "summary":
                                 f"견적 계산 못 함 (자료에 없는 값이라 뺀 것: {', '.join(dropped[:4])})"})
            else:
                # compare-cost totals cover it: not a miss
                by_option = "compare-cost" in settings.prepass
                pre.runs.append({"skill": "quote-calc", "error": not by_option,
                                 "skipped": by_option,
                                 "summary": "견적 계산 못 함 (응답에서 견적 항목을 읽지 못함)"})
        except Exception as exc:  # noqa: BLE001 — written without the block; said in the step
            log.info("quote pre-pass failed: %s", logs.safe(repr(exc)))
            pre.runs.append({"skill": "quote-calc", "summary": f"견적 계산 실패 ({_failure(exc)})",
                             "error": True})


async def _staffing(pre: Prepass, job: PrepassJob) -> None:
    if not pre.staff:
        pre.staff = staffing(job.material)


# ── the price lines as written: a code reading of the cost items ──

#: an option's head: 「A안: …」, 「[견적 1] 가온정보통신」, 「견적 2: 다솜시스템」, 「### 대안 3 누리컴」
_QUOTE_HEAD = re.compile(
    r"^\s{0,3}(?:#{1,4}\s{0,2})?(?:\*\*)?\s{0,2}(?:\[\s{0,2}(?:견적|대안|옵션|업체|제안)\s{0,2}\d{1,2}\s{0,2}\]"
    r"|(?:견적|대안|옵션|업체|제안)\s{0,2}\d{1,2}\s{0,2}[:：.)])\s{0,2}(?P<name>[^\n:：(]{1,40})")
_BULLET = re.compile(r"^\s{0,6}(?:[-*·•]|\d{1,2}[.)])\s")
#: an amount in won: 「14,000원」, 「300만원」, 「1억 2천만 원」
_WON = re.compile(r"(?<![\d,.])(\d[\d,]{0,15}(?:\.\d{1,4})?\s{0,2}(?:억|천만|백만|만)?)\s{0,2}원")
#: 「× 40명」 after a price, 「40명 ×」 before it
_TIMES_AFTER = re.compile(rf"^\s{{0,3}}(?:\([^()\n]{{0,30}}\)\s{{0,3}})?[×x*]\s{{0,2}}(\d[\d,]{{0,8}})\s{{0,2}}({_COUNTED_IN})")
_TIMES_BEFORE = re.compile(rf"(\d[\d,]{{0,8}})\s{{0,2}}({_COUNTED_IN})\s{{0,2}}[×x*]\s{{0,3}}$")
_EACH = re.compile(rf"(?:1\s{{0,2}})?(?:{_COUNTED_IN})\s{{0,2}}당|/\s{{0,2}}(?:{_COUNTED_IN})(?![가-힣])")
_PER_MONTH = re.compile(r"(?:^|[\s(/])(?:월|매월|월간|월\s{0,2}요금)\s{0,2}$|/\s{0,2}월")
_PER_YEAR = re.compile(r"(?:^|[\s(/])(?:연|매년|연간|년)\s{0,2}$|/\s{0,2}(?:년|연)")
_PER_USE = re.compile(r"(?:시간|km|㎞|건|장|회|GB|분|일)\s{0,2}당|/\s{0,2}(?:시간|km|건|장|회|GB|분|일)", re.I)
#: the condition a clause is priced under: 「3년 연장 시」, 「연 단위 계약 시」, 「30대 이상 구매 시」
_WHEN = re.compile(r"((?:[^\s,，;]{1,20}\s){0,3}[^\s,，;]{1,20}\s(?:시|경우|때))(?=\s|$)")
#: a period the comparison spans: 「5년 총비용」, 「3년간」, 「연간 비용」
_SPAN = re.compile(r"(\d{1,2})\s{0,2}년\s{0,2}(?:간|동안|총비용|총액|합계|TCO|누적|기준)")
_ONE_YEAR = re.compile(r"연간\s{0,2}(?:비용|총비용|금액|합계)|첫해|이듬해|1년\s{0,2}(?:비용|기준)")
_CLAUSE = re.compile(r",\s|，|;\s")
_ITEM_NOISE = re.compile(
    rf"(?:1\s{{0,2}})?(?:{_COUNTED_IN})\s{{0,2}}당|\((?:부가세|부가가치세|VAT)[^()]{{0,8}}\)"
    r"|(?<![가-힣])(?:단가|매월|월|연간|연|1회|초기|약|부가세|별도|포함)(?![가-힣])|[\d,.]{1,20}|[×x*/]")


def _option_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Option heads and the bullet lines under each (the head's own words count too)."""
    blocks: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in (text or "").split("\n"):
        head = None
        if m := _QUOTE_HEAD.match(line):
            head = m.group("name").strip(" *")
        elif _OPTION_HEAD.match(line.strip().lstrip("#* ")) and not _BULLET.match(line):
            head = _option_key(line.strip().lstrip("#* "))
        if head:
            current = (head, [line.split(":", 1)[1] if ":" in line else ""])
            blocks.append(current)
        elif current is not None and _BULLET.match(line):
            current[1].append(_BULLET.sub("", line, count=1))
        elif line.strip():
            current = None
    return blocks


def _won(raw: str) -> float | None:
    try:
        return units._ko_number(raw.replace(" ", ""))
    except ValueError:
        return None


def _quantity(lines: list[str], request: str) -> float | None:
    """The count a 「대당」/「1인당」 price is multiplied by: the one count the option's own
    「× 30대」 writes, else the one the request writes (「노트북 30대」, 「교직원 40명」)."""
    found = {float(m.group(1).replace(",", "")) for line in lines
             for m in re.finditer(rf"[×x*]\s{{0,2}}(\d[\d,]{{0,8}})\s{{0,2}}(?:{_COUNTED_IN})", line)}
    if not found:
        found = {float(m.group(1).replace(",", "")) for m in re.finditer(
            r"(?<![\d,.])(\d[\d,]{0,8})\s{0,2}(?:대|명|인|석|좌석|계정|카드|세트)(?![가-힣])",
            request or "")}
    return next(iter(found)) if len(found) == 1 else None


def _item_name(before: str, per: str) -> str:
    """An item's name from the words before its price: 「기존 기기 수거는 대당」 → 「기존 기기 수거」."""
    name = re.sub(r"\s{2,}", " ", _ITEM_NOISE.sub(" ", before)).strip(" ·:,-(")
    name = re.sub(r"(?<=[가-힣])(?:은|는)$", "", name).strip()
    if name:
        return name[:30]
    # never an empty or generic name: `_same_item` takes such a row for any other
    return {"month": "월 이용료", "year": "연 이용료"}.get(per, "구입가")


def price_lines_reading(request: str, material: str) -> dict | None:
    """The cost reading code takes from price lines written plainly — 「1인당 월 14,000원 ×
    40명, 연 단위 계약 시 15% 할인」, 「1회 300만원」, 「대당 10,000원(부가세 포함)」 — in the
    shape the model's reading has (`_COST_PROMPT`). `None` unless every amount under every
    option is read: a line it cannot read (a usage price, two amounts in one clause, a discount
    with no price beside it, an amount outside the options) leaves the reading to the model.
    The request and the material are read apart (the material may be the request's own
    lines); both giving options, they must name the same ones."""
    found = [r for r in (_price_lines(request, request), _price_lines(material, request)) if r]
    if len(found) == 2 and ({_option_key(o["name"]) for o in found[0]["options"]}
                            != {_option_key(o["name"]) for o in found[1]["options"]}):
        return None
    return found[0] if found else None


def _price_lines(text: str, request: str) -> dict | None:
    blocks = _option_blocks(text)
    if len(blocks) < 2 or len({_option_key(n) for n, _ in blocks}) < len(blocks):
        return None
    inside = {line for _, lines in blocks for line in lines}
    for line in text.split("\n"):
        stripped = _BULLET.sub("", line, count=1)
        if (_WON.search(line) and stripped not in inside and not _QUOTE_HEAD.match(line)
                and not _OPTION_HEAD.match(line.strip().lstrip("#* "))
                and not _BUDGET_LINE.search(line)):
            return None  # an amount outside the options: whose it is, the model reads
    recurring = False
    options = []
    for name, lines in blocks:
        count_of = _quantity(lines, request)
        items: list[dict] = []
        for line in lines:
            last: dict | None = None
            for clause in _CLAUSE.split(line):
                amounts = list(_WON.finditer(clause))
                discount = _DISCOUNT.search(clause)
                if (len(amounts) > 1 or (amounts and discount) or _PER_USE.search(clause)
                        or ("%" in clause and not discount)):
                    return None  # 「매년 5% 인상」, a usage price: the model reads it
                when = next((w.group(1).strip() for w in _WHEN.finditer(clause)
                             if not amounts or w.end() <= amounts[0].start()), "")
                if discount:
                    if last is None:
                        return None  # 「총액 5% 할인」 alone: on what, the model reads
                    rate = float(discount.group("n") or discount.group("m")) / 100
                    items.append({**{k: v for k, v in last.items() if k not in ("when", "name")},
                                  "name": "할인", "rate": rate, "sign": -1,
                                  **({"when": when} if when else {})})
                    continue
                if not amounts:
                    continue
                m = amounts[0]
                price = _won(m.group(1))
                if price is None:
                    return None
                before, after = clause[:m.start()], clause[m.end():]
                per = ("month" if _PER_MONTH.search(before) or _PER_MONTH.search(after[:6])
                       else "year" if _PER_YEAR.search(before) or _PER_YEAR.search(after[:6])
                       else "once")
                recurring |= per != "once"
                count: float | None = 1
                if t := _TIMES_AFTER.match(after):
                    count = float(t.group(1).replace(",", ""))
                elif t := _TIMES_BEFORE.search(before):
                    count = float(t.group(1).replace(",", ""))
                elif _EACH.search(before):
                    count = count_of
                if count is None:
                    return None
                named = _item_name(before.replace(when, " ") if when else before, per)
                if when and named in ("월 이용료", "연 이용료", "구입가"):
                    # 「무상 보증 2년, 3년 연장 시 대당 45,000원」: the extension, named by its line
                    lead = re.sub(r"\([^()]{0,60}\)|[\d,.]{1,20}\s{0,2}(?:년|개월|주|일)?", " ",
                                  line[:line.find(clause)])
                    core = re.sub(r"\s(?:시|경우|때)$", "", when)
                    named = re.sub(r"\s{2,}", " ", f"{lead} {core}").strip(" ,")[:30]
                item = {"name": named, "unit_price": price, "count": count, "per": per}
                if vat := _VAT_AFTER.match(after):
                    item["vat"] = "incl" if vat.group(1) == "포함" else "excl"
                if when:
                    item["when"] = when
                items.append(item)
                last = item
        if not items:
            return None
        options.append({"name": name, "items": items})
    years = None
    period = ""
    if spans := {int(m.group(1)) for m in _SPAN.finditer(request or "")}:
        if len(spans) != 1:
            return None
        years = next(iter(spans))
        period = f"{years}년"
    elif recurring:
        if not _ONE_YEAR.search(request or ""):
            return None
        years, period = 1, "1년"
    data: dict = {"period": period, "options": options, "scenarios": []}
    if years is not None:
        data["years"] = years
    return data


#: a scenario name marked where its readings differ (the strong writer gets both)
_READINGS_DIFFER = " — 읽을 때마다 값이 다름"


def _split_scenarios(costs: dict, others: list[dict], *,
                     strong: bool) -> tuple[dict, list[str]]:
    """`costs` with its scenarios that another agreeing reading prices alike; the rest are
    never dropped silently. A strong writer gets each differing scenario with every reading's
    totals, marked, to judge from the material; for a small writer a differing scenario that
    lowers an option's total (a discount) leaves that option unsettled (the names returned),
    and any other differing scenario is listed as left out."""
    agreed = [sc for sc in costs["scenarios"] if any(
        _same_totals(sc["options"], other["options"])
        for o in others for other in o["scenarios"])]
    differing = [sc for sc in costs["scenarios"] if sc not in agreed]
    if not differing:
        return costs, []
    if strong:
        readings = [*differing]
        for sc in differing:
            readings += [{**other, "name": f"{other['name']} (다른 읽기)"} for o in others
                         for other in o["scenarios"] if other["name"] == sc["name"]
                         and not _same_totals(other["options"], sc["options"])]
        marked = [{**sc, "name": sc["name"] + _READINGS_DIFFER} for sc in readings]
        out = {**costs, "scenarios": [*agreed, *marked][:6]}
        if budget := costs.get("budget"):
            out["budget"] = dict(budget)
            scenario_budgets(out, {"amount": budget["amount"], "vat": budget["vat"]})
        return out, []
    lowered = [name for name, entry in costs["options"].items() if any(
        (sc["options"].get(name) or {}).get("total", entry["total"]) < entry["total"]
        for sc in differing)]
    if lowered:
        return costs, lowered
    return {**costs, "scenarios": agreed,
            "dropped": [*costs["dropped"], "시나리오(읽을 때마다 다름) " + ", ".join(
                sc["name"] for sc in differing)]}, []


async def _compare_cost(pre: Prepass, job: PrepassJob) -> None:
    """Each option's period total: read twice, computed by code."""
    request, material, api_key = job.request, job.material, job.api_key
    complete, model, tiebreak_model, count = job.complete, job.model, job.tiebreak_model, job.count
    if len(_amounts(material)) >= 2:
        # part-priced options (unit × count × years) settle totals even without quote-calc
        given = f"{request}\n{material}"
        numbers = _material_numbers(given) + _calendar(given)

        prompt = [{"role": "user", "content": _COST_PROMPT.format(
            request=request[:3000], material=material[:10000])}]

        async def read_costs(override: str) -> tuple[dict | None, str]:
            text, more = await complete(override or model, prompt, api_key, 2500)
            count(more)
            why: list[str] = []
            return option_costs(counted_as_written(_json(text), given), numbers, why,
                                material=given), (
                why[0] if why else "")

        # split settled by outline model w/ thinking (15/15 vs 12/15 without, 150 s median); plain third past cap
        thinker = next((m for m in (model, tiebreak_model)
                        if m and thinking.thinks_by_default(m)), "")
        deliberated = False

        async def think_costs() -> tuple[dict | None, str]:
            nonlocal deliberated
            async with asyncio.timeout(DELIBERATE_SEC):
                text, more = await job.deliberate(thinker, prompt, api_key, DELIBERATE_TOKENS)
            count(more)
            why: list[str] = []
            found = option_costs(counted_as_written(_json(text), given), numbers, why,
                                 material=given)
            deliberated = found is not None
            return found, (why[0] if why else "")

        # the price lines as written, read by code: the third reading when the model's two
        # differ, in place of a slow thinking read, and the totals when no two readings agree
        code_why: list[str] = []
        code_read = price_lines_reading(request, material)
        code_costs = (option_costs(counted_as_written(code_read, given), numbers, code_why,
                                   material=given) if code_read else None)
        if code_costs is not None and code_costs["dropped"]:
            code_costs = None

        async def code_settle() -> tuple[dict | None, str]:
            return code_costs, ""

        by_code = False
        try:
            costs, how, outcomes = await _vote(
                read_costs, _costs_agree, tiebreak_model or model,
                code_settle if code_costs is not None else
                think_costs if job.deliberate is not None and thinker else None)
            if code_costs is not None:
                if costs is None:
                    # no two readings agree: the code's reading of the price lines stands
                    costs, how, by_code = code_costs, "code", True
                elif costs is code_costs or _costs_agree(costs, code_costs):
                    # a reading agrees with the code's: its scenarios are the code's too
                    costs, by_code = code_costs, True
                else:
                    log.info("cost pre-pass: two readings agree against the price lines")
            if costs is not None and (unpriced := _missing_discount(
                    costs["options"], costs["scenarios"], f"{request}\n{material}")):
                # a discount the material names that no reading applied: those options are
                # not settled, the others are
                pre.costs_withheld = True
                kept = {n: e for n, e in costs["options"].items() if n not in unpriced}
                pre.blocks.append(_partial_cost_block(kept, unpriced, costs["period"]) if kept
                                  else _COST_WITHHELD)
                pre.runs.append({"skill": "compare-cost", "error": True, "summary": (
                    "총비용 일부 보류 — 자료의 할인을 반영하지 않은 읽기: " + ", ".join(unpriced))})
            elif costs is not None:
                # scenario kept only if another agreeing reading has same totals; the code's
                # scenarios are priced from the lines as written
                others = [o[0] for o in outcomes if o[0] is not None and o[0] is not costs
                          and _costs_agree(o[0], costs)]
                costs, unsettled = ((costs, []) if by_code else
                                    _split_scenarios(costs, others, strong=job.strong))
                if unsettled:
                    # a discount scenario the readings price differently: the option's total
                    # without it is not settled either (「30대 이상 구매 시」)
                    pre.costs_withheld = True
                    kept = {n: e for n, e in costs["options"].items() if n not in unsettled}
                    pre.blocks.append(_partial_cost_block(kept, unsettled, costs["period"])
                                      if kept else _COST_WITHHELD)
                    pre.costs_partial = _partial_budget(
                        kept, [o[0] for o in outcomes if o[0] is not None])
                    pre.runs.append({"skill": "compare-cost", "error": True, "summary": (
                        "총비용 일부 보류 — 읽을 때마다 다른 할인 조건: " + ", ".join(unsettled))})
                    return
                pre.costs = costs
                pre.blocks.append(_cost_block(costs, data=job.strong))
                summary = (f"총비용 {len(costs['options'])}개 대안"
                           + (f"({costs['period']})" if costs["period"] else "") + ": "
                           + ", ".join(f"{o} {e['total']:,}원" for o, e in costs["options"].items()))
                if costs.get("applied"):
                    summary += " · 자료로 충족된 조건 적용: " + ", ".join(costs["applied"][:3])
                if costs["scenarios"]:
                    summary += f" · 시나리오 {len(costs['scenarios'])}개"
                if costs["dropped"]:
                    summary += f" (자료에 없는 값이라 뺀 것: {', '.join(costs['dropped'][:4])})"
                if how == "voted":
                    summary += " · 세 번 읽어 둘이 같은 값" + (
                        " (세 번째는 생각을 켜고 읽음)" if deliberated else
                        " (세 번째는 자료의 가격 줄을 코드가 읽음)" if by_code else "")
                elif how == "code":
                    summary += " · 읽을 때마다 값이 달라 자료의 가격 줄을 코드가 읽어 계산"
                pre.runs.append({"skill": "compare-cost", "summary": summary})
            elif how == "split":
                pre.costs_withheld = True
                kept, unsettled = settled_options([o[0] for o in outcomes if o[0] is not None])
                for name in _missing_discount(kept, [], f"{request}\n{material}"):
                    kept.pop(name)
                    unsettled.append(name)
                period = next((o[0]["period"] for o in outcomes if o[0] is not None), "")
                pre.blocks.append(_partial_cost_block(kept, unsettled, period) if kept
                                  else _COST_WITHHELD)
                # an option every reading priced alike is still judged against the budget
                # (no 「예산 자료 없음」 when all three readings agree)
                pre.costs_partial = _partial_budget(
                    kept, [o[0] for o in outcomes if o[0] is not None])
                readings = " / ".join(", ".join(f"{n} {e['total']:,}원" for n, e in o[0]["options"].items())
                                      for o in outcomes if o[0] is not None)
                pre.runs.append({"skill": "compare-cost", "error": True,
                                 "summary": f"{_COST_SPLIT} ({readings[:600]})"})
            else:
                why = next((o[1] for o in outcomes if o[1]), "")
                pre.runs.append({"skill": "compare-cost", "error": True, "summary": "총비용 계산 못 함 ("
                                 + (why or "응답에서 대안별 비용 항목을 읽지 못함") + ")"})
        except Exception as exc:  # noqa: BLE001 — written without the block; said in the step
            log.info("cost pre-pass failed: %s", logs.safe(repr(exc)))
            pre.runs.append({"skill": "compare-cost", "summary": f"총비용 계산 실패 ({_failure(exc)})",
                             "error": True})
    else:
        pre.runs.append({"skill": "compare-cost", "skipped": True,
                         "summary": "자료의 금액이 2개 미만이라 총비용 계산 안 함"})


async def _compare_check(pre: Prepass, job: PrepassJob) -> None:
    """Must-haves per option; weighted scores if weights given."""
    request, material, api_key = job.request, job.material, job.api_key
    complete, model, count = job.complete, job.model, job.count
    try:
        text, more = await complete(
            model, [{"role": "user", "content": _SCORE_PROMPT.format(
                request=request[:3000], material=material[:10000])}], api_key, 1500,
        )
        count(more)
        data = _json(text)
        numbers = _material_numbers(f"{request}\n{material}")
        if musts := must_status(data, numbers, f"{request}\n{material}",
                                pre.costs or pre.costs_partial, today=job.today):
            pre.musts = musts
            pre.blocks.append(_must_block(musts, data=job.strong))
            pre.runs.append({"skill": "compare-check", "summary": "필수 조건 " + ", ".join(
                f"{o} {r['status']}" for o, r in musts["options"].items()) + (
                f" (판정 안 함: {', '.join(musts['unjudged'])})" if musts.get("unjudged") else "")})
        if not _WEIGHTS.search(f"{request}\n{material}") or any(
                not _grounded(c.get("weight"), numbers) for c in data.get("criteria") or []
                if isinstance(c, dict)):
            data = {}
        if result := scores(data):
            pre.scores = result
            pre.blocks.append(_score_block(result))
            pre.runs.append({"skill": "compare-check", "summary":
                             f"점수 {len(result['options'])}개 대안 × {len(result['criteria'])}기준"
                             + (" (동점)" if result["tie"] else "")})
        if not musts and not result:
            pre.runs.append({"skill": "compare-check", "skipped": True, "summary": (
                "응답을 읽지 못해 판정·점수 계산 안 함" if not _json(text) else
                "요청·자료에 필수 조건과 가중치가 없어 판정·점수 계산 안 함")})
    except Exception as exc:  # noqa: BLE001
        log.info("score pre-pass failed: %s", logs.safe(repr(exc)))
        pre.runs.append({"skill": "compare-check", "summary": f"점수 계산 실패 ({_failure(exc)})",
                         "error": True})


async def _career_periods(pre: Prepass, job: PrepassJob) -> None:
    """Period months and total, by code."""
    material, request, today = job.material, job.request, job.today
    if span := career_periods(material, request, today or dt.date.today()):
        pre.periods = span
        pre.blocks.append(_career_block(span))
        pre.runs.append({"skill": "date-calc", "summary":
                         f"기간 {len(span['periods'])}개" + (
                             f" → 총 경력 {span['total_text']}" if not span.get("vague")
                             else " (달이 정해지지 않은 기간 있음)")})
    else:
        pre.runs.append({"skill": "date-calc", "skipped": True,
                         "summary": "자료에서 경력 기간을 읽지 못해 기간 계산 안 함"})


PREPASS_STEPS: dict[str, Callable[[Prepass, PrepassJob], Any]] = {
    "date-calc": _date_calc,
    "proposal-rfp": _proposal_rfp,
    "quote-calc": _quote_calc,
    "staffing": _staffing,
    "compare-cost": _compare_cost,
    "compare-check": _compare_check,
    "career-periods": _career_periods,
}


# ── after writing: checks repaired by verify stage ──


def _amounts(text: str) -> list[float]:
    out = []
    for m in re.finditer(rf"({units._TERM})\s{{0,2}}원", text or ""):
        try:
            out.append(units._ko_number(m.group(1)))
        except ValueError:
            continue
    return out


_QUOTE_LABELS = (
    ("supply", re.compile(r"공급\s{0,2}가(?:액)?|(?:부가세|VAT)\s{0,2}(?:별도|제외)\s{0,2}(?:합계|금액|총액)?", re.I)),
    ("vat", re.compile(r"^(?:\*\*)?\s{0,2}(?:부가(?:가치)?세|VAT)(?:\s{0,2}\(?10\s{0,2}%\)?)?(?:\*\*)?$", re.I)),
    ("total", re.compile(r"총\s{0,2}액|총\s{0,2}견적|견적\s{0,2}총|합계\s{0,2}\(?\s{0,2}(?:부가세|VAT)\s{0,2}포함"
                         r"|총\s{0,2}사업비|최종\s{0,2}금액", re.I)),
    ("subtotal", re.compile(r"^(?:\*\*)?\s{0,2}소\s{0,2}계(?:\*\*)?$")),
    ("either", re.compile(r"^(?:\*\*)?\s{0,2}(?:합\s{0,2}계|계)(?:\*\*)?$")),
)
_SCENARIO_TABLE = re.compile(r"시나리오|대안|옵션|안\s{0,2}\d|전제|단계")


def _row_amount(line: str) -> float | None:
    """Last amount in a table row (bare 「47,100,000」 = won) or sentence."""
    if line.lstrip().startswith("|"):
        found = [a for c in cells(line)[1:] for a in _amounts(c if "원" in c else c + "원") if a >= 1000]
    else:
        found = [a for a in _amounts(line) if a >= 1000]
    return found[-1] if found else None


def _quote_expected(kind: str, quote: dict) -> list[int]:
    if kind == "either":
        return [quote["supply"], quote["total"], quote["subtotal"]]
    return [quote[kind]]


async def quote_totals(doc: Doc, quote: dict) -> tuple[list[Finding], dict]:
    """Quote-table rows whose 공급가액·부가세·총액 differ from quote-calc."""
    found: list[Finding] = []
    if not quote or not quote.get("lines"):
        return found, {}
    for table in tables(doc.sections):
        if _SCENARIO_TABLE.search(" ".join(table.header) + " " + table.above):
            continue
        for line, row in table.rows:
            label = next((c for c in row if c.strip()), "")
            kind = next((k for k, rx in _QUOTE_LABELS if rx.search(label.strip())), None)
            if kind is None:
                continue
            written = _row_amount(line)
            if written is None:
                continue
            expected = _quote_expected(kind, quote)
            if any(abs(written - e) < 0.5 for e in expected):
                continue
            want = f"{expected[0]:,}원" if kind != "either" else " 또는 ".join(f"{e:,}원" for e in expected[:2])
            found.append(Finding(
                table.section, line, "quote_total",
                f"「{label}」이 {written:,.0f}원인데 견적 계산(코드)은 {want}이다. 견적 확정 블록의 값으로 고쳐라.",
                evidence=quote.get("table_markdown", "")[:1200], rank=0,
                still=lambda s, e=expected: (a := _row_amount(s)) is None or not any(
                    abs(a - x) < 0.5 for x in e)))
    flagged = {f.sentence for f in found}
    bogus = _back_computed(quote)
    settled = (f"공급가액(VAT 별도) {quote['supply']:,}원, 부가세 {quote['vat']:,}원, "
               f"합계(VAT 포함) {quote['total']:,}원")
    for index, sec in enumerate(doc.sections):
        for line in str(sec.get("content") or "").split("\n"):
            text = line.strip()
            if not text or text in flagged or text.startswith(("#", "```", "!")):
                continue
            hits = _bogus_in(text, bogus)
            slips = [] if text.startswith("|") else _total_slips(text, quote)
            if not hits and not slips:
                continue
            flagged.add(text)
            why = [f"{', '.join(f'{h:,}원' for h in hits)}은 견적 확정 블록에 없는 값이다(부가세 "
                   "기준을 거꾸로 계산한 값)"] if hits else []
            why += [f"「{label}」이 {written:,}원인데 견적 계산(코드)은 {want:,}원이다"
                    for label, written, want in slips]
            found.append(Finding(
                index, text, "quote_total", "; ".join(why) + f". 견적 금액은 {settled}만 쓴다.",
                evidence=quote.get("table_markdown", "")[:1200], rank=0,
                still=lambda s, b=bogus, q=quote: bool(_bogus_in(s.strip(), b)
                                                       or _total_slips(s.strip(), q))))
    return found, {}


#: sentence quote total: 「견적 총액 85,000,000원(부가세 포함)」, 「합계(VAT 별도)는 …원」
_TOTAL_SAID = re.compile(
    r"(?P<label>(?:견적|제안)?\s{0,2}(?:총액|총\s{0,2}견적\s{0,2}금액|합계)"
    r"(?:\s{0,2}\(\s{0,2}(?P<b1>부가세|VAT)\s{0,2}(?P<k1>포함|별도)\s{0,2}\))?)"
    r"\s{0,2}(?:은|는|이|:)?\s{0,2}(?:금\s{0,2})?(?P<amount>\d{1,3}(?:,\d{3}){1,5})\s{0,2}원"
    r"(?:\s{0,2}\(\s{0,2}(?P<b2>부가세|VAT)\s{0,2}(?P<k2>포함|별도)\s{0,2}\))?", re.I)


def _total_slips(text: str, quote: dict) -> list[tuple[str, int, int]]:
    """Sentence totals differing from quote-calc (VAT 별도 vs 공급가액, else 합계); scenario totals pass."""
    if not quote.get("lines"):
        return []
    others = {int(r.get(k) or 0) for r in quote.get("scenarios") or [] for k in ("supply", "total")}
    out = []
    for m in _TOTAL_SAID.finditer(text or ""):
        written = int(m.group("amount").replace(",", ""))
        basis = m.group("k1") or m.group("k2")
        want = quote["supply"] if basis == "별도" else quote["total"]
        loose = set()
        if not basis and "합계" in m.group("label"):
            # bare 「합계」 may sum any part: 「PM 합계 35,000,000원」
            loose = {quote["supply"], quote["subtotal"],
                     *(int(line.get("amount") or 0) for line in quote["lines"])}
        if written != want and written not in others | loose:
            out.append((m.group("label").strip(), written, want))
    return out


def _back_computed(quote: dict) -> set[int]:
    """Amounts from misreading a settled amount's VAT basis (85,000,000 → 77,272,727 + 7,727,273)."""
    known = {int(quote.get(k) or 0) for k in ("subtotal", "discount", "supply", "vat", "total")}
    known |= {int(line.get("amount") or 0) for line in quote.get("lines") or []}
    for row in quote.get("scenarios") or []:
        known |= {int(row.get(k) or 0) for k in ("subtotal", "supply", "vat", "total")}
    for row in quote.get("quotes") or []:
        known |= {int(row.get(k) or 0) for k in ("amount", "excl", "incl", "headroom")}
    budget = quote.get("budget") or {}
    for part in (budget, budget.get("if_excl") or {}, budget.get("if_incl") or {}):
        known |= {int(part.get(k) or 0) for k in ("amount", "compared", "headroom")}
    out = set()
    for amount in (quote.get("supply"), quote.get("total")):
        if not amount:
            continue
        base = int((Decimal(amount) * 100 / 110).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        out |= {base, amount - base, int((Decimal(amount) / 10).quantize(Decimal(1), rounding=ROUND_FLOOR))}
    return {a for a in out if a >= 1000 and a not in known}


def _bogus_in(text: str, bogus: set[int]) -> list[int]:
    if text.startswith("|"):
        found = [a for c in cells(text) for a in _amounts(c if "원" in c else c + "원")]
    else:
        found = _amounts(text)
    return sorted({int(round(a)) for a in found if int(round(a)) in bogus})


_DUE_CELL_OK = re.compile(
    r"^(?:\*\*)?\s{0,2}(?:20\d{2}[-./]\d{1,2}[-./]\d{1,2}\s{0,2}(?:\([월화수목금토일]\))?"
    r"|\d{1,2}\s{0,2}[/.]\s{0,2}\d{1,2}\s{0,2}(?:\([월화수목금토일]\))?"
    r"|\d{1,2}\s{0,2}월\s{0,2}\d{1,2}\s{0,2}일\s{0,2}(?:\([월화수목금토일]\))?"
    r"|미정|확인\s{0,2}필요|해당\s{0,2}없음|완료|-|—|\[[가-힣][가-힣 ·]{0,9}\]"
    r"|(?:매주|격주)\s{0,2}[월화수목금토일]요일(?:\s{0,2}\([^)]{0,30}\))?)\s{0,2}(?:\*\*)?$"
)
_RELATIVE_WORDS = re.compile(r"다음\s{0,2}주|이번\s{0,2}주|모레|내일|이번\s{0,2}달|\d\s{0,2}주\s{0,2}뒤|까지")
_ISO_IN = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})|(?<!\d)(\d{1,2})\s{0,2}(?:[/.]|월\s{0,2})(\d{1,2})(?!\d)")


def _cell_dates(cell: str) -> list[tuple[int | None, int, int]]:
    out = []
    for m in _ISO_IN.finditer(cell or ""):
        if m.group(1):
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        else:
            out.append((None, int(m.group(4)), int(m.group(5))))
    return out


def bound_rows(row_text: str, dates: list[dict]) -> list[dict]:
    """Resolved dates whose source line is this row's (most shared words)."""
    words = _words(row_text)
    if not words:
        return []
    scored = []
    for d in dates:
        overlap = len(words & _words(d.get("source_line", "")))
        if overlap:
            scored.append((overlap, d))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [d for s, d in scored if s == best]


def date_matches(found: tuple[int | None, int, int], entry: dict) -> bool:
    year, month, day = found
    pool = [entry["date"], *entry.get("dates", [])]
    return any(int(p[5:7]) == month and int(p[8:10]) == day and (year is None or int(p[:4]) == year)
               for p in pool)


#: what an empty owner/deadline cell is told to hold: a value never invented, nor a bracket blank
EMPTY_OWNER = "담당 칸이 비었다 — 이름, 자료에 없으면 「미정」(표 아래 「확인 필요」 한 줄)"
EMPTY_DUE = "기한 칸이 비었다 — 날짜, 자료에 없으면 「미정」(표 아래 「확인 필요」 한 줄)"


async def minutes_rows(doc: Doc, dates: list[dict]) -> tuple[list[Finding], dict]:
    """Each action row: owner and due date (「미정」 + reason if none given or deferred), per date-calc."""
    found: list[Finding] = []
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")]
    for table in tables(doc.sections):
        owner, due = _owner_due(table.header)
        if owner is None or due is None:
            continue
        note = _column(table.header, r"비고|사유|메모|원문")
        task = _task_column(table.header, (owner, due, note))
        for line, row in table.rows:
            if len(row) <= max(owner, due):
                continue
            who, when = row[owner].strip(" *"), row[due].strip(" *")
            problems: list[str] = []
            if not who:
                problems.append(EMPTY_OWNER)
            if not when:
                problems.append(EMPTY_DUE)
            elif not _DUE_CELL_OK.match(when):
                if _RELATIVE_WORDS.search(when) or len(when) > 16:
                    problems.append("기한 칸에는 값만 쓴다(날짜(요일)·미정·[기한])")
            if note is not None and len(row) > note and not row[note].strip(" -—*"):
                if "미정" in (who + when):
                    problems.append("「미정」이면 비고에 사유(회의에서 정하지 않음 등)를 적는다")
            if _stray_repeat(when, row[task] if task is not None and task < len(row) else "", bound):
                problems.append("반복 일정(「매주 …」)은 그 일을 말한 행의 기한에만 쓴다 — 이 행은 "
                                "자료가 이 일에 준 기한(날짜·미정·[기한])으로")
            others = " ".join(c for k, c in enumerate(row) if k != due)
            entries = bound_rows(others, bound)
            written = _cell_dates(when)
            evidence = ""
            if entries and written and not any(date_matches(w, e) for w in written for e in entries):
                shown = ", ".join(dict.fromkeys(e["shown"] for e in entries))
                problems.append(f"기한이 date-calc 해석값({shown})과 다르다")
                evidence = "\n".join(f"「{e['text']}」 → {e['shown']} (출처: {e['source_line'][:120]})"
                                     for e in entries[:3])
            if problems:
                found.append(Finding(
                    table.section, line, "minutes_row", "; ".join(problems) + ". 이 행만 고쳐라.",
                    evidence=evidence, rank=1,
                    still=lambda s, t=table, o=owner, d=due, n=note, b=bound, k=task:
                    _row_still_wrong(s, t, o, d, n, b, k)))
    return found, {}


#: a memo clause that gives someone a task or leaves one open: 「…하기로 함」, 「검토」
_ACTION_SAID = re.compile(r"기로|가져오|맡|담당|검토|예정|해야|필요|정하지|미정|추후|나중에|요청")
_UNOWNED = re.compile(r"^(?:미정|확인\s{0,2}필요|\[[가-힣 ·]{1,10}\])")
#: a memo's clauses: at stops and commas, after 「…함」「…됨」「…했음」 (not 「다음」「모임」)
_MEMO_CLAUSE = re.compile(r"[\n.,:;。]|(?<=[함됨])\s|(?<=[었았였했있없]음)\s")
UNASSIGNED_WHY = ("이 행의 할 일은 자료에서 누구에게도 맡겨지지 않았다(결정이나 안건일 뿐이다). "
                  "실행 항목 표에서 이 행을 빼라 — 결정이면 결정 사항에만 둔다.")


def unassigned_rows(sections: list[dict], material: str) -> list[tuple[int, str, list[str]]]:
    """(section, row, [task]) for action rows with no owner whose task no memo clause gives
    anyone or leaves open: 「2학기 튜터링 모집 시행 | 미정」 from 「모집 기간은 11/3~11/14로 확정」.
    A task sharing a word with a memo clause giving one of its people a task is that person's
    (the writer left the owner out), and a decision table's rows are no action rows."""
    from app.services import stated_days, stated_meeting

    clauses = [re.sub(r"\s+", "", c).lower() for c in _MEMO_CLAUSE.split(material or "")
               if _ACTION_SAID.search(c)]
    named = [c for c in clauses if stated_meeting._ASSIGNED.search(c)
             and any(n in c for n in stated_meeting.people(material))]
    out = []
    for table in tables(sections):
        owner, due = _owner_due(table.header)
        if owner is None or due is None or _DECIDED_TABLE.search(
                " ".join([*table.header, str(sections[table.section].get("heading") or "")])):
            continue
        task = _task_column(table.header, (owner, due, _column(table.header, r"비고|사유|메모|원문")))
        for line, row in table.rows:
            if task is None or len(row) <= max(owner, task):
                continue
            if not _UNOWNED.match(row[owner].strip(" *")):
                continue
            if not stated_meeting.task_given(row[task], clauses) and not any(
                    w in c for w in stated_days._words(row[task]) for c in named):
                out.append((table.section, line, [row[task].strip(" *")]))
    return out


#: a table of what was decided, whose rows no one is to do
_DECIDED_TABLE = re.compile(r"결정|합의|의결")


def _reference_heading() -> re.Pattern:
    from app.services import report

    return report._REFERENCE_HEADING


def minutes_unsourced(doc: Doc) -> list[Finding]:
    """Findings sent back to the writer once; what stands is settled by `settle_unsourced`."""
    from app.services import stated_meeting

    material = doc.memo or doc.given or doc.material
    rows = [Finding(i, row, "minutes_unsourced", UNASSIGNED_WHY, rank=1,
                    still=lambda s, t=task[0]: t in s)
            for i, row, task in unassigned_rows(doc.sections, material)]
    def next_part(index: int) -> bool:
        return stated_meeting.is_next_part(str(doc.sections[index].get("heading") or ""))

    held = stated_meeting.open_rows(doc.sections)
    # an agenda table's row is judged whole (`w`)
    return rows + [Finding(i, piece, "minutes_unsourced",
                           stated_meeting.why(said, piece, material, held), rank=1,
                           still=lambda s, m=material, n=next_part(i),
                           w=piece.startswith("|") and said == [piece.strip()]: bool(
                               stated_meeting.untraced(s, m, next_part=n, rows=held)
                               or (w and stated_meeting.unset_agenda(s, m, listed=True))))
                   for i, piece, said in stated_meeting.findings(
                       doc.sections, material, _reference_heading())]


def settle_unsourced(sections: list[dict], material: str, *, strong: bool) -> list[dict]:
    """What the writer left of `minutes_unsourced` after its one repair (`stated_meeting.settle`
    for sentences): a strong writer's row is marked 「(확인 필요)」, anyone else's goes with the
    「※」 line naming its task."""
    from app.services import stated_meeting

    out = [dict(sec) for sec in sections]
    for index, row, task in unassigned_rows(out, material):
        content = str(out[index].get("content") or "")
        if strong:
            out[index]["content"] = stated_policy.mark(content, row, task)
            continue
        # the 「※」 line may name it short: 「3번(튜터링 모집 시행)」, or by its number alone
        short = " ".join(task[0].split()[-2:])
        number = re.match(r"\|\s{0,4}(\d{1,2})\s{0,4}\|", row.strip())
        by_number = re.compile(rf"※\s{{0,2}}{number.group(1)}\s{{0,2}}번(?!\d)") if number else None

        out[index]["content"] = "\n".join(
            ln for ln in content.split("\n") if ln.strip() != row.strip()
            and not (ln.lstrip().startswith("※") and (
                (short and short in ln) or (by_number and by_number.match(ln.lstrip())))))
    held = stated_meeting.open_rows(out)
    for index, piece, said in stated_meeting.findings(out, material, _reference_heading()):
        whole = "\n".join(str(sec.get("content") or "") for sec in out).replace(piece, "", 1)
        out[index]["content"] = stated_meeting.settle(
            str(out[index].get("content") or ""), piece, said, strong=strong, material=material,
            elsewhere=whole, rows=held,
            next_part=stated_meeting.is_next_part(str(out[index].get("heading") or "")))
    return out


#: a cell that says nothing: 「미정」, 「확인 필요」, 「-」
_EMPTY_CELL = re.compile(r"\(?(?:미정|확인\s{0,2}필요|해당\s{0,2}없음|-|—|–)?\)?")
_RULE_ROW = re.compile(r"\|?(?:[ \t]{0,4}:?-{2,}:?[ \t]{0,4}\|){1,40}[ \t]{0,4}(?::?-{2,}:?)?[ \t]{0,4}$")
#: columns minutes keep even when empty
_ACTION_COLUMN = re.compile(r"할\s{0,2}일|담당|책임|기한|마감|비고|출처|안건|항목|결정|내용|구분|상태")


def minutes_blank_columns(sections: list[dict], material: str) -> tuple[list[dict], int]:
    """Table columns no memo word names and no cell fills (「납기 | 미정 | 미정」) taken out."""
    flat = re.sub(r"\s+", "", material or "").lower()
    out, dropped = [], 0
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        i = 0
        while i < len(lines):
            if not (lines[i].lstrip().startswith("|") and i + 1 < len(lines)
                    and _RULE_ROW.match(lines[i + 1].strip())):
                i += 1
                continue
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            header = cells(lines[i])
            body = [cells(ln) for ln in lines[i + 2:j]]
            gone = [k for k, name in enumerate(header)
                    if body and len(header) > 2 and not _ACTION_COLUMN.search(name)
                    and re.sub(r"\s+|\([^)]{0,20}\)|[*_`]", "", name).lower() not in flat
                    and all(len(r) <= k or _EMPTY_CELL.fullmatch(r[k].strip(" *")) for r in body)]
            if gone and len(gone) < len(header) - 1:
                lines[i:j] = ["| " + " | ".join(c for k, c in enumerate(row) if k not in gone)
                              + " |" for row in (header, cells(lines[i + 1]), *body)]
                dropped += len(gone)
            i = j
        out.append({**sec, "content": "\n".join(lines)})
    return out, dropped


_REPEAT_CELL = re.compile(r"(?:매주|격주)\s{0,2}[월화수목금토일]요일")


def _stray_repeat(when: str, action: str, bound: list[dict]) -> bool:
    """「매주 월요일」 on a row other than the recurring line's task."""
    repeats = [d for d in bound if d.get("status") == "repeat"]
    return bool(repeats and _REPEAT_CELL.search(when or "") and action.strip()
                and not any(_action_hits(action, d) for d in repeats))


def _row_still_wrong(line: str, table: Table, owner: int, due: int, note: int | None,
                     bound: list[dict], task: int | None = None) -> bool:
    row = cells(line)
    if len(row) <= max(owner, due):
        return True
    who, when = row[owner].strip(" *"), row[due].strip(" *")
    if not who or not when:
        return True
    if _stray_repeat(when, row[task] if task is not None and task < len(row) else "", bound):
        return True
    if not _DUE_CELL_OK.match(when) and (_RELATIVE_WORDS.search(when) or len(when) > 16):
        return True
    entries = bound_rows(" ".join(c for k, c in enumerate(row) if k != due), bound)
    written = _cell_dates(when)
    return bool(entries and written and not any(date_matches(w, e) for w in written for e in entries))


_BLANK = {"", "-", "—", "–", "?", "n/a", "N/A"}


async def comparison_cells(doc: Doc, scored: dict | None) -> tuple[list[Finding], dict]:
    """Comparison tables: no blank cell; weighted scores match code's."""
    found: list[Finding] = []
    options = list((scored or {}).get("options") or {})
    for table in tables(doc.sections):
        heading = str(doc.sections[table.section].get("heading") or "")
        is_comparison = "비교" in heading or sum(any(o and o in h for h in table.header) for o in options) >= 2
        if not is_comparison:
            continue
        for line, row in table.rows:
            if any(c.strip(" *") in _BLANK for c in row[1:]):
                found.append(Finding(
                    table.section, line, "comparison_blank",
                    "비교표에 빈칸이 있다. 값이 자료에 없으면 「자료 없음」, 측정 조건이 다르면 「조건 다름」으로 채워라.",
                    rank=2, still=lambda s: any(c.strip(" *") in _BLANK for c in cells(s)[1:])))
        if not scored:
            continue
        for line, row in table.rows:
            label = row[0] if row else ""
            if not re.search(r"총점|합계|점수|가중", label):
                continue
            for k, head in enumerate(table.header):
                option = next((o for o in options if o and o in head), None)
                if option is None or k >= len(row):
                    continue
                try:
                    written = float(re.sub(r"[^\d.]", "", row[k]) or "nan")
                except ValueError:
                    continue
                want = scored["options"][option]
                allowed = [v for v in (want["total"], want["renormalized"]) if v is not None]
                if not math.isnan(written) and allowed and all(abs(written - a) > 0.051 for a in allowed):
                    found.append(Finding(
                        table.section, line, "comparison_score",
                        f"「{option}」 점수가 {written:g}인데 코드 계산은 {allowed[0]:g}"
                        + (f"(자료 없음 제외 환산 {allowed[1]:g})" if len(allowed) > 1 else "") + "이다.",
                        evidence=_score_block(scored)[:1200], rank=0,
                        still=lambda s, k=k, a=allowed: _score_still(s, k, a)))
                    break
    return found, {}


def _score_still(line: str, column: int, allowed: list[float]) -> bool:
    row = cells(line)
    if column >= len(row):
        return True
    try:
        written = float(re.sub(r"[^\d.]", "", row[column]) or "nan")
    except ValueError:
        return True
    return math.isnan(written) or all(abs(written - a) > 0.051 for a in allowed)


_NUMBER_TOKEN = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


def _tokens(text: str) -> list[float]:
    out = []
    for m in _NUMBER_TOKEN.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _derived(numbers: list[float]) -> set[float]:
    """Values derived from material numbers (difference, % change, small multiple), rounded half up; counts < 10 skipped."""
    pool = sorted({n for n in numbers if n >= 10 or n != int(n)})[:80]
    out: set[float] = set()

    def rounded(value: float, places: tuple[int, ...]) -> set[float]:
        exact = Decimal(str(value))
        return {float(exact.quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP)) for p in places}

    for a in pool:
        for b in pool:
            if a == b:
                continue
            out.add(round(abs(b - a), 6))
            out |= rounded(abs(b - a) / a * 100, (0, 1, 2))
            if b / a < 10:
                out |= rounded(b / a, (0, 1))
    return out


_DATE_LIKE = re.compile(
    r"(?<![\d.])(?:19|20)\d{2}\s{0,2}(?:[./-]|년)\s{0,2}\d{1,2}(?:\s{0,2}(?:[./-]|월)\s{0,2}\d{1,2})?\s{0,2}[월일]?"
    r"|(?<![\d.])\d{2}\s{0,2}년\s{0,2}\d{1,2}\s{0,2}월"
    r"|(?<![\d.,])(?:19|20)\d{2}(?![\d.,])(?!\s{0,2}(?:원|명|만|개|건|%|kg|시간|자|바이트))"
    r"|\d{1,2}\s{0,2}월\s{0,2}\d{1,2}\s{0,2}일|(?<![\d./])\d{1,2}/\d{1,2}(?![\d/])"
)


def figure_tokens(text: str) -> list[float]:
    return _tokens(_DATE_LIKE.sub(" ", text or ""))


def _known(value: float, known: set[float]) -> bool:
    return any(abs(value - k) < 1e-6 for k in known)


async def career_numbers(doc: Doc, periods: dict | None) -> tuple[list[Finding], dict]:
    """Numbers not in or derived from material, and a total career differing from date-calc's."""
    material_numbers = figure_tokens(doc.material)
    known = set(material_numbers) | _derived(material_numbers)
    if periods:
        for p in periods["periods"]:
            known.update({float(p["months"]), *map(float, p["years_months"])})
        known.update({float(periods["total_months"]), *map(float, periods["total"])})
        for gap in periods.get("gaps") or []:
            known.add(float(gap["months"]))
    found: list[Finding] = []
    for index, sec in enumerate(doc.sections):
        body = str(sec.get("content") or "")
        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith(("```", "!")):
                continue
            strays = [n for n in figure_tokens(stripped) if n >= 2 and not _known(n, known)]
            if strays:
                found.append(Finding(
                    index, stripped, "career_number",
                    "자료에 없는 숫자(" + ", ".join(f"{n:g}" for n in strays[:4]) + ")가 있다. 자료의 값으로 "
                    "고치거나 그 숫자를 빼라. 성과를 지어내지 마라.",
                    rank=0, still=lambda s, k=known: any(
                        n >= 2 and not _known(n, k) for n in figure_tokens(s))))
    if periods and not periods.get("vague"):
        total, (years, months) = periods["total_months"], periods["total"]
        claim = re.compile(
            r"총\s{0,2}경력[^\n.\d]{0,12}(?:(\d{1,2})\s{0,2}년\s{0,2})?(?:(\d{1,3})\s{0,2}개월)?")
        for index, sec in enumerate(doc.sections):
            for line in str(sec.get("content") or "").split("\n"):
                m = claim.search(line)
                if not m or not (m.group(1) or m.group(2)):
                    continue
                y, mo = int(m.group(1) or 0), int(m.group(2) or 0)
                if (m.group(1) and (y, mo) != (years, months)) or (not m.group(1) and mo != total):
                    found.append(Finding(
                        index, line.strip(), "career_total",
                        f"총 경력이 date-calc 계산({periods['total_text']}, {total}개월)과 다르다.",
                        evidence=_career_block(periods)[:1200], rank=0,
                        still=lambda s, c=claim, t=(years, months), tm=total: _total_still(s, c, t, tm)))
    return found, {}


def _total_still(line: str, claim: re.Pattern[str], total: tuple[int, int], months: int) -> bool:
    m = claim.search(line)
    if not m or not (m.group(1) or m.group(2)):
        return False
    y, mo = int(m.group(1) or 0), int(m.group(2) or 0)
    return (bool(m.group(1)) and (y, mo) != total) or (not m.group(1) and mo != months)


_PERIOD_TEXT = re.compile(
    r"(?P<y1>(?:19|20)\d{2})[ \t]{0,2}(?:[./-]|년)[ \t]{0,2}(?P<m1>\d{1,2})[ \t]{0,2}월?[ \t]{0,3}[~∼〜–—-][ \t]{0,3}"
    r"(?:(?P<y2>(?:19|20)\d{2})[ \t]{0,2}(?:[./-]|년)[ \t]{0,2}(?P<m2>\d{1,2})(?!\d)[ \t]{0,2}월?"
    r"|(?P<now>현재(?:[ \t]{0,2}\(?재직[ \t]{0,2}중\)?)?|재직[ \t]{0,2}중))"
)


def tidy_periods(sections: list[dict]) -> tuple[list[dict], int]:
    """One period style: 「2019.04 ~ 2021.01」, 「2023.03 ~ 현재」."""
    changed = 0

    def one(m: re.Match[str]) -> str:
        nonlocal changed
        if not (1 <= int(m.group("m1")) <= 12) or (m.group("m2") and not 1 <= int(m.group("m2")) <= 12):
            return m.group(0)
        end = f"{m.group('y2')}.{int(m.group('m2')):02d}" if m.group("y2") else "현재"
        shown = f"{m.group('y1')}.{int(m.group('m1')):02d} ~ {end}"
        if shown != m.group(0):
            changed += 1
        return shown

    out = [{**sec, "content": _PERIOD_TEXT.sub(one, str(sec.get("content") or ""))} for sec in sections]
    return out, changed


# ── lead times: 「발주 후 8주」 as code-computed date ──

#: written date: 「2026. 12. 14.」 「2026-12-14」 「12월 14일」 「12. 14.」
_DAY_IN = re.compile(
    r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})(?!\d)"
    r"|(?<![\d.,])(\d{1,2})\s{0,2}(?:\.|월)\s{0,2}(\d{1,2})\s{0,2}[.일](?!\d)")
_DONE_WORDS = re.compile(r"납기|납품|완료|설치|도착|입고|준공|검수|인도|\d{1,3}\s{0,2}주")
#: late verdict vs deadline (「기한 2026. 12. 18. 초과」), not a risk (「초과 위험」)
_LATE_WORDS = r"초과|넘(?:어|음|김|는다|긴다)|미충족|불충족|맞추지\s{0,2}못"
_ON_TIME_WORDS = r"이내|충족|준수|맞춤|전\s{0,2}완료"
#: done date: 「완료일: 2026. 12. 14.」, 「납품 예정 12월 14일」
_DONE_DATE = re.compile(
    r"(?:완료|납품|도착|입고|준공|인도)(?:[^\n\d기마까]{0,8})(?P<day>"
    r"20\d{2}\s{0,2}(?:[-./]|년)\s{0,2}\d{1,2}\s{0,2}(?:[-./]|월)\s{0,2}\d{1,2}(?!\d)"
    r"|\d{1,2}\s{0,2}(?:\.|월)\s{0,2}\d{1,2}\s{0,2}[.일](?!\d))")
_UNTIL = re.compile(r"\.?\s{0,2}(?:\([월화수목금토일]\)\s{0,2})?까지")
_NOT_VERDICT = re.compile(r"^\s{0,2}(?:위험|우려|가능성|시\b|여부|할\s{0,2}경우|되면)")


def _days_in(text: str) -> list[tuple[int | None, int, int]]:
    out = []
    for m in _DAY_IN.finditer(text or ""):
        if m.group(1):
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        else:
            out.append((None, int(m.group(4)), int(m.group(5))))
    return out


def _lead_keys(label: str) -> list[str]:
    """Names for a lead-time row: 「A사(에이브이원)」 → A사, 에이브이원."""
    return [k for k in re.split(r"[\s()（）\[\]·,/:]+", label or "") if len(k) >= 2][:4]


def _day_matches(found: tuple[int | None, int, int], iso: str) -> bool:
    year, month, day = found
    return int(iso[5:7]) == month and int(iso[8:10]) == day and (year is None or int(iso[:4]) == year)


def _verdict_near(text: str, deadline: str, words: str) -> bool:
    """Whether `words` closely follow 「기한」 or the deadline date."""
    month, day = int(deadline[5:7]), int(deadline[8:10])
    near = rf"(?:기한|마감|{month}\s{{0,2}}[.월]\s{{0,2}}{day}\s{{0,2}}[.일]?)[^\n]{{0,16}}?(?:{words})"
    for m in re.finditer(near, text):
        if not _NOT_VERDICT.match(text[m.end():m.end() + 8]):
            return True
    return False


def _lead_problem(text: str, row: dict, allowed: list[str]) -> str:
    """A lead-time line's error: uncomputed date or inverted deadline verdict."""
    problems = []
    if _DONE_WORDS.search(text):
        wrong = [d for d in _days_in(text) if not any(_day_matches(d, iso) for iso in allowed)]
        # 「완료일:」 date is the done date even if the material has it
        wrong += [d for m in _DONE_DATE.finditer(text) if not _UNTIL.match(text[m.end():m.end() + 4])
                  for d in _days_in(m.group("day")) if not _day_matches(d, row["date"])]
        if wrong:
            problems.append(f"완료 예정일이 date-calc 계산({row['shown']})과 다르다")
    if row.get("deadline"):
        if row["within"] and _verdict_near(text, row["deadline"], _LATE_WORDS):
            problems.append(f"기한 판정이 거꾸로다 — {_lead_verdict(row)}")
        elif not row["within"] and _verdict_near(text, row["deadline"], _ON_TIME_WORDS) \
                and not _verdict_near(text, row["deadline"], _LATE_WORDS):
            problems.append(f"기한 판정이 거꾸로다 — {_lead_verdict(row)}")
    return "; ".join(problems)


def _lead_rows_in(text: str, rows: list[dict]) -> list[dict]:
    return [r for r in rows if any(k in text for k in _lead_keys(r.get("label") or ""))]


async def lead_time_dates(doc: Doc, lead: dict | None) -> tuple[list[Finding], dict]:
    """Lead-time lines whose date or verdict differs from date-calc; row-as-column tables read by column."""
    found: list[Finding] = []
    rows = [r for r in (lead or {}).get("items") or [] if r.get("status") == "ok" and r.get("label")]
    if not rows:
        return found, {}
    known = [f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
             for d in (_safe_day(m) for m in _FULL_DATE.finditer(f"{doc.request}\n{doc.material}")) if d]
    allowed = {id(r): [r["date"], r["start"], *([r["deadline"]] if r.get("deadline") else []), *known]
               for r in rows}

    def still_for(row: dict):
        return lambda s, r=row: bool(_lead_problem(s, r, allowed[id(r)]))

    def finding(section: int, line: str, row: dict, why: str) -> Finding:
        return Finding(
            section, line, "lead_time_date",
            f"{why}. 이 줄의 {row['label']} 날짜와 기한 판정을 확정값으로 고쳐라: 「{row['text']}」 → "
            f"{row['shown']}" + (f", {_lead_verdict(row)}" if row.get("deadline") else "") + ".",
            evidence=f"「{row['text']}」 = {row['anchor']} {row['start']} + {row['n']}{row['unit']} → "
                     f"{row['shown']} (출처: {row['source_line'][:120]})",
            rank=1, still=still_for(row))

    for index, sec in enumerate(doc.sections):
        fenced = False
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced or line.lstrip().startswith("|") or not line.strip():
                continue
            hit = _lead_rows_in(line, rows)
            if len(hit) == 1 and (why := _lead_problem(line, hit[0], allowed[id(hit[0])])):
                found.append(finding(index, line.strip(), hit[0], why))
    for table in tables(doc.sections):
        columns = {k: hit[0] for k, h in enumerate(table.header) if len(hit := _lead_rows_in(h, rows)) == 1}
        for line, cells_ in table.rows:
            hit = _lead_rows_in(" ".join(cells_[:1]), rows)
            if len(hit) == 1 and (why := _lead_problem(line, hit[0], allowed[id(hit[0])])):
                found.append(finding(table.section, line, hit[0], why))
                continue
            label = cells_[0] if cells_ else ""
            for k, row in columns.items():
                if k < len(cells_) and k and (why := _lead_problem(f"{label} {cells_[k]}", row,
                                                                   allowed[id(row)])):
                    found.append(Finding(
                        table.section, line, "lead_time_date",
                        f"{why}. 이 행의 {row['label']} 칸을 확정값으로 고쳐라: 「{row['text']}」 → "
                        f"{row['shown']}" + (f", {_lead_verdict(row)}" if row.get("deadline") else "") + ".",
                        evidence=f"{row['anchor']} {row['start']} + {row['n']}{row['unit']} → {row['shown']}",
                        rank=1, still=lambda s, r=row, c=k: _cell_still(s, c, r, allowed[id(r)])))
                    break
    return found, {}


def _safe_day(m: re.Match[str]) -> dt.date | None:
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _cell_still(line: str, column: int, row: dict, allowed: list[str]) -> bool:
    parts = cells(line)
    if column >= len(parts):
        return False
    return bool(_lead_problem(f"{parts[0]} {parts[column]}", row, allowed))


def lead_dates_missing(sections: list[dict], lead: dict | None) -> list[dict]:
    """Lead-time rows whose computed date is never written."""
    whole = _whole(sections)
    days = _days_in(whole)
    return [r for r in (lead or {}).get("items") or [] if r.get("status") == "ok"
            and not any(_day_matches(d, r["date"]) for d in days)]


def _quote_totals_check(pre: Prepass) -> Check | None:
    if not pre.quote:
        return None
    quote = pre.quote

    async def proposal_quote(doc: Doc):
        return await quote_totals(doc, quote)

    return proposal_quote


def _proposal_schedule_check(pre: Prepass) -> Check | None:
    if not pre.rfp:
        return None
    rfp = pre.rfp

    async def proposal_check(doc: Doc):
        return await proposal_schedule(doc, rfp)

    return proposal_check


def _lead_dates_check(pre: Prepass) -> Check | None:
    if not pre.lead:
        return None
    lead = pre.lead

    async def lead_check(doc: Doc):
        return await lead_time_dates(doc, lead)

    return lead_check


def _minutes_rows_check(pre: Prepass) -> Check:
    dates = pre.dates

    async def minutes_check(doc: Doc):
        table_rows, _ = await minutes_rows(doc, dates)
        list_rows, _ = await minutes_list_rows(doc, dates)
        return table_rows + list_rows + minutes_unsourced(doc), {}

    return minutes_check


def _compare_cells_check(pre: Prepass) -> Check:
    scored, musts = pre.scores, pre.musts

    async def compare_check(doc: Doc):
        cell_findings, _ = await comparison_cells(doc, scored)
        advice, _ = await comparison_recommendation(doc, musts, scored)
        return cell_findings + advice, {}

    return compare_check


def _career_numbers_check(pre: Prepass) -> Check:
    periods = pre.periods

    async def career_check(doc: Doc):
        return await career_numbers(doc, periods)

    return career_check


def _staff_counts_check(pre: Prepass) -> Check | None:
    if not pre.staff:
        return None
    roles = pre.staff

    async def staff_check(doc: Doc):
        return await staff_counts(doc, roles)

    return staff_check


CHECK_FAMILIES: dict[str, Callable[[Prepass], Check | None]] = {
    "quote-totals": _quote_totals_check,
    "proposal-schedule": _proposal_schedule_check,
    "gaejo-style": lambda pre: gaejo_style,
    "lead-dates": _lead_dates_check,
    "minutes-rows": _minutes_rows_check,
    "compare-cells": _compare_cells_check,
    "career-numbers": _career_numbers_check,
    "staff-counts": _staff_counts_check,
    "material-blanks": lambda pre: material_blanks,
    "count-labels": lambda pre: count_labels,
}


def checks(pre: Prepass | None) -> list[Check]:
    """Code checks from pipeline settings (structure-implied first); run before and after repair."""
    if pre is None:
        return []
    built = [CHECK_FAMILIES[name](pre) for name in pre.settings.code_checks]
    return [check for check in built if check is not None]


# ── staffing: people vs MM ──

#: 「AI 엔지니어 1명 3.0MM」, 「퍼블리셔 2명 각 4MM」
_STAFF_LINE = re.compile(
    r"([A-Za-z가-힣][A-Za-z가-힣 ]{0,20}?)\s{0,2}(\d{1,3})\s{0,2}명\s{0,3}(?:[,·×x*]\s{0,2})?(?:각\s{0,2})?"
    r"(\d{1,3}(?:\.\d{1,2})?)\s{0,2}(?:MM|M/M|맨먼스)", re.I)


def staffing(material: str, quote_input: dict | None = None) -> dict[str, dict]:
    """Per-role headcount and MM from material (code, then quote-calc): `{role: {"people", "mm"}}`."""
    out: dict[str, dict] = {}
    for m in _STAFF_LINE.finditer(material or ""):
        role = re.sub(r"^(?:[-*·•]\s{0,2}|및\s|그리고\s)", "", m.group(1).strip()).strip()
        if len(role) >= 2:
            out.setdefault(role, {"people": float(m.group(2)), "mm": float(m.group(3))})
    for item in (quote_input or {}).get("items") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if not name or item.get("mm") is None or name in out:
            continue
        try:
            people = float(item["people"]) if item.get("people") is not None else None
            out[name] = {"people": people, "mm": float(item["mm"])}
        except (TypeError, ValueError):
            continue
    return out


def _headcount_problem(role: str, said: float, row: dict) -> str:
    people, mm = row.get("people"), row.get("mm")
    if people is not None and abs(said - people) > 1e-9:
        return (f"「{role} {said:g}명」: 자료는 {role} {people:g}명"
                + (f" × {mm:g}MM" if mm is not None else "") + "이다. 인원은 자료의 사람 수로, "
                "투입량은 MM으로 따로 써라(MM을 인원으로 쓰지 않는다)")
    if people is None and (said != int(said) or (mm is not None and abs(said - mm) < 1e-9
                                                    and said != 1)):
        return (f"「{role} {said:g}명」은 투입량 {mm:g}MM을 인원으로 옮긴 것이다. 자료가 인원을 "
                "밝히지 않으면 인원은 쓰지 말고 MM만 쓴다")
    return ""


def _said_headcounts(text: str, roles: dict[str, dict]) -> list[tuple[str, float]]:
    found = []
    for role in roles:
        for m in re.finditer(re.escape(role) + r"\s{0,2}(?:\(|은|는|이|가)?\s{0,2}(\d{1,3}(?:\.\d{1,2})?)"
                             r"\s{0,2}명(?!분)", text):
            found.append((role, float(m.group(1))))
    return found


async def staff_counts(doc: Doc, roles: dict[str, dict]) -> tuple[list[Finding], dict]:
    """Headcount written as MM (「3명(3.0MM)」 where plan is 1명 × 3.0MM)."""
    found: list[Finding] = []
    for index, sec in enumerate(doc.sections):
        content = str(sec.get("content") or "")
        for table in tables([{"content": content}]):
            people_col = _column(table.header, r"인원|인력\s{0,2}수|명수")
            if people_col is None:
                continue
            for line, row in table.rows:
                role = next((r for r in roles if row and r in row[0]), None)
                cell = row[people_col] if role and people_col < len(row) else ""
                m = re.match(r"\s{0,2}(\d{1,3}(?:\.\d{1,2})?)\s{0,2}명?\s{0,2}$", cell)
                if role and m and (why := _headcount_problem(role, float(m.group(1)), roles[role])):
                    found.append(Finding(index, line, "staff_headcount", why + ".", rank=1,
                                         still=lambda s, r=role, c=people_col: _row_off(s, r, c, roles)))
        for raw in content.split("\n"):
            if raw.lstrip().startswith(("|", "#", "```", "!")):
                continue
            for sentence in units.SENTENCE_BREAK.split(raw):
                whys = [w for role, n in _said_headcounts(sentence, roles)
                        if (w := _headcount_problem(role, n, roles[role]))]
                if whys:
                    found.append(Finding(
                        index, sentence.strip(), "staff_headcount", " ".join(whys) + ".", rank=1,
                        still=lambda s: any(_headcount_problem(r, n, roles[r])
                                            for r, n in _said_headcounts(s, roles))))
    return found, {}


def _row_off(line: str, role: str, column: int, roles: dict[str, dict]) -> bool:
    row = cells(line)
    cell = row[column] if column < len(row) else ""
    m = re.match(r"\s{0,2}(\d{1,3}(?:\.\d{1,2})?)\s{0,2}명?\s{0,2}$", cell)
    return bool(m and _headcount_problem(role, float(m.group(1)), roles[role]))


# ── placeholders the material can fill ──

_DAY_VALUE = (r"(?:20\d{2}\s{0,2}[-./년]\s{0,2})?\d{1,2}\s{0,2}(?:[/.]|월\s{0,2})\s{0,2}\d{1,2}\s{0,2}일?"
              r"(?:\s{0,2}\([월화수목금토일]\))?")
_SLOTS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"이메일|메일\s{0,2}주소|e-?mail", re.I),
     re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}")),
    (re.compile(r"연락처|전화"), re.compile(r"\d{2,3}-\d{3,4}-\d{4}")),
    (re.compile(r"석수|좌석"), re.compile(r"(?<![\d,])\d[\d,]{0,6}\s{0,2}석")),
    (re.compile(r"인원|명수|참석자\s{0,2}수|인력|정원"),
     re.compile(r"(?<![\d,])\d[\d,]{0,6}\s{0,2}명(?!분)")),
    (re.compile(r"입사|시작\s{0,2}월|시작일|연월"),
     re.compile(r"(?:20\d{2}|(?<!\d)\d{2})\s{0,2}(?:년|[./])\s{0,2}\d{1,2}\s{0,2}월?")),
    (re.compile(r"기한|일자|날짜|마감|완료일|게시일|시점"), re.compile(_DAY_VALUE)),
    (re.compile(r"금액|비용|단가|예산"),
     re.compile(r"(?<![\d,])\d[\d,]{0,15}\s{0,2}(?:억|천만|만)?\s{0,2}원")),
    (re.compile(r"진행률|비율|퍼센트"), re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3})?\s{0,2}(?:%|퍼센트)")),
    (re.compile(r"기간"), re.compile(r"(?<!\d)\d{1,3}\s{0,2}(?:개월|년|주)")),
    (re.compile(r"수치|측정값"),
     re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3})?\s{0,2}(?:%|퍼센트)"
                r"|(?<![\d,])\d[\d,]{0,12}\s{0,2}(?:명|석|건|개|대|원|일|kg|km)(?!분)")),
)
_COMMON = frozenset({"회의", "자료", "확인", "다음", "기준", "경우", "결과", "내용", "관련", "예정",
                     "필요", "정도", "이번", "해당", "진행", "현재", "부분", "사항", "미정", "원문",
                     "비고", "출처", "담당", "기한", "완료"})


def _slot(name: str) -> re.Pattern[str] | None:
    return next((value for pattern, value in _SLOTS if pattern.search(name)), None)


_OPTION_LABEL = re.compile(r"(?<![가-힣A-Za-z0-9])([A-Z]|[1-9])안")


def _option_part(src: str, option: str) -> str:
    """Part of a multi-option material line about one option; whole line otherwise."""
    marks = [m for m in _OPTION_LABEL.finditer(src)]
    if len({m.group(1) for m in marks}) < 2:
        return src
    starts = [m.start() for m in marks if m.group(1) == option]
    if not starts:
        return ""
    start = starts[0]
    after = next((m.start() for m in marks if m.start() > start and m.group(1) != option),
                 len(src))
    return src[start:after]


def answerable_blanks(sections: list[dict], material: str) -> list[tuple[int, str, str, list[str]]]:
    """Blanks the material fills nearby: `(section, sentence/row, blank, evidence lines)`."""
    lines = [ln.strip() for ln in (material or "").splitlines() if ln.strip()]
    out: list[tuple[int, str, str, list[str]]] = []
    for index, sec in enumerate(sections):
        for line in str(sec.get("content") or "").split("\n"):
            if not placeholders.PLACEHOLDER.search(line) or line.lstrip().startswith(("#", "```")):
                continue
            pieces = [line] if line.lstrip().startswith("|") else [
                p for p in units.SENTENCE_BREAK.split(line) if placeholders.PLACEHOLDER.search(p)]
            # option paragraph reads only its option's part, so 「A안 … [금액]」 never gets B안's value
            named_options = {m.group(1) for m in _OPTION_LABEL.finditer(line)}
            option = (next(iter(named_options)) if len(named_options) == 1
                      and not line.lstrip().startswith("|") else "")
            for piece in pieces:
                own = _words(placeholders.PLACEHOLDER.sub(" ", piece)) - _COMMON
                for m in placeholders.PLACEHOLDER.finditer(piece):
                    name = m.group(1)
                    # blank's own name ties too: 「[경력 시작 월]」 ↔ 「총 경력 … 2019년 4월」
                    named = _words(name) - _COMMON
                    labelled = re.compile(re.escape(name) + r"\s{0,2}[:：]\s{0,2}\S")
                    value = _slot(name)
                    near = _words(piece[max(0, m.start() - 12):m.start()]) - _COMMON
                    evidence = []
                    for src in lines:
                        if option:
                            src = _option_part(src, option)
                            if not src:
                                continue
                        if placeholders.PLACEHOLDER.search(src) and not labelled.search(src):
                            continue
                        if labelled.search(src):
                            evidence.append(src)
                            continue
                        if value is None:
                            continue
                        # value already in the sentence is not the blank's
                        fresh = [v.group(0) for v in value.finditer(src)
                                 if _digits_of(v.group(0)) not in _digits_of(piece)]
                        if not fresh:
                            continue
                        shared = (own | named) & _words(src)
                        if len(shared) >= 2 or (shared and shared & (near | named)):
                            evidence.append(src)
                    if evidence:
                        out.append((index, piece, m.group(0), evidence[:4]))
    return out


async def material_blanks(doc: Doc) -> tuple[list[Finding], dict]:
    """Blanks the material can fill: repair fills from shown lines."""
    found: list[Finding] = []
    for index, piece, blank, evidence in answerable_blanks(doc.sections, doc.material):
        if any(f.section == index and f.sentence == piece for f in found):
            continue
        values = [v.group(0) for src in evidence for v in (_slot(blank[1:-1]) or re.compile(r"$^"))
                  .finditer(src)]

        def still(text: str, blank=blank, values=values) -> bool:
            if blank in text:
                return True
            return bool(values) and not any(_digits_of(v) in _digits_of(text) for v in values)

        found.append(Finding(
            index, piece, "placeholder_answerable",
            f"「{blank}」 자리의 값을 자료가 준다. 아래 자료 줄의 값으로 채워라(자료에 없는 값은 쓰지 "
            "말고, 자료가 다른 대상을 말하면 원문 그대로 답한다).",
            evidence="\n".join(evidence), still=still, rank=2))
    return found, {}


def _digits_of(text: str) -> str:
    return re.sub(r"[\s,]", "", text)


#: a count the material ties to a quoted remark: 「'밤샘 중 휴식 공간 부족' 23건」
_QUOTED_COUNT = re.compile(
    r"[‘'\"「『“](?P<label>[^’'\"」』”\n]{2,40})[’'\"」』”]\s{0,2}(?P<n>\d{1,5})\s{0,2}(?P<unit>건|명|회|표)")
_ITEM_LINE = re.compile(r"^(?P<indent>\s{0,12})(?:[-*+]|\d{1,2}[.)])\s{1,4}(?P<text>.*)$")


def quoted_counts(material: str) -> list[tuple[str, str, str]]:
    """(label, count, unit) for each count the material gives with its quoted remark."""
    return [(m.group("label").strip(), m.group("n"), m.group("unit"))
            for m in _QUOTED_COUNT.finditer(material or "")]


def _parent_item(lines: list[str], k: int) -> str:
    """Text of the nearest list item above line `k` that is less indented."""
    m = _ITEM_LINE.match(lines[k])
    if not m:
        return ""
    depth = len(m.group("indent"))
    for j in range(k - 1, -1, -1):
        up = _ITEM_LINE.match(lines[j])
        if up and len(up.group("indent")) < depth:
            return up.group("text")
        if not lines[j].strip():
            break
    return ""


#: words any complaint shares (「부족」, 「불만」): they tie a count to nothing
_REMARK_WORDS = frozenset({"부족", "문제", "지적", "불만", "불편", "개선", "미흡", "필요", "요청", "의견",
                           "응답", "답변", "주관식", "건수"})


def _remark_words(text: str) -> set[str]:
    return _words(text) - _REMARK_WORDS


def count_label_problems(text: str, lines: list[str], k: int,
                         counts: list[tuple[str, str, str]]) -> list[str]:
    """Counts in line `k` written for something other than their remark, or listed as the
    evidence of an item that is about something else."""
    out = []
    parent = _parent_item(lines, k)
    for label, n, unit in counts:
        for m in re.finditer(rf"(?<![\d,.]){n}\s{{0,2}}{unit}", text):
            near = text[max(0, m.start() - 40):m.start()]
            words = _remark_words(label)
            if not words:
                continue
            if not (words & _words(near)):
                out.append(f"「{n}{unit}」은 자료에서 「{label}」의 건수다. 이 문장의 대상에 붙이지 말고 "
                           f"「{label}」 지적 {n}{unit}이라고 쓰거나 빼라")
            elif parent and not (words & _words(parent)):
                out.append(f"「{label}」 {n}{unit}은 상위 항목 「{parent[:40]}」의 근거가 아니다. "
                           f"그 지적이 말하는 문제 항목에 붙이거나 따로 적어라")
    return out


async def count_labels(doc: Doc) -> tuple[list[Finding], dict]:
    """A count the material gives for one remark (「'휴식 공간 부족' 23건」) set to another."""
    counts = quoted_counts(doc.material)
    found: list[Finding] = []
    if not counts:
        return found, {}
    for index, sec in enumerate(doc.sections):
        lines = str(sec.get("content") or "").split("\n")
        for k, line in enumerate(lines):
            if line.lstrip().startswith(("|", "#", "```")):
                continue
            whys = count_label_problems(line, lines, k, counts)
            if whys:
                found.append(Finding(
                    index, line.strip(), "count_label", "; ".join(dict.fromkeys(whys)) + ".",
                    evidence="\n".join(f"자료: 「{lb}」 {n}{u}" for lb, n, u in counts), rank=1,
                    still=lambda s, c=counts: bool(count_label_problems(s, [s], 0, c))))
    return found, {}


# ── after writing: add missing, never rewrite ──


@dataclass(slots=True)
class Amendment:
    section: int
    code: str
    instruction: str
    missing: Callable[[list[dict]], bool]
    append: str = ""
    #: shown only; add-only edit can't fix
    report_only: bool = False


def _whole(sections: list[dict]) -> str:
    return "\n".join(str(s.get("content") or "") for s in sections)


def _section_for(sections: list[dict], pattern: str, fallback: int = -1) -> int:
    rx = re.compile(pattern)
    for index, sec in enumerate(sections):
        if rx.search(str(sec.get("heading") or "")):
            return index
    return fallback if fallback >= 0 else len(sections) - 1


def _summary_instruction(lines: int) -> str:
    """Summary callout ask: 「핵심」 then `lines` lines (conclusion, decision needed)."""
    if lines == 2:
        return ("Add a callout block, opened by the line 「```callout」, at the very start of this section. Its "
                "first line is 「핵심」, the "
                "second one line of conclusion, the third one line of the decision needed (the next action "
                "if none) — plain lines, not list items. End both lines in the noun form (명사형), and use only "
                "content and numbers in the document.")
    return ("Add a callout block, opened by the line 「```callout」, at the very start of this section. Its "
            f"first line is 「핵심」, then exactly {lines} plain lines (not list items) that sum up the "
            "conclusion and what must be decided or done next. Use only content and numbers in the "
            "document.")


def amendments(pre: Prepass | None, sections: list[dict], material: str, request: str,
               conflicts: list[key_figures.Conflict], title_block: dict | None = None) -> list[Amendment]:
    """Missing required content: pipeline `amend` names and the summary callout."""
    if pre is None or not sections:
        return []
    out: list[Amendment] = []
    whole = _whole(sections)
    amend, structure = pre.settings.amend, pre.settings.structure
    if "requirement-ids" in amend:
        wanted = list(dict.fromkeys(_REQ_ID.findall(material)))
        if len(wanted) >= 2:
            missing = [i for i in wanted if i not in whole]
            if missing:
                lines = {i: next((ln.strip()[:160] for ln in material.splitlines() if i in ln), "")
                         for i in missing}
                index = _section_for(sections, r"요구\s{0,2}사항|대응", fallback=max(
                    range(len(sections)), key=lambda k: len(_REQ_ID.findall(str(sections[k].get("content") or "")))))
                out.append(Amendment(
                    index, "requirement_missing",
                    "Append the requirement-ID rows missing from the 요구사항 대응표 to the end of the table, "
                    "with the same columns. Write the 대응 방안 only as far as the material covers it; "
                    "otherwise leave 「[대응 방안]」:\n" + "\n".join(
                        f"- {i}: {lines[i]}" for i in missing[:20]),
                    lambda secs, ids=missing: any(i not in _whole(secs) for i in ids)))
    if "quote-validity" in amend:
        stated = next((m.group(1).strip() for m in _VALIDITY.finditer(f"{request}\n{material}")), "")
        values = " ".join(str(v) for _, v in (title_block or {}).get("fields") or [])
        if stated and "유효" not in whole and stated not in values:
            index = _section_for(sections, r"견적|가격|금액")
            out.append(Amendment(index, "validity_missing", "", lambda secs: "유효" not in _whole(secs),
                                 append=f"- 견적 유효기간: {stated}"))
    if pre.lead and (absent := lead_dates_missing(sections, pre.lead)):
        # span like 「8주」: computed dates and deadline standing added as one line
        index = _section_for(sections, r"검토|문제|비교|분석|일정")
        starts = ", ".join(dict.fromkeys(f"{r['anchor']} {r['start']}" for r in absent))
        said = ", ".join(f"{r['label'] or r['text']} {r['date']}"
                         + (f"({_lead_verdict(r)})" if r.get("deadline") else "") for r in absent[:8])
        out.append(Amendment(
            index, "lead_date_missing", "", lambda secs, lead=pre.lead: bool(lead_dates_missing(secs, lead)),
            append=f"- 납기 날짜({starts} 기준): {said}"))
    if want := structure.summary_lines:
        lines = summary_lines(sections)
        first = next((i for i, s in enumerate(sections)
                      if not _ATTACHMENT.match(str(s.get("heading") or ""))), 0)
        if lines is None:
            out.append(Amendment(
                first, "summary_missing", _summary_instruction(want),
                lambda secs: summary_lines(secs) is None))
        elif lines != want:
            out.append(Amendment(
                first, "summary_lines", "",
                lambda secs, n=want: summary_lines(secs) not in (None, n), report_only=True))
    if "option-status" in amend and pre.musts:
        index = _section_for(sections, r"필수|조건|상태|판정|비교")
        for option, row in pre.musts["options"].items():
            if row["status"] == "충족":
                continue
            word = row["status"]
            marked = re.compile(re.escape(option) + r"[^\n]{0,80}" + re.escape(word)
                                + "|" + re.escape(word) + r"[^\n]{0,20}" + re.escape(option))
            if marked.search(whole) or _status_cell(sections, option, word):
                continue
            reason = "; ".join(row["failed"]) if word == "탈락" else ", ".join(row["unknown"]) + " 자료 없음(확인 필요)"
            out.append(Amendment(
                index, "status_missing", "",
                lambda secs, rx=marked, o=option, w=word: not rx.search(_whole(secs))
                and not _status_cell(secs, o, w),
                append=f"- {option}: {word} — {reason}"))
    if "conflict-values" in amend:
        for conflict in conflicts:
            absent = [v for v in conflict.values if not _shown(v["value"], whole)]
            if not absent:
                continue
            index = _section_for(sections, r"충돌|상충|다른\s{0,2}값")
            listed = "\n".join(
                f"- {v['value']}" + (f" ({', '.join(x for x in (v.get('where'), v.get('when')) if x)})"
                                     if v.get("where") or v.get("when") else "")
                + (f" — 자료: 「{v['line'][:100]}」" if v.get("line") else "") for v in conflict.values)
            out.append(Amendment(
                index, "conflict_missing",
                f"The material gives different values for 「{conflict.name}」. Write the two values side by "
                f"side with their source and time, and state in one line which value you followed (in a "
                f"table, append a row):\n{listed}",
                lambda secs, c=conflict: any(not _shown(v["value"], _whole(secs)) for v in c.values)))
    if "unverified-options" in amend:
        unverified = _unverified_options(sections, list((pre.scores or {}).get("options") or {}))
        if unverified:
            index = _section_for(sections, r"확인|위험|리스크")
            body = str(sections[index].get("content") or "")
            absent = [o for o in unverified if o not in body]
            if absent:
                out.append(Amendment(
                    index, "unverified_missing",
                    "Append to this section the alternatives left as 「조건 미확인」 and what must be "
                    "checked for each: " + ", ".join(absent),
                    lambda secs, i=index, a=absent: any(o not in str(secs[i].get("content") or "") for o in a)))
    return out


def _status_cell(sections: list[dict], option: str, word: str) -> bool:
    """Whether a table marks `option` with `word` (status row or column)."""
    for table in tables(sections):
        column = next((k for k, h in enumerate(table.header) if option in h), None)
        for _, row in table.rows:
            if column is not None and column < len(row) and word in row[column]:
                return True
            if row and option in row[0] and any(word in c for c in row[1:]):
                return True
    return False


def _shown(value: str, text: str) -> bool:
    numbers = key_figures._loose(value)
    if numbers:
        unit, number = numbers[0]
        return any(u == unit and abs(v - number) < 1e-6 * max(1.0, number)
                   for u, v in key_figures._loose(text))
    return re.sub(r"\s+", "", value) in re.sub(r"\s+", "", text)


def _unverified_options(sections: list[dict], options: list[str]) -> list[str]:
    found = []
    for table in tables(sections):
        for _, row in table.rows:
            for k, cell in enumerate(row):
                if "조건 미확인" in cell and k < len(table.header):
                    head = table.header[k].strip(" *")
                    label = row[0].strip(" *")
                    found.append(head if head and not re.search(r"상태|판정|구분|기준", head) else label)
    for line in _whole(sections).split("\n"):
        if "조건 미확인" in line:
            found.extend(o for o in options if o in line)
    return [o for o in dict.fromkeys(found) if o and len(o) <= 30]


# ── page count ──


#: excluded from the page limit: 붙임 (개조식), 부록·별지·가격 제안서 (제안서, E7)
_ATTACHMENT = re.compile(r"^\s{0,4}\[?(?:붙임|첨부|별첨|부록|별지|가격\s{0,2}제안)")
_PAGES_ASKED = re.compile(
    r"(?<!\d)(\d{1,2}|한|두|세)\s{0,2}(?:쪽|장|페이지|page)\s{0,3}(?:이내|이하|안으로|짜리|분량|으로|로|내외)"
)


def page_limit(process: Any, request: str, material: str = "") -> int | None:
    """Body page limit: user's, else RFP's (`page_limit_rfp`), else 서식's (`page_limit`)."""
    structure = process.structure
    m = _PAGES_ASKED.search(request or "")
    if m:
        word = m.group(1)
        return int(word) if word.isdigit() else {"한": 1, "두": 2, "세": 3}[word]
    if structure.page_limit_rfp and (rfp := _BODY_PAGES.search(f"{request}\n{material}")):
        return int(rfp.group(1))
    return structure.page_limit or None


def body_sections(sections: list[dict]) -> list[dict]:
    """Body as counted: everything before 붙임."""
    return [s for s in sections if not _ATTACHMENT.match(str(s.get("heading") or ""))]


async def page_count(
    title: str, sections: list[dict], title_block: dict | None, tokens: dict | None = None
) -> int | None:
    """Body pages in PDF export; None if unrenderable."""
    from app.services import report_export  # heavy: fonts and reportlab load on first use

    try:
        pdf = await asyncio.to_thread(
            report_export.to_pdf, title, body_sections(sections), tokens=tokens,
            title_block=title_block,
        )
    except Exception as exc:  # noqa: BLE001 — the count is a check, not the export
        log.info("page count render failed: %s", logs.safe(str(exc)))
        return None
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf)) or None


#: 개조식 closing 「끝.」 after the 붙임 list (시행규칙 제4조제5항)
_CLOSING_LINE = re.compile(r"(?:^|\s)끝\.[ \t\u00a0]{0,8}$", re.M)


async def body_pages(
    title: str,
    sections: list[dict],
    title_block: dict | None,
    tokens: dict | None = None,
    page_settings: dict | None = None,
) -> tuple[int | None, int | None]:
    """`(hwpx, pdf)` pages of a 개조식 body through 붙임 and 「끝.」, as exported; None if unrenderable."""
    from app.services import report_export

    def measure() -> tuple[int, int]:
        pdf = report_export.to_pdf(title, sections, tokens=tokens, page_settings=page_settings,
                                   title_block=title_block)
        _, hwpx = report_export.hwpx_pages(title, sections, tokens=tokens,
                                           page_settings=page_settings, title_block=title_block,
                                           until=_CLOSING_LINE)
        return hwpx, report_export.pdf_pages(pdf, _CLOSING_LINE)[1]

    try:
        hwpx, pdf = await asyncio.to_thread(measure)
    except Exception as exc:  # noqa: BLE001 — the count is a check, not the export
        log.info("body page count render failed: %s", logs.safe(str(exc)))
        return None, None
    return hwpx or None, pdf or None


_CONDENSE_PROMPT = """Below is the 「{heading}」 section of a 개조식 report. The report body is now {pages} pages and must fit in {limit}.
This section is now {lines} lines ({chars:,} characters). Rewrite it in {target} lines or fewer (cut about {cut} lines).

Rules:
- Cut history, background and detailed explanation first; keep decision requests, legal obligations, budget shortfalls and deadlines.
- Do not change numbers. Numbers in the items you keep stay exactly as in the original.
- The detailed tables have already been moved to 「붙임」. Do not write the tables again; point to the details you cut with one last line 「※ 세부는 붙임」.
- If there is a leading ```callout block (the two 핵심 lines), keep it as it is.
- Keep the 개조식 form (lists with □ ○ - ㆍ depth). Do not write the section heading.

Section:
{content}"""

CONDENSE_ROUNDS = 2


def _lines(text: str) -> int:
    return sum(1 for ln in (text or "").split("\n") if ln.strip())


def _tables_to_attachment(sections: list[dict]) -> list[dict]:
    """Move body pipe tables and captions to 붙임 (created if missing), leaving pointers."""
    out = [dict(s) for s in sections]
    moved: list[str] = []
    for sec in out:
        if _ATTACHMENT.match(str(sec.get("heading") or "")):
            continue
        kept: list[str] = []
        lines = str(sec.get("content") or "").split("\n")
        i, fenced = 0, False
        while i < len(lines):
            line = lines[i]
            if line.lstrip().startswith("```"):
                fenced = not fenced
            if not fenced and line.lstrip().startswith("|"):
                caption = kept.pop() if kept and re.match(r"^\s{0,4}표\s{0,2}[:：]", kept[-1]) else ""
                j = i
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    j += 1
                moved.append("\n".join(x for x in (caption, *lines[i:j]) if x))
                kept.append(f"※ 세부 표는 붙임 {len(moved)}")
                i = j
                continue
            kept.append(line)
            i += 1
        sec["content"] = "\n".join(kept)
    if not moved:
        return [dict(s) for s in sections]
    where = next((k for k, s in enumerate(out) if _ATTACHMENT.match(str(s.get("heading") or ""))), None)
    tables = "\n\n".join(f"붙임 {n}.\n{t}" for n, t in enumerate(moved, 1))
    if where is None:
        out.append({"id": f"att_{len(out)}", "heading": "붙임", "content": tables,
                    "format": "markdown", "status": "done"})
    else:
        body = str(out[where].get("content") or "").rstrip()
        out[where]["content"] = f"{body}\n\n{tables}".strip()
    return out


async def condense(sections: list[dict], *, pages: int, limit: int, complete: Complete,
                   model: str, api_key: str, usage: dict | None = None) -> list[dict]:
    """One condensing round; rewrites that drop a number are discarded."""
    out = _tables_to_attachment(sections)
    body = [i for i, s in enumerate(out) if not _ATTACHMENT.match(str(s.get("heading") or ""))]
    total = sum(_lines(str(out[i].get("content") or "")) for i in body) or 1
    keep = max(1.0, total * limit / max(pages, 1) * 0.9)

    targets = [i for i in body if len(str(out[i].get("content") or "")) > 120]

    async def one(index: int):
        sec = out[index]
        content = str(sec.get("content") or "")
        lines = _lines(content)
        target = max(2, math.floor(lines * keep / total))
        return await complete(model, [{"role": "user", "content": _CONDENSE_PROMPT.format(
            heading=sec.get("heading") or "", pages=pages, limit=limit, lines=lines,
            chars=len(content), target=target, cut=max(1, lines - target),
            content=content)}], api_key, min(4000, len(content) + 400))

    replies = await asyncio.gather(*(one(i) for i in targets), return_exceptions=True)
    for index, reply in zip(targets, replies, strict=True):
        if isinstance(reply, BaseException):
            continue
        text, spent = reply
        for key in ("inputTokens", "outputTokens"):
            if usage is not None:
                usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))
        before = str(out[index].get("content") or "")
        after = (text or "").strip()
        kept = set(_tokens(after)) >= {n for n in _tokens(before) if n >= 10}
        if after and len(after) < len(before) and kept:
            out[index]["content"] = after
    whole = set(_tokens(_whole(out)))
    if not {n for n in _tokens(_whole(sections)) if n >= 10} <= whole:
        return [dict(s) for s in sections]
    return out


# ── proposal: RFP terms ──

_CLIENT_LABEL = re.compile(
    r"(?:발주\s{0,2}기관|수요\s{0,2}기관|발주처|고객사|기관명|수\s{0,2}신)\s{0,2}[:：]\s{0,2}([^\n,|·「『(\[]{2,40})")
#: 「[RFP 발췌 — 한빛물류(주) 「…」]」, 「[미팅 메모 — 그린텍스타일(주) 인사팀, …]」
_CLIENT_HEAD = re.compile(
    r"^\s{0,4}\[?\s{0,2}(?:RFP|제안\s{0,2}요청서|미팅\s{0,2}메모|고객\s{0,2}메일|과업\s{0,2}지시서|공고)[^\n—–\]]{0,20}[—–-]\s{0,2}"
    r"([^\n「『,\]]{2,40}?)\s{0,2}(?:[「『,\]]|$)", re.M)
#: RFP PDF page header: 「제안요청서  |  한국농식품정보원  |  2026」
_CLIENT_RUNNING = re.compile(r"^\s{0,4}(?:제안\s{0,2}요청서|RFP)\s{0,3}\|\s{0,3}([^|\n]{2,30}?)\s{0,3}\|")
_OURS_LINE = re.compile(r"우리\s{0,2}회사|제안사|당사|실적")
_PERIOD_LINE = re.compile(r"(?:사업|용역|수행|계약|과업)\s{0,2}기간[^\n]{0,100}")
_MONTHS = re.compile(r"(\d{1,2})\s{0,2}개월")
_BODY_PAGES = re.compile(r"본문\s{0,3}(\d{1,3})\s{0,2}(?:쪽|페이지|매|page)\s{0,3}(?:이내|이하|까지)")


def _client_key(name: str) -> str:
    """Client name for comparison: 「(주)」, 「주식회사」, 「(가상)」, spaces stripped."""
    name = re.sub(r"\(\s{0,2}(?:주|가상|사|재|유)\s{0,2}\)|주식회사|㈜", "", name or "")
    return re.sub(r"[\s\"'“”‘’]+", "", name).strip(" -·:")


def rfp_terms(material: str, request: str) -> dict | None:
    """RFP's client names, period end (or months) and body page limit."""
    text = f"{request}\n{material}"
    clients: list[str] = []
    for line in text.splitlines():
        if _OURS_LINE.search(line[:20]):
            continue
        for m in (*_CLIENT_LABEL.finditer(line), *_CLIENT_HEAD.finditer(line),
                  *_CLIENT_RUNNING.finditer(line)):
            name = m.group(1).strip(" -·:")
            if (2 <= len(_client_key(name)) <= 30 and re.match(r"[가-힣A-Za-z(㈜]", name)
                    and not re.search(r"\d{2,4}\s{0,2}[-./년]", name)):
                clients.append(name)
    end, months, period_line = None, None, ""
    for m in _PERIOD_LINE.finditer(text):
        line = m.group(0)
        tilde = re.search(r"[~∼〜]", line)
        after = list(_FULL_DATE.finditer(line, tilde.end())) if tilde else []
        if after:
            try:
                end = dt.date(int(after[0].group(1)), int(after[0].group(2)), int(after[0].group(3)))
            except ValueError:
                end = None
        if (n := _MONTHS.search(line)) and end is None:
            months = int(n.group(1))
        if end or months:
            period_line = line.strip()[:120]
            break
    pages = int(m.group(1)) if (m := _BODY_PAGES.search(text)) else None
    out = {"clients": list(dict.fromkeys(clients))[:4], "end": end.isoformat() if end else None,
           "months": months, "pages": pages, "period_line": period_line}
    return out if any(out[k] for k in ("clients", "end", "months", "pages")) else None


def client_head(block: dict | None, rfp: dict | None, text: str = "") -> tuple[dict | None, str]:
    """Head 고객사 vs RFP client: `(fixed block or None, problem or "")`; fixed only for a single client."""
    if not block or not rfp or not rfp.get("clients"):
        return None, ""
    fields = [list(f) for f in block.get("fields") or []]
    at = next((i for i, f in enumerate(fields) if f and f[0] == "고객사"), None)
    if at is None:
        return None, ""
    written = str(fields[at][1] or "").strip()
    ours = next((str(f[1] or "") for f in fields if f and f[0] == "제안사"), "")
    keys = [_client_key(c) for c in rfp["clients"]]
    mine = _client_key(written)
    if mine and mine != _client_key(ours) and (
            any(mine in k or k in mine for k in keys if k) or mine in _client_key(text)):
        return None, ""
    unwritten = written in ("", "(기입)") or placeholders.is_blank(written)
    if unwritten and len(rfp["clients"]) > 1:
        return None, ""
    problem = (f"표지의 고객사 「{written}」가 RFP의 고객사({', '.join(rfp['clients'][:3])})와 다르다"
               if not unwritten else "표지에 고객사가 비었다")
    if len(set(keys)) == 1:
        fields[at][1] = rfp["clients"][0]
        return {**block, "fields": fields}, problem
    return None, problem


# ── minutes with no meeting date ──

_WD = "월화수목금토일"
#: written day: 「2026년 10월 8일(목)」, 「10월 8일」, 「2026-10-08」, optional time
_SOME_DAY = (r"(?:(?:20\d{2}\s{0,2}년\s{0,2})?\d{1,2}\s{0,2}월\s{0,2}\d{1,2}\s{0,2}일"
             r"|20\d{2}\s{0,2}[-./]\s{0,2}\d{1,2}\s{0,2}[-./]\s{0,2}\d{1,2}\.?)"
             rf"(?:\s{{0,2}}\([{_WD}]\))?"
             r"(?:\s{0,2}(?:오전|오후|저녁|밤)?\s{0,2}\d{1,2}\s{0,2}(?:시|:\d{2})(?:\s{0,2}\d{1,2}\s{0,2}분)?)?")
#: day stated as the meeting's: 「10월 8일에 열린 … 회의」
_MEETING_DAY = re.compile(
    rf"(?P<day>{_SOME_DAY})(?P<tail>\s{{0,2}}(?:에\s{{0,2}})?(?:열린|열렸던|개최된|진행된|진행한|있었던|가진|한)"
    r"\s{1,3}(?:[^\s.\n]{1,14}\s{1,3}){0,4}?[^\s.\n]{0,14}(?:회의|미팅|모임|간담회))"
    rf"|(?P<lead>(?:회의\s{{0,2}}(?:일시|일자|날짜|일)|개최\s{{0,2}}일시?)\s{{0,2}}(?:[:：]|은|는)?\s{{0,2}})(?P<day2>{_SOME_DAY})"
)
_RELATIVE_DUE = re.compile(
    rf"다음\s{{0,2}}주|이번\s{{0,2}}주|다다음\s{{0,2}}주|모레|내일|글피|이번\s{{0,2}}달|다음\s{{0,2}}달|\d\s{{0,2}}주\s{{0,2}}(?:뒤|후)"
    rf"|[{_WD}]요일"
)
UNDATED_NOTE = "회의 일자 미기재로 날짜 미확정"


def _day_known(day: str, known: str) -> bool:
    """Whether material or request writes this month and day."""
    found = _cell_dates(day)
    if not found:
        return False
    _, month, date = found[0]
    flat = re.sub(r"\s+", "", known or "")
    return any(sp in flat for sp in (f"{month}월{date}일", f"{month}/{date}", f"{month}.{date}",
                                     f"-{month:02d}-{date:02d}", f"{month:02d}.{date:02d}"))


#: a body line giving the meeting day as a blank: 「| 일시 | [회의 일자] |」, 「일시: [회의 일자]」
_BLANK_DAY_ROW = re.compile(
    r"^(?P<lead>\s{0,4}(?:\|\s{0,3}|[-*>]\s{0,3})?(?:\*\*)?(?:회의\s{0,2})?(?:일시|일자|날짜)(?:\*\*)?"
    r"\s{0,3}(?:\||[:：])\s{0,3})(?P<blank>\[[^\[\]\n]{0,12}\])(?P<tail>\s{0,3}\|?\s{0,3})$", re.M)


#: a body line giving the meeting place as nothing: 「- 장소: 미정」, 「| 장소 | [장소] |」
_BLANK_PLACE_ROW = re.compile(
    r"^(?P<lead>\s{0,4}(?:\|\s{0,3}|[-*>]\s{0,3})?(?:\*\*)?(?:회의\s{0,2})?장소(?:\*\*)?"
    r"\s{0,3}(?:\||[:：])\s{0,3})(?P<blank>미정|\[[^\[\]\n]{0,12}\]|-|—)(?P<tail>\s{0,3}\|?\s{0,3})$",
    re.M)
#: a section about the next meeting, whose place is another one
_NEXT_HEADING = re.compile(r"다음|차기|향후")


def minutes_head_place(sections: list[dict], head: dict | None) -> list[dict]:
    """The body's blank meeting place agrees with the head: the head's place, or, with the
    head blank too, the line goes (「[장소]」 in the head and 「미정」 in the overview read as two
    answers; the head is where the person fills it)."""
    place = next((str(f[1] if len(f) > 1 else "").strip() for f in (head or {}).get("fields") or []
                  if f and str(f[0]) in ("장소", "회의 장소")), None)
    if place is None:
        return sections
    known = place if place and not placeholders.is_blank(place) else ""

    def settle(m: re.Match) -> str:
        return m.group("lead") + known + m.group("tail") if known else "\x00"

    out = []
    for sec in sections:
        content = str(sec.get("content") or "")
        if _NEXT_HEADING.search(str(sec.get("heading") or "")) or not _BLANK_PLACE_ROW.search(content):
            out.append(sec)
            continue
        lines = _BLANK_PLACE_ROW.sub(settle, content).split("\n")
        out.append({**sec, "content": "\n".join(ln for ln in lines if ln != "\x00")})
    return out


def minutes_head_day(sections: list[dict], head: dict | None, pre: Prepass | None) -> list[dict]:
    """A blank meeting day in the body (the overview table's 일시) takes the head's, when code
    resolved that day; else it stays. A blank place takes the head's (`minutes_head_place`)."""
    sections = minutes_head_place(sections, head)
    if not head or pre is None or not pre.base_date:
        return sections
    day = next((str(f[1]) for f in head.get("fields") or []
                if len(f) >= 2 and str(f[0]) in ("일시", "회의 일시", "일자")), "")
    if not day.startswith(pre.base_date):
        return sections
    return [{**sec, "content": _BLANK_DAY_ROW.sub(
        lambda m: m.group("lead") + day + m.group("tail")
        if placeholders.is_blank(m.group("blank")) else m.group(0), str(sec.get("content") or ""))}
        for sec in sections]


def minutes_undated(sections: list[dict], dates: list[dict], known: str) -> tuple[list[dict], list[str]]:
    """No meeting date known: meeting day → 「[회의 일자]」, word/invented deadlines → 「[기한]」 + 비고."""
    words = [d for d in dates if d.get("status") == _NO_BASE]
    out = [dict(s) for s in sections]
    codes: list[str] = []

    def meeting_day(m: re.Match) -> str:
        if m.group("day"):
            if _day_known(m.group("day"), known):
                return m.group(0)
            codes.append("minutes_meeting_day")
            return "[회의 일자]" + m.group("tail")
        if _day_known(m.group("day2"), known):
            return m.group(0)
        codes.append("minutes_meeting_day")
        return m.group("lead") + "[회의 일자]"

    for k, sec in enumerate(out):
        lines = str(sec.get("content") or "").split("\n")
        for i, line in enumerate(lines):
            if not line.lstrip().startswith(("|", "```", "#", "!")):
                lines[i] = _MEETING_DAY.sub(meeting_day, line)
        out[k] = {**sec, "content": "\n".join(lines)}
    for table in tables(out):
        owner, due = _owner_due(table.header)
        if owner is None or due is None:
            continue
        note = _column(table.header, r"비고|사유|메모|원문")
        content = str(out[table.section].get("content") or "")
        for line, row in table.rows:
            if len(row) <= due:
                continue
            when = row[due].strip(" *")
            if not when or placeholders.is_blank(when) or re.fullmatch(
                    r"\(?(?:미정|확인\s{0,2}필요|해당\s{0,2}없음|완료|-|—)\)?", when):
                continue
            dated = bool(_cell_dates(when))
            value = "[기한]"
            if repeat := _REPEAT_CELL.search(when):
                if not dated or _day_known(when, known):
                    continue
                value, said = repeat.group(0), ""
            elif _RELATIVE_DUE.search(when) and not dated:
                said = re.sub(r"\s{0,2}까지\s{0,2}$", "", when)
            elif dated and not _day_known(when, known):
                hits = bound_rows(" ".join(c for j, c in enumerate(row) if j != due), words)
                said = hits[0]["text"] if hits else ""
            else:
                continue
            row = list(row)
            row[due] = value
            if value == "[기한]" and note is not None and len(row) > note:
                why = (f"원문 「{said}」, " if said and said not in row[note] else "") + UNDATED_NOTE
                kept = row[note].strip(" -—")
                row[note] = f"{kept}, {why}" if kept and UNDATED_NOTE not in kept else (kept or why)
            content = content.replace(line, "| " + " | ".join(row) + " |", 1)
            codes.append("minutes_undated_due")
        out[table.section] = {**out[table.section], "content": content}
    return out, codes


#: a yearless day written in the head: 「10/14」, 「10월 14일 (화) 10시」
_HEAD_DAY = re.compile(
    rf"^(?P<m>\d{{1,2}})\s{{0,2}}(?:/|월\s{{0,2}})(?P<d>\d{{1,2}})(?![\d/])(?:\s{{0,2}}일)?"
    rf"(?:\s{{0,2}}\(\s{{0,2}}(?P<wd>[{_WD}])(?:요일)?\s{{0,2}}\)|\s{{1,2}}(?P<wd2>[{_WD}])(?:요일)?(?![가-힣]))?")


def _head_year(written: str, pre: Prepass, today: dt.date | None) -> str:
    """「10/14」 → 「2026-10-14(수)」: the read meeting date, else the year nearest today when
    clear, else 「[연도]」; a weekday the person wrote is kept, flagged when the calendar differs."""
    from app.services import weekdays

    # 「2026년 10/14」: the year the person gave, the day in one form
    year = re.match(r"(20\d{2})\s{0,2}(?:년|[.-])\s{0,2}(?=\d{1,2}\s{0,2}(?:/|월))", written)
    if year:
        written = written[year.end():]
    m = _HEAD_DAY.match(written)
    if not m or _FULL_DATE.search(written):
        return year.group(0) + written if year else written
    month, dom = int(m.group("m")), int(m.group("d"))
    day = None
    if year:
        try:
            day = dt.date(int(year.group(1)), month, dom)
        except ValueError:
            return year.group(0) + written
    elif pre.base_date:
        try:
            base = dt.date.fromisoformat(pre.base_date)
            day = base if (base.month, base.day) == (month, dom) else None
        except ValueError:
            day = None
    if day is None and not year and today is not None and (
            near := weekdays._nearest(month, dom, today)):
        day = near[0] if near[1] else None
    rest = written[m.end():].strip()
    said = m.group("wd") or m.group("wd2")
    if day is None:
        return " ".join(filter(None, [f"[연도] {month}월 {dom}일" + (f"({said})" if said else ""), rest]))
    real = _WD[day.weekday()]
    mark = real if not said or said == real else f"{said}, 확인 필요: 달력상 {real}요일"
    return " ".join(filter(None, [f"{day.isoformat()}({mark})", rest]))


def minutes_head(block: dict | None, pre: Prepass | None,
                 today: dt.date | None = None) -> dict | None:
    """Head 일시: code-read meeting date, else 「[회의 일자]」, never guessed from today; a
    yearless written day gets its year."""
    if not block or pre is None or "meeting-date" not in pre.settings.head:
        return None
    fields = [list(f) for f in block.get("fields") or []]
    at = next((i for i, f in enumerate(fields) if f and str(f[0]) in ("일시", "회의 일시", "일자")), None)
    if at is None or len(fields[at]) < 2:
        return None
    written = str(fields[at][1] or "").strip()
    if written and not placeholders.is_blank(written):
        dated = _head_year(written, pre, today)
        if dated == written:
            return None
        fields[at][1] = dated
        return {**block, "fields": fields}
    if pre.base_source and pre.base_date:
        try:
            day = dt.date.fromisoformat(pre.base_date)
            fields[at][1] = f"{pre.base_date}({_WD[day.weekday()]})"
        except ValueError:
            return None
    elif not pre.base_source:
        fields[at][1] = "[회의 일자]"
    return {**block, "fields": fields}


_SCHEDULE_HEADING = re.compile(r"일정|추진|수행\s{0,2}(?:계획|방안)|사업\s{0,2}관리|마일스톤")
_SCHEDULE_HEADER = re.compile(r"일정|기간|일자|완료|마감|시기|착수|종료")
_AFTER_PROJECT = re.compile(r"유지\s{0,2}보수|하자|보증|무상|사후|운영\s{0,2}지원")
_YEAR_MONTH = re.compile(r"(20\d{2})\s{0,2}년\s{0,2}(\d{1,2})\s{0,2}월(?!\s{0,2}\d)|(20\d{2})[.-](\d{2})(?![.\-\d])")
_MONTH_INDEX = re.compile(r"(?<![A-Za-z0-9])M\s{0,1}\+?\s{0,1}(\d{1,2})(?!\d)|(\d{1,2})\s{0,2}개월\s{0,2}(?:차|째)")


def _schedule_over(line: str, rfp: dict) -> str:
    """Why a schedule line exceeds the RFP period, or ""."""
    if _AFTER_PROJECT.search(line):
        return ""
    if end := rfp.get("end"):
        limit = dt.date.fromisoformat(end)
        for m in _FULL_DATE.finditer(line):
            try:
                day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            if day > limit:
                return f"{day.isoformat()}이 사업 종료일 {end}보다 늦다"
        for m in _YEAR_MONTH.finditer(line):
            year, month = int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))
            if 1 <= month <= 12 and (year, month) > (limit.year, limit.month):
                return f"{year}년 {month}월이 사업 종료({end})보다 늦다"
    if months := rfp.get("months"):
        for m in _MONTH_INDEX.finditer(line):
            index = int(m.group(1) or m.group(2))
            if index > months:
                return f"{index}개월 차가 사업 기간 {months}개월을 넘는다"
    return ""


async def proposal_schedule(doc: Doc, rfp: dict | None) -> tuple[list[Finding], dict]:
    """Schedule lines ending after the RFP period."""
    found: list[Finding] = []
    if not rfp or not (rfp.get("end") or rfp.get("months")):
        return found, {}
    seen: set[tuple[int, str]] = set()
    lines: list[tuple[int, str]] = []
    for index, sec in enumerate(doc.sections):
        if _SCHEDULE_HEADING.search(str(sec.get("heading") or "")):
            fenced = False
            for ln in str(sec.get("content") or "").split("\n"):
                if ln.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                    continue
                # image data URI matches the month pattern (「M6」); unrepairable
                if fenced or not ln.strip() or ln.lstrip().startswith("![") or "data:image" in ln:
                    continue
                lines.append((index, ln))
    period = rfp.get("period_line") or ""
    bound = (f"사업 종료일 {rfp['end']}" if rfp.get("end")
             else f"사업 기간 {rfp['months']}개월(1개월 차~{rfp['months']}개월 차)")
    for table in tables(doc.sections):
        if _SCHEDULE_HEADER.search(" ".join(table.header)):
            lines.extend((table.section, line) for line, _ in table.rows)
    for index, line in lines:
        if (index, line) in seen:
            continue
        seen.add((index, line))
        if why := _schedule_over(line, rfp):
            found.append(Finding(
                index, line.strip() if not line.lstrip().startswith("|") else line, "schedule_over",
                f"일정이 RFP 사업 기간을 넘는다: {why}. 이 줄의 모든 날짜·기간을 {bound} 안으로 "
                "옮겨라(마지막 단계가 종료일을 넘지 않게 앞 단계를 당긴다). 사업 기간 뒤의 일은 "
                "「하자보수」·「사후 지원」으로 적고, 자료에 없는 날짜는 「협의」로 둔다.",
                evidence=f"RFP 사업 기간: {period} — {bound}", rank=1,
                still=lambda s, r=rfp: bool(_schedule_over(s, r))))
    return found, {}


# ── 개조식: no prose, noun endings, two-line 핵심 ──

GAEJO_PROSE_CHARS = 60
_LIST_LINE = re.compile(r"^\s{0,12}(?:[-*+]\s|\d{1,2}[.)]\s|[□■○◦ㅇ·ㆍ∙-]\s)")
_NOT_PROSE = re.compile(r"^\s{0,4}(?:\||#|※|>|표\s{0,2}[:：]|붙\s{0,2}임|\(?끝\.?\)?$|!\[|<)")
#: sentence endings; noun endings (「…필요」 「…예정」 「…함」) pass
_VERB_ENDING = re.compile(r"(?:니다|세요|어요|아요|해요|에요|예요|네요|[가-힣]다)[.!]?$")
_TRAILING = re.compile(r"(?:\s{0,2}\[\d{1,3}(?:[,\s]{1,3}\d{1,3}){0,5}\]|\s{0,2}\([^()\n]{0,60}\)|[*_\s])+$")


def _gaejo_lines(sections: list[dict]):
    """`(section, line)` of body outside fences, 붙임 and references."""
    for index, sec in enumerate(sections):
        if _ATTACHMENT.match(str(sec.get("heading") or "")):
            continue
        fenced = False
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if not fenced and line.strip():
                yield index, line


def _prose(line: str) -> bool:
    return (not _LIST_LINE.match(line) and not _NOT_PROSE.match(line)
            and len(line.strip()) > GAEJO_PROSE_CHARS)


def _verb_ended(line: str) -> bool:
    if line.lstrip().startswith(("|", "#", ">")) or _NOT_PROSE.match(line):
        return False
    text = _TRAILING.sub("", re.sub(_LIST_LINE, "", line, count=1)).strip()
    return bool(text) and bool(_VERB_ENDING.search(text)) and not text.endswith(("과다", "최다"))


async def gaejo_style(doc: Doc) -> tuple[list[Finding], dict]:
    """Prose paragraphs and sentence-ended items (「…합니다」) in the body."""
    found: list[Finding] = []
    for index, line in _gaejo_lines(doc.sections):
        if _prose(line):
            found.append(Finding(
                index, line.strip(), "gaejo_prose",
                "개조식 본문에 줄글 문단이 있다. 한 줄짜리 목록 항목(- )으로 줄이고 명사형(…함, …임, …예정, "
                "…필요)으로 끝내라. 숫자와 사실은 그대로 둔다.",
                rank=2, still=lambda s: _prose(s) or _verb_ended(s)))
        elif _verb_ended(line):
            found.append(Finding(
                index, line.strip(), "gaejo_ending",
                "개조식 항목은 명사형(…함, …임, …예정, …필요)으로 끝낸다. 「…합니다」「…이다」로 끝내지 마라. "
                "목록 기호와 숫자는 그대로 둔다.",
                rank=3, still=_verb_ended))
    return found, {}


_CALLOUT_OPEN = re.compile(r"^\s{0,3}```\s{0,2}callout\s*$")


def summary_lines(sections: list[dict]) -> int | None:
    """Lines under 「핵심」 in the first section's callout; None if absent."""
    body = [s for s in sections if not _ATTACHMENT.match(str(s.get("heading") or ""))]
    if not body:
        return None
    lines = str(body[0].get("content") or "").split("\n")
    for i, line in enumerate(lines):
        if not _CALLOUT_OPEN.match(line):
            continue
        inside = []
        for later in lines[i + 1:]:
            if later.lstrip().startswith("```"):
                break
            if later.strip():
                inside.append(later.strip())
        if inside and re.sub(r"[*_\s:：]", "", inside[0]) == "핵심":
            return len(inside) - 1
    return None


# ── minutes: rows form and five columns ──

_ACTION_HEADING = re.compile(r"실행|할\s{0,2}일|조치|후속|담당")
#: rows item value end: 「<small>정다은 · 2026-10-23(금)</small>」 or bare 「정다은 · 10월 23일」
_ROW_VALUES = re.compile(
    r"(?:<small>\s{0,2}(?P<a>[^<·]{1,20}?)\s{0,2}·\s{0,2}(?P<b>[^<]{1,40}?)\s{0,2}</small>"
    r"|\s(?P<c>[가-힣A-Za-z○]{2,10}(?:\s[가-힣]{2,5})?|미정|확인\s필요|\[[가-힣]{2,6}\])\s{0,2}·\s{0,2}(?P<d>[^·\n]{1,30}?))[\s*]{0,4}$")
_SOURCE_WORD = re.compile(r"회의|메일|출처|자료|전사|녹음|메모")


def _list_row_values(line: str) -> tuple[str, str, str] | None:
    """`(담당, 기한, preceding text)` of a rows item, or None."""
    if not _LIST_LINE.match(line):
        return None
    m = _ROW_VALUES.search(line)
    if not m:
        return None
    who = (m.group("a") or m.group("c") or "").strip(" *")
    when = (m.group("b") or m.group("d") or "").strip(" *")
    return who, when, line[: m.start()]


def _list_row_problems(line: str, bound: list[dict]) -> tuple[list[str], str]:
    values = _list_row_values(line)
    if values is None:
        return [], ""
    who, when, text = values
    problems: list[str] = []
    if not _DUE_CELL_OK.match(when):
        if _RELATIVE_WORDS.search(when) or len(when) > 16:
            problems.append("기한 값에는 날짜(요일)·미정·[기한]만 쓴다 — 원문 표현은 앞 문장으로")
    rest = re.sub(r"^\s{0,12}[-*+]\s{1,3}\*\*[^*\n]{1,120}\*\*", "", text)
    if "미정" in f"{who} {when}" and len(re.sub(r"<[^>]{1,20}>|[*\-\s.]", "", rest)) < 4:
        problems.append("「미정」이면 사유(회의에서 정하지 않음 등)를 앞 문장에 적는다")
    if _mailed(bound) and not _SOURCE_WORD.search(text):
        problems.append("출처(회의 또는 메일)를 앞 문장에 적는다")
    entries = bound_rows(text, bound)
    written = _cell_dates(when)
    evidence = ""
    if entries and written and not any(date_matches(w, e) for w in written for e in entries):
        shown = ", ".join(dict.fromkeys(e["shown"] for e in entries))
        problems.append(f"기한이 date-calc 해석값({shown})과 다르다")
        evidence = "\n".join(f"「{e['text']}」 → {e['shown']} (출처: {e['source_line'][:120]})"
                             for e in entries[:3])
    return problems, evidence


async def minutes_list_rows(doc: Doc, dates: list[dict]) -> tuple[list[Finding], dict]:
    """Row-form action items (「- **할 일** … <small>담당 · 기한</small>」) checked like table rows."""
    found: list[Finding] = []
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")]
    for index, sec in enumerate(doc.sections):
        if not _ACTION_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            problems, evidence = _list_row_problems(line, bound)
            if problems:
                found.append(Finding(
                    index, line.strip(), "minutes_row", "; ".join(problems) + ". 이 항목만 고쳐라.",
                    evidence=evidence, rank=1,
                    still=lambda s, b=bound: bool(_list_row_problems(s, b)[0])))
    return found, {}


def _mailed(dates: list[dict]) -> bool:
    """Whether follow-up mail is part of the material (a date counted from a mail)."""
    return any(d.get("base_kind") == "mail" for d in dates)


def minutes_columns(sections: list[dict], dates: list[dict]) -> tuple[list[dict], int]:
    """Add missing 출처·비고 columns; 출처 = the date's source line (회의 or mail), else 「[출처]」.
    One meeting and no mail: no 출처 column, every row would say 「회의」."""
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")]
    mailed = _mailed(dates)
    out = [dict(s) for s in sections]
    added = 0
    for table in tables(out):
        owner, due = _owner_due(table.header)
        if owner is None or due is None:
            continue
        lacks_source = mailed and _column(table.header, r"출처|근거") is None
        lacks_note = _column(table.header, r"비고|사유|메모|원문") is None
        if not (lacks_source or lacks_note):
            continue
        content = str(out[table.section].get("content") or "")
        lines = content.split("\n")
        start = next((i for i, ln in enumerate(lines) if cells(ln) == table.header
                      and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1])), None)
        if start is None:
            continue
        extra = ["출처"] * lacks_source + ["비고"] * lacks_note
        lines[start] = "| " + " | ".join([*table.header, *extra]) + " |"
        lines[start + 1] = "|" + "---|" * (len(table.header) + len(extra))
        for k, (_, row) in enumerate(table.rows):
            cellsrc = []
            if lacks_source:
                entries = bound_rows(" ".join(c for j, c in enumerate(row) if j != due), bound)
                kinds = {("메일(" + e["base_date"] + ")") if e.get("base_kind") == "mail" else "회의"
                         for e in entries}
                cellsrc.append(next(iter(kinds)) if len(kinds) == 1 else "[출처]")
            if lacks_note:
                cellsrc.append("-")
            lines[start + 2 + k] = "| " + " | ".join([*row, *cellsrc]) + " |"
        out[table.section] = {**out[table.section], "content": "\n".join(lines)}
        added += 1
    return out, added


#: 「출처: 회의」 in a 비고 cell, with the comma before or after it
_SAID_SOURCE = re.compile(r"(?:,\s{0,2})?출처\s{0,2}[:：]\s{0,2}(?:회의|회의록|메모)(?:\s{0,2},)?")


def minutes_one_source(sections: list[dict], dates: list[dict]) -> tuple[list[dict], int]:
    """No mail in the material: an action table's 출처 column saying one thing on every row
    dropped, and 「출처: 회의」 taken out of its 비고 cells — a column that tells nothing."""
    if _mailed(dates):
        return sections, 0
    out = [dict(s) for s in sections]
    dropped = 0
    for table in tables(out):
        owner, due = _owner_due(table.header)
        if owner is None or due is None:
            continue
        source = _column(table.header, r"^\**\s{0,2}(?:출처|근거)\s{0,2}\**$")
        note = _column(table.header, r"비고|사유|메모|원문")
        values = {row[source].strip(" *") for _, row in table.rows if source is not None
                  and source < len(row)}
        cut = source is not None and len(values) <= 1 and values <= {"회의", "회의록", "메모", "-", ""}
        content = str(out[table.section].get("content") or "")
        lines = content.split("\n")
        start = next((i for i, ln in enumerate(lines) if cells(ln) == table.header
                      and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1])), None)
        if start is None:
            continue
        changed = False
        width = len(table.header)
        for k, (_, row) in enumerate(table.rows):
            row = list(row)
            if note is not None and note < len(row) and _SAID_SOURCE.search(row[note]):
                row[note] = _SAID_SOURCE.sub("", row[note]).strip(" ,") or "-"
                changed = True
            if cut and source < len(row):
                row.pop(source)
                changed = True
            lines[start + 2 + k] = "| " + " | ".join(row) + " |"
        if cut:
            header = [h for j, h in enumerate(table.header) if j != source]
            lines[start] = "| " + " | ".join(header) + " |"
            lines[start + 1] = "|" + "---|" * (width - 1)
            dropped += 1
        if changed:
            out[table.section] = {**out[table.section], "content": "\n".join(lines)}
    return out, dropped


_WRITTEN_DATES = frozenset({"full_date", "month_day", "named_month_end", "vague", "unclear"})


def _action_hits(action: str, entry: dict) -> int:
    """Row-task words shared with the date's line (and the line before; repeats: own line only)."""
    own = _SPEAKER.sub("", entry.get("source_line", ""))
    near = own if entry.get("status") == "repeat" else (entry.get("context") or own)
    return len(_words(action) & _words(near))


def _strong_dates(row_text: str, bound: list[dict], action: str | None = None, *,
                  written: bool = False) -> list[dict]:
    """Dates a row is plainly about: 2+ shared words incl. a task word, no closer rival date;
    `written`: the material's own dates count too."""
    words = _words(row_text)
    scored = []
    for d in bound:
        # code-resolved dates only: material's own dates left 미정 = writer's call; 「오늘」 = meeting day
        if (d.get("kind") in _WRITTEN_DATES and not written) or d.get("date") == d.get("base_date"):
            continue
        # names and 「회의」 on every row: shared name alone binds nothing
        if action is not None and not _action_hits(action, d):
            continue
        near = words & _words(d.get("context") or d.get("source_line", ""))
        if len(near) >= 2:
            # own-line words weighted double
            own = _words(_SPEAKER.sub("", d.get("source_line", "")))
            scored.append((len(near) + len(words & own), d))
    if not scored:
        return []
    best = max(n for n, _ in scored)
    top = [d for n, d in scored if n == best]
    # 「다음 주부터 매주 월요일」: repeat is the deadline
    repeating = {d.get("source_line") for d in top if d.get("status") == "repeat"}
    top = [d for d in top if d.get("status") == "repeat" or d.get("source_line") not in repeating]
    return top if len({_due_value(d) for d in top}) == 1 else []


def _due_value(entry: dict) -> str:
    if entry.get("status") == "repeat":
        every = "격주" if entry.get("repeat") == "biweekly" else "매주"
        return f"{every} {entry['weekday']}요일({entry['first_date']}부터)"
    return str(entry.get("shown") or "")


#: 「미정(정현우 과장 복귀 후)」: undecided, with the reason
_DEFERRED = re.compile(r"^\**\s{0,2}미정\s{0,2}[(（]\s{0,2}(?P<why>[^()（）]{1,60}?)\s{0,2}[)）]\s{0,2}\**$")


#: prose calling a deadline open: 「시안 제출 기한은 미정입니다」, 「마감이 정해지지 않았습니다」
_OPEN_DUE = re.compile(
    r"(기한|마감일?|제출일|완료일)\s?(?:은|는|이|가)\s?(?:아직\s?)?"
    r"(?:미정|정해지지\s?않았|정하지\s?않았|확정되지\s?않았)"
    r"(입니다|습니다|이며|으며|이고|이다|고|음|다)(?![가-힣])")
_OPEN_TAIL = {"이며": "이며", "으며": "이며", "이고": "이고", "고": "이고", "음": "임", "이다": "이다",
              "다": "이다"}
_DUE_CELL = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\([월화수목금토일]\))?")


def minutes_open_due(sections: list[dict], dates: list[dict]) -> tuple[list[dict], int]:
    """A sentence calling a task's deadline open (「시안 제출 기한은 미정입니다」) while the one
    action row for that task gives the memo's date: the sentence takes the row's date."""
    from app.services import stated_days, stated_policy

    known = {d.get("date") for d in dates if d.get("status") in ("ok", "repeat") and d.get("date")}
    rows: list[tuple[set[str], str]] = []
    for table in tables(sections):
        owner, due = _owner_due(table.header)
        if due is None:
            continue
        for _, row in table.rows:
            if len(row) <= due:
                continue
            cell = row[due].strip(" *")
            if (m := _DUE_CELL.fullmatch(cell)) and m.group(1) in known:
                task = " ".join(c for k, c in enumerate(row) if k not in (owner, due))
                rows.append((stated_days._words(task), cell))
    if not rows:
        return sections, 0
    out, fixed = [dict(s) for s in sections], 0
    for index, sec in enumerate(out):
        content = str(sec.get("content") or "")
        for piece in stated_policy.pieces(content):
            if piece.lstrip().startswith("|") or not (m := _OPEN_DUE.search(piece)):
                continue
            clause = piece[:m.end()].rsplit(",", 1)[-1]
            words = stated_days._words(clause) or stated_days._words(piece)
            hits = {cell for task, cell in rows if task & words}
            if len(hits) != 1:
                continue
            cell = hits.pop()
            noun = m.group(1)
            josa = "은" if (ord(noun[-1]) - 0xAC00) % 28 else "는"
            tail = _OPEN_TAIL.get(m.group(2), "입니다")
            mended = f"{piece[:m.start()]}{noun}{josa} {cell}{tail}{piece[m.end():]}"
            content = content.replace(piece, mended, 1)
            fixed += 1
        out[index] = {**sec, "content": content}
    return out, fixed


def minutes_deferred(sections: list[dict]) -> tuple[list[dict], int]:
    """「미정(사유)」 owner/due cells become 「미정」 with the reason in 비고: the reason is no owner."""
    out = [dict(s) for s in sections]
    moved = 0
    for table in tables(out):
        owner, due = _owner_due(table.header)
        note = _column(table.header, r"비고|사유|메모|원문")
        if owner is None or due is None or note is None:
            continue
        content = str(out[table.section].get("content") or "")
        for line, row in table.rows:
            if len(row) <= max(owner, due, note):
                continue
            row = list(row)
            reasons = []
            for k in (owner, due):
                if m := _DEFERRED.match(row[k].strip()):
                    row[k] = "미정"
                    reasons.append(m.group("why").strip())
            if not reasons:
                continue
            kept = row[note].strip(" -—")
            row[note] = ", ".join(dict.fromkeys([*([kept] if kept else []), *reasons]))
            content = content.replace(line, "| " + " | ".join(row) + " |", 1)
            moved += 1
        out[table.section] = {**out[table.section], "content": content}
    return out, moved


def _task_column(header: list[str], others: tuple[int | None, ...]) -> int | None:
    """Task column: 「할 일」, else 「조치」·「과제」…, 「내용」, 「항목」 (a number column before a
    「할 일」 one is no task), else first non-owner·due·note column."""
    for pattern in (r"할\s{0,2}일", r"조치|과제|업무|실행", r"내용", r"항목"):
        found = _column(header, pattern)
        if found is not None and found not in others:
            return found
    return next((k for k in range(len(header)) if k not in others), None)


def _owner_in_line(cell: str, entry: dict) -> bool:
    """Whether the row's owner said the due or is named in its line; no owner, no binding."""
    line = re.sub(r"\s", "", str(entry.get("source_line") or ""))
    names = [re.sub(r"\s|\([^)]{0,30}\)|(?:님|씨)$", "", part.strip(" *"))
             for part in _OWNER_SPLIT.split(cell or "")]
    names = [n for n in names if len(n) >= 2 and not _ANY_OWNER.search(n)]
    # 「박정민 팀장」 matched by the name before the title
    return any(n in line or (len(n) > 3 and n[:3] in line) for n in names)


#: a note saying the meeting set no due (「회의에서 기한을 정하지 않음」)
_SAID_UNDECIDED = re.compile(
    r"정하지\s{0,2}않|미정|확인\s{0,2}(?:이\s{0,2})?필요|(?:언급|명시|지정|확정)(?:되지|하지)\s{0,2}않")


def _in_date_sentence(action: str, entry: dict) -> bool:
    """Whether the task is named in the sentence holding the date, when its line has several
    (「… 다음 회의 때 공유하겠다. 학생 테스트 그룹 모집은 …」: the date is the checklist's)."""
    own = _SPEAKER.sub("", str(entry.get("source_line") or ""))
    said = str(entry.get("text") or "")
    sentences = [s for s in re.split(r"(?<=[.!?。])\s+", own) if s.strip()]
    if len(sentences) < 2 or not said:
        return True
    holding = next((s for s in sentences if said in s), "")
    # 「갱신」 in the row, 「갱신해 줘요」 in the sentence: the same word
    return not holding or any(a.startswith(b) or b.startswith(a)
                              for a in _words(action) for b in _words(holding))


def minutes_due_fill(sections: list[dict], dates: list[dict], *,
                     blanked: bool = False) -> tuple[list[dict], int]:
    """Fill 「미정」/「[기한]」 action rows with date-calc's resolved date.
    `blanked`: after the checks, only 「[기한]」 cells, and the material's own written dates too."""
    # meeting date only: lines re-dated by later header or mail left to writer
    base = next((d.get("base_date") for d in dates), "")
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")
             and d.get("base_date") == base and d.get("base_kind") in ("meeting", "given")]
    if not bound:
        return sections, 0
    out = [dict(s) for s in sections]
    filled = 0
    for table in tables(out):
        owner, due = _owner_due(table.header)
        if owner is None or due is None:
            continue
        note = _column(table.header, r"비고|사유|메모|원문")
        task = _task_column(table.header, (owner, due, note))
        content = str(out[table.section].get("content") or "")
        picks = []
        for line, row in table.rows:
            if len(row) <= max(owner, due) or not placeholders.is_blank(row[due]) and (
                    blanked or row[due].strip(" *()") != "미정"):
                continue
            if note is not None and note < len(row) and _SAID_UNDECIDED.search(row[note]):
                # the writer read the material as giving no due: 「미정」 stands
                continue
            action = row[task] if task is not None and task < len(row) else ""
            entries = _strong_dates(" ".join(c for k, c in enumerate(row) if k not in (due, note)),
                                    bound, action, written=blanked)
            if (entries and _owner_in_line(row[owner], entries[0])
                    and _in_date_sentence(action, entries[0])):
                picks.append((line, row, entries, _action_hits(action, entries[0])))
        # one line's date → best-matching row; ties only when line names several tasks
        actions = [row[task] if task is not None and task < len(row) else "" for _, row in table.rows]
        for line, row, entries, hits in picks:
            if hits < max(_action_hits(a, entries[0]) for a in actions):
                continue
            row = list(row)
            row[due] = _due_value(entries[0])
            content = content.replace(line, "| " + " | ".join(row) + " |", 1)
            filled += 1
        out[table.section] = {**out[table.section], "content": content}
    return out, filled


_QUANTITY = re.compile(
    rf"(?<![\d.,A-Za-z])({units._TERM})\s?(km|cm|mm|kg|kW|m|V|W|L|명분|인분|개월|개|명|원|시간|시|대|팀|곳)(?![A-Za-z])"
)
#: owner words naming no one: need no material backing
_ANY_OWNER = re.compile(
    r"미정|확인\s{0,2}필요|해당\s{0,2}없음|전체|전원|공동|참석자|각자|각\s{0,2}(?:담당|팀원|구성원|부서)"
    r"|담당자\s{0,2}별|^[-—–]?$|^\[[가-힣 ·]{1,10}\]$")
_OWNER_SPLIT = re.compile(r"[,·/、→]|\s(?:및|와|과)\s")


_NATIVE = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
           "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
#: spoken counts (「두 명씩」=「2명」); material side only, so a stray match only excuses
_NATIVE_COUNT = re.compile(
    r"(?<![가-힣])(하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉|열)\s?(명|개|곳|군데|대|팀|시간|시)"
)


def _digits(text: str) -> str:
    return _NATIVE_COUNT.sub(
        lambda m: f"{_NATIVE[m.group(1)]}{'곳' if m.group(2) == '군데' else m.group(2)}", text or "")


def _quantities(text: str) -> list[tuple[float, str, re.Match]]:
    out = []
    for m in _QUANTITY.finditer(text or ""):
        try:
            value = units._ko_number(m.group(1))
        except ValueError:
            continue
        unit = {"명분": "명", "인분": "명"}.get(m.group(2), m.group(2))
        out.append((value, unit, m))
    return out


def _quantity_known(value: float, unit: str, given: set[tuple[float, str]], numbers: list[float]) -> bool:
    if unit == "시":
        return _grounded(value, numbers)
    if any(u == unit and abs(v - value) < 1e-6 * max(1.0, abs(v)) for v, u in given):
        return True
    same = [v for v, u in given if u == unit][:60]
    # gap between two material values: 「B사는 A사보다 40만원 비싸다」
    if any(abs(abs(a - b) - value) < 0.5 for a in same for b in same if a != b):
        return True
    if unit == "원":
        # sum of material amounts: 「총상금 40만 원(20만 원 × 2팀)」
        money = [v for v, u in given if u == "원"][:60]
        pool = sorted(set(numbers))[:80]
        return any(abs(a * b - value) < 0.5 for a in money for b in pool) or any(
            abs(a + b - value) < 0.5 for a in money for b in money)
    return False


_HEADCOUNT = re.compile(r"참석|불참|참여|출석|인원")


#: unitless number after a label: 「(정원 40)」
_LABELLED = re.compile(rf"([가-힣A-Za-z]{{1,10}})\s{{0,2}}({units._TERM})(?![\d.,]|\s?[가-힣A-Za-z%])")
_LIST_MARK = re.compile(r"(?m)^\s{0,4}(\d{1,2})[.)]\s")


def _labelled_numbers(material: str) -> list[tuple[str, float]]:
    out = []
    for m in _LABELLED.finditer(material or ""):
        try:
            out.append((m.group(1), units._ko_number(m.group(2))))
        except ValueError:
            continue
    return out


def _listed_count(material: str) -> int:
    """Count of items the material numbers 1, 2, 3 …."""
    marks = [int(m.group(1)) for m in _LIST_MARK.finditer(material or "")]
    count = 0
    while count + 1 in marks:
        count += 1
    return count


_CLAUSE_BREAK = re.compile(
    r"(?:(?<=며)|(?<=[하되있었았였이않]고)|(?<=지만)|(?<=는데))(?:,\s{0,2}|\s{1,2})(?=\S)"
    r"|(?<=,)\s{1,2}")


def _owner_known(cell: str, flat: str) -> bool:
    value = cell.strip(" *")
    if _ANY_OWNER.search(value):
        return True
    for part in _OWNER_SPLIT.split(value):
        name = re.sub(r"\([^)]{0,30}\)", "", part).strip(" *")
        name = re.sub(r"(?:님|씨)$", "", name).replace(" ", "")
        if len(name) >= 2 and name not in flat:
            return False
    return True


def _skip_line(line: str) -> bool:
    stripped = line.strip()
    return (not stripped or stripped.startswith(("#", "![", "```", "|"))
            or bool(re.match(r"^\*\*(?:표|그림)\s{0,2}\d", stripped)))


def minutes_grounding(sections: list[dict], material: str) -> tuple[list[dict], list[str]]:
    """Repair owners/quantities absent from material; returns sections and repair codes."""
    flat = re.sub(r"\s+", "", material or "")
    numbers = _material_numbers(material)
    given = {(v, u) for v, u, _ in _quantities(_digits(material))}
    out = [dict(s) for s in sections]
    codes: list[str] = []

    labelled = _labelled_numbers(material)
    listed = _listed_count(material)

    def known(value: float, unit: str, text: str, at: int) -> bool:
        if _quantity_known(value, unit, given, numbers):
            return True
        # 「정원 40명」 vs memo's 「(정원 40)」: same label before the number
        before = text[max(0, at - 16):at]
        if any(label in before and abs(v - value) < 1e-9 for label, v in labelled):
            return True
        return unit == "개" and listed >= 2 and value == listed

    def unknown(text: str, around: str = "") -> list[re.Match]:
        headcount = bool(_HEADCOUNT.search(f"{around} {text}"))
        return [m for v, u, m in _quantities(text)
                if not (headcount and u == "명") and not known(v, u, text, m.start())]

    def blank(text: str) -> str:
        for m in reversed(unknown(text)):
            text = text[:m.start()] + "[수치]" + text[m.end():]
        return text

    def repair_sentence(sentence: str) -> str:
        """Drop the clause with an unsupported number; last clause → 「[수치]」; lone sentence dropped."""
        clauses, cut = [], 0
        for m in _CLAUSE_BREAK.finditer(sentence):
            clauses.append(sentence[cut:m.start()])
            cut = m.end()
        clauses.append(sentence[cut:])
        bad = [bool(unknown(c)) for c in clauses]
        if all(bad):
            return ""
        kept = [c for c, b in zip(clauses[:-1], bad[:-1], strict=True) if not b]
        last = blank(clauses[-1]) if bad[-1] else clauses[-1]
        return " ".join([*kept, last])

    for table in tables(out):
        owner, due = _owner_due(table.header, r"기한|마감|완료일|due|일시|시점")
        # one 「담당·기한」 column: its row is a sentence, kept whole
        combined = owner is None and _column(table.header, r"담당|책임|owner") is not None
        content = str(out[table.section].get("content") or "")
        for line, row in table.rows:
            row = list(row)
            changed = False
            if owner is not None and len(row) > owner and not _owner_known(row[owner], flat):
                row[owner] = placeholders.for_header(table.header[owner])
                codes.append("minutes_owner")
                changed = True
            around = " ".join([*table.header, *row])
            spec_cells = [k for k, c in enumerate(row) if k not in (owner, due) and unknown(c, around)]
            if spec_cells and owner is None and not combined:
                content = content.replace(line + "\n", "", 1).replace(line, "", 1)
                codes.append("minutes_spec")
                continue
            for k in spec_cells:
                found = unknown(row[k], around)
                if len(found) >= 2:
                    row[k] = "[수치]"
                else:
                    m = found[0]
                    row[k] = (row[k][:m.start()] + "[수치]" + row[k][m.end():]).strip()
                codes.append("minutes_spec")
                changed = True
            if changed:
                content = content.replace(line, "| " + " | ".join(row) + " |", 1)
        out[table.section] = {**out[table.section], "content": content}
    for index, sec in enumerate(out):
        lines = str(sec.get("content") or "").split("\n")
        fenced = False
        for k, line in enumerate(lines):
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced or _skip_line(line) or not unknown(line):
                continue
            kept = []
            for part in units.SENTENCE_BREAK.split(line):
                if unknown(part):
                    codes.append("minutes_spec")
                    part = repair_sentence(part)
                if part:
                    kept.append(part)
            lines[k] = " ".join(kept)
        out[index] = {**sec, "content": "\n".join(lines)}
    return out, codes


# ── comparison ──

_YES = ("있음", "예", "O", "o", "true", "True", "1", "충족", "지원", "가능")


def _must_value(value: Any, must: dict) -> float | bool | None:
    if value is None:
        return None
    basis = None
    if isinstance(value, dict):
        basis, value = value.get("vat_basis"), value.get("value")
        if value is None:
            return None
    if must.get("kind") == "yesno":
        return str(value).strip() in _YES
    try:
        number = Decimal(str(value).replace(",", ""))
    except ArithmeticError:
        return None
    want = must.get("vat_basis")
    if want == "incl" and basis == "excl":
        number = (number * Decimal("1.1")).to_integral_value(rounding=ROUND_FLOOR)
    elif want == "excl" and basis == "incl":
        number = (number / Decimal("1.1")).to_integral_value(rounding=ROUND_HALF_UP)
    return float(number)


_PIPE_ROW = re.compile(r"^\s{0,8}\|(?:[^|\n]{0,200}\|){2,16}[^|\n]{0,200}$")
_NO_WORDS = ("없음", "아니오", "X", "x", "false", "False", "0", "미지원", "불가", "미충족")


def material_tables(material: str) -> list[dict[str, dict[str, str]]]:
    """Material pipe tables as `{row name: {header: cell}}`; rule line optional."""
    found: list[dict[str, dict[str, str]]] = []
    block: list[list[str]] = []

    def close() -> None:
        if len(block) >= 3:
            header, rows = block[0], block[1:]
            table = {}
            for row in rows:
                if len(row) >= 2 and row[0]:
                    table[row[0][:60]] = {h: c for h, c in zip(header[1:], row[1:], strict=False) if h}
            if len(table) >= 2:
                found.append(table)
        block.clear()

    for line in (material or "").splitlines():
        if _PIPE_ROW.match(line):
            if not _TABLE_RULE.match(line.strip().strip("|").strip()):
                block.append(cells(line))
            continue
        close()
    close()
    return found


def _header_for(must: dict, header: list[str]) -> str | None:
    """Must-have's column: most shared words, unit breaks ties."""
    want = _words(str(must.get("name") or ""))
    unit = str(must.get("unit") or "").strip()
    best, best_score = None, 0
    for h in header:
        score = len(want & _words(h)) * 2 + (1 if unit and f"({unit})" in h.replace(" ", "") else 0)
        if score > best_score:
            best, best_score = h, score
    return best if best_score >= 2 else None


def _table_value(cell: str, must: dict) -> Any:
    """Must-have value from a cell; `None` if none."""
    text = (cell or "").strip()
    if must.get("kind") == "yesno":
        if text in _YES or text.startswith(("있음", "지원", "가능")):
            return "있음"
        if text in _NO_WORDS or text.startswith(("없음", "미지원", "불가")):
            return "없음"
        return None
    m = re.search(units._TERM, text)
    if not m:
        return None
    try:
        return units._ko_number(m.group(0))
    except ValueError:
        return None


def must_values_from_tables(data: dict, material: str) -> dict:
    """Must-have values from pasted tables (header = condition, row = option), read before the model."""
    musts = [m for m in data.get("musts") or [] if isinstance(m, dict) and m.get("name")]
    if not musts:
        return data
    options = [dict(o) for o in data.get("options") or [] if isinstance(o, dict) and o.get("name")]
    for table in material_tables(material):
        header = list(next(iter(table.values())).keys())
        columns = {m["name"]: h for m in musts if (h := _header_for(m, header))}
        if not columns:
            continue
        rows = list(table.items())
        if len(options) < 2:
            options = [{"name": name, "musts": {}} for name, _ in rows]
        for option in options:
            key = _option_key(option["name"])
            row = next((r for name, r in rows
                        if key and (key in _option_key(name) or _option_key(name) in key)), None)
            if row is None:
                continue
            given = dict(option.get("musts") or {})
            for must in musts:
                header_name = columns.get(must["name"])
                if header_name and (value := _table_value(row.get(header_name, ""), must)) is not None:
                    given[must["name"]] = value
            option["musts"] = given
    return {**data, "options": options}


#: must on a yearly amount: 「연구비 연간 집행 한도」, 「1년 예산」
_ANNUAL = re.compile(r"연간|매년|해마다|1년|한\s?해|연\s{0,2}(?:한도|예산|집행|비용)|년\s{0,2}(?:한도|예산)")


def _budget_total(option: str, must: dict, costs: dict | None) -> float | None:
    """Option's total compare-cost judged against the same budget as `must`, on its VAT basis."""
    budget = (costs or {}).get("budget") or {}
    try:
        if abs(float(str(must.get("limit")).replace(",", "")) - float(budget.get("amount"))) > 0.5:
            return None
    except (TypeError, ValueError):
        return None
    key = _option_key(option)
    for name, total in (budget.get("compared") or {}).items():
        other = _option_key(name)
        if key and other and (key in other or other in key):
            return float(total)
    return None


def _yearly_cost(option: str, costs: dict | None) -> float | None:
    """Option's peak yearly cost from compare-cost (first year or a later one)."""
    key = _option_key(option)
    for name, entry in ((costs or {}).get("options") or {}).items():
        other = _option_key(name)
        if key and other and (key in other or other in key):
            years = [entry.get(k) for k in ("first_year", "next_year") if entry.get(k) is not None]
            return float(max(years)) if years else None
    return None


def _within_under(option: str, must: dict, costs: dict | None) -> tuple[str, float] | None:
    """A scenario under which `option`'s total meets the budget `must` names, with that total."""
    budget = (costs or {}).get("budget") or {}
    try:
        limit = float(str(must.get("limit")).replace(",", ""))
        if abs(limit - float(budget.get("amount"))) > 0.5:
            return None
    except (TypeError, ValueError):
        return None
    key = _option_key(option)
    for scenario, compared in (budget.get("scenarios") or {}).items():
        for name, total in compared.items():
            other = _option_key(name)
            if key and other and (key in other or other in key) and float(total) <= limit:
                return scenario, float(total)
    return None


#: a must that data stay in-house
_RESIDENCY = re.compile(
    r"외부\s{0,2}반출|반출\s{0,2}(?:불가|금지)|외부\s{0,2}(?:저장|전송|보관|이전)\s{0,2}(?:불가|금지)"
    r"|국외\s{0,2}이전\s{0,2}(?:불가|금지)|데이터\s{0,2}(?:주권|레지던시)|망\s{0,2}분리")
_CLOUD = re.compile(r"클라우드|cloud|SaaS|퍼블릭|구독형|외부\s{0,2}(?:서버|호스팅|데이터\s{0,2}센터)",
                    re.I)
_IN_HOUSE = re.compile(
    r"온\s{0,2}프레미스|설치형|자체\s{0,2}(?:서버|구축)|사내\s{0,2}(?:서버|구축)|내부\s{0,2}(?:망|서버)"
    r"|프라이빗\s{0,2}클라우드|전용\s{0,2}(?:리전|망)|(?:반출|외부\s{0,2}전송)\s{0,2}(?:없|않|불필요)")


def _described(option: str, options: list[str], text: str) -> str:
    """What the material says of `option`: its name and the words after it up to the next
    option's name or the line's end (「A안: 클라우드 GPU(A100 80GB 4장) 시간당 …」)."""
    out = [option]
    for m in re.finditer(re.escape(option), text or ""):
        end = text.find("\n", m.end())
        end = len(text) if end < 0 else end
        for other in options:
            if other != option and (at := text.find(other, m.end(), end)) >= 0:
                end = min(end, at)
        out.append(text[m.end():end])
    return " ".join(out)


def _residency(option: str, options: list[str], text: str) -> bool | None:
    """Whether `option` keeps data in-house, by what it is: a cloud service holds them outside
    unless the material says otherwise; `None` when the material doesn't say."""
    said = _described(option, options, text)
    if _IN_HOUSE.search(said):
        return True
    if _CLOUD.search(said):
        return False
    return None


_ABSENT = re.compile(r"없|미지원|미제공|불포함|불가|안\s{0,2}됨|제공\s{0,2}안|(?<![A-Za-z])[Xx](?![A-Za-z])")


def _states_absence(option: str, options: list[str], must: str, material: str) -> bool:
    """Whether the material says `option` lacks `must`: a pasted table row of it, or a word of
    the must beside 「없음」·「미지원」 in what the material writes of it."""
    if any(option in line for line in material.split("\n") if _PIPE_ROW.match(line)):
        return True
    said = _option_text(option, options, material)
    stems = {w[:2] for w in re.findall(r"[가-힣]{2,}", must) if w[:2] not in ("필수", "조건", "여부")}
    stems |= {w.lower() for w in re.findall(r"[A-Za-z]{2,}", must)}
    low = said.lower()
    for line in low.split("\n"):
        for stem in stems:
            for m in re.finditer(re.escape(stem), line):
                if _ABSENT.search(line[max(0, m.start() - 12): m.end() + 20]):
                    return True
    return False


# ── lead times and periods a must-have names (「납기 4주 이내」, 「보증 3년 필수」) ──

#: a duration as written: 「3주」, 「1~4주」, 「6개월」, 「2달」, 「10일」, 「15영업일」, 「1년」
_DURATION = re.compile(
    r"(?<![\d.,~∼\-–])(?P<lo>\d{1,3})(?:\s{0,2}(?P<lo_unit>영업일|주일?|개월|달|일|년))?"
    r"(?:\s{0,2}[~∼\-–]\s{0,2}(?P<hi>\d{1,3}))?\s{0,2}(?P<unit>영업일|주일?|개월|달|일|년)"
    r"(?![가-힣]{0,1}차)")
#: days in a unit; months and years are calendar approximations
_UNIT_DAYS = {"일": 1.0, "영업일": 7 / 5, "주": 7.0, "주일": 7.0, "개월": 30.0, "달": 30.0,
              "년": 365.0}
_ROUGH = ("개월", "달", "년")
#: a must-have's unit that is a duration
_DURATION_UNITS = {"일", "영업일", "주", "주일", "개월", "달", "년", "day", "days", "week",
                   "weeks", "month", "months", "year", "years"}
_EN_UNIT = {"day": "일", "days": "일", "week": "주", "weeks": "주", "month": "개월",
            "months": "개월", "year": "년", "years": "년"}
#: what a delivery-time must is called, in the request or under an option
_LEAD_WORDS = re.compile(r"납기|납품|배송|인도|설치|구축|도입|소요|리드\s{0,2}타임|공급|일정|지급|완료|기한|마감")
_LEAD_STEMS = ("납기", "납품", "배송", "인도", "설치", "구축", "소요", "공급")
#: stems too general to find a must's own line (「보증 기간」 → 「보증」, not 「기간」)
_GENERIC_STEMS = ("필수", "조건", "여부", "기간", "기한", "이내", "이상", "이하", "최대", "최소")
#: a duration written beside these words is an extension the option sells, not its own
_EXTENSION = re.compile(r"^\s{0,2}(?:으로\s{0,2}|로\s{0,2})?(?:연장|추가|확대|업그레이드|옵션)")
_MONTH_DAY = r"(?:(?P<y>20\d{2})\s{0,2}년\s{0,2})?(?P<m>\d{1,2})\s{0,2}월\s{0,2}(?P<d>\d{1,2})\s{0,2}일"
#: the day a lead time counts from: 「오늘 10월 12일 계약 가정」, 「10월 12일 계약」
_BASE_DAYS = (
    re.compile(rf"(?:오늘|금일|계약일?|발주일?|착수일?|기준일|체결일?)\s{{0,3}}(?:은|는|이|:|：)?"
               rf"\s{{0,3}}{_MONTH_DAY}"),
    re.compile(rf"{_MONTH_DAY}\s{{0,2}}(?:에\s{{0,2}})?(?:계약|발주|착수|체결)"))
_DEADLINE_DAY = re.compile(rf"{_MONTH_DAY}\s{{0,2}}(?:까지|이전|전까지|안에|내)")
_WITHIN = re.compile(r"^\s{0,2}(?:이내|안에|내|까지|이하|미만|안쪽)")


def _duration(m: re.Match) -> tuple[float, float, bool]:
    """A duration match in days: `(low, high, rough)`; a range keeps both ends."""
    unit = m.group("unit")
    lo_unit = m.group("lo_unit") or unit
    lo = float(m.group("lo")) * _UNIT_DAYS[lo_unit]
    hi = float(m.group("hi")) * _UNIT_DAYS[unit] if m.group("hi") else lo
    return min(lo, hi), max(lo, hi), unit in _ROUGH or lo_unit in _ROUGH


def _months(n: str, unit: str) -> float:
    return float(n) * (12 if unit == "년" else 1)


def durations(text: str) -> list[dict]:
    """Every duration in `text`, in days: 「1~4주」 → 7~28, 「2달」 → 60; a day of a month
    (「11월 30일」) is a date, not a duration."""
    out = []
    for m in _DURATION.finditer(text or ""):
        if m.group("unit") == "일" and re.search(r"월\s{0,2}$", text[max(0, m.start() - 3):m.start()]):
            continue
        lo, hi, rough = _duration(m)
        row = {"text": m.group(0).strip(), "lo": lo, "hi": hi, "rough": rough,
               "unit": m.group("unit"), "start": m.start(), "end": m.end()}
        lo_unit = m.group("lo_unit") or m.group("unit")
        if m.group("unit") in _ROUGH and lo_unit in _ROUGH:
            # months and years compare among themselves in months (3년 = 36개월)
            ends = sorted([_months(m.group("lo"), lo_unit),
                           _months(m.group("hi") or m.group("lo"),
                                   m.group("unit") if m.group("hi") else lo_unit)])
            row["months"] = (ends[0], ends[1])
        out.append(row)
    return out


def duration_must(must: dict) -> bool:
    """Whether a max/min must-have is a duration: a duration unit, or a lead-time name."""
    if must.get("kind") not in ("max", "min"):
        return False
    unit = str(must.get("unit") or "").strip().lower()
    if unit in _DURATION_UNITS:
        return True
    return not unit and bool(_LEAD_WORDS.search(str(must.get("name") or "")))


def _must_stems(must: dict) -> list[str]:
    name = str(must.get("name") or "")
    stems = [w[:2] for w in re.findall(r"[가-힣]{2,}", name) if w[:2] not in _GENERIC_STEMS]
    if _LEAD_WORDS.search(name):
        stems += [s for s in _LEAD_STEMS if s not in stems]
    return list(dict.fromkeys(stems))


def option_duration(option: str, names: list[str], must: dict, material: str) -> dict | None:
    """The duration the material writes for `option` under `must`'s word: the first one after
    the word on its line (「납기 계약 후 3주」 → 21일), plus a longer one the option sells as an
    extension (「무상 보증 2년, 3년 연장 시 …」 → `extended` 3년). `None` when it writes none."""
    said = _option_text(option, names, material)
    for stem in _must_stems(must):
        for line in said.split("\n"):
            at = line.find(stem)
            if at < 0:
                continue
            found = [d for d in durations(line[at:at + 40])
                     if not _EXTENSION.match(line[at + d["end"]:at + d["end"] + 8])]
            if not found:
                continue
            row = dict(found[0])
            longer = [d for d in durations(line)
                      if _EXTENSION.match(line[d["end"]:d["end"] + 8]) and d["lo"] > row["hi"]]
            if longer:
                row["extended"] = longer[0]
            return row
    return None


def _day_of(m: re.Match, year: int) -> dt.date | None:
    try:
        return dt.date(int(m.group("y") or year), int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None


def duration_limit(must: dict, text: str, today: dt.date | None = None) -> dict | None:
    """A duration must's limit in days, from the request and material by code: the limit as
    written with its unit (「4주 이내」), else a lead-time limit the text writes (「납품 6주
    이내」), else a deadline counted from a contract day both written (「10월 12일 계약」,
    「11월 30일까지」 → 49일). `None` when the text settles none — the reading's own number
    (「1달」 for 「11월 30일까지」) is never the limit."""
    unit = str(must.get("unit") or "").strip().lower()
    unit = _EN_UNIT.get(unit, unit)
    limit = _plain(must.get("limit"))
    found = durations(text)
    if limit is not None and unit in _UNIT_DAYS:
        for d in found:
            if (d["text"].endswith(unit) and abs(float(re.match(r"\d+", d["text"]).group(0))
                                                 - float(limit)) < 1e-9 and d["lo"] == d["hi"]):
                return {"days": d["hi"], "rough": d["rough"], "unit": d["unit"], "text": d["text"],
                        **({"months": d["months"][1]} if "months" in d else {})}
    if must.get("kind") == "max":
        stems = _must_stems(must)
        written = [d for d in found if _WITHIN.match(text[d["end"]:d["end"] + 6])
                   and any(s in text[max(0, d["start"] - 30):d["start"]] for s in stems)]
        if len({d["hi"] for d in written}) == 1:
            d = written[0]
            return {"days": d["hi"], "rough": d["rough"], "unit": d["unit"],
                    "text": d["text"] + " 이내",
                    **({"months": d["months"][1]} if "months" in d else {})}
        year = (today or dt.date.today()).year
        bases = {day for pattern in _BASE_DAYS for m in pattern.finditer(text or "")
                 if (day := _day_of(m, year))}
        if len(bases) == 1:
            base = next(iter(bases))
            ends = {day for m in _DEADLINE_DAY.finditer(text or "")
                    if (day := _day_of(m, base.year)) and day != base}
            ends = {d if d >= base else d.replace(year=d.year + 1) for d in ends}
            if len(ends) == 1:
                end = next(iter(ends))
                return {"days": float((end - base).days), "rough": False, "unit": "일",
                        "text": f"{(end - base).days}일({base.month}월 {base.day}일~"
                                f"{end.month}월 {end.day}일)"}
    return None


def _days_text(d: dict, limit: dict) -> str:
    """A duration as written, with its days when the limit counts in another unit."""
    if d.get("unit") == limit.get("unit"):
        return d["text"]
    days = f"{d['lo']:g}" if d["lo"] == d["hi"] else f"{d['lo']:g}~{d['hi']:g}"
    return f"{d['text']}({days}일)"


def duration_verdict(value: dict, limit: dict, kind: str) -> str:
    """「met」 / 「failed」 / 「open」 for a duration against its limit; a range across the limit,
    or months against days within a day a month of it, is open; months and years compare
    exactly among themselves (「3년」 meets 「3년 이상」 and 「36개월」)."""
    if "months" in value and "months" in limit:
        (lo, hi), bound, slack = value["months"], limit["months"], 0.0
    else:
        lo, hi, bound = value["lo"], value["hi"], limit["days"]
        slack = (1.0 + bound / 30) if value["rough"] or limit["rough"] else 0.0
    if kind == "max":
        return "met" if hi <= bound - slack else "failed" if lo > bound + slack else "open"
    return "met" if lo >= bound + slack else "failed" if hi < bound - slack else "open"


def _mentions(option: str, options: list[str], must: str, material: str) -> bool:
    """Whether the material writes anything of `must` for `option`: a pasted table row of it,
    or a word of the must in what the material writes of it."""
    if any(option in line for line in material.split("\n") if _PIPE_ROW.match(line)):
        return True
    said = _option_text(option, options, material).lower()
    stems = {w[:2] for w in re.findall(r"[가-힣]{2,}", must) if w[:2] not in _GENERIC_STEMS}
    stems |= {w.lower() for w in re.findall(r"[A-Za-z]{2,}", must)}
    return not stems or any(stem in said for stem in stems)


def must_status(data: dict, numbers: list[float], material: str = "",
                costs: dict | None = None, today: dt.date | None = None) -> dict | None:
    """Each option 충족 / 탈락(사유) / 조건 미확인; unstated limits/values left out; a yearly
    money limit the reading left blank is judged on compare-cost's yearly cost. A duration
    must (납기, 보증 기간) is judged in days by code from the material's own words, never on
    the reading's numbers, whose units it does not say (「납기 2주」 is not 14달)."""
    if material:
        data = must_values_from_tables(data, material)
    musts = []
    limits: dict[str, dict] = {}
    unjudged: list[str] = []
    for must in data.get("musts") or []:
        if not isinstance(must, dict) or not must.get("name"):
            continue
        kind = must.get("kind")
        if material and duration_must(must):
            if limit := duration_limit(must, material, today):
                limits[must["name"]] = limit
                musts.append(must)
            else:
                # no limit the text settles: not judged, said so
                unjudged.append(str(must["name"]))
        elif kind == "yesno" or (kind in ("max", "min") and _grounded(must.get("limit"), numbers)):
            musts.append(must)
    options = [o for o in data.get("options") or [] if isinstance(o, dict) and o.get("name")]
    if not musts or len(options) < 2:
        return None
    out: dict[str, dict] = {}
    names = [str(o["name"]) for o in options]
    for option in options:
        given = option.get("musts") or {}
        failed, unknown, conditional = [], [], []
        for must in musts:
            raw = given.get(must["name"])
            if must["name"] in limits:
                _judge_duration(str(option["name"]), names, must, limits[must["name"]], raw,
                                material, failed, unknown, conditional)
                continue
            plain = raw.get("value") if isinstance(raw, dict) else raw
            if must.get("kind") != "yesno" and plain is not None and not _grounded(plain, numbers):
                raw = None
            value = _must_value(raw, must)
            if (must.get("kind") == "max" and _ANNUAL.search(must["name"])
                    and str(must.get("unit") or "원") == "원"
                    and (yearly := _yearly_cost(option["name"], costs)) is not None):
                # a yearly money limit is judged on the code-computed yearly cost, never on a
                # reading of one of the material's figures (the hourly price, a count)
                value = yearly
            elif (value is None and must.get("kind") == "max"
                    and str(must.get("unit") or "원") == "원"
                    and (total := _budget_total(option["name"], must, costs)) is not None):
                # the budget limit read no total: compare-cost's total on the budget's VAT
                # basis is the option's, computed from the material, not 「자료 없음」
                value = total
            residency = (_residency(str(option["name"]), names, material)
                         if must.get("kind") == "yesno" and _RESIDENCY.search(must["name"])
                         else None)
            if residency is False and value is not False:
                # 「데이터 외부 반출 불가」 and a cloud service: the data sit on its servers
                failed.append(f"{must['name']} 아님(클라우드라 데이터가 외부 서버에 있음)")
                continue
            if residency is True and value is None:
                value = True
            if (value is False and material and residency is None
                    and not _states_absence(str(option["name"]), names, must["name"], material)):
                # a reading of 「없음」 the material never states is no verdict
                # (「탈락 — 보증기간 없음」 for a stated warranty)
                value = None
            if (value is True and material and residency is None
                    and not _mentions(str(option["name"]), names, must["name"], material)):
                # a 「있음」 the material never writes of this option is no verdict either: the
                # option's lines say nothing of the must
                value = None
            if value is None:
                unknown.append(must["name"])
            elif must["kind"] == "yesno" and value is False:
                failed.append(f"{must['name']} 없음")
            elif must["kind"] in ("max", "min"):
                limit = float(str(must["limit"]).replace(",", ""))
                unit = str(must.get("unit") or "")
                if (value > limit) if must["kind"] == "max" else (value < limit):
                    sign = ">" if must["kind"] == "max" else "<"
                    if must["kind"] == "max" and (within := _within_under(
                            option["name"], must, costs)):
                        # within the budget under a condition the material states: open,
                        # not out (never 탈락 on the total without its discount)
                        unknown.append(f"{must['name']}(「{within[0]}」 적용 시 "
                                       f"{within[1]:,.0f}{unit}, 적용 없이 {value:,.0f}{unit})")
                        continue
                    failed.append(f"{must['name']} {value:,.0f}{unit} {sign} 조건 {limit:,.0f}{unit}")
        status = "탈락" if failed else "조건 미확인" if unknown else "충족"
        out[option["name"]] = {"status": status, "failed": failed, "unknown": unknown}
        if conditional:
            out[option["name"]]["conditional"] = conditional
    result = {"musts": [m["name"] for m in musts], "options": out}
    if unjudged:
        result["unjudged"] = unjudged
    return result


def _judge_duration(option: str, names: list[str], must: dict, limit: dict, raw: Any,
                    material: str, failed: list[str], unknown: list[str],
                    conditional: list[str]) -> None:
    """One duration must for one option, in days: the material's own duration under the
    must's word, else the reading's number only where the option's text writes it with the
    must's unit; a range across the limit is open; a longer period the option sells as an
    extension (「3년 연장 시」) meets a minimum, said with its cost."""
    value = option_duration(option, names, must, material)
    if value is None:
        plain = _plain(raw.get("value") if isinstance(raw, dict) else raw)
        unit = _EN_UNIT.get(str(must.get("unit") or "").strip().lower(),
                            str(must.get("unit") or "").strip())
        said = _option_text(option, names, material)
        if plain is not None and unit in _UNIT_DAYS and any(
                re.sub(r"\s", "", d["text"]) == f"{float(plain):g}{unit}" for d in durations(said)):
            days = float(plain) * _UNIT_DAYS[unit]
            value = {"text": f"{float(plain):g}{unit}", "lo": days, "hi": days,
                     "rough": unit in _ROUGH, "unit": unit}
            if unit in _ROUGH:
                value["months"] = (float(plain) * (12 if unit == "년" else 1),) * 2
    if value is None:
        unknown.append(must["name"])
        return
    kind = must["kind"]
    verdict = duration_verdict(value, limit, kind)
    if verdict != "met" and kind == "min" and (more := value.get("extended")) and (
            duration_verdict(more, limit, kind) == "met"):
        conditional.append(f"{must['name']} {more['text']} 연장 시 충족(연장 비용 추가)")
        return
    if verdict == "failed":
        sign = ">" if kind == "max" else "<"
        failed.append(f"{must['name']} {_days_text(value, limit)} {sign} "
                      + ("기한" if kind == "max" else "조건") + f" {limit['text']}")
    elif verdict == "open":
        unknown.append(f"{must['name']}({_days_text(value, limit)}, 조건 {limit['text']} — 범위가 걸침)")


def _open_text(unknown: list[str]) -> str:
    """Open conditions: a bare name has no value in the material; one with its reason in
    brackets (a range across the limit, a budget met only under a condition) says why."""
    return ", ".join(u if "(" in u else f"{u} 자료 없음" for u in unknown)


def _must_line(option: str, row: dict) -> str:
    if row["status"] == "탈락":
        line = f"- {option}: 탈락 — " + "; ".join(row["failed"])
    elif row["status"] == "조건 미확인":
        line = f"- {option}: 조건 미확인 — " + _open_text(row["unknown"])
    else:
        line = f"- {option}: 충족"
    if row.get("conditional"):
        line += " (" + "; ".join(row["conditional"]) + ")"
    return line


def _must_block(result: dict, *, data: bool = False) -> str:
    unjudged = result.get("unjudged") or []
    left = ([f"- Not judged: {', '.join(unjudged)} — the request and material settle no limit for "
             "it the code can count in days; work it out from the dates and periods they write."]
            if unjudged else [])
    if data:
        lines = ["# 필수 조건 대조(코드)",
                 "The request's must-have conditions (" + ", ".join(result["musts"]) + ") set "
                 "against the values read from the material. 탈락 means a value the material gives "
                 "misses a condition; 조건 미확인 means the material gives no value for it — say "
                 "which condition is open where you recommend. Weigh these with the rest of the "
                 "material; the reading may miss something the material says."]
        lines += [_must_line(option, row) for option, row in result["options"].items()]
        return "\n".join(lines + left)
    lines = ["# 필수 조건 판정(코드)",
             "The request's must-have conditions (" + ", ".join(result["musts"]) + ") checked against the "
             "material's values. Each alternative's status follows this result. Do not recommend a 탈락 "
             "alternative; recommend a 조건 미확인 alternative only conditionally, as in 「확인되면 권고」."]
    lines += [_must_line(option, row) for option, row in result["options"].items()]
    if all(r["status"] == "탈락" for r in result["options"].values()):
        lines.append("- Every alternative is 탈락: the conclusion is 「조건을 모두 충족하는 대안 없음」")
    return "\n".join(lines + left)


_RECOMMEND = re.compile(r"권고|추천|선정|채택|도입\s{0,2}(?:을|를)?\s{0,2}(?:제안|건의)|고른다|골랐")
_DROPPED_WORD = re.compile(r"탈락|제외|미충족|충족하지\s{0,2}못|불가")
_CONDITIONAL = re.compile(r"확인되면|확인\s{0,2}후|확인되는\s{0,2}경우|조건부|전제로|충족되면")
_REASON = re.compile(r"다만|하지만|그러나|비록|낮지만|높지만|때문|이유|감안|고려|불구")
_NONE_MEETS = re.compile(r"충족(?:하는)?\s{0,2}대안(?:이)?\s{0,2}없")


def recommended_option(line: str, options: list[str]) -> str | None:
    """Recommended option: first named outside a 「탈락」 clause."""
    if not _RECOMMEND.search(line):
        return None
    named = sorted((line.find(o), o) for o in options if o and o in line)
    for k, (at, option) in enumerate(named):
        clause = line[at: named[k + 1][0] if k + 1 < len(named) else len(line)]
        if not _DROPPED_WORD.search(clause[: 30 + len(option)]):
            return option
    return None


def _recommend_problem(line: str, musts: dict | None, scored: dict | None) -> str:
    status = (musts or {}).get("options") or {}
    options = list(dict.fromkeys([*status, *((scored or {}).get("options") or {})]))
    if not _RECOMMEND.search(line):
        return ""
    if status and all(r["status"] == "탈락" for r in status.values()):
        return "" if _NONE_MEETS.search(line) else (
            "모든 대안이 필수 조건에서 탈락했다. 「조건을 모두 충족하는 대안 없음」을 결론으로 써라")
    pick = recommended_option(line, options)
    if pick is None:
        return ""
    row = status.get(pick)
    if row and row["status"] == "탈락":
        return f"권고한 「{pick}」은 필수 조건에서 탈락했다({'; '.join(row['failed'])}). 탈락 대안은 권고하지 않는다"
    if row and row["status"] == "조건 미확인" and not _CONDITIONAL.search(line):
        return (f"「{pick}」은 조건 미확인({', '.join(row['unknown'])})이다. 「확인되면 권고」처럼 조건부로만 쓴다")
    if scored and pick in scored["options"]:
        eligible = [o for o in scored["options"] if status.get(o, {}).get("status", "충족") == "충족"]
        if eligible:
            best = max(scored["options"][o]["total"] for o in eligible)
            leaders = [o for o in eligible if scored["options"][o]["total"] == best]
            if pick not in leaders and not _REASON.search(line):
                return (f"권고한 「{pick}」이 점수(코드 계산)로 조건을 충족한 대안 중 1위가 아니다"
                        f"(1위: {', '.join(leaders)} {best:g}점). 1위를 권고하거나 다른 대안을 고른 이유를 함께 써라")
    return ""


async def comparison_recommendation(doc: Doc, musts: dict | None, scored: dict | None):
    """Recommendation vs must-haves and computed scores."""
    found: list[Finding] = []
    if not musts and not scored:
        return found, {}
    if not doc.sections:
        return found, {}
    # each recommending sentence read separately: summary listing all options before the pick;
    # the opening section too, where the bottom line is stated first
    stating = sorted({0, *(i for i, s in enumerate(doc.sections)
                           if re.search(r"결론|권고|요약|추천|제언", str(s.get("heading") or "")))})
    for index in stating:
        for line in str(doc.sections[index].get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "#", "```")):
                continue
            for sentence in units.SENTENCE_BREAK.split(line):
                if why := _recommend_problem(sentence, musts, scored):
                    found.append(Finding(
                        index, sentence.strip(), "recommend_mismatch", why + ".",
                        evidence=_must_block(musts) if musts else _score_block(scored)[:1200],
                        rank=0,
                        still=lambda s, m=musts, sc=scored: bool(_recommend_problem(s, m, sc))))
    return found, {}


def _object(word: str) -> str:
    last = (word or " ")[-1]
    return "을" if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else "를"


def verdict_sentence(musts: dict, scored: dict | None = None) -> str:
    """The recommendation the must-have matrix (and scores) allow, as one sentence."""
    rows = (musts or {}).get("options") or {}
    if not rows:
        return ""

    def said(option: str) -> str:
        row = rows[option]
        if row["status"] == "탈락":
            return f"{option}: 탈락 — " + "; ".join(row["failed"])
        if row["status"] == "조건 미확인":
            return f"{option}: 조건 미확인 — " + _open_text(row["unknown"])
        return f"{option}: 충족"

    passing = [o for o, r in rows.items() if r["status"] == "충족"]
    pending = [o for o, r in rows.items() if r["status"] == "조건 미확인"]
    if scored and len(passing) > 1:
        totals = {o: scored["options"][o]["total"] for o in passing if o in scored.get("options", {})}
        if totals:
            best = max(totals.values())
            leaders = [o for o, t in totals.items() if t == best]
            passing = leaders if len(leaders) == 1 else passing
    others = "; ".join(said(o) for o in rows if o not in passing)
    tail = f"({others})" if others else ""
    if len(passing) == 1:
        return f"필수 조건을 모두 충족하는 {passing[0]}{_object(passing[0])} 권고합니다{tail}."
    if passing:
        return f"필수 조건을 모두 충족하는 대안은 {', '.join(passing)}입니다{tail}."
    if pending:
        return (f"{', '.join(pending)}{_object(pending[-1])} 조건이 확인되면 권고합니다"
                f"({'; '.join(said(o) for o in rows)}).")
    return f"필수 조건을 모두 충족하는 대안이 없습니다({'; '.join(said(o) for o in rows)})."


def settle_verdict(sections: list[dict], still: list[Finding], musts: dict | None,
                   scored: dict | None = None) -> tuple[list[dict], list[Finding]]:
    """Recommendations the repair left against the matrix replaced by the code's verdict."""
    verdict = verdict_sentence(musts or {}, scored)
    wrong = [f for f in still if f.code == "recommend_mismatch"]
    if not verdict or not wrong:
        return sections, still
    out = [dict(s) for s in sections]
    settled: list[Finding] = []
    placed = False
    for finding in wrong:
        body = str(out[finding.section].get("content") or "")
        if not finding.sentence or finding.sentence not in body:
            continue
        if placed:
            # a later one is left as written and reported (deleting it leaves the next
            # sentence with no subject)
            continue
        # the first wrong sentence carries the verdict
        body = body.replace(finding.sentence, verdict, 1)
        out[finding.section]["content"] = re.sub(r"[ \t]{2,}", " ", body).strip()
        placed = True
        settled.append(finding)
    return out, [f for f in still if f not in settled]


# ── judge (large self-hosted model): one call per document, shown not rewritten ──

JUDGE_TIMEOUT = 60.0

_OWNER_PROMPT = """Below are the meeting material and the action items of the minutes. Among the items whose owner the material
changed (○○ 대신 △△, changed by mail) or newly assigned, pick only the rows where the minutes do not give the final owner.
Do not pick a row the material gives no grounds for.

Material:
{material}

Action items (one row per line):
{rows}

Answer with one JSON object only: {{"items": [{{"row": "행을 그대로", "final_owner": "자료의 최종 담당", "why": "자료 근거 한 줄"}}]}}"""

_BINDING_PROMPT = """Below are the material and the document's sentences with numbers. Pick only the sentences where the number
belongs to a different target in the material (another alternative, item, scenario or period) and the document attached it to
the wrong one. Do not pick calculated values or numbers not in the material.

Material:
{material}

Document sentences (one per line):
{lines}

Answer with one JSON object only: {{"items": [{{"sentence": "문장을 그대로", "number": "숫자", "belongs_to": "자료에서 그 숫자의 대상", "why": "한 줄"}}]}}"""

_REPEAT_PROMPT = """Below is one applicant's {what}. Pick only the sentences that repeat the same situation, action or result
of the same experience in two or more places. Do not pick them when they are different aspects of the same experience
(a one-line motive and the detailed process).

{text}

Answer with one JSON object only: {{"items": [{{"sentence": "뒤에 나온 문장을 그대로", "repeats": "앞에 나온 같은 내용", "why": "한 줄"}}]}}"""


async def judge_json(complete: Complete, model: str, api_key: str, prompt: str,
                     max_tokens: int = 1200) -> tuple[list[dict], dict]:
    """Judge `items`; [] on failure, timeout or bad shape."""
    try:
        text, spent = await asyncio.wait_for(
            complete(model, [{"role": "user", "content": prompt}], api_key, max_tokens),
            JUDGE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — a judge that cannot answer finds nothing
        log.info("judge call failed: %s", logs.safe(repr(exc)))
        return [], {}
    items = _json(text).get("items")
    return [i for i in items if isinstance(i, dict)][:12] if isinstance(items, list) else [], spent


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _place(sections: list[dict], quoted: str) -> tuple[int, str] | None:
    """Section and line a judge quoted."""
    want = _flat(quoted).strip("「」\"' ")
    if len(want) < 6:
        return None
    for index, sec in enumerate(sections):
        for line in str(sec.get("content") or "").split("\n"):
            flat = _flat(line)
            if want in flat or (len(flat) >= 6 and flat in want):
                return index, line if line.lstrip().startswith("|") else line.strip()
    return None


def _action_lines(sections: list[dict]) -> list[str]:
    rows = [line for t in tables(sections) if _column(t.header, r"담당|책임") is not None
            for line, _ in t.rows]
    for sec in sections:
        if _ACTION_HEADING.search(str(sec.get("heading") or "")):
            rows.extend(ln for ln in str(sec.get("content") or "").split("\n")
                        if _list_row_values(ln) is not None)
    return list(dict.fromkeys(rows))[:40]


def judge_owners(complete: Complete, judge: str, api_key: str) -> Check:
    @model_check
    async def owner_change(doc: Doc):
        rows = _action_lines(doc.sections)
        if not rows or not (doc.material or "").strip():
            return [], {}
        items, spent = await judge_json(complete, judge, api_key, _OWNER_PROMPT.format(
            material=doc.material[:8000], rows="\n".join(rows)))
        found = []
        for item in items:
            final = str(item.get("final_owner") or "").strip()
            where = _place(doc.sections, str(item.get("row") or ""))
            if not where or not final or final in where[1]:
                continue
            found.append(Finding(
                where[0], where[1], "owner_change",
                f"자료의 최종 담당은 「{final}」일 수 있다(판정: {str(item.get('why') or '')[:160]}). 확인 필요.",
                rank=2, auto=False, still=lambda s, f=final: f not in s))
        return found, spent

    return owner_change


_NUMBER_LINE = re.compile(r"\d[\d,.]{0,15}\s{0,2}(?:%|원|만|억|명|건|개월|분|시간|점|대|개)")


def judge_bindings(complete: Complete, judge: str, api_key: str) -> Check:
    @model_check
    async def number_binding(doc: Doc):
        lines = [ln.strip() for sec in doc.sections for ln in str(sec.get("content") or "").split("\n")
                 if _NUMBER_LINE.search(ln)]
        if not lines or not (doc.material or "").strip():
            return [], {}
        items, spent = await judge_json(complete, judge, api_key, _BINDING_PROMPT.format(
            material=doc.material[:8000], lines="\n".join(dict.fromkeys(lines))[:6000]))
        found = []
        for item in items:
            where = _place(doc.sections, str(item.get("sentence") or ""))
            number = str(item.get("number") or "").strip()
            if not where or not number:
                continue
            found.append(Finding(
                where[0], where[1], "number_binding",
                f"「{number}」은 자료에서 「{str(item.get('belongs_to') or '')[:60]}」의 값일 수 있다"
                f"({str(item.get('why') or '')[:160]}). 확인 필요.", rank=2, auto=False))
        return found, spent

    return number_binding


def judge_repeats(complete: Complete, judge: str, api_key: str) -> Check:
    @model_check
    async def experience_repeat(doc: Doc):
        text = "\n\n".join(f"## {s.get('heading') or ''}\n{s.get('content') or ''}" for s in doc.sections)
        if len(text) < 200:
            return [], {}
        items, spent = await judge_json(complete, judge, api_key, _REPEAT_PROMPT.format(
            what="경력기술서", text=text[:10000]))
        found = []
        for item in items:
            if where := _place(doc.sections, str(item.get("sentence") or "")):
                found.append(Finding(
                    where[0], where[1], "experience_repeat",
                    f"같은 경험의 같은 내용이 되풀이된다: 「{str(item.get('repeats') or '')[:80]}」. 확인 필요.",
                    rank=3, auto=False))
        return found, spent

    return experience_repeat


_RULES_PROMPT = """Below is a document and the review rules its writer must follow. Pick only the sentences that break \
a rule. Copy each sentence exactly as written in the document. Do not pick a sentence the rules do not cover, and do \
not suggest rewrites.

Rules (numbered):
{rules}

Document:
{text}

Answer with one JSON object only: {{"items": [{{"sentence": "문장을 그대로", "rule": 1, "why": "어긋나는 이유 한 줄"}}]}}"""


def judge_rules(rules: tuple[str, ...] | list[str], complete: Complete, judge: str,
                api_key: str) -> Check:
    """User 검토 규칙 via one judge call; findings shown, never rewritten."""
    listed = list(rules)

    @model_check
    async def rule_review(doc: Doc):
        text = "\n\n".join(f"## {s.get('heading') or ''}\n{s.get('content') or ''}"
                            for s in doc.sections)
        if not listed or len(text.strip()) < 20:
            return [], {}
        items, spent = await judge_json(complete, judge, api_key, _RULES_PROMPT.format(
            rules="\n".join(f"{n}. {r}" for n, r in enumerate(listed, 1)), text=text[:12000]))
        found = []
        for item in items:
            where = _place(doc.sections, str(item.get("sentence") or ""))
            try:
                number = int(item.get("rule") or 0)
            except (TypeError, ValueError):
                number = 0
            if not where or not 1 <= number <= len(listed):
                continue
            found.append(Finding(
                where[0], where[1], "rule_broken",
                f"검토 규칙 {number}「{listed[number - 1][:80]}」에 어긋날 수 있다"
                f"({str(item.get('why') or '')[:160]}). 확인 필요.", rank=3, auto=False))
        return found, spent

    return rule_review


# ── structure rules (서식 shape, code-checked) ──

_SPACE = re.compile(r"\s+")


def _squeezed(text: str) -> str:
    return _SPACE.sub("", str(text or "")).strip("*# ").lower()


def _section_order(sections_wanted: tuple[str, ...]) -> Check:
    """서식 sections present and in order (shown, not rewritten)."""

    async def section_order(doc: Doc) -> tuple[list[Finding], dict]:
        headings = [_squeezed(s.get("heading")) for s in doc.sections]
        found: list[Finding] = []
        last = -1
        for want in sections_wanted:
            key = _squeezed(want)
            at = next((i for i, h in enumerate(headings) if key and (key in h or h in key) and h),
                      None)
            if at is None:
                found.append(Finding(
                    max(len(doc.sections) - 1, 0), str(doc.sections[-1].get("heading") or "")
                    if doc.sections else "", "section_missing",
                    f"템플릿의 절 「{want}」이 없다. 확인 필요.", rank=4, auto=False))
                continue
            if at < last:
                found.append(Finding(
                    at, str(doc.sections[at].get("heading") or ""), "section_order",
                    f"템플릿이 정한 순서와 다르게 「{want}」이 앞에 있다. 확인 필요.", rank=4,
                    auto=False))
            last = max(last, at)
        return found, {}

    return section_order


def _required_table(table: dict) -> Check:
    """Table with the 서식's required columns (under matching heading if given)."""
    columns = [str(c) for c in table.get("columns") or []]
    heading = _squeezed(table.get("heading"))

    async def required_table(doc: Doc) -> tuple[list[Finding], dict]:
        wanted = [_squeezed(c) for c in columns]
        for t in tables(doc.sections):
            if heading and heading not in _squeezed(doc.sections[t.section].get("heading")):
                continue
            header = [_squeezed(h) for h in t.header]
            if all(any(w and w in h for h in header) for w in wanted):
                return [], {}
        index = next((i for i, s in enumerate(doc.sections)
                      if heading and heading in _squeezed(s.get("heading"))),
                     max(len(doc.sections) - 1, 0))
        sentence = str(doc.sections[index].get("heading") or "") if doc.sections else ""
        return [Finding(index, sentence, "table_missing",
                        f"템플릿이 정한 표(열: {' · '.join(columns)})가 없다. 확인 필요.", rank=4,
                        auto=False)], {}

    return required_table


def structure_checks(process: Any) -> list[Check]:
    """서식 structure checks: section order (unless outline fixed) and required tables."""
    structure = process.structure
    out: list[Check] = []
    if structure.sections and not process.fixed_outline:
        out.append(_section_order(tuple(structure.sections)))
    out += [_required_table(t) for t in structure.tables]
    return out


JUDGED_CHECKS: dict[str, Callable[[Complete, str, str], Check]] = {
    "judge-owners": lambda complete, judge, api_key: judge_owners(complete, judge, api_key),
    "judge-bindings": lambda complete, judge, api_key: judge_bindings(complete, judge, api_key),
    "judge-repeats": lambda complete, judge, api_key: judge_repeats(complete, judge, api_key),
}


def model_checks(pre: Prepass | None, *, complete: Complete | None, judge: str,
                 api_key: str) -> list[Check]:
    """Judged checks (E5) from pipeline settings, then 검토 규칙; findings shown, never rewritten."""
    if pre is None or complete is None or not judge:
        return []
    out = [JUDGED_CHECKS[name](complete, judge, api_key) for name in pre.settings.judged]
    if pre.settings.rules:
        out.append(judge_rules(pre.settings.rules, complete, judge, api_key))
    return out


# ── record ──

LABELS = {
    "quote_total": "견적 합계가 계산과 다름",
    "minutes_row": "실행 항목 칸(담당·기한)",
    "comparison_blank": "비교표 빈칸",
    "comparison_score": "점수가 계산과 다름",
    "career_number": "자료에 없는 숫자",
    "ungrounded_value": "근거 없는 값 → 확인 필요",
    "career_total": "총 경력이 계산과 다름",
    "requirement_missing": "빠진 요구 ID",
    "validity_missing": "견적 유효기간 없음",
    "conflict_missing": "자료 충돌 값 누락",
    "unverified_missing": "조건 미확인 대안 누락",
    "page_over": "요청 쪽수 초과",
    "schedule_over": "일정이 사업 기간을 넘음",
    "client_name": "표지 고객사명",
    "gaejo_prose": "개조식 본문의 줄글 문단",
    "gaejo_ending": "명사형이 아닌 종결",
    "summary_missing": "핵심 두 줄 없음",
    "summary_lines": "핵심이 두 줄이 아님",
    "minutes_columns": "실행 항목 출처·비고 열 없음",
    "minutes_due": "풀린 기한이 「미정」·「[기한]」으로 남음",
    "minutes_meeting_day": "자료에 없는 회의 날짜",
    "minutes_undated_due": "회의 일자 없이 날짜로 푼 기한",
    "minutes_owner": "자료에 없는 담당",
    "minutes_spec": "자료에 없는 수치",
    "minutes_unsourced": "자료에 없는 회의 내용·실행 항목",
    "minutes_blank_column": "자료에 없는 빈 열",
    "recommend_mismatch": "권고가 필수 조건·점수와 어긋남",
    "status_missing": "탈락·조건 미확인 표시 없음",
    "lead_time_date": "납기 날짜·기한 판정이 계산과 다름",
    "lead_date_missing": "납기를 날짜로 풀지 않음",
    "placeholder_answerable": "자료에 있는 값을 빈칸으로 둠",
    "staff_headcount": "투입 MM을 인원으로 씀",
    "count_label": "지적 건수가 다른 대상에 붙음",
}
#: judge findings (E5): shown for review, not counted as repairs
MODEL_LABELS = {
    "owner_change": "담당 변경 미반영 후보(확인 필요)",
    "number_binding": "다른 대상에 붙은 숫자 후보(확인 필요)",
    "experience_repeat": "같은 경험 반복 후보(확인 필요)",
    "rule_broken": "검토 규칙 위반 후보(확인 필요)",
    "section_missing": "템플릿의 절 없음(확인 필요)",
    "section_order": "템플릿과 다른 절 순서(확인 필요)",
    "table_missing": "템플릿이 정한 표 없음(확인 필요)",
}


def summary(codes_before: list[str], codes_after: list[str]) -> str:
    """「견적 합계 1 → 0, 실행 항목 칸 2 → 1」."""
    keys = list(dict.fromkeys(codes_before + codes_after))
    return ", ".join(
        f"{LABELS.get(k, k)} {codes_before.count(k)} → {codes_after.count(k)}" for k in keys
    )
