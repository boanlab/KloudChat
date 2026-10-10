"""문서를 쓰기 전에, 물어볼 질문 목록 중 요청·자료에 없거나 모호한 것을 찾아 물을 질문으로 냅니다.

질문 목록은 데이터다: 템플릿(template.toml 의 [[questions]])과 에이전트(에이전트 양식의 「빠진 정보 질문」,
기본 제공 에이전트는 agents/*.toml 의 [[questions]])가 들고 있고, 서버가 합쳐서 넘긴다.
질문마다 detector 가 「자료가 이미 답했나」를 정한다:
  {"type": "builtin", "name": "meeting_date"}  — 아래 DETECTORS 의 이름(서버 추출기 값을 믿는 것 포함)
  {"type": "keyword", "words": ["참석"], "when": ["회의"]}  — 낱말 하나라도 있으면 답한 것(when: 요청에 있을 때만 묻기)
  {"type": "regex", "pattern": "...", "when": "..."}  — 요청+자료에서 찾음(대소문자 무시, 줄 단위)
  {"type": "judge"}  — 서버가 판단 모델에 물은 결과(computed["judged"][id])

인자(JSON 하나):
  {"op": "check", "questions": [...], "request": "<요청과 사용자 답>", "material": "<자료>",
   "computed": {"meeting_date": "2026-10-14", "clients": [...], "judged": {"q1": true}, ...}}
  {"op": "rule", "findings": [<check 결과>]}  → 「자료대로 진행」 때 작성자에게 줄 빈칸 규칙 한 줄
찾은 것: {"kind": "gap", "fact", "label", "question", "why", "placeholder"} — 최대 4개. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable

MAX_QUESTIONS = 4

_DUE = re.compile(
    r"까지|기한|마감|\d{1,2}\s{0,2}[/.]\s{0,2}\d{1,2}|\d{1,2}\s{0,2}월|\d{1,2}\s{0,2}일|다음\s{0,2}주|이번\s{0,2}주|모레|"
    r"내일|오늘|[월화수목금토일]요일|월말|말일|초순|중순|매주|매일|즉시|바로|당일|\d\s{0,2}주\s{0,2}(?:뒤|후|안)")
_UNDECIDED = re.compile(r"누가\s{0,2}\?|담당\s{0,2}(?:미정|없음)|미정|정하지\s{0,2}못|다음\s{0,2}(?:회의|에)")
#: An action line in a memo: 「포스터 → 지민」, 「견적 — 도윤 담당」, 「도윤이 맡기로」.
_ACTION = re.compile(
    r"(?P<task>[^\n→:]{2,30}?)\s{0,2}(?:→|->|=>)\s{0,2}(?P<who>[가-힣]{2,4}|[가-힣]○○)"
    r"(?=\s{0,2}(?:[,(]|$)|(?:이|가|님|씨)?\s{0,2}(?:담당|맡))")


def _has(pattern: str) -> Callable[[str, str, dict], tuple[bool, str]]:
    compiled = re.compile(pattern, re.I | re.M)
    return lambda request, material, computed: (bool(compiled.search(f"{request}\n{material}")), "")


# ── minutes ──────────────────────────────────────────────────────────────

_YEARLESS_MEETING_DAY = re.compile(r"(?<!\d)(\d{1,2})\s{0,2}(?:월\s{0,2}|/)(\d{1,2})\s{0,2}일?[^\n]{0,20}(?:회의|미팅|모임)")


def _meeting_date(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if "meeting_date" in computed:
        if computed["meeting_date"]:
            return True, ""
    elif re.search(r"(?<![가-힣])(?:오늘|금일|어제|그제|그저께)\s{1,3}(?:[가-힣]{1,10}\s{1,3}){0,2}(?:회의|미팅|모임)",
                   request) or re.search(r"^#{2,3}\s[^\n]{0,120}(?<!\d)20\d{2}[-_.]?[01]\d[-_.]?[0-3]\d(?!\d)",
                                         material, re.M) or re.search(r"(?:19|20)\d{2}\s{0,2}(?:[-./]|년)\s{0,2}\d{1,2}\s{0,2}(?:[-./]|월)\s{0,2}\d{1,2}[^\n]{0,40}"
                   r"(?:회의|미팅|모임|위원회|간담회)|(?:회의|미팅|모임)[^\n]{0,40}(?:19|20)\d{2}\s{0,2}(?:[-./]|년)",
                   f"{request}\n{material}"):
        return True, ""
    if m := _YEARLESS_MEETING_DAY.search(f"{request}\n{material}"):
        return False, (f"자료에 「{m.group(1)}월 {m.group(2)}일」만 있고 연도가 없습니다. 기한(‘다음 주 금요일’)을 "
                       "날짜로 풀고 회의록 머리에 적으려면 연도까지 필요합니다.")
    return False, ""


#: Who a memo names: 「포스터 → 지민」, 「… 하자 (회장)」, 「수아가 하기로」. The same reading as
#: `doc_formats.named_people`, which writes them into the head's 참석자 field.
_NAMED = (
    re.compile(r"(?:→|->|=>)\s{0,2}([가-힣]{2,4}|[가-힣]○○)(?=\s{0,2}(?:[,(]|$)|(?:이|가|님|씨)?\s{0,2}(?:담당|맡))", re.M),
    re.compile(r"\((회장|부회장|위원장|부위원장|팀장|총무|간사|대표|사회자?|의장)\)\s{0,2}$", re.M),
    re.compile(r"(?<![가-힣])([가-힣]{2,3})(?:이|가)\s{0,2}(?:하기로|맡기로|맡는|담당하기로)"),
)


def named_people(text: str) -> list[str]:
    """Names and roles the memo itself puts on someone, in order of first mention."""
    found: list[tuple[int, str]] = []
    for pattern in _NAMED:
        found += [(m.start(1), m.group(1)) for m in pattern.finditer(text or "")]
    out: list[str] = []
    for _, name in sorted(found):
        if name not in out and name not in ("누가", "누구"):
            out.append(name)
    return out


def _attendees(request: str, material: str, computed: dict) -> tuple[bool, str]:
    text = f"{request}\n{material}"
    if re.search(r"참석|참여자|출석|참가자|attend", text, re.I):
        return True, ""
    speakers = {m.group(1) for m in re.finditer(r"^\s{0,4}([가-힣A-Za-z○]{1,10})\s{0,2}[:：]\s", text, re.M)}
    # People the memo names (speakers, owners) stand in for a list: the head says so.
    return len(speakers) >= 2 or len(named_people(text)) >= 2, ""


def _action_dues(request: str, material: str, computed: dict) -> tuple[bool, str]:
    missing = []
    for line in f"{request}\n{material}".splitlines():
        for m in _ACTION.finditer(line):
            rest = line[m.start():]
            if _DUE.search(rest) or _UNDECIDED.search(rest):
                continue
            missing.append(m.group("task").strip(" -*·•"))
    if not missing:
        return True, ""
    shown = ", ".join(f"「{task}」" for task in missing[:3])
    return False, f"담당은 있는데 기한이 없는 실행 항목: {shown}. 회의에서 정했으면 알려 주세요."


# ── proposals ────────────────────────────────────────────────────────────


def _client(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if "clients" in computed:
        return bool(computed["clients"]), ""
    return _has(r"고객사|발주\s{0,2}기관|발주처|수요\s{0,2}기관|^\s{0,4}\[?\s{0,2}(?:RFP|제안\s{0,2}요청서|미팅\s{0,2}메모)[^\n]{0,20}[—–-]\s{0,2}\S"
                r"|\(주\)|㈜|주식회사")(request, material, computed)


def _period(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if computed.get("period"):
        return True, ""
    return _has(r"(?:사업|용역|수행|계약|과업|교육|운영)\s{0,2}기간|\d{1,2}\s{0,2}개월|\d{1,3}\s{0,2}(?:주|일)\s{0,2}(?:간|과정)"
                r"|(?:19|20)\d{2}\s{0,2}[-./년][^\n]{0,20}[~∼〜]|반당\s{0,2}\d|\d{1,2}\s{0,2}월\s{0,2}(?:중|초|말)")(
        request, material, computed)


def _staffing(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if computed.get("staff") or computed.get("priced"):
        return True, ""
    return _has(r"\d\s{0,2}(?:MM|M/M)|단가|투입\s{0,2}(?:인력|계획)|총액|견적\s{0,2}금액|시간당\s{0,2}[\d,]|"
                r"\d[\d,]{2,}\s{0,2}(?:만\s{0,2})?원")(request, material, computed)


def _vat(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if computed.get("vat"):
        return True, ""
    return _has(r"VAT|부가세|부가가치세")(request, material, computed)


# ── comparisons ──────────────────────────────────────────────────────────


def _prices(request: str, material: str, computed: dict) -> tuple[bool, str]:
    text = f"{request}\n{material}"
    if not re.search(r"비용|가격|요금|총비용|최저|예산|저렴|싼", request):
        return True, ""
    return bool(re.search(r"\d[\d,.]*\s{0,2}(?:천|만|억)?\s{0,2}원|\$\s{0,2}\d|\d\s{0,2}달러|USD", text)), ""


def _musts(request: str, material: str, computed: dict) -> tuple[bool, str]:
    text = f"{request}\n{material}"
    if not re.search(r"필수|반드시|꼭", request):
        return True, ""
    named = re.search(
        r"[가-힣A-Za-z0-9]{2,}\s{0,2}(?:은|는|이|가)?\s{0,2}(?:필수|반드시\s{0,2}(?:있어야|지원|필요|되어야))"
        r"|필수\s{0,2}(?:조건|요건)?\s{0,2}[:：]\s{0,2}\S|꼭\s{0,2}(?:있어야|되어야|필요)|이상이어야|이하여야|넘(?:으면|지)\s{0,2}안", text)
    if named and not re.fullmatch(r"필수\s{0,2}조건", named.group(0).strip()):
        return True, ""
    return False, ""


# ── careers ──────────────────────────────────────────────────────────────


def _career_periods(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if "periods" in computed:
        return bool(computed["periods"]), ""
    return _has(r"(?:19|20)?\d{2}\s{0,2}[.년-]\s{0,2}\d{1,2}\s{0,2}월?\s{0,2}[~∼〜-]|\d+\s{0,2}년\s{0,2}\d*\s{0,2}개월|재직|입사")(
        request, material, computed)


def _career_metrics(request: str, material: str, computed: dict) -> tuple[bool, str]:
    text = f"{request}\n{material}"
    return bool(re.search(r"\d[\d,.]*\s{0,2}(?:%|%p|건|명|원|배|시간|분|초|kg|ms|점|위|개|회|세대)|→", text)), ""


def _length_rule(request: str, material: str, computed: dict) -> tuple[bool, str]:
    text = f"{request}\n{material}"
    if not re.search(r"자기\s{0,2}소개서|자소서|문항", text):
        return True, ""
    return bool(re.search(r"\d[\d,]*\s{0,2}(?:자|바이트|byte)|글자\s{0,2}수\s{0,2}(?:제한|없)", text, re.I)), ""


# ── data analysis ────────────────────────────────────────────────────────


def _deadline_basis(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if not re.search(r"기한|마감|지연|준수율|처리\s{0,2}기간|늦게|SLA", request):
        return True, ""
    return bool(re.search(r"\d+\s{0,2}(?:영업일|일|시간|주)\s{0,2}(?:이내|안|기준|넘|초과|까지|이상)|기한\s{0,2}[:：=]",
                          f"{request}\n{material}")), ""


def _period_split(request: str, material: str, computed: dict) -> tuple[bool, str]:
    m = re.search(r"방학|학기\s{0,2}중|성수기|비수기|시즌|평상시", request)
    if not m:
        return True, ""
    text = f"{request}\n{material}"
    split = re.search(r"\d{1,2}\s{0,2}월?\s{0,2}[~∼〜-]\s{0,2}\d{1,2}\s{0,2}월|\d{1,2}\s{0,2}·\s{0,2}\d{1,2}\s{0,2}월|"
                      rf"{m.group(0)}\s{{0,2}}(?:은|는|=|:|：|\()", text)
    return bool(split), f"「{m.group(0)}」을 어느 달로 볼지 자료에 없습니다. 기준이 다르면 비교 숫자가 달라집니다."


def _denominator(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if not re.search(r"비율|비중|응답률|퍼센트|%|율(?:을|를|이|은|로)?\s", request):
        return True, ""
    if not re.search(r"무응답|미응답|결측|N/?A|빈\s{0,2}칸|미기재|해당\s{0,2}없음", material):
        return True, ""
    return bool(re.search(r"무응답[^\n]{0,15}(?:포함|제외|빼)|분모", request)), ""


def _letter_company(request: str, material: str, computed: dict) -> tuple[bool, str]:
    if not re.search(r"지원\s{0,2}동기|입사\s{0,2}후|귀사|회사에\s{0,2}(?:지원|입사|들어)|직무|포부", f"{request}\n{material}"):
        # A question about one experience needs no company.
        return True, ""
    return _company_role(request, material, computed)


def _company_role(request: str, material: str, computed: dict) -> tuple[bool, str]:
    return _has(r"지원\s{0,2}(?:회사|기업|직무|분야|부문)?\s{0,2}[:：]|채용\s{0,2}공고|공고|모집|지원(?:할|하려|하는|합니다|해)|"
                r"입사\s{0,2}(?:지원|하려)|\(주\)|㈜|주식회사")(request, material, computed)


#: The built-in detectors a question may name (`{"type": "builtin", "name": …}`): each reads
#: the request and material, and trusts what the server's extractors already read.
DETECTORS: dict[str, Callable[[str, str, dict], tuple[bool, str]]] = {
    "meeting_date": _meeting_date,
    "attendees": _attendees,
    "action_dues": _action_dues,
    "client": _client,
    "period": _period,
    "staffing": _staffing,
    "vat": _vat,
    "prices": _prices,
    "musts": _musts,
    "career_periods": _career_periods,
    "career_metrics": _career_metrics,
    "length_rule": _length_rule,
    "deadline_basis": _deadline_basis,
    "period_split": _period_split,
    "denominator": _denominator,
    "letter_company": _letter_company,
}
DETECTOR_TYPES = ("builtin", "keyword", "regex", "judge")


def _words(raw) -> list[str]:
    items = [raw] if isinstance(raw, str) else list(raw or []) if isinstance(raw, list) else []
    return [str(w).strip().lower() for w in items if str(w or "").strip()]


def _found(pattern: str, text: str) -> bool:
    # Patterns are checked bounded when saved (`gap_questions.regex_ok`); never user text here.
    return bool(_compiled(pattern).search(text))


_PATTERNS: dict[str, re.Pattern[str]] = {}


def _compiled(pattern: str) -> re.Pattern[str]:
    if pattern not in _PATTERNS:
        if len(_PATTERNS) > 256:
            _PATTERNS.clear()
        _PATTERNS[pattern] = re.compile(pattern, re.I | re.M)
    return _PATTERNS[pattern]


def present(question: dict, request: str, material: str, computed: dict) -> tuple[bool, str]:
    """Whether the request and material answer the question, by its detector; the detail,
    when given, replaces its `why`. A detector this script cannot run asks nothing."""
    detector = question.get("detector") if isinstance(question.get("detector"), dict) else {}
    kind = str(detector.get("type") or "")
    text = f"{request}\n{material}"
    if kind == "builtin":
        found = DETECTORS.get(str(detector.get("name") or ""))
        return found(request, material, computed) if found else (True, "")
    if kind == "keyword":
        when = _words(detector.get("when"))
        if when and not any(w in request.lower() for w in when):
            return True, ""
        words = _words(detector.get("words"))
        return (not words or any(w in text.lower() for w in words)), ""
    if kind == "regex":
        when = str(detector.get("when") or "")
        if when and not _found(when, request):
            return True, ""
        pattern = str(detector.get("pattern") or "")
        return (not pattern or _found(pattern, text)), ""
    if kind == "judge":
        # The server asked the judge model; unasked (no model), it asks nothing.
        return bool((computed.get("judged") or {}).get(str(question.get("id") or ""), True)), ""
    return True, ""


def check(request: str, material: str, computed: dict | None = None, options: dict | None = None) -> list[dict]:
    """The questions the request and material do not answer, at most `MAX_QUESTIONS`."""
    options = options or {}
    computed = computed or {}
    out = []
    for question in options.get("questions") or ():
        if not isinstance(question, dict) or not question.get("id"):
            continue
        answered, detail = present(question, request or "", material or "", computed)
        if answered:
            continue
        out.append({
            "kind": "gap", "fact": str(question["id"]), "label": str(question.get("label") or ""),
            "question": str(question.get("question") or ""),
            "why": detail or str(question.get("why") or ""),
            "placeholder": str(question.get("placeholder") or ""),
        })
    return out[:MAX_QUESTIONS]


def rule(findings: list[dict]) -> str:
    """The writer's line when the person goes on without them: each fact left as its blank."""
    shown = [f"{f['label']} → {f['placeholder']}" for f in findings or [] if f.get("placeholder")]
    plain = [f["label"] for f in findings or [] if not f.get("placeholder")]
    parts = []
    if shown:
        parts.append("사용자가 더 줄 정보가 없다고 했다. 다음은 지어내지 말고 대괄호 빈칸으로 둔다: " + ", ".join(shown) + ".")
    if plain:
        parts.append(f"{', '.join(plain)}은(는) 자료에 없어 기본 기준으로 하고, 그렇게 했다고 한 줄로 밝힌다.")
    return " ".join(parts)


def run(options: dict) -> dict:
    op = options.get("op") or "check"
    if op == "rule":
        return {"ok": True, "op": op, "rule": rule(list(options.get("findings") or []))}
    if op == "check":
        found = check(str(options.get("request") or ""), str(options.get("material") or ""),
                      dict(options.get("computed") or {}),
                      {"questions": list(options.get("questions") or [])})
        return {"ok": True, "op": op, "findings": found, "rule": rule(found)}
    raise ValueError(f"모르는 op: {op}")


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else json.load(sys.stdin)
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    except (KeyError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    for row in result.get("findings") or []:
        print(f"- {row['label']}: {row['why']}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
