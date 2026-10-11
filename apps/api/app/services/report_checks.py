"""Fixed-schema work the report pipeline does by code for the lineup's formats.

Before writing (`prepare`): dates resolved against the meeting or mail date, a quote summed,
weighted scores computed, career periods counted — handed to the writer as settled blocks.
After writing (`checks`, `amendments`, `page_count`): what each format must carry, found by code;
the verify stage repairs once and checks again. Every run is recorded as a step.
"""

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
from app.services import key_figures, placeholders, skill_scripts, thinking, units
from app.services.verify import Check, Doc, Finding, model_check

log = logging.getLogger(__name__)

#: Which steps run, checks apply and amendments are made is the document's 진행 과정 설정
#: (`services/process_settings.py`), carried on the pre-pass (`Prepass.process`).

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
    # 「비용 50, 모바일 앱 30, HR 연동 20」 (comparison-report-4's follow-up): three or more
    # criteria each with a bare number.
    r"|(?:[가-힣A-Za-z][가-힣A-Za-z ]{0,10}\s\d{1,3}(?:점|%)?\s{0,2}[,，]\s{0,2}){2,6}"
    r"[가-힣A-Za-z][가-힣A-Za-z ]{0,10}\s\d{1,3}(?:점|%)?(?!\d)")
_FULL_DATE = re.compile(
    r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})"
)
_MEETING = re.compile(r"회의|미팅|모임|위원회|간담회|녹음|전사")
_REQ_ID = re.compile(r"(?<![A-Za-z0-9-])([A-Z]{2,4}-\d{2,3})(?![\d])")
_VALIDITY = re.compile(r"(?:견적\s{0,2})?유효\s{0,2}기간\s{0,2}(?:은|는|:|：)?\s{0,2}([^\n,.;]{2,30})")
_TABLE_RULE = re.compile(r"^\s{0,8}\|?\s{0,8}:?-{2,}")


# ── tables ───────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Table:
    section: int
    header: list[str]
    #: `(line as written, cells)` per body row.
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


def _words(text: str) -> set[str]:
    """Words of two characters or more, particles trimmed: what binds a row to a line."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9○]{2,20}", text or ""):
        word = re.sub(r"(?:까지|에서|으로|에게|님이|님은|님|이가|은|는|이|가|을|를|의|에|로|과|와|도)$", "", word)
        if len(word) >= 2 and not word.isdigit():
            out.add(word)
    return out


# ── before writing ──────────────────────────────────────────────────────


@dataclass(slots=True)
class Prepass:
    format_id: str = ""
    #: `process_settings.Process` the document is written with; None: the defaults.
    process: Any = None
    base_date: str = ""
    #: Where `base_date` came from: 「text」, 「file」 (the attachment's name), 「said:오늘」
    #: (the person said the meeting was today or yesterday), or "" when nothing gives it.
    base_source: str = ""
    #: date-calc rows (`scan`), each bound to its `source_line`.
    dates: list[dict] = field(default_factory=list)
    #: Lead times turned into dates from the material's anchor date (`lead_times`).
    lead: dict | None = None
    quote_input: dict | None = None
    quote: dict | None = None
    scores: dict | None = None
    periods: dict | None = None
    #: Must-have conditions and each option's status under them (`must_status`).
    musts: dict | None = None
    #: Each option's total cost (`option_costs`), for a comparison priced by parts.
    costs: dict | None = None
    #: Readings of the costs that never agreed: the writer is handed no totals at all.
    costs_withheld: bool = False
    #: What the RFP fixes for a proposal: client names, period end or length, page limit.
    rfp: dict | None = None
    #: Each role's headcount and MM (`staffing`), so a document never writes MM as people.
    staff: dict[str, dict] = field(default_factory=dict)
    figures: list[key_figures.Figure] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    #: `{"skill", "summary"}` per run, for the step record; `error` marks a failed run and
    #: `skipped` one with nothing to work on, both said in the step.
    runs: list[dict] = field(default_factory=list)

    @property
    def settings(self):
        """Its 진행 과정 설정; one made without them (an older run) reads its format's."""
        from app.services import process_settings

        if self.process is not None:
            return self.process
        return process_settings.for_format(self.format_id)

    @property
    def counts_from_meeting(self) -> bool:
        """Relative dates count from the meeting day (`date_base = "meeting"`, 회의록)."""
        return self.settings.date_base == "meeting"

    @property
    def undated(self) -> bool:
        """Minutes whose meeting date nothing gives: it stays 「[회의 일자]」."""
        return self.counts_from_meeting and not self.base_source

    @property
    def bound_dates(self) -> list[dict]:
        lead = [d for d in (self.lead or {}).get("items") or [] if d.get("status") == "ok"]
        return [d for d in self.dates if d.get("status") in ("ok", "repeat")] + lead


def meeting_date(request: str, material: str) -> dt.date | None:
    """The date a meeting was held, as the person or the material's header states it."""
    for text in (request, material):
        for line in (text or "").splitlines():
            if (m := _FULL_DATE.search(line)) and _MEETING.search(line):
                try:
                    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
    return None


#: An attached file's heading in the material: 「## 녹음_연구실주간미팅_20261014_1000.mp3」.
_FILE_HEADING = re.compile(r"^#{2,3}\s{1,3}(\S[^\n]{0,200}\.[A-Za-z0-9]{2,5})\s{0,3}$")
_FILE_DATE = re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


def file_date(material: str) -> dt.date | None:
    """A date in an attached file's name (20261014, 2026-10-14, 2026_10_14)."""
    for line in (material or "").splitlines():
        if not (head := _FILE_HEADING.match(line.strip())):
            continue
        for m in _FILE_DATE.finditer(head.group(1)):
            try:
                return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


#: The person saying the meeting was today or yesterday: 「오늘 회의」, 「어제 한 미팅」. Not
#: 「오늘까지 회의록」 (a due day) or 「오늘 회의록」 (the document).
_SAID_DAY = re.compile(
    r"(?<![가-힣])(오늘|금일|어제|어저께|그저께|그제)(?!\s{0,2}(?:까지|중|안에|내로))"
    r"(?:\s{1,3}[가-힣A-Za-z0-9]{1,10}){0,3}?\s{0,3}(?:회의|미팅|모임|간담회|세미나)(?!록)"
)
#: The gap card's answer merged into the request: 「- 회의 일시: 10월 7일 오후 2시」.
_ANSWERED_DAY = re.compile(r"회의\s{0,2}일시\s{0,2}[:：]\s{0,4}(\d{1,2})\s{0,2}(?:월|[/.])\s{0,2}(\d{1,2})")
_DAYS_BACK = {"오늘": 0, "금일": 0, "어제": 1, "어저께": 1, "그저께": 2, "그제": 2}


def meeting_base(request: str, material: str, today: dt.date | None) -> tuple[dt.date | None, str]:
    """The base relative dates are read from, and where it came from: the text, the file's
    name, or the person saying the meeting was today or yesterday (the turn's date, KST).
    A recording says no date aloud, so its 「다음 주 수요일」 stayed 미정 with 20261014 in the
    file's name (minutes-2). Nothing else gives one: the day the material was handed over
    became 「10월 8일에 열린 회의」 (minutes-3)."""
    if found := meeting_date(request, material):
        return found, "text"
    if found := file_date(material):
        return found, "file"
    if today and (said := _ANSWERED_DAY.search(request or "")):
        # The person's answer to the 「회의 일시」 question, without a year: the latest such
        # day not after today.
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
    return None, ""


def _base_words(source: str) -> str:
    """Where the base date came from, as the step and the date block say it."""
    if source == "file":
        return "첨부 파일 이름의 날짜"
    if source.startswith("said:"):
        return f"요청의 「{source[5:]}」을 대화 날짜(한국 시간)로 셈"
    return ""


#: Date-calc's reading of a line when no meeting date is known: the words kept, no date.
_NO_BASE = "no_base"


def _undated(rows: list[dict], probe: dt.date) -> list[dict]:
    """Rows scanned from a stand-in base, the relative ones turned back into words: a memo
    with no meeting date resolves no 「다음 주 금요일」. Dates the material wrote out stay."""
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
    """Each row given the words that say what it is for: its own line, and in a transcript
    the line before, where 「서○○는 데이터 정제 쪽을 맡아 줘요」 names the task the reply
    「다음 주 수요일까지 하겠습니다」 dates. A memo's line before is another item."""
    lines = (material or "").splitlines()
    for row in rows:
        number = int(row.get("line") or 0)
        own = lines[number - 1] if 0 < number <= len(lines) else str(row.get("source_line") or "")
        near = [_SPEAKER.sub("", own).strip()]
        if _SPEAKER.match(own):
            before = next((ln for ln in reversed(lines[:max(0, number - 1)]) if ln.strip()), "")
            # A transcript's speaker labels (「한교수:」) are on every line and bind nothing.
            near.insert(0, _SPEAKER.sub("", before).strip())
        row["context"] = " ".join(near)[:600]


#: The date block's head when the meeting's date is nowhere: no date is worked out from today.
_UNDATED_HEAD = (
    "회의 일자: not in the material or the request. Write the meeting date in the head field 일시 and "
    "in the body (회의 개요 etc.) as 「[회의 일자]」; do not guess it from today or the day the material "
    "arrived. Do not resolve relative deadlines (다음 주 금요일, 모레) to dates: write 「[기한]」 in the "
    "기한 cell and 「원문 「다음 주 금요일」, 회의 일자 미기재로 날짜 미확정」 in 비고."
)


def _date_block(rows: list[dict], base: str, base_note: str = "", *, undated: bool = False) -> str:
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
        where = f" — 출처: 「{r['source_line'][:120]}」(기준 {r['base_date']}"
        where += ", 메일 발신일)" if r.get("base_kind") == "mail" else ")"
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
    if base and base_note:
        head = f"기준일 {base}({base_note} — state this basis in the minutes)."
    if undated and not any(r["status"] in ("ok", "repeat") for r in relative):
        return "# 확정 날짜(코드 계산)\n" + _UNDATED_HEAD + ("\n" + "\n".join(lines) if lines else "")
    if undated:
        head = _UNDATED_HEAD + f" Only lines that carry a date, such as mails, were resolved from that date ({base})."
    return (
        "# 확정 날짜(코드 계산)\n" + head + " The code resolved the material's relative dates. In the "
        "기한 cell write the dates below like 「2026-10-23(금)」 and put the original wording in 비고. "
        "Do not recalculate. Do not move a date that falls on a weekend unless the material says "
        "to.\n" + "\n".join(lines)
    )


def _lead_verdict(row: dict) -> str:
    """How a lead-time date stands against the deadline, in words."""
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


#: A requirement that keeps processing in the country: 「국외로 이전하지 않으며, 처리 시스템은
#: 국내에 둔다」, 「외부 LLM API 사용 불가」.
_DOMESTIC_ONLY = re.compile(
    r"(?:국외|해외)\S{0,4}\s{0,4}(?:이전하지|이전\s{0,2}금지|이전\s{0,2}불가|두지\s{0,2}않)|"
    r"국내에\s{0,2}(?:둔다|두어야|설치|구축)|"
    r"외부\s{0,4}(?:LLM|API|AI|클라우드)\S{0,6}\s{0,4}(?:사용|호출|이용|연동)?\s{0,2}(?:금지|불가|하지\s{0,2}않)"
)
#: An item that sends the data abroad or to an outside model: 「외부 LLM API 사용료」.
_OFFSHORE_ITEM = re.compile(r"(?:외부|해외|국외)\s{0,4}(?:LLM|API|AI|리전|클라우드)", re.I)
_REQ_ID = re.compile(r"\b[A-Z]{2,4}-\d{2,3}\b")
#: The material itself setting an item aside: 「(대체)」, 「제외」, 「불가」. A bare 「대체」 is
#: not one: 「국내 GPU로 대체」 names the replacement, which stays in.
_SET_ASIDE = re.compile(
    r"\(\s{0,2}(?:대체|제외|불가|미포함)[^()\n]{0,20}\)|제외|불가|미포함|발주기관\s{0,2}부담|"
    r"고객\s{0,2}부담|대체\s{0,2}(?:됨|되어|되므로|대상)"
)

_CLIENT_PAYS = re.compile(
    r"(?:발주\s{0,2}기관|발주처|수요\s{0,2}기관|고객사?)\S{0,2}\s{0,4}(?:별도로?\s{0,2}|직접\s{0,2})?부담"
)


def _ruled_out(item: dict, material: str) -> str:
    """Why the material rules a quote item out, or ""; a backstop under the model's own call.

    The line must name the item, and the set-aside word must follow one of the item's own
    prices in the same clause (「3,300,000원(발주기관 부담)」), not just share the line.
    """
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
        # 「※ 품질인증 심사 수수료는 발주기관이 별도 부담함」: the RFP names the item, not its price.
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
    """「excl」 or 「incl」 when the material's own price lines state the VAT basis, else "".

    A budget's basis (「기초금액 … 부가가치세 포함」) is not the prices' basis.
    """
    marks = {
        "incl" if m.group(1) == "포함" else "excl"
        for line in (material or "").splitlines()
        if _UNIT_PRICE_LINE.search(line) and not _BUDGET_LINE.search(line)
        for m in _BASIS_MARK.finditer(line)
    }
    return marks.pop() if len(marks) == 1 else ""


def grounded_quote_input(data: dict, material: str) -> tuple[dict, list[str]]:
    """The extracted quote input with every value the material does not state dropped.

    Items the material rules out (a requirement they break, or set aside in so many
    words) stay in the list as `excluded` with a `reason`, so the total leaves them out.
    """
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
                # 「qty: 7, unit: MM, monthly_price」 is a unit price under another name.
                kept["unit_price"] = kept.pop("monthly_price")
            reason = str(item.get("reason") or "").strip()[:120] if item.get("excluded") else ""
            if item.get("excluded") and not reason:
                reason = str(item.get("note") or "").strip()[:120] or "자료에서 범위 밖"
            reason = reason or _ruled_out(kept, material)
            priced = any(k in kept for k in ("monthly_price", "unit_price", "amount"))
            if not priced:
                # An excluded item extracted without its price (「qty: 7, excluded」) failed
                # the whole quote with KeyError 'unit_price' (client-proposal-5).
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
    # The prices' stated basis wins over the model's: a 「VAT 별도」 rate card summed as VAT
    # included settled 공급가액 269,090,909 for a 296,000,000 subtotal (client-proposal-5).
    # Prices that state no basis are VAT 별도, as quoted unit prices are: the extraction took a
    # VAT-included budget's basis for the memo's 「월 7,000,000원」 and settled 85,000,000 as the
    # VAT-included total (client-proposal-3).
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
    """The quote input's items left out of the price, with why."""
    data = data or {}
    return [*(i for i in data.get("items") or [] if i.get("excluded")),
            *(data.get("set_aside") or [])]


#: The material speaks of VAT: only then is a quote with no stated basis worth flagging.
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
            # A memo that never mentions VAT gets no VAT question raised from it (S17).
            lines.append(f"- 견적 {row['vendor']}: {row['amount']:,}원 (as the material writes it)")
    return "\n".join(lines)


def _quote_figures(result: dict) -> list[key_figures.Figure]:
    if not result.get("lines"):
        return []
    return [
        key_figures.Figure("견적 공급가액(VAT 별도)", f"{result['supply']:,}원", ["공급가액"]),
        key_figures.Figure("견적 부가세", f"{result['vat']:,}원", ["부가세"]),
        key_figures.Figure("견적 합계(VAT 포함)", f"{result['total']:,}원", ["합계(VAT 포함)", "총액"]),
    ]


# Career periods: 「2019.04 ~ 2021.01」, 「2023.03~ 현재」, 「2019년 4월 – 2021년 1월」.
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
    """Weighted scores by the comparison rules S1–S4.

    S1 cost: lowest ÷ own × weight. S2 benefit: own ÷ highest × weight; 있음/없음: weight or 0.
    S3 a criterion with no value for some option: (a) left out and the rest rescaled to 100,
    and (b) scored 0 — both shown. S4 a tie is reported as a tie, never broken by order."""
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
- unit_price: the unit price exactly as written in the material (won). count: the number of units, people or items the price applies to (that number when the price is per unit or per person, otherwise 1).
- per: the unit of the price. "once" (one time), "year" (each year), "month" (each month), otherwise the usage unit
  ("km", "시간", "장", "건" …).
- years: the number of years compared. For a monthly price, "months" if the material gives the number of months, otherwise only years.
- usage: the yearly usage per unit (per person) for a price per usage unit. threshold: when only the usage above this value
  is charged, that threshold (「연 20,000km 초과 시」). Leave it out when there is none.
- rate: only when the price is a ratio of the unit price (35% → 0.35). sign: -1 for items that reduce the cost (보조금·지원금·잔존가치·매각액·환급).

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


_ONCE = ("once", "한 번", "1회", "일시")
_YEAR = ("year", "연", "년", "annual")
_MONTH = ("month", "월", "개월")


def _plain(raw: Any) -> Any:
    """A number field as the model wrote it, 「15,000」 or 「15,000km」 read as 15000."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw
    m = re.match(r"\s{0,4}(\d[\d,]{0,20}(?:\.\d{1,10})?)", str(raw or ""))
    return m.group(1).replace(",", "") if m else None


def explicit_factors(item: dict, years: Any) -> dict | None:
    """An item given by named fields (unit_price, count, per, years, months, usage,
    threshold, rate) as the factors and `over` code multiplies: the model names what each
    number is and code does the arithmetic it got wrong in comparison-report-5 (×5 vehicles
    and the hours term dropped from car-sharing). `None` when a field the unit needs is missing."""
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
        factors, over = ([count, price, months] if months is not None
                         else [count, price, span, 12]), None
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
    out = {"name": item.get("name"), "factors": factors}
    if calendar:
        # 12 months a year is code's own factor here, not one the model put in.
        out["calendar"] = True
    if over:
        out["over"] = over
    if item.get("sign") in (-1, "-1"):
        out["sign"] = -1
    return out


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


#: A monthly price over a span of years: 12 months a year is a factor no material writes.
_MONTHLY = re.compile(r"월\s{0,2}(?:[\d,]|요금|사용료|임대료|관리비|구독료|이용료)|/\s{0,2}월|매월")
_YEARLY = re.compile(r"\d\s{0,2}년|연간|연\s{0,2}(?:비용|기준|단위|총액)")


def _calendar(text: str) -> list[float]:
    """Calendar constants a cost may multiply by: 12 when monthly prices meet years."""
    return [12.0] if _MONTHLY.search(text or "") and _YEARLY.search(text or "") else []


def _percent(value: Any, numbers: list[float]) -> bool:
    """「35%」 written as the factor 0.35."""
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return False
    return 0 < number < 1 and _grounded(round(number * 100, 6), numbers)


def option_costs(data: dict, numbers: list[float], why: list[str] | None = None) -> dict | None:
    """Each option's total cost from the extracted factors, every factor one the material
    states; `None` when fewer than two options are left, the reason added to `why`."""
    def grounded(item: Any) -> bool:
        if not isinstance(item, dict) or not item.get("name"):
            return False
        factors = item.get("factors")
        over = item.get("over")
        if not isinstance(factors, list) or not factors or len(factors) > 8:
            return False
        if over is not None and not (isinstance(over, list) and len(over) == 2):
            return False
        # A factor of 1 (「1대」 unwritten) changes no amount.
        allowed = [*numbers, 1.0, *([12.0] if item.get("calendar") else [])]
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

    def run(swap: dict[float, Decimal]) -> dict[str, dict]:
        out = {}
        for option in options:
            rows = [{"name": str(i["name"])[:40], "amount": int(_cost_item(i, swap)),
                     "factors": i["factors"], "over": i.get("over")} for i in option["items"]]
            out[option["name"]] = {"rows": rows, "total": sum(r["amount"] for r in rows)}
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
    base = run({})
    lowest = min(base, key=lambda name: base[name]["total"])
    return {"period": str(data.get("period") or "")[:20], "options": base, "lowest": lowest,
            "scenarios": scenarios[:4], "dropped": dropped}


def _factor_text(row: dict) -> str:
    parts = [f"{float(str(f).replace(',', '')):,.10g}" for f in row["factors"]]
    if row.get("over"):
        a, b = row["over"]
        parts.append(f"({float(str(a).replace(',', '')):,g} − {float(str(b).replace(',', '')):,g}, 0 이상)")
    return " × ".join(parts)


def _cost_block(result: dict) -> str:
    period = result["period"] or "비교 기간"
    rows = ["| 대안 | 항목 | 계산 | 금액(원) |", "|---|---|---|---:|"]
    for option, entry in result["options"].items():
        for row in entry["rows"]:
            rows.append(f"| {option} | {row['name']} | {_factor_text(row)} | {row['amount']:,} |")
        rows.append(f"| {option} | 합계 |  | {entry['total']:,} |")
    lines = [f"# 총비용 계산(코드)\nThe code calculated the {period} total costs from the material's unit prices "
             "and quantities. Use only these values for the total cost table and the amounts in comparison "
             "sentences; do not recalculate.", "\n".join(rows)]
    lines.append("- " + ", ".join(f"{o} {e['total']:,}원" for o, e in result["options"].items())
                 + f" → 가장 적은 대안: {result['lowest']}")
    for spec in result["scenarios"]:
        totals = spec["options"]
        lines.append(f"- 시나리오 「{spec['name']}」: " + ", ".join(
            f"{o} {e['total']:,}원" for o, e in totals.items())
                     + f" → 가장 적은 대안: {min(totals, key=lambda n: totals[n]['total'])}")
    if result["dropped"]:
        lines.append("- Left out of the calculation (values not in the material): " + ", ".join(result["dropped"][:6]))
    return "\n".join(lines)


Complete = Callable[..., Any]

#: The settling reading with thinking on: its time cap, and the tokens its thinking and its
#: answer share (Flash+thinking took 150 s median on cost extraction; outlines hit 6,400).
DELIBERATE_SEC = 360.0
DELIBERATE_TOKENS = 12000


def _failure(exc: Exception) -> str:
    """Why a pre-pass failed, in words the step can show."""
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


#: What a split vote leaves the writer: no totals, the places for them marked.
_COST_SPLIT = "총비용 계산 불일치 — 직접 확인 필요"
_COST_WITHHELD = (
    "# 총비용 계산(코드): 확정 못 함\nThe cost items read from the material differed on every reading, so the "
    "total cost per alternative could not be settled. Do not calculate per-alternative total costs, sums, "
    "differences or scenario totals; leave them as 「[금액]」. The unit prices, quantities and conditions "
    "written in the material may be used as they are.")


def _option_key(name: str) -> str:
    """An option's name as two readings compare it: 「팀링크 (엔터프라이즈)」 is 「팀링크」."""
    return re.sub(r"\s+|\(.*?\)|（.*?）", "", str(name))


def _same_totals(a: dict[str, dict], b: dict[str, dict]) -> bool:
    """The same options at the same totals; a name may carry more words in one reading
    (「법인 카셰어링」 for 「카셰어링」)."""
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


def _costs_agree(a: dict, b: dict) -> bool:
    """Two readings agree when they price the same options to the same totals."""
    return _same_totals(a["options"], b["options"])


def _quote_key(result: dict) -> tuple:
    return (result.get("supply"), result.get("total"), tuple(sorted(
        (q.get("amount") or 0, str(q.get("vat_basis") or "")) for q in result.get("quotes") or [])))


async def _vote(extract: Callable[[str], Any], agree: Callable[[Any, Any], bool],
                tiebreak: str, settle: Callable[[], Any] | None = None) -> tuple[Any, str, list]:
    """Two readings by the writer's model, a third by `tiebreak` when they differ.

    `settle`, when given, is tried first for the third reading (a reading that thinks); when
    it fails, times out or reads nothing, the third reading is `tiebreak`'s as before.

    Returns `(result, how, outcomes)`: the result two readings agree on and 「agreed」 or
    「voted」; `None` and 「split」 when they all differ, or 「empty」 when none gave one. Each
    outcome is `(result | None, why)`; a reading that raised is left out of them and counts as
    none, and when every reading raised the first error is raised."""
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
    """What every pre-pass step is given: the turn's request and material, and the calls."""

    format_id: str
    request: str
    material: str
    complete: Complete
    model: str
    api_key: str
    today: dt.date | None
    tiebreak_model: str
    usage: dict
    #: A completion with the model's thinking on, for the reading that settles a split
    #: (`report._complete_thinking`); None keeps the plain third reading.
    deliberate: Complete | None = None

    def count(self, more: dict) -> None:
        for key in ("inputTokens", "outputTokens"):
            self.usage[key] = self.usage.get(key, 0) + int((more or {}).get(key, 0))


async def prepare(
    format_id: str | None, request: str, material: str, *, complete: Complete, model: str,
    api_key: str, usage: dict | None = None, today: dt.date | None = None,
    tiebreak_model: str = "", process: Any = None, deliberate: Complete | None = None,
) -> Prepass:
    """The document's pre-pass: what code settles before a word is written.

    The steps and their order are its 진행 과정 설정's `prepass` (`process`; None: the
    format's 서식's); each records its run — done, skipped or failed — in `Prepass.runs`."""
    from app.pipeline.runner import CURRENT_STAGE
    from app.services import process_settings

    if process is None:
        process = process_settings.for_format(format_id)
    pre = Prepass(format_id=format_id or "", process=process)
    if not (material or "").strip():
        return pre
    job = PrepassJob(format_id or "", request, material, complete, model, api_key, today,
                     tiebreak_model, usage if usage is not None else {}, deliberate)
    for name in process.prepass:
        token = CURRENT_STAGE.set(f"prepass:{name}")
        try:
            await PREPASS_STEPS[name](pre, job)
        finally:
            CURRENT_STAGE.reset(token)
    return pre


async def _date_calc(pre: Prepass, job: PrepassJob) -> None:
    """Relative dates resolved against the meeting's (or the memo's) date; lead times
    counted from the material's own anchor date."""
    request, material, today = job.request, job.material, job.today
    meeting = pre.counts_from_meeting
    # Only minutes are about a day the material itself was made on; an RFP's or a
    # memo's relative dates are not read from today.
    base, source = (meeting_base(request, material, today) if meeting
                    else (meeting_date(request, material), "text"))
    if base is None and meeting:
        # The meeting's date is 「[회의 일자]」; relative deadlines stay words.
        probe = today or dt.date.today()
        rows = _undated(skill_scripts.dates().scan(material, probe), probe)
    else:
        rows = skill_scripts.dates().scan(material, base)
    _with_context(rows, material)
    pre.dates = rows
    first = next((r["base_date"] for r in rows if r.get("base_date")),
                 base.isoformat() if base else "")
    pre.base_date = first
    pre.base_source = source if base else ""
    undated = meeting and base is None
    if block := _date_block(rows, first, _base_words(pre.base_source), undated=undated):
        pre.blocks.append(block)
    # 「발주 후 8주」 counts from the material's own absolute date, meeting date or not.
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
    """What the RFP fixes: the client's names, the period, the page limit."""
    if terms := rfp_terms(job.material, job.request):
        pre.rfp = terms
        shown = [x for x in (
            "고객사 " + "·".join(terms["clients"][:2]) if terms.get("clients") else "",
            f"사업 종료 {terms['end']}" if terms.get("end") else "",
            f"사업 기간 {terms['months']}개월" if terms.get("months") else "",
            f"본문 {terms['pages']}쪽 이내" if terms.get("pages") else "") if x]
        pre.runs.append({"skill": "proposal-check", "summary": "RFP: " + ", ".join(shown)})


async def _quote_calc(pre: Prepass, job: PrepassJob) -> None:
    """The quote's lines read twice (a third reading settles a split) and summed by code."""
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
            # Read twice; a third reading by the larger model settles a split.
            agreed, how, outcomes = await _vote(
                read_quote, lambda a, b: _quote_key(a[2]) == _quote_key(b[2]),
                tiebreak_model or model)
            dropped = next((o[1] for o in outcomes if o[1]), [])
            if how == "split":
                pre.runs.append({"skill": "quote-calc", "error": True,
                                 "summary": "견적 계산 불일치 — 직접 확인 필요"})
            elif agreed is not None:
                data, dropped, result = agreed
                pre.quote_input, pre.quote = data, result
                pre.staff = staffing(material, data)
                left_out = excluded_items(data)
                pre.blocks.append(_quote_block(result, left_out, bool(data.get("basis_assumed")),
                                               material))
                if "quote-totals" in settings.code_checks:
                    # A document whose totals are checked holds them as settled figures.
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
                # Prices totalled per option by compare-cost are no miss here.
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
        # Read by code even when the quote reading did not settle.
        pre.staff = staffing(job.material)


async def _compare_cost(pre: Prepass, job: PrepassJob) -> None:
    """Each option's total over the period, read twice and computed by code."""
    request, material, api_key = job.request, job.material, job.api_key
    complete, model, tiebreak_model, count = job.complete, job.model, job.tiebreak_model, job.count
    if len(_amounts(material)) >= 2:
        # Options priced by parts (unit price × count × years) settle their totals before
        # writing; with no 「단가」 line quote-calc never ran (comparison-report-5).
        given = f"{request}\n{material}"
        numbers = _material_numbers(given) + _calendar(given)

        prompt = [{"role": "user", "content": _COST_PROMPT.format(
            request=request[:3000], material=material[:10000])}]

        async def read_costs(override: str) -> tuple[dict | None, str]:
            text, more = await complete(override or model, prompt, api_key, 2500)
            count(more)
            why: list[str] = []
            return option_costs(_json(text), numbers, why), (why[0] if why else "")

        # Two readings that differ are settled by one more on the outline model with its
        # thinking on (Flash+thinking read 15/15 cost cases, 150 s median, against 12/15
        # without); past the time cap the plain third reading settles, as before.
        thinker = next((m for m in (model, tiebreak_model)
                        if m and thinking.thinks_by_default(m)), "")
        deliberated = False

        async def think_costs() -> tuple[dict | None, str]:
            nonlocal deliberated
            async with asyncio.timeout(DELIBERATE_SEC):
                text, more = await job.deliberate(thinker, prompt, api_key, DELIBERATE_TOKENS)
            count(more)
            why: list[str] = []
            found = option_costs(_json(text), numbers, why)
            deliberated = found is not None
            return found, (why[0] if why else "")

        try:
            # One reading priced comparison-report-5's purchase at 167,900,000 or 185,900,000
            # against 197,900,000: totals reach the writer only when two readings agree, a
            # third by the larger model settling a split.
            costs, how, outcomes = await _vote(
                read_costs, _costs_agree, tiebreak_model or model,
                think_costs if job.deliberate is not None and thinker else None)
            if costs is not None:
                # A scenario is kept only when another agreeing reading gives it the same totals.
                others = [o[0] for o in outcomes if o[0] is not None and o[0] is not costs
                          and _costs_agree(o[0], costs)]
                kept = [sc for sc in costs["scenarios"] if any(
                    _same_totals(sc["options"], other["options"])
                    for o in others for other in o["scenarios"])]
                if len(kept) < len(costs["scenarios"]):
                    costs = {**costs, "scenarios": kept,
                             "dropped": [*costs["dropped"], "시나리오(읽을 때마다 다름) " + ", ".join(
                                 sc["name"] for sc in costs["scenarios"] if sc not in kept)]}
                pre.costs = costs
                pre.blocks.append(_cost_block(costs))
                summary = (f"총비용 {len(costs['options'])}개 대안"
                           + (f"({costs['period']})" if costs["period"] else "") + ": "
                           + ", ".join(f"{o} {e['total']:,}원" for o, e in costs["options"].items()))
                if costs["scenarios"]:
                    summary += f" · 시나리오 {len(costs['scenarios'])}개"
                if costs["dropped"]:
                    summary += f" (자료에 없는 값이라 뺀 것: {', '.join(costs['dropped'][:4])})"
                if how == "voted":
                    summary += " · 세 번 읽어 둘이 같은 값" + (
                        " (세 번째는 생각을 켜고 읽음)" if deliberated else "")
                pre.runs.append({"skill": "compare-cost", "summary": summary})
            elif how == "split":
                pre.costs_withheld = True
                pre.blocks.append(_COST_WITHHELD)
                readings = " / ".join(", ".join(f"{n} {e['total']:,}원" for n, e in o[0]["options"].items())
                                      for o in outcomes if o[0] is not None)
                pre.runs.append({"skill": "compare-cost", "error": True,
                                 "summary": f"{_COST_SPLIT} ({readings[:600]})"})
            else:
                # Never silent: comparison-report-5 wrote its totals unaided with no step.
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
    """Must-have conditions judged per option, and weighted scores when weights are given."""
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
        if musts := must_status(data, numbers, f"{request}\n{material}"):
            pre.musts = musts
            pre.blocks.append(_must_block(musts))
            pre.runs.append({"skill": "compare-check", "summary": "필수 조건 " + ", ".join(
                f"{o} {r['status']}" for o, r in musts["options"].items())})
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
    """Each period's months and the total, counted by code."""
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


#: The pre-pass steps a pipeline file's `prepass` list names.
PREPASS_STEPS: dict[str, Callable[[Prepass, PrepassJob], Any]] = {
    "date-calc": _date_calc,
    "proposal-rfp": _proposal_rfp,
    "quote-calc": _quote_calc,
    "staffing": _staffing,
    "compare-cost": _compare_cost,
    "compare-check": _compare_check,
    "career-periods": _career_periods,
}


# ── after writing: checks the verify stage repairs ───────────────────────


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
    """The last amount of a table row (a bare 「47,100,000」 cell read as won) or a sentence."""
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
    """Rows of the quote table whose 공급가액·부가세·총액 differ from quote-calc."""
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


#: A sentence's quote total: 「견적 총액 85,000,000원(부가세 포함)」, 「합계(VAT 별도)는 …원」.
_TOTAL_SAID = re.compile(
    r"(?P<label>(?:견적|제안)?\s{0,2}(?:총액|총\s{0,2}견적\s{0,2}금액|합계)"
    r"(?:\s{0,2}\(\s{0,2}(?P<b1>부가세|VAT)\s{0,2}(?P<k1>포함|별도)\s{0,2}\))?)"
    r"\s{0,2}(?:은|는|이|:)?\s{0,2}(?:금\s{0,2})?(?P<amount>\d{1,3}(?:,\d{3}){1,5})\s{0,2}원"
    r"(?:\s{0,2}\(\s{0,2}(?P<b2>부가세|VAT)\s{0,2}(?P<k2>포함|별도)\s{0,2}\))?", re.I)


def _total_slips(text: str, quote: dict) -> list[tuple[str, int, int]]:
    """Totals a sentence states that are not quote-calc's: VAT 별도 against 공급가액, else the
    VAT-included 합계. Scenario totals pass."""
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
            # A bare 「합계」 may sum any part: 「PM 합계 35,000,000원」.
            loose = {quote["supply"], quote["subtotal"],
                     *(int(line.get("amount") or 0) for line in quote["lines"])}
        if written != want and written not in others | loose:
            out.append((m.group("label").strip(), written, want))
    return out


def _back_computed(quote: dict) -> set[int]:
    """Amounts got by reading a settled amount on the other VAT basis — 85,000,000 taken as
    VAT included gives 77,272,727 + 7,727,273 (client-proposal-3) — that no settled value is."""
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


def _cell_dates(cell: str, base_year: int) -> list[tuple[int | None, int, int]]:
    out = []
    for m in _ISO_IN.finditer(cell or ""):
        if m.group(1):
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        else:
            out.append((None, int(m.group(4)), int(m.group(5))))
    return out


def bound_rows(row_text: str, dates: list[dict]) -> list[dict]:
    """The resolved dates whose source line is the one this row came from (most words shared)."""
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


async def minutes_rows(doc: Doc, dates: list[dict]) -> tuple[list[Finding], dict]:
    """Each action row: an owner, a due date (「[기한]」 when the material gives none, 「미정」
    with its reason when the meeting put it off), a due date that is
    date-calc's reading of the line it came from."""
    found: list[Finding] = []
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")]
    for table in tables(doc.sections):
        owner = _column(table.header, r"담당|책임|owner")
        due = _column(table.header, r"기한|마감|완료일|due")
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
                problems.append("담당 칸이 비었다 — 이름, 자료에 없으면 「[담당자]」")
            if not when:
                problems.append("기한 칸이 비었다 — 날짜, 자료에 없으면 「[기한]」")
            elif not _DUE_CELL_OK.match(when):
                if _RELATIVE_WORDS.search(when) or len(when) > 16:
                    problems.append("기한 칸에는 값만 쓴다(날짜(요일)·미정·[기한]) — 원문 표현은 비고로")
            if note is not None and len(row) > note and not row[note].strip(" -—*"):
                if "미정" in (who + when):
                    problems.append("「미정」이면 비고에 사유(회의에서 정하지 않음 등)를 적는다")
            if _stray_repeat(when, row[task] if task is not None and task < len(row) else "", bound):
                problems.append("반복 일정(「매주 …」)은 그 일을 말한 행의 기한에만 쓴다 — 이 행은 "
                                "자료가 이 일에 준 기한(날짜·미정·[기한])으로")
            others = " ".join(c for k, c in enumerate(row) if k != due)
            entries = bound_rows(others, bound)
            written = _cell_dates(when, int((dates[0]["base_date"] if dates else "2026")[:4]))
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


_REPEAT_CELL = re.compile(r"(?:매주|격주)\s{0,2}[월화수목금토일]요일")


def _stray_repeat(when: str, action: str, bound: list[dict]) -> bool:
    """A 「매주 월요일」 on a row whose task is not the one the recurring line names."""
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
    written = _cell_dates(when, 2026)
    return bool(entries and written and not any(date_matches(w, e) for w in written for e in entries))


_BLANK = {"", "-", "—", "–", "?", "n/a", "N/A"}


async def comparison_cells(doc: Doc, scored: dict | None) -> tuple[list[Finding], dict]:
    """Comparison tables: no blank cell; written weighted scores equal the code's."""
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
    """What is written from the material's own numbers: a difference (exact, 「+7.0%p」), a
    percent change (「36.1% 절감」) and a small multiple (「4배」), rounded half up as people do.

    Counts under ten are left out: ratios of 「4명」 and 「2주」 would excuse any number."""
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


#: Dates and periods in material or text: read as dates, never as figures.
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
    """Numbers the material does not give (nor code derived from it), and a total career that is
    not date-calc's."""
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
            # Dates and periods are checked as periods, not as figures.
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
    """Every period written one way: 「2019.04 ~ 2021.01」, 「2023.03 ~ 현재」."""
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


# ── lead times: 「발주 후 8주」 as the date code worked out ─────────────────

#: A date as written: 「2026. 12. 14.」 「2026-12-14」 「12월 14일」 「12. 14.」.
_DAY_IN = re.compile(
    r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})(?!\d)"
    r"|(?<![\d.,])(\d{1,2})\s{0,2}(?:\.|월)\s{0,2}(\d{1,2})\s{0,2}[.일](?!\d)")
#: Words that make a line about when something is done or delivered.
_DONE_WORDS = re.compile(r"납기|납품|완료|설치|도착|입고|준공|검수|인도|\d{1,3}\s{0,2}주")
#: A late verdict by the deadline (「기한 2026. 12. 18. 초과」), not a risk (「초과 위험」).
_LATE_WORDS = r"초과|넘(?:어|음|김|는다|긴다)|미충족|불충족|맞추지\s{0,2}못"
_ON_TIME_WORDS = r"이내|충족|준수|맞춤|전\s{0,2}완료"
#: A date written as when the thing is done: 「완료일: 2026. 12. 14.」, 「납품 예정 12월 14일」.
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
    """Names a written line uses for a lead-time row: 「A사(에이브이원)」 → A사, 에이브이원."""
    return [k for k in re.split(r"[\s()（）\[\]·,/:]+", label or "") if len(k) >= 2][:4]


def _day_matches(found: tuple[int | None, int, int], iso: str) -> bool:
    year, month, day = found
    return int(iso[5:7]) == month and int(iso[8:10]) == day and (year is None or int(iso[:4]) == year)


def _verdict_near(text: str, deadline: str, words: str) -> bool:
    """Whether `words` follow 「기한」 or the deadline's date closely, as a verdict."""
    month, day = int(deadline[5:7]), int(deadline[8:10])
    near = rf"(?:기한|마감|{month}\s{{0,2}}[.월]\s{{0,2}}{day}\s{{0,2}}[.일]?)[^\n]{{0,16}}?(?:{words})"
    for m in re.finditer(near, text):
        if not _NOT_VERDICT.match(text[m.end():m.end() + 8]):
            return True
    return False


def _lead_problem(text: str, row: dict, allowed: list[str]) -> str:
    """What a line about one lead-time row gets wrong: a date code did not work out, or
    the deadline verdict turned round."""
    problems = []
    if _DONE_WORDS.search(text):
        wrong = [d for d in _days_in(text) if not any(_day_matches(d, iso) for iso in allowed)]
        # 「완료일: 2026. 12. 18.」 is the done date even when it is a date the material has.
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
    """Lines about one lead-time row (a vendor's 납기) whose date or deadline verdict is
    not what date-calc worked out; a table with the rows as columns is read by column."""
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
    """Lead-time rows whose worked-out date the document never writes."""
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
        return table_rows + list_rows, {}

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


#: The format code checks a pipeline file's `checks` list names, each built on the pre-pass
#: (None when the pre-pass gave it nothing to check).
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
}


def checks(pre: Prepass | None) -> list[Check]:
    """The document's code checks, as its 진행 과정 설정 names them (what its structure
    implies first: 개조식 문체); they run before and after the repair."""
    if pre is None:
        return []
    built = [CHECK_FAMILIES[name](pre) for name in pre.settings.code_checks]
    return [check for check in built if check is not None]


# ── staffing: people are people, MM is effort ───────────────────────────

#: 「AI 엔지니어 1명 3.0MM」, 「퍼블리셔 2명 각 4MM」 in the material.
_STAFF_LINE = re.compile(
    r"([A-Za-z가-힣][A-Za-z가-힣 ]{0,20}?)\s{0,2}(\d{1,3})\s{0,2}명\s{0,3}(?:[,·×x*]\s{0,2})?(?:각\s{0,2})?"
    r"(\d{1,3}(?:\.\d{1,2})?)\s{0,2}(?:MM|M/M|맨먼스)", re.I)


def staffing(material: str, quote_input: dict | None = None) -> dict[str, dict]:
    """Each role's headcount and MM as the material states them, read by code first and
    then from quote-calc's reading: `{role: {"people": n | None, "mm": x | None}}`."""
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
    """A role's headcount written as its MM (client-proposal-1: 「AI 엔지니어 3명(3.0MM),
    데이터 엔지니어 1.5명」 where the plan is 1명 × 3.0MM and 1명 × 1.5MM)."""
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


# ── placeholders the material can fill ──────────────────────────────────

_DAY_VALUE = (r"(?:20\d{2}\s{0,2}[-./년]\s{0,2})?\d{1,2}\s{0,2}(?:[/.]|월\s{0,2})\s{0,2}\d{1,2}\s{0,2}일?"
              r"(?:\s{0,2}\([월화수목금토일]\))?")
#: A blank's name and what a value for it looks like in the material.
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
#: Words too common to tie a blank to a line of the material.
_COMMON = frozenset({"회의", "자료", "확인", "다음", "기준", "경우", "결과", "내용", "관련", "예정",
                     "필요", "정도", "이번", "해당", "진행", "현재", "부분", "사항", "미정", "원문",
                     "비고", "출처", "담당", "기한", "완료"})


def _slot(name: str) -> re.Pattern[str] | None:
    return next((value for pattern, value in _SLOTS if pattern.search(name)), None)


def answerable_blanks(sections: list[dict], material: str) -> list[tuple[int, str, str, list[str]]]:
    """Blanks whose value the material states near the same words: `(section, sentence or
    row, blank, evidence lines)`. minutes-4 r2 wrote 「회당 [인원] 규모를 대강당 [석수]와 체육관
    [석수]로」 with 「회당 900명」「대강당이 600석」「체육관 750」 in the transcript; minutes-5 r1
    left the first meeting's deadlines as 「[기한]」 beside the table that gives them."""
    lines = [ln.strip() for ln in (material or "").splitlines() if ln.strip()]
    out: list[tuple[int, str, str, list[str]]] = []
    for index, sec in enumerate(sections):
        for line in str(sec.get("content") or "").split("\n"):
            if not placeholders.PLACEHOLDER.search(line) or line.lstrip().startswith(("#", "```")):
                continue
            pieces = [line] if line.lstrip().startswith("|") else [
                p for p in units.SENTENCE_BREAK.split(line) if placeholders.PLACEHOLDER.search(p)]
            for piece in pieces:
                own = _words(placeholders.PLACEHOLDER.sub(" ", piece)) - _COMMON
                for m in placeholders.PLACEHOLDER.finditer(piece):
                    name = m.group(1)
                    # The blank's own name ties it too: 「[경력 시작 월]」 to 「총 경력 … 2019년 4월」.
                    named = _words(name) - _COMMON
                    labelled = re.compile(re.escape(name) + r"\s{0,2}[:：]\s{0,2}\S")
                    value = _slot(name)
                    near = _words(piece[max(0, m.start() - 12):m.start()]) - _COMMON
                    evidence = []
                    for src in lines:
                        if placeholders.PLACEHOLDER.search(src) and not labelled.search(src):
                            continue
                        if labelled.search(src):
                            evidence.append(src)
                            continue
                        if value is None:
                            continue
                        # A value the sentence already carries (「교무처 마감 11/2」 in 비고)
                        # was left out of the blank on purpose.
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
    """A blank the material can fill is a defect: the repair fills it from the lines shown."""
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


# ── after writing: what is missing is added, never rewritten ─────────────


@dataclass(slots=True)
class Amendment:
    section: int
    code: str
    instruction: str
    #: True while the document still lacks it.
    missing: Callable[[list[dict]], bool]
    #: Text appended by code instead of asked of the model.
    append: str = ""
    #: Found and shown, never changed (no edit that only adds can mend it).
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
    """What the summary callout amendment asks for: 「핵심」 then `lines` lines (two: the
    conclusion and the decision needed, as a 개조식 report opens)."""
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
    """What the document must carry and does not: its 진행 과정 설정's `amend` names and its
    structure's summary callout."""
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
        # 「8주」 left as a span: the dates code worked out, and how each stands to the
        # deadline, added as one line where the options are weighed.
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
    """Whether a table marks `option` with `word` (a status row, or a status column)."""
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


# ── page count ───────────────────────────────────────────────────────────


#: What the page limit leaves out: 붙임 (개조식), 부록·별지·가격 제안서 (제안서, E7).
_ATTACHMENT = re.compile(r"^\s{0,4}\[?(?:붙임|첨부|별첨|부록|별지|가격\s{0,2}제안)")
_PAGES_ASKED = re.compile(
    r"(?<!\d)(\d{1,2}|한|두|세)\s{0,2}(?:쪽|장|페이지|page)\s{0,3}(?:이내|이하|안으로|짜리|분량|으로|로|내외)"
)


def page_limit(process: Any, request: str, material: str = "") -> int | None:
    """The body's page limit: what the person asks, else the RFP's (`page_limit_rfp`, a
    proposal), else the 서식's own (`page_limit`, 개조식: one page)."""
    structure = process.structure
    m = _PAGES_ASKED.search(request or "")
    if m:
        word = m.group(1)
        return int(word) if word.isdigit() else {"한": 1, "두": 2, "세": 3}[word]
    if structure.page_limit_rfp and (rfp := _BODY_PAGES.search(f"{request}\n{material}")):
        return int(rfp.group(1))
    return structure.page_limit or None


def body_sections(sections: list[dict]) -> list[dict]:
    """The body as the limit counts it: everything before the 붙임."""
    return [s for s in sections if not _ATTACHMENT.match(str(s.get("heading") or ""))]


async def page_count(
    title: str, sections: list[dict], title_block: dict | None, tokens: dict | None = None
) -> int | None:
    """Pages of the body as the PDF export lays it out; None when it cannot render."""
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


async def hwpx_page_count(
    title: str, sections: list[dict], title_block: dict | None, tokens: dict | None = None
) -> int | None:
    """Pages of the body through the 「붙임」 line as the `.hwpx` lays it out (an estimate,
    `report_export.hwpx_pages`); None when it cannot render. People open the HWPX."""
    from app.services import report_export

    try:
        _, body = await asyncio.to_thread(
            report_export.hwpx_pages, title, body_sections(sections), tokens=tokens,
            title_block=title_block,
        )
    except Exception as exc:  # noqa: BLE001 — the count is a check, not the export
        log.info("hwpx page count render failed: %s", logs.safe(str(exc)))
        return None
    return body or None


#: The 「끝.」 that closes a 개조식 document, after its 붙임 list (시행규칙 제4조제5항).
_CLOSING_LINE = re.compile(r"(?:^|\s)끝\.[ \t\u00a0]{0,8}$", re.M)


async def body_pages(
    title: str,
    sections: list[dict],
    title_block: dict | None,
    tokens: dict | None = None,
    page_settings: dict | None = None,
) -> tuple[int | None, int | None]:
    """`(hwpx, pdf)` pages of a 개조식 body through its 붙임 list and 「끝.」, as the exports
    lay it out: every section drawn (the 붙임 included), with the document's design, title
    block and page settings — what the 파일 탭 hands out. None where it cannot render."""
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

#: Rounds of condensing a body that runs over its page limit; each is measured again.
CONDENSE_ROUNDS = 2


def _lines(text: str) -> int:
    return sum(1 for ln in (text or "").split("\n") if ln.strip())


def _tables_to_attachment(sections: list[dict]) -> list[dict]:
    """The body's pipe tables moved, with their captions, to the 붙임 (made when missing).

    A pointer stays where each table stood. The 핵심 callout and other fences stay.
    """
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
    """One condensing round: the body's tables moved to 붙임, then each body section
    rewritten to the line count the measured overflow calls for.

    A rewrite that drops a number is not kept; nor is the round if the document as a
    whole lost one.
    """
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


# ── proposal: what the RFP fixes ────────────────────────────────────────

_CLIENT_LABEL = re.compile(
    r"(?:발주\s{0,2}기관|수요\s{0,2}기관|발주처|고객사|기관명|수\s{0,2}신)\s{0,2}[:：]\s{0,2}([^\n,|·「『(\[]{2,40})")
#: 「[RFP 발췌 — 한빛물류(주) 「…」]」, 「[미팅 메모 — 그린텍스타일(주) 인사팀, …]」.
_CLIENT_HEAD = re.compile(
    r"^\s{0,4}\[?\s{0,2}(?:RFP|제안\s{0,2}요청서|미팅\s{0,2}메모|고객\s{0,2}메일|과업\s{0,2}지시서|공고)[^\n—–\]]{0,20}[—–-]\s{0,2}"
    r"([^\n「『,\]]{2,40}?)\s{0,2}(?:[「『,\]]|$)", re.M)
#: A page header lifted from an RFP PDF: 「제안요청서  |  한국농식품정보원  |  2026」.
_CLIENT_RUNNING = re.compile(r"^\s{0,4}(?:제안\s{0,2}요청서|RFP)\s{0,3}\|\s{0,3}([^|\n]{2,30}?)\s{0,3}\|")
_OURS_LINE = re.compile(r"우리\s{0,2}회사|제안사|당사|실적")
_PERIOD_LINE = re.compile(r"(?:사업|용역|수행|계약|과업)\s{0,2}기간[^\n]{0,100}")
_MONTHS = re.compile(r"(\d{1,2})\s{0,2}개월")
_BODY_PAGES = re.compile(r"본문\s{0,3}(\d{1,3})\s{0,2}(?:쪽|페이지|매|page)\s{0,3}(?:이내|이하|까지)")


def _client_key(name: str) -> str:
    """A client name for comparing: 「(주)」, 「주식회사」, 「(가상)」 and spaces out."""
    name = re.sub(r"\(\s{0,2}(?:주|가상|사|재|유)\s{0,2}\)|주식회사|㈜", "", name or "")
    return re.sub(r"[\s\"'“”‘’]+", "", name).strip(" -·:")


def rfp_terms(material: str, request: str) -> dict | None:
    """Client names, the period's end (or length in months) and the body page limit an RFP states."""
    text = f"{request}\n{material}"
    clients: list[str] = []
    for line in text.splitlines():
        if _OURS_LINE.search(line[:20]):
            continue
        for m in (*_CLIENT_LABEL.finditer(line), *_CLIENT_HEAD.finditer(line),
                  *_CLIENT_RUNNING.finditer(line)):
            name = m.group(1).strip(" -·:")
            # A name, not a date or a number (「[고객 메일 — 2026-10-06(화), …]」).
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
    """The head's 고객사 against the RFP's client: `(fixed block or None, problem or "")`.

    A blank or a wrong name is set to the RFP's when it names one client; with several, a
    wrong name is only reported. A name `text` (request and material) gives is kept unless it
    is the proposer's own."""
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


# ── minutes with no meeting date ────────────────────────────────────────

_WD = "월화수목금토일"
#: A day as the writer spells it: 「2026년 10월 8일(목)」, 「10월 8일」, 「2026-10-08」, a time after.
_SOME_DAY = (r"(?:(?:20\d{2}\s{0,2}년\s{0,2})?\d{1,2}\s{0,2}월\s{0,2}\d{1,2}\s{0,2}일"
             r"|20\d{2}\s{0,2}[-./]\s{0,2}\d{1,2}\s{0,2}[-./]\s{0,2}\d{1,2}\.?)"
             rf"(?:\s{{0,2}}\([{_WD}]\))?"
             r"(?:\s{0,2}(?:오전|오후|저녁|밤)?\s{0,2}\d{1,2}\s{0,2}(?:시|:\d{2})(?:\s{0,2}\d{1,2}\s{0,2}분)?)?")
#: 「10월 8일에 열린 축제 부스 준비 회의」: a day the text gives as the meeting's own.
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
    """Whether the material or request writes this month and day itself."""
    found = _cell_dates(day, 0)
    if not found:
        return False
    _, month, date = found[0]
    flat = re.sub(r"\s+", "", known or "")
    return any(sp in flat for sp in (f"{month}월{date}일", f"{month}/{date}", f"{month}.{date}",
                                     f"-{month:02d}-{date:02d}", f"{month:02d}.{date:02d}"))


def minutes_undated(sections: list[dict], dates: list[dict], known: str) -> tuple[list[dict], list[str]]:
    """With no meeting date in the request, the material or the file's name, a day written as
    the meeting's (「2026년 10월 8일에 열린 … 회의」, minutes-3) becomes 「[회의 일자]」, and a
    deadline cell holding words (「다음 주 금요일」) or a day worked out from nothing becomes
    「[기한]」 with the words and why in 비고."""
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
        owner = _column(table.header, r"담당|책임|owner")
        due = _column(table.header, r"기한|마감|완료일|due")
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
            dated = bool(_cell_dates(when, 0))
            value = "[기한]"
            if repeat := _REPEAT_CELL.search(when):
                # 「매주 월요일」 holds without a start day; one worked out from nothing goes.
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


def minutes_head(block: dict | None, pre: Prepass | None) -> dict | None:
    """The minutes' 일시 left blank: the meeting date code read (the file's name, 「오늘 회의」),
    else 「[회의 일자]」 — never a day guessed from today."""
    if not block or pre is None or "meeting-date" not in pre.settings.head:
        return None
    fields = [list(f) for f in block.get("fields") or []]
    at = next((i for i, f in enumerate(fields) if f and str(f[0]) in ("일시", "회의 일시", "일자")), None)
    if at is None or len(fields[at]) < 2:
        return None
    written = str(fields[at][1] or "").strip()
    if written and not placeholders.is_blank(written):
        return None
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
    """Why this schedule line runs past the RFP's period, or ""."""
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
    """Schedule lines that end after the period the RFP fixes."""
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
                # A picture's data URI reads as 「M6」 to the month pattern; no repair can mend it.
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


# ── 개조식: no prose, noun endings, the two-line 핵심 ───────────────────

#: A line outside a list longer than this reads as a prose paragraph.
GAEJO_PROSE_CHARS = 60
_LIST_LINE = re.compile(r"^\s{0,12}(?:[-*+]\s|\d{1,2}[.)]\s|[□■○◦ㅇ·ㆍ∙-]\s)")
_NOT_PROSE = re.compile(r"^\s{0,4}(?:\||#|※|>|표\s{0,2}[:：]|붙\s{0,2}임|\(?끝\.?\)?$|!\[|<)")
#: Sentence endings; 「…필요」 「…예정」 「…함」 are the noun endings that pass.
_VERB_ENDING = re.compile(r"(?:니다|세요|어요|아요|해요|에요|예요|네요|[가-힣]다)[.!]?$")
_TRAILING = re.compile(r"(?:\s{0,2}\[\d{1,3}(?:[,\s]{1,3}\d{1,3}){0,5}\]|\s{0,2}\([^()\n]{0,60}\)|[*_\s])+$")


def _gaejo_lines(sections: list[dict]):
    """`(section, line)` of the body outside fences, 붙임 and references."""
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
    """Prose paragraphs in the body, and items closed as sentences (「…합니다」) not nouns."""
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
    """Lines under 「핵심」 in the first body section's callout; None when there is none."""
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


# ── minutes: the rows form and the five columns ─────────────────────────

_ACTION_HEADING = re.compile(r"실행|할\s{0,2}일|조치|후속|담당")
#: The value end of a rows item: 「<small>정다은 · 2026-10-23(금)</small>」 or a bare 「정다은 · 10월 23일」.
_ROW_VALUES = re.compile(
    r"(?:<small>\s{0,2}(?P<a>[^<·]{1,20}?)\s{0,2}·\s{0,2}(?P<b>[^<]{1,40}?)\s{0,2}</small>"
    r"|\s(?P<c>[가-힣A-Za-z○]{2,10}(?:\s[가-힣]{2,5})?|미정|확인\s필요|\[[가-힣]{2,6}\])\s{0,2}·\s{0,2}(?P<d>[^·\n]{1,30}?))[\s*]{0,4}$")
_SOURCE_WORD = re.compile(r"회의|메일|출처|자료|전사|녹음|메모")


def _list_row_values(line: str) -> tuple[str, str, str] | None:
    """`(담당, 기한, the item's text before them)` of a rows item, or None."""
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
    # The reason is what follows the item's bold title.
    rest = re.sub(r"^\s{0,12}[-*+]\s{1,3}\*\*[^*\n]{1,120}\*\*", "", text)
    if "미정" in f"{who} {when}" and len(re.sub(r"<[^>]{1,20}>|[*\-\s.]", "", rest)) < 4:
        problems.append("「미정」이면 사유(회의에서 정하지 않음 등)를 앞 문장에 적는다")
    if not _SOURCE_WORD.search(text):
        problems.append("출처(회의 또는 메일)를 앞 문장에 적는다")
    entries = bound_rows(text, bound)
    written = _cell_dates(when, 2026)
    evidence = ""
    if entries and written and not any(date_matches(w, e) for w in written for e in entries):
        shown = ", ".join(dict.fromkeys(e["shown"] for e in entries))
        problems.append(f"기한이 date-calc 해석값({shown})과 다르다")
        evidence = "\n".join(f"「{e['text']}」 → {e['shown']} (출처: {e['source_line'][:120]})"
                             for e in entries[:3])
    return problems, evidence


async def minutes_list_rows(doc: Doc, dates: list[dict]) -> tuple[list[Finding], dict]:
    """Action items written as rows (「- **할 일** 설명 <small>담당 · 기한</small>」): the same
    values a table row must carry."""
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


def minutes_columns(sections: list[dict], dates: list[dict]) -> tuple[list[dict], int]:
    """Action tables given the 출처 and 비고 columns they lack; 출처 is the line the row's date
    was read from (회의, or the mail by its sent date), else 「[출처]」."""
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")]
    out = [dict(s) for s in sections]
    added = 0
    for table in tables(out):
        owner = _column(table.header, r"담당|책임|owner")
        due = _column(table.header, r"기한|마감|완료일|due")
        if owner is None or due is None:
            continue
        lacks_source = _column(table.header, r"출처|근거") is None
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


_WRITTEN_DATES = frozenset({"full_date", "month_day", "named_month_end", "vague", "unclear"})


def _action_hits(action: str, entry: dict) -> int:
    """Words the row's task shares with the line the date was read from (a transcript's line
    before included); a repeat counts its own line only, the one that names the recurring task."""
    own = _SPEAKER.sub("", entry.get("source_line", ""))
    near = own if entry.get("status") == "repeat" else (entry.get("context") or own)
    return len(_words(action) & _words(near))


def _strong_dates(row_text: str, bound: list[dict], action: str | None = None) -> list[dict]:
    """The resolved dates a row is plainly about: two words or more shared with the line and
    the two before it, one of them a word of the row's task, and no other date as close."""
    words = _words(row_text)
    scored = []
    for d in bound:
        # Only what code worked out: a date the material wrote out (「11/2」, 「1월 말」) left
        # as 미정 is the writer's call, and 「오늘」 is the meeting day, not a deadline.
        if d.get("kind") in _WRITTEN_DATES or d.get("date") == d.get("base_date"):
            continue
        # Names and 「회의」 are on every row: 「매주 월요일」 went to 연구비 확인 and 숙소 예약
        # through 한교수 alone (minutes-2).
        if action is not None and not _action_hits(action, d):
            continue
        near = words & _words(d.get("context") or d.get("source_line", ""))
        if len(near) >= 2:
            # The line's own words count twice over the line before it.
            own = _words(_SPEAKER.sub("", d.get("source_line", "")))
            scored.append((len(near) + len(words & own), d))
    if not scored:
        return []
    best = max(n for n, _ in scored)
    top = [d for n, d in scored if n == best]
    # 「다음 주부터 매주 월요일」: the repeat is the deadline, its start date is part of it.
    repeating = {d.get("source_line") for d in top if d.get("status") == "repeat"}
    top = [d for d in top if d.get("status") == "repeat" or d.get("source_line") not in repeating]
    return top if len({_due_value(d) for d in top}) == 1 else []


def _due_value(entry: dict) -> str:
    if entry.get("status") == "repeat":
        every = "격주" if entry.get("repeat") == "biweekly" else "매주"
        return f"{every} {entry['weekday']}요일({entry['first_date']}부터)"
    return str(entry.get("shown") or "")


def _task_column(header: list[str], others: tuple[int | None, ...]) -> int | None:
    """The column that says what is to be done: 「할 일」, else the first one not owner·due·note."""
    found = _column(header, r"할\s{0,2}일|내용|항목|조치|과제|업무|실행")
    if found is not None and found not in others:
        return found
    return next((k for k in range(len(header)) if k not in others), None)


def minutes_due_fill(sections: list[dict], dates: list[dict]) -> tuple[list[dict], int]:
    """Action rows left 「미정」 or 「[기한]」 though date-calc resolved their line get that date, the words
    said moved to 비고: the prose said 10월 21일 while the cell stayed 미정 (minutes-2)."""
    # Read from the meeting's own date only: a line a later header or mail re-dated is left
    # to the writer (minutes-5 read 「다음 주 월요일」 from 「11/13(금)」 as 11-16).
    base = next((d.get("base_date") for d in dates), "")
    bound = [d for d in dates if d.get("status") in ("ok", "repeat")
             and d.get("base_date") == base and d.get("base_kind") in ("meeting", "given")]
    if not bound:
        return sections, 0
    out = [dict(s) for s in sections]
    filled = 0
    for table in tables(out):
        owner = _column(table.header, r"담당|책임|owner")
        due = _column(table.header, r"기한|마감|완료일|due")
        if owner is None or due is None:
            continue
        note = _column(table.header, r"비고|사유|메모|원문")
        task = _task_column(table.header, (owner, due, note))
        content = str(out[table.section].get("content") or "")
        picks = []
        for line, row in table.rows:
            if len(row) <= max(owner, due) or (
                    row[due].strip(" *()") != "미정" and not placeholders.is_blank(row[due])):
                continue
            action = row[task] if task is not None and task < len(row) else ""
            entries = _strong_dates(" ".join(c for k, c in enumerate(row) if k not in (due, note)),
                                    bound, action)
            if entries:
                picks.append((line, row, entries, _action_hits(action, entries[0])))
        # One line's date goes to the row that names its task best, filled or not; rows tie
        # only when the line names several tasks.
        actions = [row[task] if task is not None and task < len(row) else "" for _, row in table.rows]
        for line, row, entries, hits in picks:
            if hits < max(_action_hits(a, entries[0]) for a in actions):
                continue
            row = list(row)
            row[due] = _due_value(entries[0])
            if note is not None and len(row) > note and entries[0]["text"] not in row[note]:
                said = f"원문 「{entries[0]['text']}」"
                row[note] = f"{row[note].strip(' -—')}, {said}" if row[note].strip(" -—") else said
            content = content.replace(line, "| " + " | ".join(row) + " |", 1)
            filled += 1
        out[table.section] = {**out[table.section], "content": content}
    return out, filled


#: A quantity with a unit a booth spec or an action is stated in: 「4m」, 「220V」, 「2개」.
_QUANTITY = re.compile(
    rf"(?<![\d.,A-Za-z])({units._TERM})\s?(km|cm|mm|kg|kW|m|V|W|L|명분|인분|개월|개|명|원|시간|시|대|팀|곳)(?![A-Za-z])"
)
#: Owner words that name no one in particular and so need no material behind them.
_ANY_OWNER = re.compile(
    r"미정|확인\s{0,2}필요|해당\s{0,2}없음|전체|전원|공동|참석자|각자|^[-—–]?$|^\[[가-힣 ·]{1,10}\]$")
_OWNER_SPLIT = re.compile(r"[,·/、→]|\s(?:및|와|과)\s")


_NATIVE = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
           "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
#: 「두 명씩」, 「세 군데」: what a transcript says for 「2명」, 「3곳」. Read on the material
#: side only, where a stray match (「세 대학」) can only excuse, never accuse.
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
        # 「13시」 against 「13:00」.
        return _grounded(value, numbers)
    if any(u == unit and abs(v - value) < 1e-6 * max(1.0, abs(v)) for v, u in given):
        return True
    if unit == "원":
        # A total of the material's own amounts: 「총상금 40만 원(20만 원 × 2팀)」.
        money = [v for v, u in given if u == "원"][:60]
        pool = sorted(set(numbers))[:80]
        return any(abs(a * b - value) < 0.5 for a in money for b in pool) or any(
            abs(a + b - value) < 0.5 for a in money for b in money)
    return False


#: A count of people the writer made by counting the names listed: 「참석 | 5명」.
_HEADCOUNT = re.compile(r"참석|불참|참여|출석|인원")


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
    """Owners and quantities the material does not hold, repaired: an action row's owner who
    is named nowhere becomes 「[담당자]」; a spec row whose sizes and counts are not in
    the material is removed, a cell with one gets 「[수치]」 in its place, and a sentence stating
    one is dropped. Booth sizes 4m×6m and 220V outlets came out of a seven-line memo (minutes-3).

    Returns the sections and one code per repair (「minutes_owner」, 「minutes_spec」)."""
    flat = re.sub(r"\s+", "", material or "")
    numbers = _material_numbers(material)
    given = {(v, u) for v, u, _ in _quantities(_digits(material))}
    out = [dict(s) for s in sections]
    codes: list[str] = []

    def unknown(text: str, around: str = "") -> list[re.Match]:
        headcount = bool(_HEADCOUNT.search(f"{around} {text}"))
        return [m for v, u, m in _quantities(text)
                if not (headcount and u == "명") and not _quantity_known(v, u, given, numbers)]

    for table in tables(out):
        owner = _column(table.header, r"담당|책임|owner")
        due = _column(table.header, r"기한|마감|완료일|due|일시|시점")
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
            if spec_cells and owner is None:
                # A spec row: its value is the made-up number.
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
            kept = [part for part in units.SENTENCE_BREAK.split(line) if not unknown(part)]
            codes.extend(["minutes_spec"] * (len(units.SENTENCE_BREAK.split(line)) - len(kept)))
            lines[k] = " ".join(kept)
        out[index] = {**sec, "content": "\n".join(lines)}
    return out, codes


# ── comparison: must-haves and the recommendation ───────────────────────

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
    """Pipe tables pasted in the material as `{row name: {header: cell}}`, the first column
    naming the option; a table pasted without its `|---|` rule line is read too."""
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
    """The column a must-have's value sits in: most shared words, the unit breaking ties."""
    want = _words(str(must.get("name") or ""))
    unit = str(must.get("unit") or "").strip()
    best, best_score = None, 0
    for h in header:
        score = len(want & _words(h)) * 2 + (1 if unit and f"({unit})" in h.replace(" ", "") else 0)
        if score > best_score:
            best, best_score = h, score
    return best if best_score >= 2 else None


def _table_value(cell: str, must: dict) -> Any:
    """A must-have's value read from a table cell; `None` when the cell gives none."""
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
    """Option must-have values a pasted table states, read by code before the model's
    reading: a column header names the condition, a row names the option
    (comparison-report-2 gave commute minutes 47/52/58 in a table the model left null)."""
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


def must_status(data: dict, numbers: list[float], material: str = "") -> dict | None:
    """Each option 충족 / 탈락(사유) / 조건 미확인 under the must-haves; limits and values the
    request and material do not state are left out. Values a pasted table gives are read
    from it by code (`must_values_from_tables`)."""
    if material:
        data = must_values_from_tables(data, material)
    musts = []
    for must in data.get("musts") or []:
        if not isinstance(must, dict) or not must.get("name"):
            continue
        kind = must.get("kind")
        if kind == "yesno" or (kind in ("max", "min") and _grounded(must.get("limit"), numbers)):
            musts.append(must)
    options = [o for o in data.get("options") or [] if isinstance(o, dict) and o.get("name")]
    if not musts or len(options) < 2:
        return None
    out: dict[str, dict] = {}
    for option in options:
        given = option.get("musts") or {}
        failed, unknown = [], []
        for must in musts:
            raw = given.get(must["name"])
            plain = raw.get("value") if isinstance(raw, dict) else raw
            if must.get("kind") != "yesno" and plain is not None and not _grounded(plain, numbers):
                raw = None
            value = _must_value(raw, must)
            if value is None:
                unknown.append(must["name"])
            elif must["kind"] == "yesno" and value is False:
                failed.append(f"{must['name']} 없음")
            elif must["kind"] in ("max", "min"):
                limit = float(str(must["limit"]).replace(",", ""))
                unit = str(must.get("unit") or "")
                if (value > limit) if must["kind"] == "max" else (value < limit):
                    sign = ">" if must["kind"] == "max" else "<"
                    failed.append(f"{must['name']} {value:,.0f}{unit} {sign} 조건 {limit:,.0f}{unit}")
        status = "탈락" if failed else "조건 미확인" if unknown else "충족"
        out[option["name"]] = {"status": status, "failed": failed, "unknown": unknown}
    return {"musts": [m["name"] for m in musts], "options": out}


def _must_block(result: dict) -> str:
    lines = ["# 필수 조건 판정(코드)",
             "The request's must-have conditions (" + ", ".join(result["musts"]) + ") checked against the "
             "material's values. Each alternative's status follows this result. Do not recommend a 탈락 "
             "alternative; recommend a 조건 미확인 alternative only conditionally, as in 「확인되면 권고」."]
    for option, row in result["options"].items():
        if row["status"] == "탈락":
            lines.append(f"- {option}: 탈락 — " + "; ".join(row["failed"]))
        elif row["status"] == "조건 미확인":
            lines.append(f"- {option}: 조건 미확인 — " + ", ".join(row["unknown"]) + " 자료 없음")
        else:
            lines.append(f"- {option}: 충족")
    if all(r["status"] == "탈락" for r in result["options"].values()):
        lines.append("- Every alternative is 탈락: the conclusion is 「조건을 모두 충족하는 대안 없음」")
    return "\n".join(lines)


_RECOMMEND = re.compile(r"권고|추천|선정|채택|도입\s{0,2}(?:을|를)?\s{0,2}(?:제안|건의)|고른다|골랐")
_DROPPED_WORD = re.compile(r"탈락|제외|미충족|충족하지\s{0,2}못|불가")
_CONDITIONAL = re.compile(r"확인되면|확인\s{0,2}후|확인되는\s{0,2}경우|조건부|전제로|충족되면")
_REASON = re.compile(r"다만|하지만|그러나|비록|낮지만|높지만|때문|이유|감안|고려|불구")
_NONE_MEETS = re.compile(r"충족(?:하는)?\s{0,2}대안(?:이)?\s{0,2}없")


def recommended_option(line: str, options: list[str]) -> str | None:
    """The option a recommendation line picks: the first named outside a 「탈락」 clause."""
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
    """The recommendation against the must-haves and the computed scores."""
    found: list[Finding] = []
    if not musts and not scored:
        return found, {}
    if not doc.sections:
        return found, {}
    # Every part that states the recommendation, sentence by sentence: a summary that lists
    # all three options before 「에듀노트를 권고합니다」 is read for the one it recommends
    # (comparison-report-4 r2 kept the old pick in 요약 and 권고안 both).
    stating = [i for i, s in enumerate(doc.sections)
               if re.search(r"결론|권고|요약|추천|제언", str(s.get("heading") or ""))] or [0]
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


# ── the judge (the larger self-hosted model): one call per document, shown not rewritten ────────

#: Seconds a judge call may take; past it the document goes on unjudged.
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
    """The judge's `items`, or [] when it fails, times out or answers out of shape."""
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
    """The section and the line as written that a judge quoted."""
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
    """A person's 검토 규칙, in words: one judge call quotes the sentences that break them.
    Shown to the person with the rule they break; never rewritten on the judge's word."""
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


# ── structure rules: the shape a 서식 declares, checked by code ──────────────

_SPACE = re.compile(r"\s+")


def _squeezed(text: str) -> str:
    return _SPACE.sub("", str(text or "")).strip("*# ").lower()


def _section_order(sections_wanted: tuple[str, ...]) -> Check:
    """Every section the 서식 lists is there, in its order (shown, not rewritten)."""

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
    """A table with the columns the 서식 requires (under a heading with its words, when
    given): shown when none has them all."""
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
    """The 서식's structure rules checked by code: its sections in order (not when the
    outline is fixed: code set them) and its required tables. The 개조식 style, the summary
    callout and the page limit run as their own check, amendment and stage."""
    structure = process.structure
    out: list[Check] = []
    if structure.sections and not process.fixed_outline:
        out.append(_section_order(tuple(structure.sections)))
    out += [_required_table(t) for t in structure.tables]
    return out


#: The judged checks a pipeline file's `judged` list names.
JUDGED_CHECKS: dict[str, Callable[[Complete, str, str], Check]] = {
    "judge-owners": lambda complete, judge, api_key: judge_owners(complete, judge, api_key),
    "judge-bindings": lambda complete, judge, api_key: judge_bindings(complete, judge, api_key),
    "judge-repeats": lambda complete, judge, api_key: judge_repeats(complete, judge, api_key),
}


def model_checks(pre: Prepass | None, *, complete: Complete | None, judge: str,
                 api_key: str) -> list[Check]:
    """The document's judged checks (E5), as its 진행 과정 설정 names them, then its 검토
    규칙: one judge call each per document, findings shown to the reader and never rewritten
    on the judge's word."""
    if pre is None or complete is None or not judge:
        return []
    out = [JUDGED_CHECKS[name](complete, judge, api_key) for name in pre.settings.judged]
    if pre.settings.rules:
        out.append(judge_rules(pre.settings.rules, complete, judge, api_key))
    return out


# ── the record ──────────────────────────────────────────────────────────

LABELS = {
    "quote_total": "견적 합계가 계산과 다름",
    "minutes_row": "실행 항목 칸(담당·기한)",
    "comparison_blank": "비교표 빈칸",
    "comparison_score": "점수가 계산과 다름",
    "career_number": "자료에 없는 숫자",
    "ungrounded_value": "근거 없는 값 → 빈칸",
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
    "recommend_mismatch": "권고가 필수 조건·점수와 어긋남",
    "status_missing": "탈락·조건 미확인 표시 없음",
    "lead_time_date": "납기 날짜·기한 판정이 계산과 다름",
    "lead_date_missing": "납기를 날짜로 풀지 않음",
    "placeholder_answerable": "자료에 있는 값을 빈칸으로 둠",
    "staff_headcount": "투입 MM을 인원으로 씀",
}
#: The judge's findings (E5): shown for a person to check, not counted as format repairs.
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
