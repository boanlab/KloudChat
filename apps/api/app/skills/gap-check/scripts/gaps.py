"""문서를 쓰기 전에, 그 서식에 꼭 필요한 사실 중 요청·자료에 없거나 모호한 것을 찾아 물을 질문으로 냅니다.

인자(JSON 하나):
  {"op": "check", "facts": "minutes", "request": "<요청과 사용자 답>", "material": "<첨부·붙여 넣은 자료>",
   "computed": {"meeting_date": "2026-10-14", "clients": ["한빛물류(주)"], ...}}
  {"op": "rule", "findings": [<check 결과>]}  → 「자료대로 진행」 때 작성자에게 줄 빈칸 규칙 한 줄
facts 는 서식(doc_format)이나 에이전트 키: minutes·client_proposal·gaejo·comparison·career_history·
career-docs·data-analysis. computed 는 서버의 추출기가 이미 읽은 값(있으면 그것을 믿고, 없으면 글에서 찾음).
찾은 것: {"kind": "gap", "fact", "label", "question", "why", "placeholder", "rule"} — 문서를 바로잡는 데
꼭 필요한 사실만, 최대 4개. 자료가 이미 답한 것은 묻지 않는다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

MAX_QUESTIONS = 4

_DUE = re.compile(
    r"까지|기한|마감|\d{1,2}\s{0,2}[/.]\s{0,2}\d{1,2}|\d{1,2}\s{0,2}월|\d{1,2}\s{0,2}일|다음\s{0,2}주|이번\s{0,2}주|모레|"
    r"내일|오늘|[월화수목금토일]요일|월말|말일|초순|중순|매주|매일|즉시|바로|당일|\d\s{0,2}주\s{0,2}(?:뒤|후|안)")
_UNDECIDED = re.compile(r"누가\s{0,2}\?|담당\s{0,2}(?:미정|없음)|미정|정하지\s{0,2}못|다음\s{0,2}(?:회의|에)")
#: An action line in a memo: 「포스터 → 지민」, 「견적 — 도윤 담당」, 「도윤이 맡기로」.
_ACTION = re.compile(
    r"(?P<task>[^\n→:]{2,30}?)\s{0,2}(?:→|->|=>)\s{0,2}(?P<who>[가-힣]{2,4}|[가-힣]○○)"
    r"(?=\s{0,2}(?:[,(]|$)|(?:이|가|님|씨)?\s{0,2}(?:담당|맡))")


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    label: str
    question: str
    why: str
    placeholder: str
    #: Whether the request and material hold it: `(present, detail)`; the detail, when given,
    #: replaces `why` (what exactly is missing).
    present: Callable[[str, str, dict], tuple[bool, str]]


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


# ── reports to a superior ────────────────────────────────────────────────

_ADDRESSEE = r"[가-힣]{1,10}(?:님|장|처장|과장|팀장|부장|원장|총장|이사|대표)\s{0,2}(?:께|에게|한테)|보고\s{0,2}대상|결재\s{0,2}(?:권자|라인|선)"
_REPORTER = (r"보고자|작성자|기안자|보고\s{0,2}부서|작성\s{0,2}부서|"
             r"[가-힣]{2,10}(?:팀|과|실|처|센터|부)\s{0,2}[가-힣]{2,4}\s{0,2}(?:주무관|주임|대리|과장|팀장|연구원|사원|선생님)")


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


FACTS: dict[str, tuple[Fact, ...]] = {}


def _facts(*rows: Fact) -> tuple[Fact, ...]:
    return rows


_LETTER_COMPANY = Fact("company_role", "지원 회사·직무", "어느 회사의 어떤 직무에 지원하나요?",
                       "문항이 지원 동기·입사 후 계획을 묻는데 회사와 직무가 자료에 없습니다.", "[지원 회사·직무]",
                       _letter_company)
_QUESTION = Fact("question", "자기소개서 문항", "어떤 문항에 답하나요? (문항 원문)",
                 "문항이 없으면 무엇에 답할지 정할 수 없습니다.", "",
                 _has(r"문항|질문|^\s{0,4}\d\s{0,2}[.)]\s|쓰세요|쓰시오|기술하시오|서술|경험을\s{0,2}(?:쓰|적)"))
_COMPANY = Fact("company_role", "지원 회사·직무", "어느 회사의 어떤 직무에 지원하나요?",
                "회사와 직무를 모르면 지원 동기와 경험 연결을 쓸 수 없습니다.", "[지원 회사·직무]", _company_role)
_METRICS = Fact("metrics", "성과 수치", "경험별 성과를 보여 줄 수치가 있으면 알려 주세요(없으면 수치 없이 씁니다).",
                "자료에 수치가 하나도 없습니다. 수치를 지어 넣지 않으므로, 있으면 알려 주셔야 들어갑니다.",
                "[구체적 성과 수치]", _career_metrics)

FACTS.update({
    "minutes": _facts(
        Fact("meeting_date", "회의 일시", "회의를 언제 했나요? (연도 포함)",
             "자료에 회의 날짜가 없습니다. ‘다음 주 금요일’ 같은 기한을 날짜로 풀고 머리의 일시를 채우려면 필요합니다.",
             "[회의 일자]", _meeting_date),
        Fact("attendees", "참석자", "참석자는 누구였나요?",
             "자료에 참석자 목록이 없습니다. 회의록의 참석·불참 칸에 들어갑니다.", "[참석자]", _attendees),
        Fact("action_dues", "실행 항목 기한", "실행 항목의 기한을 정했나요?",
             "담당은 정했는데 기한이 없는 항목이 있습니다.", "[기한]", _action_dues),
    ),
    "client_proposal": _facts(
        Fact("client", "고객사", "제안서를 받을 고객사는 어디인가요?",
             "자료에서 고객사 이름을 찾지 못했습니다. 표지와 본문 수신처에 들어갑니다.", "[고객사]", _client),
        Fact("period", "사업 기간", "사업(과업) 기간은 얼마인가요?",
             "기간이 없으면 일정과 투입 MM, 견적을 맞출 수 없습니다.", "[사업 기간]", _period),
        Fact("staffing", "투입 인력·MM·단가", "투입 인력(역할·인원·MM)과 단가를 알려 주세요.",
             "견적을 코드로 계산하려면 인력·MM·단가(또는 총액)가 필요합니다. 없으면 금액을 빈칸으로 둡니다.",
             "[투입 인력·단가]", _staffing),
        Fact("vat", "부가세 기준", "금액은 부가세 포함인가요, 별도인가요?",
             "부가세 기준이 없으면 예산과 견적을 같은 기준으로 비교할 수 없습니다.", "[부가세 기준]", _vat),
    ),
    "gaejo": _facts(
        Fact("addressee", "보고 대상", "누구에게 올리는 보고인가요? (예: 시설과장님)",
             "보고 대상이 없으면 머리의 보고 대상과 결정 요청의 높이를 정할 수 없습니다.", "[보고 대상]",
             _has(_ADDRESSEE)),
        Fact("reporter", "보고자", "보고자(부서·이름)는 누구인가요?",
             "머리의 보고자(부서) 칸에 들어갑니다.", "[보고자·부서]", _has(_REPORTER)),
    ),
    "comparison": _facts(
        Fact("prices", "대안별 가격", "대안별 가격(요금·견적)을 알려 주세요.",
             "비용으로 고르라고 하셨는데 자료에 금액이 없습니다. 가격을 지어 넣지 않습니다.", "[가격]", _prices),
        Fact("musts", "필수 조건", "꼭 맞아야 하는 조건은 무엇인가요?",
             "필수 조건으로 거르라고 하셨는데 어떤 조건인지 자료에 없습니다.", "[필수 조건]", _musts),
    ),
    "career_history": _facts(
        _COMPANY,
        Fact("periods", "경력별 기간", "회사별 재직 기간(시작·끝 연월)을 알려 주세요.",
             "총 경력은 기간으로 계산합니다. 기간이 없으면 계산하지 않고 빈칸으로 둡니다.", "[기간]",
             _career_periods),
        _METRICS,
    ),
    "career-docs": _facts(
        _QUESTION,
        _LETTER_COMPANY,
        Fact("length_rule", "글자 수 규칙", "문항별 글자 수 제한과 세는 법(공백 포함·바이트)을 알려 주세요.",
             "글자 수 기준이 없으면 분량을 맞출 수 없습니다.", "", _length_rule),
        _METRICS,
    ),
    "data-analysis": _facts(
        Fact("deadline_basis", "기한 기준", "며칠을 넘으면 기한을 넘긴 것으로 볼까요? (예: 접수일부터 14일)",
             "기한 기준이 요청과 자료에 없습니다. 기준에 따라 지연 건수와 비율이 달라집니다.", "[기한 기준]",
             _deadline_basis),
        Fact("period_split", "기간 구분", "비교할 기간을 어느 달로 나눌까요?",
             "기간을 나누는 기준이 자료에 없습니다.", "[기간 기준]", _period_split),
        Fact("denominator", "분모 기준", "비율의 분모에 무응답·빈칸을 넣을까요, 뺄까요?",
             "자료에 무응답·빈칸이 있어 분모에 따라 비율이 달라집니다.", "[분모 기준]", _denominator),
    ),
})


def check(request: str, material: str, computed: dict | None = None, options: dict | None = None) -> list[dict]:
    """The format's facts the request and material do not hold, at most `MAX_QUESTIONS`."""
    options = options or {}
    computed = computed or {}
    out = []
    for fact in FACTS.get(str(options.get("facts") or ""), ()):
        present, detail = fact.present(request or "", material or "", computed)
        if present:
            continue
        out.append({
            "kind": "gap", "fact": fact.id, "label": fact.label, "question": fact.question,
            "why": detail or fact.why, "placeholder": fact.placeholder,
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
                      dict(options.get("computed") or {}), {"facts": options.get("facts")})
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
