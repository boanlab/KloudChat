"""서비스·기능 종료 일정(공지 → 신규 중단 → 종료 → 데이터 삭제)의 순서와 간격을 재고, 사용자가 정한 최소 고지 기간과 안내문 속 날짜를 맞춰 봅니다.

인자(JSON 하나):
  {"dates": {"announce": "2026-10-15",      # 공지일
             "stop_new": "2026-11-01",      # 신규 가입·구매 중단
             "billing_stop": "2026-11-30",  # 자동 결제 중단(선택)
             "end": "2026-12-31",           # 서비스(기능) 종료
             "export_until": "2027-01-31",  # 데이터 내보내기 마감(선택)
             "delete": "2027-03-31"},       # 데이터 삭제
   "min_gaps": [{"from": "announce", "to": "end", "days": 90, "basis": "이용약관 제12조"}],
   "telecom": "value_added" | "facilities" | null,   # 전기통신사업법상 부가통신·기간통신 사업자의 사업 휴업·폐업이면
   "text": "<안내문 초안(선택)>",
   "today": "2026-10-08"}
점검:
  순서      공지 ≤ 신규 중단 ≤ 자동 결제 중단 ≤ 종료 ≤ 데이터 삭제, 내보내기 마감 ≤ 삭제(block)
  간격      두 날짜 사이 일수, 사용자가 준 최소 간격(min_gaps) 미달(block)
  법정 기간 telecom 을 주면 전기통신사업법 제26조①(부가 30일)·제19조①(기간 60일) 이용자 고지 기간을
            재고(미달 block), 과기정통부 신고 기한을 알려 준다. 해당 여부 자체는 법무 확인
  빈 날짜   없는 날짜는 빈칸과 물을 말로 낸다
  안내문    text 를 주면 일정의 날짜가 안내문에 모두 있는지, 안내문에 일정에 없는 날짜나 틀린 요일이 있는지(block)
날짜를 옮기지 않습니다. 주말은 표시만 합니다. 마지막 줄에 요약 JSON. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys

WEEKDAYS = "월화수목금토일"

#: (key, 이름, 빈칸, 물을 말) — 순서가 곧 일정의 순서다(export_until 은 따로 잰다).
STEPS = (
    ("announce", "공지일", "[공지일]", "언제 공지하나요?"),
    ("stop_new", "신규 가입·구매 중단일", "[신규 중단일]", "신규 가입·구매는 언제부터 막나요?"),
    ("billing_stop", "자동 결제 중단일", "[자동 결제 중단일]", "자동 결제(정기 결제)는 언제 멈추나요?"),
    ("end", "서비스 종료일", "[종료일]", "서비스(기능)는 언제 종료하나요?"),
    ("delete", "데이터 삭제일", "[데이터 삭제일]", "남은 데이터는 언제 삭제하나요?"),
)
OPTIONAL = {"billing_stop"}
EXTRA = {"export_until": ("데이터 내보내기 마감일", "[내보내기 마감일]", "데이터 내보내기는 언제까지 되나요?")}
REQUIRED = ("announce", "end")

#: 법정 이용자 고지 기간 — 출처 원문은 SKILL.md 「법·소비자 보호」.
LEGAL = {
    "value_added": {"days": 30, "basis": "전기통신사업법 제26조제1항(부가통신사업 휴업·폐업 예정일 30일 전까지 이용자 고지)",
                    "report_days": 15, "report_basis": "같은 법 시행령 제33조제1항(예정일 15일 전까지 신고서 제출, 이용자 통보 증명 첨부)"},
    "facilities": {"days": 60, "basis": "전기통신사업법 제19조제1항(기간통신사업 휴업·폐업 예정일 60일 전까지 이용자 고지, 승인 또는 신고)",
                   "report_days": 60, "report_basis": "같은 법 시행령 제24조제1항(승인 신청은 예정일 60일 전까지; "
                                                      "소규모 사업자 신고는 제24조의2제2항, 15일 전까지)"},
}

_DATE_FULL = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.)?"
    r"(?:\s*\(\s*([월화수목금토일])(?:요일)?\s*\))?"
)
_DATE_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일(?:\s*\(\s*([월화수목금토일])(?:요일)?\s*\))?")
_RELATIVE = re.compile(r"(?<![가-힣])(?:다음\s*주|다음\s*달|이번\s*달\s*말|내달|익월|연말|월말|조만간|추후)")


def _parse(value) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _show(day: dt.date) -> str:
    return f"{day.year}. {day.month}. {day.day}.({WEEKDAYS[day.weekday()]})"


def _label(key: str) -> str:
    for step in STEPS:
        if step[0] == key:
            return step[1]
    return EXTRA.get(key, (key,))[0]


def _finding(severity: str, kind: str, text: str, why: str, fix: str, **extra) -> dict:
    return {"severity": severity, "type": kind, "text": text, "why": why, "fix": fix, **extra}


def text_dates(text: str, year_hint: int | None, known: tuple[dt.date, ...] = ()) -> list[dict]:
    """Dates in `text`; a month-day without a year takes the year of the timeline date it names."""
    out: list[dict] = []
    for index, line in enumerate(text.split("\n")):
        taken: list[tuple[int, int]] = []
        for match in _DATE_FULL.finditer(line):
            y, m, d = (int(g) for g in match.groups()[:3])
            try:
                day = dt.date(y, m, d)
            except ValueError:
                continue
            taken.append(match.span())
            out.append({"date": day, "text": match.group(0).strip(), "weekday": match.group(4), "line": index,
                        "year_given": True})
        for match in _DATE_MD.finditer(line):
            if any(a < match.end() and match.start() < b for a, b in taken) or year_hint is None:
                continue
            month, dom = int(match.group(1)), int(match.group(2))
            same = [k for k in known if (k.month, k.day) == (month, dom)]
            try:
                day = same[0] if same else dt.date(year_hint, month, dom)
            except ValueError:
                continue
            out.append({"date": day, "text": match.group(0).strip(), "weekday": match.group(3), "line": index,
                        "year_given": False})
    return out


def check(dates: dict, min_gaps: list[dict] | None = None, telecom: str | None = None,
          text: str | None = None, today: str | None = None) -> dict:
    findings: list[dict] = []
    questions: list[dict] = []
    parsed: dict[str, dt.date] = {}
    for key, value in (dates or {}).items():
        day = _parse(value)
        if value and day is None:
            findings.append(_finding("block", "format", f"{key}={value}", "날짜를 읽지 못했습니다.", "YYYY-MM-DD로 주세요."))
        elif day:
            parsed[key] = day
    for key, label, placeholder, question in STEPS:
        if key not in parsed and key not in OPTIONAL:
            severity = "block" if key in REQUIRED else "check"
            findings.append(_finding(severity, "missing_date", label, f"{label}이 없습니다.",
                                     f"자료에 있으면 넣고, 없으면 {placeholder}로 두고 물으세요."))
            questions.append({"fact": key, "label": label, "question": question, "placeholder": placeholder})
    for key, (label, placeholder, question) in EXTRA.items():
        if key not in parsed:
            questions.append({"fact": key, "label": label, "question": question, "placeholder": placeholder})
    # order
    ordered = [(key, parsed[key]) for key, *_ in STEPS if key in parsed]
    for (a, day_a), (b, day_b) in zip(ordered, ordered[1:], strict=False):
        if day_b < day_a:
            findings.append(_finding("block", "order", f"{_label(a)} {_show(day_a)} → {_label(b)} {_show(day_b)}",
                                     f"{_label(b)}이 {_label(a)}보다 앞섭니다.", "일정을 확인하세요."))
        elif day_b == day_a and {a, b} in ({"announce", "end"}, {"end", "delete"}, {"announce", "stop_new"}):
            findings.append(_finding("check", "same_day", f"{_label(a)} = {_label(b)} {_show(day_a)}",
                                     "같은 날입니다. 이용자가 준비할 시간이 없습니다.", "의도인지 확인하세요."))
    export = parsed.get("export_until")
    if export:
        if "delete" in parsed and export > parsed["delete"]:
            findings.append(_finding("block", "order", f"내보내기 마감 {_show(export)} > 삭제 {_show(parsed['delete'])}",
                                     "삭제한 뒤에는 내보낼 수 없습니다.", "내보내기 마감을 삭제일 이전으로 맞추세요."))
        if "announce" in parsed and export < parsed["announce"]:
            findings.append(_finding("block", "order", f"내보내기 마감 {_show(export)} < 공지 {_show(parsed['announce'])}",
                                     "공지 전에 내보내기가 끝납니다.", "일정을 확인하세요."))
        if "end" in parsed and export < parsed["end"]:
            findings.append(_finding("check", "export_before_end", f"내보내기 마감 {_show(export)} < 종료 {_show(parsed['end'])}",
                                     "서비스가 끝나기 전에 내보내기가 먼저 막힙니다.", "의도인지 확인하세요."))
    elif "end" in parsed and "delete" in parsed and parsed["delete"] <= parsed["end"]:
        findings.append(_finding("check", "no_export_window", "종료일 = 삭제일",
                                 "종료와 함께 데이터가 지워지면 종료 뒤 내보낼 기간이 없습니다.",
                                 "종료 전 내보내기 방법과 마감을 안내하세요."))
    gaps = [{"from": a, "to": b, "days": (day_b - day_a).days} for (a, day_a), (b, day_b) in zip(ordered, ordered[1:], strict=False)]
    # gaps the user set
    for gap in min_gaps or []:
        a, b, need = gap.get("from"), gap.get("to"), gap.get("days")
        if a not in parsed or b not in parsed or not isinstance(need, int):
            findings.append(_finding("check", "gap_unchecked", f"{a}→{b}", "날짜나 일수가 없어 간격을 재지 못했습니다.",
                                     "두 날짜와 days(정수)를 주세요."))
            continue
        have = (parsed[b] - parsed[a]).days
        if have < need:
            # A decision for the person, not a date: a proposed date read as settled ends up
            # in the notice (eol-2).
            findings.append(_finding("block", "gap", f"{_label(a)}→{_label(b)} {have}일 < {need}일",
                                     f"정한 최소 간격({gap.get('basis') or '사용자 기준'})보다 {need - have}일 짧습니다.",
                                     f"{_label(a)}을 앞당길지 {_label(b)}을 늦출지 정해 주세요. "
                                     "정하기 전에는 안내문에 새 날짜를 쓰지 않습니다.", need=need, have=have,
                                     question=f"{_label(a)}과 {_label(b)} 사이가 {need}일에 못 미칩니다. "
                                              f"{_label(a)}을 앞당길까요, {_label(b)}을 늦출까요?"))
    legal: dict | None = None
    if telecom:
        rule = LEGAL.get(telecom)
        if rule is None:
            findings.append(_finding("check", "telecom", telecom, "telecom 은 value_added·facilities 중 하나입니다.", "고치세요."))
        elif "end" in parsed:
            have = (parsed["end"] - parsed["announce"]).days if "announce" in parsed else None
            latest = parsed["end"] - dt.timedelta(days=rule["days"])
            report_by = parsed["end"] - dt.timedelta(days=rule["report_days"])
            legal = {"basis": rule["basis"], "notice_days": rule["days"], "announce_by": latest.isoformat(),
                     "report_by": report_by.isoformat(), "report_basis": rule["report_basis"], "have": have}
            if have is not None and have < rule["days"]:
                findings.append(_finding("block", "legal_gap", f"공지→종료 {have}일 < {rule['days']}일",
                                         f"{rule['basis']}에 {rule['days'] - have}일 못 미칩니다.",
                                         "공지를 앞당길지 종료일을 늦출지 정해 주세요(해당 여부는 법무 확인 필요). "
                                         "정하기 전에는 안내문에 새 날짜를 쓰지 않습니다.",
                                         question="법정 고지 기간에 못 미칩니다. 공지를 앞당길까요, 종료일을 늦출까요?"))
            findings.append(_finding("info", "legal_report", f"신고 기한 {_show(report_by)}",
                                     rule["report_basis"], "해당 사업자인지와 신고 절차는 법무 확인 필요."))
    today_day = _parse(today)
    if today_day and "announce" in parsed and parsed["announce"] < today_day:
        findings.append(_finding("info", "past", f"공지일 {_show(parsed['announce'])}", "공지일이 오늘보다 앞입니다.",
                                 "이미 공지했는지, 간격을 오늘 기준으로 다시 재야 하는지 확인하세요."))
    for key, day in parsed.items():
        if day.weekday() >= 5:
            findings.append(_finding("info", "weekend", f"{_label(key)} {_show(day)}", "주말입니다.",
                                     "고객 문의 대응이 되는 날인지 확인하세요(날짜는 옮기지 않음)."))
    # the draft
    if text:
        years = [d.year for d in parsed.values()]
        hint = max(set(years), key=years.count) if years else (today_day.year if today_day else None)
        found = text_dates(text, hint, tuple(parsed.values()))
        found_days = {item["date"] for item in found}
        for key, day in parsed.items():
            if day not in found_days:
                findings.append(_finding("check" if key == "announce" else "block", "date_missing_in_text", f"{_label(key)} {_show(day)}",
                                         "일정의 날짜가 안내문에 없습니다.", "안내문에 넣으세요."))
        allowed = set(parsed.values())
        for item in found:
            if item["date"] not in allowed:
                findings.append(_finding("block", "date_not_in_timeline", item["text"],
                                         "일정에 없는 날짜입니다.", "일정의 날짜로 고치거나, 일정에 넣을 날짜면 dates 에 추가하세요.",
                                         line=item["line"]))
            if item["weekday"] and item["weekday"] != WEEKDAYS[item["date"].weekday()]:
                findings.append(_finding("block", "weekday", item["text"],
                                         f"{item['date'].isoformat()}은 {WEEKDAYS[item['date'].weekday()]}요일입니다.",
                                         "요일을 고치세요.", line=item["line"]))
        for index, line in enumerate(text.split("\n")):
            for match in _RELATIVE.finditer(line):
                findings.append(_finding("check", "relative_date", match.group(0),
                                         "종료 안내의 날짜는 정확한 날짜(요일)로 씁니다.", "날짜로 바꾸세요.", line=index))
    counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("block", "check", "info")}
    timeline = [{"key": key, "label": _label(key), "date": day.isoformat(), "shown": _show(day)}
                for key, day in sorted(parsed.items(), key=lambda kv: kv[1])]
    return {"timeline": timeline, "gaps": gaps, "legal": legal, "findings": findings, "questions": questions,
            "counts": counts}


# ── the server's post check on a chat answer (`post_check: timeline`) ────

#: Which schedule step a line's date is, by the words around it (first match wins).
_STEP_WORDS = (
    ("delete", re.compile(r"삭제|파기")),
    ("export_until", re.compile(r"내보내기|백업|다운로드")),
    ("billing_stop", re.compile(r"자동\s?결제|정기\s?결제|과금")),
    ("stop_new", re.compile(r"신규\s?(?:가입|구매|판매|신청|계약)")),
    ("announce", re.compile(r"공지|안내일|게시")),
    ("end", re.compile(r"종료|중단|폐지|지원\s?끝")),
)


_MONTH_DAY = (
    re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    re.compile(r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.(?=\s*\(|\s|$)"),
    re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])"),
)
#: A question to the person may name a date it proposes (「11월 30일로 잡을까요?」).
_ASK_HEAD = "확인이 필요한 날짜(자료에 없어 [날짜]로 두었습니다. 언제로 할지 정해 주세요)"
#: Where a deadline the law sets (worked out by code) is in place.
_LEGAL = re.compile(r"신고|법|시행령|제\d{1,3}조|고지\s?기한|통지\s?기한|기한")
_QUESTION = re.compile(r"\?|까요|할지|정해\s?주세요|\(예\s?[:：]")


def _around(line: str) -> str:
    """The words around a line's first blank, as the question names it."""
    flat = re.sub(r"[#*|>]+|\s+", " ", line).strip()
    at = flat.find("[날짜]")
    return flat[max(0, at - 24): at + 20].strip(" -·,")


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)


def _relative(material: str, today: dt.date | None) -> set[dt.date]:
    """Ends the request names relative to today (「다음 달 말」「이번 달 말」「연말」)."""
    if today is None:
        return set()
    out = set()
    if re.search(r"다음\s?달\s?말|내달\s?말|익월\s?말", material):
        out.add(_month_end(today.year + (today.month == 12), today.month % 12 + 1))
    if re.search(r"이번\s?달\s?말|월말", material):
        out.add(_month_end(today.year, today.month))
    if "연말" in material:
        out.add(dt.date(today.year, 12, 31))
    return out


def _known_dates(texts: list[str], hint: int | None) -> set[dt.date]:
    out: set[dt.date] = set()
    for text in texts:
        out |= {item["date"] for item in text_dates(text, hint)}
    return out


def _month_days(texts: list[str]) -> set[tuple[int, int]]:
    """Month-day pairs the texts give, however written (「12. 10.(목)」「12월 10일」「12/10」)."""
    out: set[tuple[int, int]] = set()
    for text in texts:
        for rx in _MONTH_DAY:
            out |= {(int(m.group(1)), int(m.group(2))) for m in rx.finditer(text)}
        for m in _DATE_FULL.finditer(text):
            out.add((int(m.group(2)), int(m.group(3))))
    return out


def schedule(text: str, hint: int | None) -> dict[str, str]:
    """The schedule a notice states: each step's first date, read from the words on its line."""
    out: dict[str, str] = {}
    lines = text.split("\n")
    for item in text_dates(text, hint):
        line = lines[item["line"]]
        for key, rx in _STEP_WORDS:
            if key not in out and rx.search(line):
                out[key] = item["date"].isoformat()
                break
    return out


def post(text: str, args: dict) -> dict:
    """A date the material does not give becomes 「[날짜]」 with a question for the person
    (code), a wrong weekday is corrected, and the schedule the notice states is checked for
    order (listed)."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    material = "\n".join([str(args.get("material") or ""), str(args.get("request") or "")])
    computed = [str(c) for c in args.get("computed") or []]
    today = _parse(args.get("today"))
    years = [d.year for d in _known_dates([material], today.year if today else None)]
    hint = max(set(years), key=years.count) if years else (today.year if today else None)
    known = _known_dates([material], hint) | _relative(material, today)
    # A date only code worked out (a filing deadline) belongs where the law is discussed; as a
    # schedule date it is a decision nobody made (eol-2: 「2027. 1. 1.(금): 종료 공지」).
    worked = _known_dates(computed, hint) - known
    days = _month_days([material])
    if not known and not material.strip():
        return out
    lines = text.split("\n")
    for item in sorted(text_dates(text, hint, tuple(known)), key=lambda i: i["line"], reverse=True):
        line = lines[item["line"]]
        given = item["date"] in known or (
            (item["date"].month, item["date"].day) in days and hint is not None
            and 0 <= item["date"].year - hint <= 1)
        if given or (item["date"] in worked and _LEGAL.search(line)):
            real = WEEKDAYS[item["date"].weekday()]
            if item["weekday"] and item["weekday"] != real:
                fixed = re.sub(r"\(\s*[월화수목금토일](?:요일)?\s*\)", f"({real})", item["text"], count=1)
                lines[item["line"]] = line.replace(item["text"], fixed, 1)
                out["fixed"].append({"kind": "weekday", "text": item["text"], "why": f"{real}요일 → 고침"})
            continue
        if _QUESTION.search(line):
            continue
        lines[item["line"]] = line.replace(item["text"], "[날짜]", 1)
        out["fixed"].append({"kind": "date", "text": item["text"], "why": "자료에 없는 날짜 → [날짜]"})
    text = "\n".join(lines)
    if _ASK_HEAD not in text:
        # Every blank the notice holds (this pass's and a rewrite's), asked once at the end.
        blanks = [_around(line) for line in text.split("\n")
                  if "[날짜]" in line and not _QUESTION.search(line)]
        if blanks:
            text = text.rstrip() + f"\n\n{_ASK_HEAD}:\n" + "\n".join(f"- {w}" for w in blanks)
    stated = schedule(text, hint)
    if len(stated) >= 2:
        for finding in check(stated)["findings"]:
            if finding["type"] in ("order", "same_day", "no_export_window", "export_before_end"):
                out["flags"].append({"kind": finding["type"], "text": finding["text"], "why": finding["why"]})
    for line in text.split("\n"):
        for match in _RELATIVE.finditer(line):
            out["flags"].append({"kind": "relative_date", "text": match.group(0),
                                 "why": "종료 안내의 날짜는 정확한 날짜(요일)로 씁니다."})
    out["text"] = text
    return out


def main(argv: list[str]) -> int:
    try:
        args = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"JSON 인자를 읽지 못했습니다: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(args, dict) or not isinstance(args.get("dates"), dict):
        print(json.dumps({"error": "dates(공지·종료 등 날짜)가 필요합니다."}, ensure_ascii=False))
        return 1
    result = check(args["dates"], args.get("min_gaps"), args.get("telecom"), args.get("text"), args.get("today"))
    for row in result["timeline"]:
        print(f"{row['shown']}\t{row['label']}")
    for gap in result["gaps"]:
        print(f"  {_label(gap['from'])} → {_label(gap['to'])}: {gap['days']}일")
    for finding in result["findings"]:
        print(f"[{finding['severity']}] {finding['text']} — {finding['why']} → {finding['fix']}")
    for question in result["questions"]:
        print(f"[질문] {question['question']} (없으면 {question['placeholder']})")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
