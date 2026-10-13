"""사내 공지·안내 초안이 자료에 없는 날짜·시간·숫자·연락처를 쓰지 않았는지, 꼭 있어야 할 부분이 있는지 점검합니다."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys

WEEKDAYS = "월화수목금토일"

_DATE_FULL = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.)?"
    r"(?:\s*\(\s*([월화수목금토일])(?:요일)?\s*\))?"
)
_DATE_MD = re.compile(
    r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일(?:\s*\(\s*([월화수목금토일])(?:요일)?\s*\))?"
    r"|(?<![\d.])(\d{1,2})\s*[./]\s*(\d{1,2})\.?\s*\(\s*([월화수목금토일])(?:요일)?\s*\)"
)
_TIME = re.compile(
    r"(?:(오전|오후|새벽|저녁|밤)\s*)?(?<!\d)(\d{1,2})\s*(?::\s*(\d{2})|시(?:\s*(\d{1,2})\s*분|\s*반)?)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s)\]>]+|www\.[^\s)\]>]+")
_PHONE = re.compile(r"(?<!\d)(?:0\d{1,2}[-.\s)]\s?\d{3,4}[-.\s]\d{4}|1[5-9]\d{2}[-.\s]\d{4}|01\d{8,9})(?!\d)")
_EXT = re.compile(r"내선\s*(?:번호\s*)?[:：]?\s*(\d{2,5})")
_VERSION = re.compile(r"(?<![\w.])[vV]?(\d+\.\d+(?:\.\d+)*)(?![\w.]*\d)")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(만|천|억)?")
_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{1,30})\](?!\()")
_LIST_MARK = re.compile(r"^\s*(?:\d{1,2}|[가-하])[.)]\s")
_RELATIVE = re.compile(
    r"(?<![가-힣])(?:오늘|내일|모레|어제|금일|명일|익일|이번\s*주|다음\s*주|차주|금주|지난\s*주"
    r"|이번\s*달|다음\s*달|익월|금월|이번\s*분기|다음\s*분기|조만간|곧)(?![가-힣]*\])"
)
_POLITE_YO = re.compile(r"(?:요|죠)[.!?]?\s*$")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

#: 공지가 나가려면 있어야 하는 것 — (key, 이름, 찾는 말, 물을 말, 빈칸).
_PARTS = {
    "change": ("바뀌는 것", r"변경|바뀝|바뀌|달라|시행|도입|폐지|중단|개편|적용|신설|조정|시작|종료|운영|부터|이제|앞으로|새로|새\s",
               "무엇이 어떻게 바뀌나요(전과 후)?", "[변경 내용]"),
    "who": ("대상", r"대상|임직원|구성원|직원|부서|팀|해당자|수신|전원|모든\s*분|여러분|이용자|사용자",
            "누구에게 해당하나요(전 직원·특정 부서·해당자)?", "[대상 부서]"),
    "when": ("시점", None, "언제부터(시행일·일시) 적용되나요?", "[시행일]"),
    "action": ("할 일", r"해야|하십시오|하시기|해\s*주십시오|해\s*주시기|해\s*주세요|바랍니다|부탁|신청|제출|확인해"
               r"|조치\s*(?:는\s*)?(?:없|필요\s*없)|별도\s*(?:조치|신청)\S*\s*(?:없|필요\s*없)|할\s*일",
               "받는 사람이 해야 할 일과 기한이 있나요(없으면 없다고)?", "[할 일·기한]"),
    "contact": ("문의처", r"문의|담당|연락|내선|@|헬프\s*데스크|helpdesk",
                "문의는 어디로 하면 되나요(담당 부서·이름·내선·메일)?", "[문의처]"),
    "impact": ("영향", r"영향|이용\s*(?:불가|할\s*수\s*없|이\s*어렵|중단)|접속\s*(?:불가|할\s*수\s*없|이\s*안|이\s*끊)|중단|멈|지연",
               "점검 중 무엇을 쓸 수 없나요(영향 범위)?", "[영향 범위]"),
}
_KIND_PARTS = {
    "notice": ("change", "who", "when", "action", "contact"),
    "change": ("change", "who", "when", "action", "contact"),
    "maintenance": ("when", "impact", "who", "action", "contact"),
    "general": ("contact",),
    "3p": (),
    "faq": (),
}


def _hour(meridiem: str | None, hour: int) -> list[int]:
    if meridiem in ("오후", "저녁", "밤") and hour < 12:
        return [hour + 12]
    if meridiem in ("오전", "새벽"):
        return [0 if hour == 12 else hour]
    if meridiem is None and 1 <= hour <= 11:
        return [hour, hour + 12]
    return [hour]


def facts(text: str) -> list[dict]:
    """Every checkable fact in `text`: dates, times, contacts, URLs, versions, numbers."""
    out: list[dict] = []
    for index, line in enumerate(text.split("\n")):
        taken: list[tuple[int, int]] = []

        def free(start: int, end: int, taken: list[tuple[int, int]] = taken) -> bool:
            return not any(start < b and a < end for a, b in taken)

        def add(kind: str, match: re.Match, key, span: tuple[int, int] | None = None,
                taken: list[tuple[int, int]] = taken, line: str = line, index: int = index,
                **extra) -> None:
            start, end = span or match.span()
            taken.append((start, end))
            out.append({"kind": kind, "text": line[start:end].strip(), "key": key, "line": index,
                        "start": start, "end": end, **extra})

        for match in _PLACEHOLDER.finditer(line):
            taken.append(match.span())
        for pattern, kind in ((_URL, "url"), (_EMAIL, "email")):
            for match in pattern.finditer(line):
                if free(*match.span()):
                    add(kind, match, match.group(0).rstrip(".,;:").lower())
        for match in _PHONE.finditer(line):
            if free(*match.span()):
                add("phone", match, re.sub(r"\D", "", match.group(0)))
        for match in _EXT.finditer(line):
            if free(*match.span(1)):
                add("phone", match, match.group(1), span=match.span(1))
        for match in _DATE_FULL.finditer(line):
            if free(*match.span()):
                y, m, d = (int(g) for g in match.groups()[:3])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    add("date", match, (y, m, d), weekday=match.group(4))
        for match in _DATE_MD.finditer(line):
            if free(*match.span()):
                m, d, w = (match.group(1), match.group(2), match.group(3)) if match.group(1) else (
                    match.group(4), match.group(5), match.group(6))
                if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
                    add("date", match, (None, int(m), int(d)), weekday=w)
        for match in _VERSION.finditer(line):
            if free(*match.span()) and re.match(r"[vV]|\d+\.\d+\.\d+", match.group(0)):
                add("version", match, match.group(1))
        for match in _TIME.finditer(line):
            if not free(*match.span()):
                continue
            hour = int(match.group(2))
            minute = int(match.group(3) or match.group(4) or (30 if "반" in match.group(0) else 0))
            if hour > 24 or minute > 59 or (match.group(3) is None and "시" not in match.group(0)):
                continue
            # 「3시간」「2시까지」: 시간 길이는 숫자로 본다.
            if line[match.end():match.end() + 1] == "간":
                continue
            hours = [hour] if match.group(3) is not None and match.group(1) is None else _hour(match.group(1), hour)
            add("time", match, sorted({h * 60 + minute for h in hours}))
        for match in _NUMBER.finditer(line):
            if not free(*match.span()):
                continue
            if _LIST_MARK.match(line) and match.start() == len(line) - len(line.lstrip()):
                continue
            whole = match.group(1).replace(",", "")
            value = float(f"{whole}.{match.group(2)}") if match.group(2) else float(whole)
            value *= {"만": 1e4, "천": 1e3, "억": 1e8}.get(match.group(3) or "", 1)
            add("number", match, value, span=(match.start(), match.end(2) if match.group(2) else match.end(1))
                if not match.group(3) else match.span())
    return out


def _grounds(material: str) -> dict:
    found = facts(material)
    keys: dict[str, set] = {"date": set(), "md": set(), "time": set(), "number": set()}
    for fact in found:
        kind, key = fact["kind"], fact["key"]
        if kind == "date":
            keys["date"].add(key)
            keys["md"].add(key[1:])
        elif kind == "time":
            keys["time"].update(key)
        else:
            keys.setdefault(kind, set()).add(key)
        if kind == "date":
            keys["number"].update(float(v) for v in key if v is not None)
        if kind == "version":
            keys["number"].update(float(p) for p in key.split(".")[:2])
    keys["digits"] = {re.sub(r"\D", "", f["text"]) for f in found}
    return keys


def _grounded(fact: dict, ground: dict) -> bool:
    kind, key = fact["kind"], fact["key"]
    if kind == "date":
        y, m, d = key
        if y is None:
            return (m, d) in ground["md"]
        return key in ground["date"] or (None, m, d) in ground["date"]
    if kind == "time":
        return bool(set(key) & ground["time"])
    if kind == "number":
        return key in ground["number"] or re.sub(r"\D", "", fact["text"]) in ground["digits"]
    if kind == "phone":
        return key in ground.get("phone", set()) or any(key and key in d for d in ground["digits"])
    return key in ground.get(kind, set())


def _years_for(month: int, day: int, dated: list[tuple], today: str | None) -> set[int]:
    """Years a year-less 「12월 31일」 may be in: the year written with the same day
    elsewhere, else every year the material or answer names and today's (r5 A14-T2: a
    schedule from 2026-11-02 to 2027-03-31 put 「12월 31일」 in 2027 and turned its (목)
    into (금))."""
    same = {y for y, m, d in dated if y and (m, d) == (month, day)}
    if same:
        return same
    years = {y for y, _, _ in dated if y}
    try:
        if today:
            years.add(dt.date.fromisoformat(today).year)
    except ValueError:
        pass
    return years


_PLACE = {"date": "[날짜]", "time": "[시간]", "email": "[메일]", "phone": "[연락처]", "url": "[링크]",
          "version": "[버전]", "number": "[수치]"}


def _fact_findings(text: str, material: str, today: str | None) -> list[dict]:
    ground = _grounds(material)
    found = facts(text)
    dated = [tuple(f["key"]) for f in [*facts(material), *found] if f["kind"] == "date"]
    findings: list[dict] = []
    for fact in found:
        if fact["kind"] == "date" and fact.get("weekday"):
            y, m, d = fact["key"]
            years = {y} if y else _years_for(m, d, dated, today)
            real: dict[int, str] = {}
            for year in sorted(years):
                try:
                    real[year] = WEEKDAYS[dt.date(year, m, d).weekday()]
                except ValueError:
                    continue
            # wrong in every year it may be in; fixed by code only when that year is certain
            if real and fact["weekday"] not in real.values():
                y = y or max(real)
                findings.append({**fact, "severity": "block", "type": "weekday",
                                 "certain": len(real) == 1,
                                 "why": f"{y}-{m:02d}-{d:02d}은 {real[y]}요일입니다.",
                                 "fix": f"요일을 ({real[y]})로 고치거나 날짜를 확인하세요."})
        if not material.strip() or _grounded(fact, ground):
            continue
        findings.append({**fact, "severity": "block", "type": "unsupported",
                         "why": "자료에 없는 값입니다.",
                         "fix": f"자료의 값으로 바꾸거나 {_PLACE[fact['kind']]} 빈칸으로 두고 물으세요.",
                         "placeholder": _PLACE[fact["kind"]]})
    for index, line in enumerate(text.split("\n")):
        for match in _RELATIVE.finditer(line):
            findings.append({"severity": "check", "type": "relative_date", "text": match.group(0),
                             "line": index, "start": match.start(), "end": match.end(),
                             "why": "받는 사람마다 읽는 날이 달라 날짜가 흔들립니다.",
                             "fix": "날짜(요일)로 쓰세요. 기준일을 모르면 [날짜]."})
    for fact in findings:
        if isinstance(fact.get("key"), (set, tuple)):
            fact["key"] = list(fact["key"])
    return findings


def _has_when(text: str) -> bool:
    if any(f["kind"] in ("date", "time") for f in facts(text)):
        return True
    return bool(re.search(r"\[(?:[^\]]*(?:일|날짜|일시|시간|시행|기간|부터))[^\]]*\]|부터|즉시", text))


def _structure(text: str, kind: str, register: str) -> tuple[list[dict], list[dict]]:
    findings: list[dict] = []
    questions: list[dict] = []
    lines = [line for line in text.split("\n") if line.strip()]
    for part in _KIND_PARTS.get(kind, _KIND_PARTS["notice"]):
        label, pattern, question, placeholder = _PARTS[part]
        present = _has_when(text) if part == "when" else bool(re.search(pattern, text))
        if not present:
            findings.append({"severity": "check", "type": "missing_part", "part": part, "text": label,
                             "why": f"공지에 {label}이(가) 보이지 않습니다.",
                             "fix": f"자료에 있으면 넣고, 없으면 {placeholder}로 두고 물으세요."})
            questions.append({"fact": part, "label": label, "question": question, "placeholder": placeholder})
    if kind in ("notice", "change", "maintenance") and lines:
        first = re.sub(r"^\s*(?:#+|제목\s*[:：]|\*\*)\s*", "", lines[0]).strip("* ")
        limit = 60 if register == "messenger" else 80
        if len(first) > limit:
            findings.append({"severity": "check", "type": "summary", "text": first[:40] + "…", "line": 0,
                             "why": f"첫 줄(요지)이 {len(first)}자입니다. {limit}자 안에 무엇이 언제 바뀌는지만.",
                             "fix": "첫 줄을 한 문장 요지로 줄이세요."})
    if kind == "3p":
        for name, pattern in (("Progress", r"progress|진행|한\s*일|성과"), ("Plans", r"plans?|계획|할\s*일"),
                              ("Problems", r"problems?|문제|이슈|막힌")):
            hit = next((line for line in lines if re.match(rf"\s*\**\s*(?:{pattern})\s*\**\s*[:：]", line, re.I)), None)
            if hit is None:
                findings.append({"severity": "check", "type": "missing_part", "part": name.lower(), "text": name,
                                 "why": f"3P의 {name} 줄이 없습니다.", "fix": f"「{name}: …」 한 줄을 넣으세요."})
            else:
                body = hit.split(":", 1)[-1].split("：", 1)[-1]
                sentences = [s for s in re.split(r"(?<=[.!?다])\s+|(?<=[.!?])$", body.strip()) if s.strip()]
                if len(sentences) > 3:
                    findings.append({"severity": "check", "type": "length", "text": name,
                                     "why": f"{name}이 {len(sentences)}문장입니다(3문장 이내).",
                                     "fix": "가장 중요한 것만 남기세요."})
    if kind == "faq":
        qs = [i for i, line in enumerate(lines) if re.match(r"\s*[-*]?\s*\**\s*(?:Q\d*|질문|문)\s*\**\s*[.:：)]", line, re.I)]
        ans = [i for i, line in enumerate(lines) if re.match(r"\s*[-*]?\s*\**\s*(?:A\d*|답변?|답)\s*\**\s*[.:：)]", line, re.I)]
        if not qs:
            findings.append({"severity": "check", "type": "missing_part", "part": "faq", "text": "Q/A",
                             "why": "「Q:」「A:」 형태의 문답이 보이지 않습니다.", "fix": "질문마다 답을 붙이세요."})
        for n, q in enumerate(qs):
            following = qs[n + 1] if n + 1 < len(qs) else len(lines)
            if not any(q < a < following for a in ans):
                findings.append({"severity": "check", "type": "unanswered", "text": lines[q].strip()[:40],
                                 "why": "답이 없는 질문입니다.",
                                 "fix": "자료에 답이 없으면 「확인 중이며 [날짜]까지 안내」처럼 쓰고 담당을 밝히세요."})
    return findings, questions


def _register(text: str, register: str) -> list[dict]:
    findings: list[dict] = []
    lines = text.rstrip().split("\n")
    if register == "official":
        if not re.search(r"끝\.\s*$", text.rstrip()):
            findings.append({"severity": "check", "type": "register", "text": lines[-1][-20:],
                             "why": "공문은 마지막 줄 끝에 두 칸 띄우고 「끝.」으로 맺습니다.", "fix": "「  끝.」을 붙이세요."})
        for index, line in enumerate(lines):
            if _POLITE_YO.search(line.strip()) and not line.strip().startswith(("Q", "A", "-")):
                findings.append({"severity": "check", "type": "register", "text": line.strip()[-20:], "line": index,
                                 "why": "공문체에 해요체가 섞였습니다.", "fix": "「~합니다/~바랍니다」로 쓰세요."})
                break
    if register == "messenger" and re.search(r"끝\.\s*$", text.rstrip()):
        findings.append({"severity": "info", "type": "register", "text": "끝.",
                         "why": "메신저 공지에는 「끝.」이 어색합니다.", "fix": "지우세요."})
    if register in ("official", "email") and _EMOJI.search(text):
        findings.append({"severity": "check", "type": "register", "text": _EMOJI.search(text).group(0),
                         "why": "공문·메일 공지에는 이모지를 쓰지 않습니다.", "fix": "지우세요."})
    return findings


def check(text: str, material: str = "", kind: str = "notice", register: str = "email",
          today: str | None = None) -> dict:
    findings = _fact_findings(text, material or "", today)
    structure, questions = _structure(text, kind, register)
    findings += structure + _register(text, register)
    placeholders = sorted({f"[{m.group(1)}]" for m in _PLACEHOLDER.finditer(text)})
    for placeholder in placeholders:
        findings.append({"severity": "info", "type": "placeholder", "text": placeholder,
                         "why": "보내기 전에 채울 빈칸입니다.", "fix": "자료가 오면 채우세요."})
    counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("block", "check", "info")}
    return {"findings": findings, "questions": questions, "placeholders": placeholders, "counts": counts,
            "material_checked": bool((material or "").strip())}


#: Facts a notice must not make up: blanked by code wherever the material lacks them.
_BLANKED = ("date", "time", "email", "phone", "url", "version")
_UNIT_AFTER = re.compile(r"\s?(?:%|명|건|개|곳|원|시간|분|초|일|주(?!차)|개월|년|회(?!차)|대|GB|MB|억|만|천)")


def _blank(text: str, fact: dict, placeholder: str) -> str:
    lines = text.split("\n")
    line = lines[fact["line"]]
    if line[fact["start"]:fact["end"]].strip() != fact["text"]:
        at = line.find(fact["text"])
        if at < 0:
            return text
        fact = {**fact, "start": at, "end": at + len(fact["text"])}
    lines[fact["line"]] = line[:fact["start"]] + placeholder + line[fact["end"]:]
    return "\n".join(lines)


def post(text: str, args: dict) -> dict:
    """Unsourced date/time/contact/link/version blanked (code), weekday fixed; numbers to writer."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    material = str(args.get("material") or "")
    if not material.strip():
        return out
    final = bool(args.get("final"))
    today = str(args.get("today") or "") or None
    found = _fact_findings(text, material, today)
    # Right to left on each line, so earlier offsets stay good.
    for fact in sorted(found, key=lambda f: (f.get("line") or 0, f.get("start") or 0), reverse=True):
        kind = fact.get("kind")
        if fact.get("type") == "weekday":
            real = re.search(r"은 (.)요일", fact["why"])
            if not fact.get("certain"):
                out["flags"].append({"kind": "weekday", "text": fact["text"], "why": fact["why"]})
            elif real and fact.get("weekday"):
                fixed_text = re.sub(rf"\(\s*{fact['weekday']}(?:요일)?\s*\)", f"({real.group(1)})",
                                    fact["text"], count=1)
                text = _blank(text, fact, fixed_text)
                out["fixed"].append({"kind": "weekday", "text": fact["text"], "why": fact["why"]})
            continue
        if fact.get("type") != "unsupported":
            if fact.get("type") == "relative_date":
                out["flags"].append({"kind": "relative", "text": fact["text"], "why": fact["why"]})
            continue
        if kind in _BLANKED:
            text = _blank(text, fact, _PLACE[kind])
            out["fixed"].append({"kind": kind, "text": fact["text"],
                                 "why": f"자료에 없는 값 → {_PLACE[kind]}"})
        elif kind == "number":
            line = text.split("\n")[fact["line"]]
            if not _UNIT_AFTER.match(line[fact["end"]:fact["end"] + 4]):
                continue
            if final:
                text = _blank(text, fact, "[수치]")
                out["fixed"].append({"kind": kind, "text": fact["text"], "why": "자료에 없는 수치 → [수치]"})
            else:
                out["rewrite"].append({"kind": kind, "text": fact["text"],
                                       "why": "자료에 없는 수치입니다. 자료의 값으로 바꾸거나 값 뒤에 「(확인 필요)」를 붙이세요."})
    out["text"] = text
    return out


def _line(finding: dict) -> str:
    where = f"{finding['line'] + 1}행 " if isinstance(finding.get("line"), int) else ""
    return f"[{finding['severity']}] {where}{finding.get('text', '')} — {finding['why']} → {finding['fix']}"


def main(argv: list[str]) -> int:
    try:
        args = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"JSON 인자를 읽지 못했습니다: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(args, dict) or not str(args.get("text") or "").strip():
        print(json.dumps({"error": "text(초안)가 필요합니다."}, ensure_ascii=False))
        return 1
    kind = str(args.get("kind") or "notice")
    register = str(args.get("register") or "email")
    if kind not in _KIND_PARTS or register not in ("official", "messenger", "email"):
        print(json.dumps({"error": "kind 는 notice·change·maintenance·3p·faq·general, "
                                   "register 는 official·messenger·email 중 하나입니다."}, ensure_ascii=False))
        return 1
    result = check(str(args["text"]), str(args.get("material") or ""), kind, register, args.get("today"))
    if not result["material_checked"]:
        print("[check] material 이 없어 사실 대조를 하지 않았습니다. 자료 원문을 넘기세요.")
    for finding in result["findings"]:
        print(_line(finding))
    for question in result["questions"]:
        print(f"[질문] {question['question']} (없으면 {question['placeholder']})")
    print(json.dumps(result, ensure_ascii=False, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
