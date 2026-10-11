"""상대 날짜(다음 주 금요일·이번 달 말·2주 뒤)를 기준일로 풀고, 기간·영업일을 셉니다.

인자(JSON 하나), "op" 로 고릅니다.
  {"op": "resolve", "base_date": "2026-10-12", "base_kind": "meeting",
   "expressions": [{"text": "다음 주 금요일", "source_line": "강의계획서 → 이교수, 다음 주 금요일까지"}]}
  {"op": "scan", "text": "<회의 메모나 전사본 전체>", "base_date": "2026-10-12"}
      줄마다 상대·절대 날짜를 찾아 풉니다. 「[메일 — 10/9(금) …]」 줄부터는 그 메일 발신일이 기준입니다.
  {"op": "span", "periods": [{"label": "A사", "start": "2019-04", "end": "2021-01"},
                             {"label": "B사", "start": "2022-04", "end": "현재"}], "as_of": "2026-10"}
      시작·끝 달 포함 개월 수, 겹침을 뺀 합계, 공백 구간.
  {"op": "add", "date": "2026-10-19", "weeks": 8, "deadline": "2026-12-18"}
  {"op": "lead_times", "text": "<자료 전체>"}
      「발주 2026. 10. 19.」와 「발주 후 8주」처럼 기준 날짜와 기간을 짝지어 날짜로 풀고,
      자료가 밝힌 완료 기한 하나와 견줍니다. 회의일이 없어도 됩니다.
  {"op": "business_days", "start": "2026-10-01", "end": "2026-10-31"}
  {"op": "add_business_days", "date": "2026-09-23", "days": 5}
결과는 줄마다 사람이 읽는 한 줄을 출력하고 마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
날짜가 주말·공휴일이면 표시만 하고 옮기지 않습니다. 뜻이 둘 이상인 표현은 "unresolved", 잡음으로
끊긴 표현은 "unclear" 로 돌려주고 날짜를 짐작하지 않습니다.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import sys

WEEKDAYS = "월화수목금토일"

#: 관공서의 공휴일에 관한 규정(2026-04 개정, 노동절·제헌절 추가)과 우주항공청 2026·2027년 월력요항.
HOLIDAYS: dict[str, str] = {
    "2026-01-01": "신정",
    "2026-02-16": "설날 연휴", "2026-02-17": "설날", "2026-02-18": "설날 연휴",
    "2026-03-01": "삼일절", "2026-03-02": "대체공휴일(삼일절)",
    "2026-05-01": "노동절",
    "2026-05-05": "어린이날",
    "2026-05-24": "부처님오신날", "2026-05-25": "대체공휴일(부처님오신날)",
    "2026-06-03": "전국동시지방선거일",
    "2026-06-06": "현충일",
    "2026-07-17": "제헌절",
    "2026-08-15": "광복절", "2026-08-17": "대체공휴일(광복절)",
    "2026-09-24": "추석 연휴", "2026-09-25": "추석", "2026-09-26": "추석 연휴",
    "2026-10-03": "개천절", "2026-10-05": "대체공휴일(개천절)",
    "2026-10-09": "한글날",
    "2026-12-25": "기독탄신일",
    "2027-01-01": "신정",
    "2027-02-06": "설날 연휴", "2027-02-07": "설날", "2027-02-08": "설날 연휴",
    "2027-02-09": "대체공휴일(설날)",
    "2027-03-01": "삼일절",
    "2027-05-01": "노동절", "2027-05-03": "대체공휴일(노동절)",
    "2027-05-05": "어린이날",
    "2027-05-13": "부처님오신날",
    "2027-06-06": "현충일",
    "2027-07-17": "제헌절", "2027-07-19": "대체공휴일(제헌절)",
    "2027-08-15": "광복절", "2027-08-16": "대체공휴일(광복절)",
    "2027-09-14": "추석 연휴", "2027-09-15": "추석", "2027-09-16": "추석 연휴",
    "2027-10-03": "개천절", "2027-10-04": "대체공휴일(개천절)",
    "2027-10-09": "한글날", "2027-10-11": "대체공휴일(한글날)",
    "2027-12-25": "기독탄신일", "2027-12-27": "대체공휴일(기독탄신일)",
}
HOLIDAY_YEARS = (2026, 2027)

_KO_NUMBER = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_KO_DAYS = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "엿새": 6, "이레": 7,
            "열흘": 10, "보름": 15, "일주일": 7}
_DAY_WORDS = {"오늘": 0, "내일": 1, "모레": 2, "내일모레": 2,
              "글피": 3, "어제": -1, "그제": -2, "그저께": -2}
_WEEK_SHIFT = {"지지난": -2, "지난": -1, "저번": -1, "이번": 0, "금": 0, "요번": 0, "이": 0,
               "다음": 1, "담": 1, "차": 1, "다다음": 2}

_N = r"(?:\d{1,3}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("unclear", re.compile(
        r"\((?:잡음|불명확|안\s{0,2}들림|청취\s{0,2}불가|inaudible)\)[^\n,.]{0,12}?까지"
        r"|…\s{0,2}까지|\.\.\.\s{0,2}까지")),
    ("repeat", re.compile(
        r"(?:(?P<from>이번|다음|다다음)\s{0,2}주\s{0,2}부터\s{0,2})?"
        r"(?P<every>매주|격주|매\s{0,2}주|2주\s{0,2}마다|주\s{0,2}1\s{0,2}회)\s{0,2}"
        r"(?P<wd>[월화수목금토일])요일")),
    ("business", re.compile(
        rf"(?:영업일\s{{0,2}}(?:기준\s{{0,2}})?(?P<n1>{_N})\s{{0,2}}일|(?P<n2>{_N})\s{{0,2}}영업일)"
        r"\s{0,2}(?P<dir>뒤|후|이내|안에|내|전)?")),
    ("week_day", re.compile(
        r"(?P<shift>지지난|지난|저번|이번|요번|다다음|다음|담|금|차)\s{0,2}주\s{0,2}"
        r"(?P<wd>[월화수목금토일])요일")),
    ("month_part", re.compile(
        r"(?P<which>이번|이|요번|다음|담|다다음|지난|저번)\s{0,2}(?:달|월)\s{0,2}"
        r"(?P<part>말일?|마지막\s{0,2}날|초|중순|하순|(?P<day>\d{1,2})\s{0,2}일)")),
    ("named_month_end", re.compile(r"(?<![\d.])(?P<month>\d{1,2})\s{0,2}월\s{0,2}(?P<part>말일?|초|중순|하순)")),
    ("month_end", re.compile(r"(?<![가-힣])월말|이달\s{0,2}말|이번\s{0,2}월말")),
    ("quarter", re.compile(r"(?:(?P<which>이번|다음|지난)\s{0,2})?분기\s{0,2}(?P<part>말|초)")),
    ("year_end", re.compile(r"(?P<which>올해|금년|내년|연)\s{0,2}말|연말")),
    ("offset", re.compile(
        rf"(?P<n>{_N})\s{{0,2}}(?P<unit>일|주|주일|개월|달)\s{{0,2}}(?:뒤|후|이내|안에|내|전|지나서)"
        r"|(?P<word>하루|이틀|사흘|나흘|닷새|엿새|이레|열흘|보름|일주일)\s{0,2}(?:뒤|후|이내|안에|내|전)")),
    ("vague", re.compile(
        r"(?:이번|다음|담)\s{0,2}주\s{0,2}(?:까지|중|안|내)|(?:이번|다음)\s{0,2}달\s{0,2}(?:까지|중|안)"
        r"|조만간|곧바로|빠른\s{0,2}시일|추후|나중에|다음\s{0,2}회의\s{0,2}(?:때|에서|까지|전)"
        r"|(?:다음|담)\s{0,2}(?P<wd>[월화수목금토일])요일")),
    ("day_word", re.compile(r"(?<![가-힣])(?P<word>내일모레|오늘|내일|모레|글피|어제|그저께|그제)")),
    ("full_date", re.compile(
        r"(?<!\d)(?P<y>20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(?P<m>\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}"
        r"(?P<d>\d{1,2})(?!\d)\s{0,2}일?")),
    ("month_day", re.compile(
        r"(?<![\d./-])(?P<m>\d{1,2})\s{0,2}월\s{0,2}(?P<d>\d{1,2})\s{0,2}일"
        r"|(?<![\d./:-])(?P<m2>\d{1,2})/(?P<d2>\d{1,2})(?![\d/])(?!\s{0,2}(?:학기|분기|쪽|페이지))")),
    ("weekday", re.compile(r"(?<![가-힣])(?<!일 )(?<!\) )(?P<wd>[월화수목금토일])요일")),
]
#: Where one sentence ends and the next begins: an offset past it no longer leans on the date.
_SENTENCE_END = re.compile(r"[.!?。]\s|다\.|\n|;")
_MAIL_HEAD = re.compile(r"^\s{0,4}[\[(<]?\s{0,4}(?:메일|e-?mail|mail|회신|답장|공지)", re.I)
_MEETING_HEAD = re.compile(r"회의|미팅|일시|녹음|전사|간담회|위원회")


def parse_day(text: str) -> dt.date:
    """「2026-10-12」, 「2026.10.12」, 「2026년 10월 12일」 → date."""
    m = re.search(r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})", str(text))
    if not m:
        raise ValueError(f"날짜를 읽지 못했습니다: {text}")
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _month(text: str) -> tuple[int, int]:
    m = re.search(r"(\d{2,4})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})", str(text))
    if not m:
        raise ValueError(f"연·월을 읽지 못했습니다: {text}")
    year = int(m.group(1))
    if year < 100:
        year += 2000
    month = int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"달이 맞지 않습니다: {text}")
    return year, month


def iso_weekday(day: dt.date) -> str:
    return f"{day.isoformat()}({WEEKDAYS[day.weekday()]})"


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _KO_NUMBER[word]


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _add_months(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _week_day(base: dt.date, weekday: int, shift: int) -> dt.date:
    """Weeks run Monday to Sunday: 「다음 주 금요일」 is Friday of the week after `base`'s."""
    monday = base - dt.timedelta(days=base.weekday())
    return monday + dt.timedelta(weeks=shift, days=weekday)


def _upcoming(base: dt.date, weekday: int) -> dt.date:
    ahead = (weekday - base.weekday()) % 7 or 7
    return base + dt.timedelta(days=ahead)


def _year_for(base: dt.date, month: int, day: int) -> tuple[dt.date, bool]:
    """A month and day with no year: the base year, or the next when that is long past."""
    candidate = dt.date(base.year, month, day)
    if (base - candidate).days > 92:
        return dt.date(base.year + 1, month, day), True
    return candidate, False


def is_holiday(day: dt.date) -> str:
    return HOLIDAYS.get(day.isoformat(), "")


def is_business_day(day: dt.date) -> bool:
    return day.weekday() < 5 and not is_holiday(day)


def add_business_days(day: dt.date, days: int) -> dt.date:
    step = 1 if days >= 0 else -1
    left = abs(days)
    while left:
        day += dt.timedelta(days=step)
        if is_business_day(day):
            left -= 1
    return day


def business_days(start: dt.date, end: dt.date) -> int:
    """Working days from `start` to `end`, both counted."""
    if end < start:
        start, end = end, start
    return sum(is_business_day(start + dt.timedelta(days=i)) for i in range((end - start).days + 1))


def _dated(day: dt.date, **extra) -> dict:
    row = {"status": "ok", "date": day.isoformat(), "weekday": WEEKDAYS[day.weekday()],
           "shown": iso_weekday(day), "weekend": day.weekday() >= 5}
    if holiday := is_holiday(day):
        row["holiday"] = holiday
    if day.year not in HOLIDAY_YEARS:
        row["holidays_unknown"] = True
    row.update(extra)
    return row


def _resolve_match(kind: str, m: re.Match[str], base: dt.date, *, count: int = 4) -> dict:
    g = m.groupdict()
    if kind == "unclear":
        return {"status": "unclear", "reason": "잡음·끊김으로 기한이 들리지 않음"}
    if kind == "vague":
        return {"status": "unresolved", "reason": "날짜 하나로 정해지지 않는 표현"}
    if kind == "repeat":
        weekday = WEEKDAYS.index(g["wd"])
        every = 2 if re.search(r"격주|2주", g["every"]) else 1
        if g.get("from"):
            first = _week_day(base, weekday, _WEEK_SHIFT[g["from"]])
            if first <= base:
                first += dt.timedelta(weeks=1)
        else:
            first = _upcoming(base, weekday)
        dates = [first + dt.timedelta(weeks=every * i) for i in range(max(1, count))]
        return {**_dated(first), "status": "repeat", "repeat": "biweekly" if every == 2 else "weekly",
                "first_date": first.isoformat(), "dates": [d.isoformat() for d in dates]}
    if kind == "business":
        n = _count(g.get("n1") or g.get("n2"))
        return _dated(add_business_days(base, -n if g.get("dir") == "전" else n), business_days=n)
    if kind == "week_day":
        day = _week_day(base, WEEKDAYS.index(g["wd"]), _WEEK_SHIFT[g["shift"]])
        return _dated(day, **({"past": True} if day < base else {}))
    if kind == "month_part":
        shift = {"이번": 0, "이": 0, "요번": 0, "다음": 1, "담": 1, "다다음": 2, "지난": -1, "저번": -1}[g["which"]]
        first = _add_months(base.replace(day=1), shift)
        part = g["part"]
        if g.get("day"):
            day = int(g["day"])
            if day > calendar.monthrange(first.year, first.month)[1]:
                return {"status": "unresolved", "reason": "그 달에 없는 날"}
            return _dated(first.replace(day=day))
        if part.startswith("말") or part.startswith("마지막"):
            return _dated(_month_end(first.year, first.month))
        return {"status": "unresolved", "reason": f"「{part}」는 날짜 하나가 아님"}
    if kind == "named_month_end":
        month = int(g["month"])
        if not 1 <= month <= 12:
            return {"status": "unresolved", "reason": "달이 맞지 않음"}
        if not g["part"].startswith("말"):
            return {"status": "unresolved", "reason": f"「{g['part']}」는 날짜 하나가 아님"}
        start, rolled = _year_for(base, month, 1)
        return _dated(_month_end(start.year, month), **({"year_assumed": True} if rolled else {}))
    if kind == "month_end":
        return _dated(_month_end(base.year, base.month))
    if kind == "quarter":
        shift = {"이번": 0, None: 0, "다음": 1, "지난": -1}[g.get("which")]
        quarter = (base.month - 1) // 3 + shift
        year, quarter = base.year + quarter // 4, quarter % 4
        if g["part"] == "초":
            return {"status": "unresolved", "reason": "「분기 초」는 날짜 하나가 아님"}
        return _dated(_month_end(year, quarter * 3 + 3))
    if kind == "year_end":
        year = base.year + (1 if g.get("which") == "내년" else 0)
        return _dated(dt.date(year, 12, 31))
    if kind == "offset":
        if g.get("word"):
            n, unit = _KO_DAYS[g["word"]], "일"
        else:
            n, unit = _count(g["n"]), g["unit"]
        sign = -1 if m.group(0).rstrip().endswith("전") else 1
        if unit in ("개월", "달"):
            return _dated(_add_months(base, sign * n))
        days = n * 7 if unit.startswith("주") else n
        return _dated(base + dt.timedelta(days=sign * days))
    if kind == "day_word":
        return _dated(base + dt.timedelta(days=_DAY_WORDS[g["word"]]))
    if kind == "full_date":
        try:
            return _dated(dt.date(int(g["y"]), int(g["m"]), int(g["d"])), absolute=True)
        except ValueError:
            return {"status": "unresolved", "reason": "없는 날짜"}
    if kind == "month_day":
        month, day = int(g.get("m") or g.get("m2")), int(g.get("d") or g.get("d2"))
        try:
            found, rolled = _year_for(base, month, day)
        except ValueError:
            return {"status": "unresolved", "reason": "없는 날짜"}
        return _dated(found, absolute=True, **({"year_assumed": True} if rolled else {}))
    if kind == "weekday":
        # A bare weekday is the coming one; said as an assumption.
        return _dated(_upcoming(base, WEEKDAYS.index(g["wd"])), assumed=True)
    raise ValueError(kind)


#: An event a date is given for, and an offset counted from it: 「개강이 2027년 3월 2일」,
#: 「3월 2일 개강」, 「개강 30일 전」, 「학기 개시 30일 전」.
_EVENT = r"[가-힣]{2,6}(?:\s[가-힣]{2,4})?"
_EVENT_BEFORE = re.compile(rf"({_EVENT})\s{{0,2}}(?:일)?(?:이|가|은|는|:|=)?\s{{0,3}}$")
_EVENT_AFTER = re.compile(rf"^\s{{0,2}}(?:\([월화수목금토일]\))?\s{{1,2}}({_EVENT})")
_OFFSET_EVENT = re.compile(rf"({_EVENT})\s{{0,2}}(?:일)?\s{{0,2}}$")
#: What joins two offsets from one day: 「30일 전부터 14일 전까지」, 「30일 ~ 14일 전」.
_CHAIN = re.compile(r"^\s{0,2}(?:부터|에서)?\s{0,2}(?:~|∼|-|,|와|과|및)?\s{0,2}$")
#: Names of one event: a term's start is 개강 whichever way it is said.
_EVENT_ALIASES = {"개학": "개강", "학기개시": "개강", "학기시작": "개강", "학기개강": "개강"}
#: Words before an offset that name no event of their own.
_NOT_EVENT = re.compile(r"^(?:부터|까지|에서|기준|기준일|으로|및|매|각|약|그|이번|다음|지난|오늘|내일|이후|이전)$")


def _event(words: str) -> str:
    """「학기 개시」, 「1학기 개강이」, 「개강일은」 → 「개강」: the event's last word, without its
    particle or 「일」, under its usual name."""
    def bare(word: str) -> str:
        word = re.sub(r"(?<=[가-힣]{2})(?:이|가|은|는)$", "", word)
        return re.sub(r"(?<=[가-힣]{2})일$", "", word)

    parts = [bare(w) for w in words.split()]
    compact = "".join(parts)
    for key, name in _EVENT_ALIASES.items():
        if compact.endswith(key):
            return name
    return _EVENT_ALIASES.get(parts[-1], parts[-1]) if parts else ""


def named_dates(text: str, base: dt.date) -> dict[str, dt.date]:
    """The events `text` dates, each with its one date (an event given two dates names none)."""
    out: dict[str, set[dt.date]] = {}
    for line in (text or "").splitlines():
        for row in find(line, base, named={}):
            if row["kind"] not in ("full_date", "month_day") or row.get("status") != "ok":
                continue
            start = row["at"]
            end = start + len(row["text"])
            head = line[:start]
            cut = max((m.end() for m in _SENTENCE_END.finditer(head)), default=0)
            names = []
            if before := _EVENT_BEFORE.search(head[cut:]):
                names.append(before.group(1))
            if after := _EVENT_AFTER.match(line[end:]):
                names.append(after.group(1))
            for name in map(_event, names):
                if name and not _NOT_EVENT.match(name):
                    out.setdefault(name, set()).add(dt.date.fromisoformat(row["date"]))
    return {name: next(iter(days)) for name, days in out.items() if len(days) == 1}


def find(text: str, base: dt.date, *, absolute: bool = True,
         named: dict[str, dt.date] | None = None,
         anchors: set[str] | None = None) -> list[dict]:
    """Every date expression in `text`, resolved against `base`, left to right.

    An offset counts from the date said before it in its sentence (only a date in `anchors`,
    when given), else from the event it names when `text` (or `named`) dates that event."""
    taken: list[tuple[int, int]] = []
    found: list[dict] = []
    matches: dict[int, tuple[re.Match[str], int]] = {}
    for kind, pattern in _PATTERNS:
        if not absolute and kind in ("full_date", "month_day"):
            continue
        for m in pattern.finditer(text or ""):
            start, end = m.span()
            if any(start < b and a < end for a, b in taken):
                continue
            taken.append((start, end))
            row = {"text": m.group(0).strip(), "at": start, "kind": kind}
            try:
                row.update(_resolve_match(kind, m, base))
            except (ValueError, KeyError) as exc:
                row.update({"status": "unresolved", "reason": str(exc)})
            found.append(row)
            matches[id(row)] = (m, end)
    found.sort(key=lambda r: r["at"])
    # 「개강 2027-03-02, 30일 전부터 14일 전까지」: an offset said right after a date in the same
    # sentence counts from that date, not from the meeting or today.
    anchor: dict | None = None
    chained: tuple[int, dt.date, str] | None = None
    events = named if named is not None else None
    for row in found:
        m, end = matches[id(row)]
        if row["kind"] in ("full_date", "month_day") and row.get("status") == "ok":
            if anchors is None or row["date"] in anchors:
                anchor = {**row, "end": end}
            continue
        if row["kind"] != "offset" or not re.search(r"(?:전|후|뒤)\s{0,2}$", m.group(0)):
            continue
        if anchor is not None and not _SENTENCE_END.search(text[anchor["end"]:row["at"]]):
            day, said = dt.date.fromisoformat(anchor["date"]), anchor["text"]
        elif chained is not None and _CHAIN.match(text[chained[0]:row["at"]]):
            # 「개강 30일 전부터 14일 전까지」: the second offset counts from the same day.
            day, said = chained[1], chained[2]
        else:
            # 「개강 30일 전」: the event's own date, wherever the text gave it.
            before = _OFFSET_EVENT.search(text[max(0, row["at"] - 16):row["at"]])
            if before is None:
                continue
            if events is None:
                events = named_dates(text, base)
            name = _event(before.group(1))
            if _NOT_EVENT.match(name) or name not in events:
                continue
            day, said = events[name], before.group(1).strip()
        kept = {k: v for k, v in row.items() if k in ("text", "at", "kind")}
        row.clear()
        row.update({**kept, **_resolve_match("offset", m, day), "anchor": said})
        chained = (end, day, said)
    return found


def resolve(text: str, base: dt.date) -> dict:
    """One expression: the first date it states, or why it states none."""
    found = find(text, base)
    # A bare weekday beside a fuller expression (「11월 20일 금요일」) only repeats it.
    rows = [r for r in found if r["kind"] != "weekday"] or found
    if not rows:
        return {"text": text, "status": "unresolved", "reason": "날짜 표현을 찾지 못함"}
    row = dict(rows[0])
    row["text"] = text
    row.pop("at", None)
    return row


def _line_base(line: str, base: dt.date | None) -> tuple[dt.date | None, str]:
    """The date a header line sets as the base for what follows, with its kind."""
    stamp = re.search(
        r"(20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}(\d{1,2})"
        r"|(?<![\d./])(\d{1,2})\s{0,2}(?:/|월\s{0,2})(\d{1,2})(?![\d/])", line)
    if not stamp:
        return None, ""
    try:
        if stamp.group(1):
            day = dt.date(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)))
        elif base is not None:
            day, _ = _year_for(base, int(stamp.group(4)), int(stamp.group(5)))
        else:
            return None, ""
    except ValueError:
        return None, ""
    if _MAIL_HEAD.search(line):
        return day, "mail"
    head = line.strip().startswith(("[", "(", "#", "<")) or line.strip()[:12].count(":") == 1
    if _MEETING_HEAD.search(line) and (head or base is None):
        return day, "meeting"
    return None, ""


def scan(text: str, base: dt.date | None = None, *, absolute: bool = True) -> list[dict]:
    """Date expressions line by line, each bound to its line and the base it was read from.

    A meeting header sets the base; a mail header sets the base to its sent date from that
    line on. Without any base, only full dates are read."""
    out: list[dict] = []
    current, kind = base, "meeting" if base else ""
    # Events the whole text dates (「개강은 3월 2일」 in the request, 「학기 개시 30일 전」 in a rule).
    events = named_dates(text, base) if base else {}
    for number, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        stamped, stamp_kind = _line_base(line, current)
        if stamped is not None and (stamp_kind == "mail" or current is None or stamp_kind == "meeting"):
            current, kind = stamped, stamp_kind
        if current is None:
            continue
        for row in find(line, current, absolute=absolute, named=events):
            row.pop("at", None)
            row.update({"source_line": line.strip()[:300], "line": number,
                        "base_date": current.isoformat(), "base_kind": kind or "given"})
            out.append(row)
    return out


def span(periods: list[dict], as_of: str | None = None) -> dict:
    """Months per period (both ends counted), the total with overlaps counted once, the gaps."""
    now = _month(as_of) if as_of else (dt.date.today().year, dt.date.today().month)
    rows, spans = [], []
    for index, period in enumerate(periods, start=1):
        label = str(period.get("label") or f"기간 {index}")
        start = _month(period["start"])
        end_text = str(period.get("end") or "").strip()
        ongoing = not end_text or bool(re.search(r"현재|재직|진행|now|present", end_text, re.I))
        end = now if ongoing else _month(end_text)
        a, b = start[0] * 12 + start[1] - 1, end[0] * 12 + end[1] - 1
        if b < a:
            raise ValueError(f"{label}: 끝이 시작보다 앞섭니다")
        months = b - a + 1
        rows.append({"label": label, "start": f"{start[0]}-{start[1]:02d}",
                     "end": f"{end[0]}-{end[1]:02d}", "ongoing": ongoing, "months": months,
                     "years_months": list(divmod(months, 12)),
                     "counted": period.get("counted", True) is not False})
        if rows[-1]["counted"]:
            spans.append((a, b, label))
    merged: list[list[int]] = []
    overlaps = []
    for a, b, label in sorted(spans):
        if merged and a <= merged[-1][1]:
            overlaps.append({"label": label, "months": min(b, merged[-1][1]) - a + 1})
            merged[-1][1] = max(merged[-1][1], b)
        elif merged and a == merged[-1][1] + 1:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    total = sum(b - a + 1 for a, b in merged)
    gaps = [
        {"start": f"{(merged[i][1] + 1) // 12}-{(merged[i][1] + 1) % 12 + 1:02d}",
         "end": f"{(merged[i + 1][0] - 1) // 12}-{(merged[i + 1][0] - 1) % 12 + 1:02d}",
         "months": merged[i + 1][0] - merged[i][1] - 1}
        for i in range(len(merged) - 1)
    ]
    years, months = divmod(total, 12)
    return {"periods": rows, "total_months": total, "total": [years, months],
            "total_text": f"{years}년 {months}개월" if months else f"{years}년",
            "sum_months": sum(r["months"] for r in rows if r["counted"]),
            "overlaps": overlaps, "gaps": gaps, "as_of": f"{now[0]}-{now[1]:02d}"}


def add(date: str, *, days: int = 0, weeks: int = 0, months: int = 0,
        deadline: str | None = None) -> dict:
    start = parse_day(date)
    day = _add_months(start, int(months)) + dt.timedelta(days=int(days) + 7 * int(weeks))
    row = _dated(day, start=start.isoformat())
    if deadline:
        limit = parse_day(deadline)
        row.update({"deadline": limit.isoformat(), "within": day <= limit,
                    "slack_days": (limit - day).days})
    return row


#: Events a lead time counts from (「발주 후 8주」, 「계약일로부터 30일 이내」).
_ANCHOR = r"발주|계약|착수|착공|개시|접수|체결|통보|기준일"
_FULL = (r"(?P<y>20\d{2})\s{0,2}(?:[-./]|년)\s{0,2}(?P<m>\d{1,2})\s{0,2}(?:[-./]|월)\s{0,2}"
         r"(?P<d>\d{1,2})(?!\d)")
#: An anchor word and the date it is given: 「발주(계약) 예정일: 2026. 10. 19.」.
_ANCHOR_DATE = re.compile(rf"(?:{_ANCHOR})[^\n\d]{{0,30}}?{_FULL}")
_UNIT = r"(?P<u>영업일|주일?|일|개월|달)"
_LEAD = re.compile(
    rf"(?<![가-힣])(?P<a>{_ANCHOR})(?:\s{{0,2}}(?:예정)?일자?)?\s{{0,2}}"
    rf"(?:(?:(?:으)?로부터|부터|후|뒤|이후)\s{{0,2}}(?P<n>\d{{1,3}})\s{{0,2}}{_UNIT}"
    r"(?:\s{0,2}(?:이내|내|안에|후|뒤|만에))?"
    r"|(?P<n2>\d{1,3})\s{0,2}(?P<u2>영업일|주일?|일|개월|달)\s{0,2}(?:후|뒤|이내|내|안에|만에))")
_DEADLINE_LINE = re.compile(r"기한|마감|까지|완료일|납기일|준공일")
_DEADLINE_KIND = re.compile(r"완료|설치|납품|준공|검수|집행|납기|인도")


def _full_dates(line: str) -> list[dt.date]:
    out = []
    for m in re.finditer(_FULL, line):
        try:
            out.append(dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d"))))
        except ValueError:
            continue
    return out


def _lead_label(line: str, start: int, end: int) -> str:
    """What a lead time is for: a table row's first cell, else the words before it (or
    after it, 「계약일로부터 30일 이내 납품」)."""
    text = line.strip()
    if text.startswith("|"):
        cells = [c.strip() for c in text.strip("|").split("|")]
        return next((c for c in cells if c), "")[:60]
    head = re.sub(r"^[\s\-*+□○◦ㅇ·ㆍ∙]{0,6}(?:\d{1,2}[.)]\s{0,2})?", "", line[:start])
    return (head.strip(" :：—-,·") or line[end:].strip(" :：—-,·.")).strip()[:60]


def lead_times(text: str) -> dict:
    """Lead times stated against an anchor date the text gives (「발주 2026. 10. 19.」 and
    「발주 후 8주」) turned into dates, each checked against the one completion deadline
    the text states (「설치·검수 완료 2026. 12. 18.까지」) when there is exactly one.

    Needs no meeting date: the anchor is an absolute date. An anchor word given two dates
    is left unresolved rather than guessed."""
    lines = (text or "").splitlines()
    seen: dict[str, set[dt.date]] = {}
    for line in lines:
        for m in _ANCHOR_DATE.finditer(line):
            try:
                day = dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            except ValueError:
                continue
            for word in re.findall(_ANCHOR, m.group(0)):
                seen.setdefault(word, set()).add(day)
    anchors = {w: next(iter(d)) for w, d in seen.items() if len(d) == 1}
    anchor_days = {d for days in seen.values() for d in days}
    found: dict[dt.date, str] = {}
    for line in lines:
        if _DEADLINE_LINE.search(line):
            for day in _full_dates(line):
                if day not in anchor_days:
                    found.setdefault(day, line.strip()[:300])
    if len(found) > 1:
        found = {d: ln for d, ln in found.items() if _DEADLINE_KIND.search(ln)}
    deadline, deadline_line = (next(iter(found.items())) if len(found) == 1 else (None, ""))
    items = []
    for number, line in enumerate(lines, start=1):
        for m in _LEAD.finditer(line):
            n = int(m.group("n") or m.group("n2"))
            unit = m.group("u") or m.group("u2")
            row = {"label": _lead_label(line, m.start(), m.end()), "text": m.group(0).strip(),
                   "anchor": m.group("a"), "n": n, "unit": unit, "kind": "lead_time",
                   "source_line": line.strip()[:300], "line": number}
            start = anchors.get(m.group("a"))
            if start is None:
                why = "날짜가 둘 이상" if m.group("a") in seen else "날짜가 자료에 없음"
                items.append({**row, "status": "unresolved", "reason": f"{m.group('a')}일 {why}"})
                continue
            if unit == "영업일":
                day = add_business_days(start, n)
            elif unit in ("개월", "달"):
                day = _add_months(start, n)
            else:
                day = start + dt.timedelta(days=n * 7 if unit.startswith("주") else n)
            row.update(_dated(day, start=start.isoformat(), base_date=start.isoformat(),
                              base_kind="anchor"))
            if deadline is not None:
                row.update({"deadline": deadline.isoformat(), "within": day <= deadline,
                            "slack_days": (deadline - day).days})
            items.append(row)
    return {"anchors": {w: d.isoformat() for w, d in anchors.items()},
            "deadline": deadline.isoformat() if deadline else "", "deadline_line": deadline_line,
            "items": items}


def run(options: dict) -> dict:
    op = str(options.get("op") or "resolve")
    if op == "resolve":
        base = parse_day(options["base_date"])
        items = []
        for item in options.get("expressions") or []:
            item = {"text": item} if isinstance(item, str) else dict(item)
            row = resolve(str(item.get("text") or ""), base)
            row.update({k: item[k] for k in ("source_line", "label") if k in item})
            row["base_date"] = base.isoformat()
            row["base_kind"] = str(options.get("base_kind") or "given")
            items.append(row)
        return {"ok": True, "op": op, "items": items}
    if op == "scan":
        base = parse_day(options["base_date"]) if options.get("base_date") else None
        return {"ok": True, "op": op, "items": scan(str(options.get("text") or ""), base)}
    if op == "span":
        return {"ok": True, "op": op, **span(list(options.get("periods") or []), options.get("as_of"))}
    if op == "add":
        return {"ok": True, "op": op, **add(
            str(options["date"]), days=int(options.get("days") or 0),
            weeks=int(options.get("weeks") or 0), months=int(options.get("months") or 0),
            deadline=options.get("deadline"))}
    if op == "lead_times":
        return {"ok": True, "op": op, **lead_times(str(options.get("text") or ""))}
    if op == "business_days":
        start, end = parse_day(options["start"]), parse_day(options["end"])
        return {"ok": True, "op": op, "start": start.isoformat(), "end": end.isoformat(),
                "business_days": business_days(start, end),
                "holidays": [f"{d} {n}" for d, n in HOLIDAYS.items() if start.isoformat() <= d <= end.isoformat()]}
    if op == "add_business_days":
        return {"ok": True, "op": op, **_dated(add_business_days(parse_day(options["date"]), int(options["days"])))}
    raise ValueError(f"모르는 op: {op}")


#: What the person's relative dates resolve to, as a post check compares them with the answer.
_WORKED_KINDS = ("offset", "day_word", "week_day", "business", "month_part", "month_end",
                 "named_month_end", "quarter", "year_end")
_BLANK_AFTER = r"\s{0,2}(?:은|는|이|가|:|=|→)?\s{0,3}(\[[^\]\n]{1,12}\])"


def _redate(written: str, old: dt.date, new: dt.date) -> str:
    """`written` (「2027-02-01」, 「2027년 2월 1일」, 「2월 1일」) saying `new` in the same shape."""
    numbers = list(re.finditer(r"\d+", written))
    values = [new.year, new.month, new.day] if len(numbers) >= 3 else [new.month, new.day]
    out, last = [], 0
    for m, value in zip(numbers, values, strict=False):
        width = len(m.group(0)) if m.group(0).startswith("0") else 0
        out.append(written[last:m.start()] + str(value).zfill(width))
        last = m.end()
    return "".join(out) + written[last:]


def post(text: str, args: dict) -> dict:
    """The answer's dates against the script: a date the person's relative words resolve to
    (「개강 2027-03-02, 30일 전」 → 2027-01-31) that the answer missed by a day or three, or
    left as a blank, is set to the worked-out date with its weekday (code)."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    try:
        today = parse_day(str(args.get("today") or "")) if args.get("today") else dt.date.today()
    except ValueError:
        today = dt.date.today()
    source = "\n".join(str(args.get(k) or "") for k in ("request", "material"))
    scanned = scan(source, today)
    # `anchored_only`: only an offset counted from a date the person gave, never from today.
    worked = [r for r in scanned
              if r.get("status") == "ok" and r.get("date") and r.get("kind") in _WORKED_KINDS
              and (not args.get("anchored_only") or r.get("anchor"))]
    stated = {r["date"] for r in scanned if r.get("kind") in ("full_date", "month_day")
              and r.get("status") == "ok"}
    # The answer's own working from a date the person gave: 「3월 2일에서 30일 전은 …」.
    if stated and text:
        worked += [r for r in find(text, today, anchors=stated, named={})
                   if r["kind"] == "offset" and r.get("anchor") and r.get("status") == "ok"
                   and r["date"] not in stated]
    if not worked or not text:
        return out
    expected = {r["date"]: r for r in worked}
    # A blank left where the person's words had a worked-out date: 「30일 전은 [일자]」.
    for row in worked:
        pattern = re.compile(re.escape(row["text"]) + _BLANK_AFTER)
        match = pattern.search(text)
        if match:
            text = text[:match.start(1)] + row["shown"] + text[match.end(1):]
            out["fixed"].append({"kind": "date", "text": match.group(0),
                                 "why": f"{row['text']} → {row['shown']}"})
    said = [r for r in find(text, today) if r["kind"] in ("full_date", "month_day")
            and r.get("status") == "ok" and r.get("date")]
    present = {r["date"] for r in said}
    for row in sorted(said, key=lambda r: r["at"], reverse=True):
        if row["date"] in expected or row["date"] in stated:
            continue
        day = dt.date.fromisoformat(row["date"])
        near = [d for d in expected if d not in present
                and 1 <= abs((dt.date.fromisoformat(d) - day).days) <= 3]
        if len(near) != 1:
            continue
        new = dt.date.fromisoformat(near[0])
        start, end = row["at"], row["at"] + len(row["text"])
        written = text[start:end]
        fixed = _redate(written, day, new)
        tail = re.match(r"\s{0,2}\(\s{0,2}[월화수목금토일](?:요일)?\s{0,2}\)", text[end:])
        if tail:
            end += tail.end()
            fixed += f"({WEEKDAYS[new.weekday()]})"
        out["fixed"].append({"kind": "date", "text": text[start:end],
                             "why": f"{expected[near[0]]['text']} → {iso_weekday(new)}"})
        text = text[:start] + fixed + text[end:]
        present.add(near[0])
    out["text"] = text
    return out


def _line(row: dict) -> str:
    label = row.get("text") or row.get("label") or ""
    if row.get("status") in ("ok", "repeat"):
        notes = [n for n in (
            "주말" if row.get("weekend") else "", row.get("holiday", ""),
            "가정: 다가오는 요일" if row.get("assumed") else "",
            "연도 가정" if row.get("year_assumed") else "",
            "반복: " + ", ".join(row.get("dates", [])) if row.get("dates") else "") if n]
        return f"{label} → {row['shown']}" + (f" ({'; '.join(notes)})" if notes else "")
    return f"{label} → {row.get('status')}: {row.get('reason', '')}"


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    except (KeyError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    for row in result.get("items") or []:
        print(_line(row))
    for row in result.get("periods") or []:
        print(f"{row['label']}: {row['start']} ~ {row['end']} = {row['months']}개월")
    if "total_months" in result:
        print(f"합계(겹침 제외): {result['total_months']}개월 = {result['total_text']}")
    if result.get("op") in ("add", "add_business_days"):
        print(_line(result))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
