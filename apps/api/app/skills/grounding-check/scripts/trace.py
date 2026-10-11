"""문서의 수치·날짜·시각·이름·기관·직함·제품명이 자료나 계산값에 있는지 대조하고, 없는 값은 빈칸으로 바꿉니다.

인자(JSON 하나):
  {"op": "check", "text": "<문서>", "material": "<요청·자료·사용자 답>",
   "computed": ["<코드가 계산한 값: 확정 수치 블록, run_code 출력, calculate 결과>"],
   "options": {"today": "2026-10-08", "bare": false, "numbers": "all"}}
  {"op": "fix", "text": "<문서>", "findings": [<check 결과>]}
  {"op": "repair", ...check 와 같은 인자...}  → 고친 글과 찾은 것
options: titles=true 는 직함, products=true 는 제품·도구 이름도 대조(경력 문서),
  bare=true 는 단위 없는 숫자(분석 표의 칸)도 대조, numbers="computed" 는 숫자를
  computed 와 요청에서만 찾음(데이터 파일의 원래 칸 값은 분석 결과가 아님). today 는 요청이
  「오늘」이라고 했을 때만 근거가 된다.
자료에 명단(성명 열, 학번 옆 이름)이 있으면 글의 표·목록에 나오는 이름은 그 명단에 있어야 하고,
  명단의 학번 옆이면 그 학번의 이름이어야 한다(옵션 없이 늘 대조).
찾은 것: {"kind", "text", "line", "start", "end", "placeholder"}; kind 는 number·date·month·time·
person·org·title·product·claim(자료에 기술 목록으로만 있는 도구로 일했다는 문장, 문장째 뺌).
결과는 사람이 읽을 요약 줄과 마지막 줄 JSON 으로 낸다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys

# ── numbers as Korean documents write them ────────────────────────────────

#: 「1억 8천만」, 「3,000만」, 「5천」, 「1.2조」, or a bare number (same as the server's units._TERM).
TERM = (
    r"\d[\d,]*(?:\.\d+)?\s?(?:조|억|천만|만|천)(?:\s?\d[\d,]*(?:\.\d+)?\s?(?:억|천만|만|천))*"
    r"|\d[\d,]*(?:\.\d+)?"
)
#: Whitespace that ends a sentence, outside a dotted date or number run (units.SENTENCE_BREAK).
SENTENCE_BREAK = re.compile(
    r"(?<=[.!?。])(?!(?<=\d\.)\s{0,8}[\d(])(?<!\d\.\s\d\.)(?<!\d\.\s\d\d\.)\s+")


def ko_number(raw: str) -> float:
    """「4억 8,000만」 → 480000000; 「1.2조」 → 1.2e12; 「12.4」 → 12.4."""
    text = raw.replace(" ", "")
    text = re.sub(r"(\d[\d,]{0,20}(?:\.\d{1,15})?)천",
                  lambda m: f"{float(m.group(1).replace(',', '')) * 1000:g}", text)
    text = re.sub(r"[^\d.,조억만]", "", text)
    total, rest = 0.0, text
    for unit, scale in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        if unit in rest:
            head, rest = rest.split(unit, 1)
            total += float(head.replace(",", "") or 0) * scale
    if rest.replace(",", ""):
        total += float(rest.replace(",", ""))
    return total


#: Unit words, longest first; a source value must share the unit's family.
UNIT_WORDS = (
    "%p", "%", "퍼센트", "원", "달러", "USD", "명분", "인분", "명", "건", "개월", "개", "가지",
    "종", "년", "주", "일", "시간", "분", "초", "회", "배", "점", "MM", "M/M", "행", "팀", "곳",
    "대", "가구", "세트", "박스", "km", "cm", "mm", "kg", "ms", "m", "V", "kW", "W", "GB", "TB",
    "MB", "L", "권", "줄", "부", "장",
)
FAMILY = {"퍼센트": "%", "USD": "달러", "인분": "명분", "가지": "개", "종": "개", "M/M": "MM"}
_UNIT = "|".join(re.escape(u) for u in UNIT_WORDS)
#: A unit syllable that begins another word: 「62, 주의 45」, 「1일부」, 「3대비」.
_NOT_UNIT = ("(?<=주)[의요]|(?<=일)[부반]|(?<=분)[석야기]|(?<=초)[과기]|(?<=회)[의사]|(?<=대)[비상학]"
             "|(?<=점)검|(?<=부)서|(?<=장)[소비]|(?<=행)사|(?<=팀)장|(?<=종)류|(?<=배)송|(?<=개)[발선]"
             "|(?<=명)단|(?<=건)[의물]|(?<=원)[인장]")
#: A number with an optional unit. Not inside an identifier (「REQ-03」, 「T0161」, 「A100」,
#: 「p95」) nor glued to another number.
NUMBER = re.compile(
    rf"(?<![A-Za-z\d.,_/#-])(?P<sign>[+-]|−|▲|▼)?(?P<num>{TERM})"
    rf"\s?(?P<unit>{_UNIT})?(?![A-Za-z\d])(?!{_NOT_UNIT})"
)
#: After a bare number: order and structure, not a quantity (「2차 회의」, 「3학년」, 「4절」).
ORDINAL_AFTER = re.compile(
    r"\s?(?:차(?!이)|학년|학기|번째|번(?!역)|위(?!원|험|치|해)|단계|층|동(?![안시일])|호실|호(?!텔|응|출)"
    r"|주차|교시|쪽|페이지|절|조(?!원|건|사|정|직|치|합)|항(?!목|공)|기(?!간|업|관|준|능|존|여)"
    r"|라운드|레벨|등급|순위|안(?=[\s,)·]|$)|부(?=[\s.]|$))"
)
LIST_MARKER = re.compile(r"^\s{0,8}(?:[-*+]\s{1,4})?(?:\(?\d{1,2}[.)]|[①-⑳])\s{1,4}")
#: Spans holding no value of their own: blanks, citations, links, code, numbered labels.
MASKED = re.compile(
    r"\[[^\[\]\n]{0,40}\](?:\([^)\n]{0,300}\))?"
    r"|`[^`\n]{0,200}`"
    r"|https?://\S{1,300}"
    r"|[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}"
    r"|(?:표|그림|붙임|별첨|부록|첨부|문항|질문|제|Q|사례|안건|단계|STEP|Step)\s?\d{1,3}(?:[.-]\d{1,3})?"
    r"|\d{2,4}-\d{3,4}-\d{4}"
)

# ── dates and times ───────────────────────────────────────────────────────

_WD = "월화수목금토일"
FULL_DAY = re.compile(
    r"(?<![\d.])(?P<y>(?:19|20)\d{2})\s{0,2}(?:년|[-./])\s{0,2}(?P<m>\d{1,2})\s{0,2}(?:월|[-./])\s{0,2}"
    rf"(?P<d>\d{{1,2}})(?!\d)(?!\s{{0,2}}(?:호|차|층|명|개|건|번|회|동|학년|분기|주|시간|년))"
    rf"\s{{0,2}}(?:일|\.)?(?:\s{{0,2}}\([{_WD}]\))?"
)
#: 「20261014」 in a file's name.
COMPACT_DAY = re.compile(r"(?<!\d)(?P<y>20\d{2})(?P<m>[01]\d)(?P<d>[0-3]\d)(?!\d)")
MONTH_DAY = re.compile(
    rf"(?<![\d.])(?P<m>\d{{1,2}})\s{{0,2}}월\s{{0,2}}(?P<d>\d{{1,2}})\s{{0,2}}일(?:\s{{0,2}}\([{_WD}]\))?")
#: 「10/9(금)」, 「10. 9.(금)」: a short day, read only with its weekday or 「까지」 after it.
SHORT_DAY = re.compile(
    rf"(?<![\d./])(?P<m>\d{{1,2}})\s{{0,1}}[/.]\s{{0,1}}(?P<d>\d{{1,2}})\.?(?=\s{{0,2}}(?:\([{_WD}]\)|까지))")
#: 「2026년 5월」, 「2026.05」. After 「년」 the month needs its 「월」: 「2022년 5건」 is a count.
YEAR_MONTH = re.compile(
    r"(?<![\d.])(?P<y>(?:19|20)\d{2})\s{0,2}(?:년\s{0,2}(?=\d{1,2}\s{0,2}월)|[.-]\s{0,2})(?P<m>\d{1,2})"
    r"(?:\s{0,2}월|\.)?(?![\d-]|\s{0,2}\d|\s{0,2}[A-Za-z]|\s{0,2}(?:건|명|개|편|회|권|곳|차|위|점|%))")
#: A span of months in the material: 「2026. 12. ~ 2027. 5.」, 「2026.1~3」, 「2019.03 ~ 2022.08」.
MONTH_RANGE = re.compile(
    r"(?P<y1>(?:19|20)\d{2})\s{0,2}(?:년|[.-])\s{0,2}(?P<m1>\d{1,2})(?:\s{0,2}월|\.)?(?:\s{0,2}\d{1,2}\s{0,2}(?:일|\.))?"
    r"\s{0,2}[~∼〜-]\s{0,2}(?:(?P<y2>(?:19|20)\d{2})\s{0,2}(?:년|[.-])\s{0,2})?(?P<m2>\d{1,2})(?!\d|,\d)")
#: 「11월 초」, 「11월까지」: a month given as a time, with no day.
VAGUE_MONTH = re.compile(r"(?<![\d.])(?P<m>\d{1,2})\s{0,2}월\s{0,2}(?P<part>초|중순|하순|말|중|안|내|까지|부터)")
TIME = re.compile(
    r"(?P<half>오전|오후|저녁|밤|새벽|아침)?\s{0,2}(?<![\d:])(?P<h>\d{1,2})\s{0,2}"
    r"(?:시(?!간|각|점|작|장|기|도|청|민|세|설|스|험|행|즌|트|키|켜)(?:\s{0,2}(?P<min>\d{1,2})\s{0,2}분)?"
    r"|:(?P<mm>[0-5]\d)(?!\d))"
)

# ── names ─────────────────────────────────────────────────────────────────

HONORIFIC = (
    r"교수(?:님)?|박사|팀장|파트장|과장|부장|차장|실장|본부장|센터장|처장|원장|학과장|대리|사원|주임|"
    r"주무관|조교|선생님|연구원|위원장|회장|부회장|총무|대표|이사|매니저|간사|님|씨"
)
#: A person with a title or honorific: 「박준영 대리」, 「배교수」, 「류○○」, 「지민 님」.
PERSON = re.compile(
    rf"(?<![가-힣A-Za-z○])(?P<name>[가-힣]{{1,3}}○○|[가-힣]{{2,4}}(?=\s(?:{HONORIFIC})(?![가-힣]))"
    rf"|[가-힣]{{1,3}}(?=(?:{HONORIFIC})(?![가-힣])))\s?(?P<title>{HONORIFIC})?"
)
#: Words before a title that are not a name: 「지도교수」, 「담당 과장」, 「각 팀장」.
NOT_A_NAME = re.compile(
    r"^(?:지도|담당|해당|각|전|현|신임|소속|학과|부서|외부|내부|관련|책임|주관|공동|초빙|겸임|명예|객원|전임|"
    r"부|정|수석|선임|선배|후배|동료|팀|우리|저희|상대|고객|고객사|발주처|기관|회사|센터|대학|본|이|그|저|"
    r"모든|여러|두|세|네|한|다른|같은|일반|참석|불참|참여|위원|상담|영양사|사무|행정|시설|보건|교무|학생|교수|"
    r"연구|개발|사업|프로젝트|운영|인사|총무|기획|재무|회계|영업|마케팅|품질|안전|보안|전산|정보|홍보|대외|"
    r"원어민|강사|외국인|신입|경력|정규|계약|인턴|파트|주문|결제|정산|검색|데이터|분석|백엔드|프론트|조원|"
    r"총|부총|사|과|처|국|실|단|원|청|시|도|군|구|동|면|읍|리|및|또는|겸|대행|직무|업무|임시|차기|역대|"
    r"당시|현재|이후|이전|최초|최종|공학|인문|자연|사회|경영|의과|법학|교육|예술|체육|명|분|여러분|고객님|회원|사용자|이용자|담당자|관리자)$"
)
#: The end of a word that is not a name: 「참석한 교수」, 「맡은 대리」.
VERB_END = re.compile(r"(?:한|된|는|은|던|할|의|를|을|에|로|와|과|도|만|인)$")
ORG = re.compile(
    r"\(주\)\s?(?P<a>[가-힣A-Za-z][가-힣A-Za-z0-9]{1,15})"
    r"|(?<![가-힣A-Za-z0-9])(?P<b>[가-힣A-Za-z][가-힣A-Za-z0-9]{1,15})(?:\(주\)|㈜)"
    r"|(?:주식회사|㈜)\s?(?P<c>[가-힣A-Za-z][가-힣A-Za-z0-9]{1,15})"
    r"|(?<![가-힣])(?P<d>[가-힣]{2,10}(?:대학교|은행|병원|재단|협회|공사|공단|그룹))(?![가-힣])"
    r"|(?P<e>[A-Z][A-Za-z0-9&]{1,20}\s(?:Inc|Corp|Ltd|LLC|Co)\.?)(?![A-Za-z])"
)
#: A particle after a name: 「(주)라온페이에서는」 is 「라온페이」.
PARTICLE = re.compile(r"(?:로서는|로서|으로서|에서는|에서의|에서|으로|에게|께서|이라|라는|와의|과의|는|은|이|가|을|를|의|와|과|로|에|도|만)$")
#: Job titles a career document must not invent (the server's cover_letter list).
TITLE = re.compile(
    r"팀장|파트장|파트\s{0,2}리드|본부장|센터장|실장|부장|차장|과장(?![하된되])|대리|수석|책임(?![지져질감을이은의])|"
    r"선임|매니저|대표(?![적하로])|위원장|회장|조장|(?<![A-Za-z])(?:PM|PL|CTO|CEO|리더(?!십))(?![A-Za-z])"
)
#: A product, tool or service in Latin letters: 「Tableau」, 「AWS」, 「Elasticsearch」.
LATIN = re.compile(
    r"(?<![A-Za-z0-9_./@#-])(?P<w>[A-Z][A-Za-z0-9+#]{1,30}(?:[.-][A-Za-z0-9]{1,10}){0,2})(?![A-Za-z0-9_/@-])")
#: Latin words any document may use without a source.
COMMON_LATIN = frozenset(w.lower() for w in (
    "AI", "API", "PDF", "CSV", "URL", "KPI", "ROI", "MM", "VAT", "PM", "PL", "PoC", "QA", "IT", "HR",
    "UI", "UX", "FAQ", "SaaS", "SLA", "ERP", "CRM", "OCR", "STT", "TTS", "LLM", "GPU", "CPU", "DB",
    "OK", "NO", "YES", "Y", "N", "ID", "PC", "SNS", "MOU", "RFP", "TF", "TFT", "CEO", "CTO", "CFO",
    "B2B", "B2C", "IoT", "Excel", "Word", "PowerPoint", "HWP", "HWPX", "DOCX", "PPTX", "XLSX", "MSA",
    "ISMS", "ISMS-P", "VPN", "SSO", "MFA", "DR", "RTO", "RPO", "TCO", "OPEX", "CAPEX", "Q1", "Q2",
    "Q3", "Q4", "H1", "H2", "AM", "KST", "UTC", "GPT", "NLP", "ML", "DX", "ESG", "CS", "VOC", "OJT",
    "MVP", "SW", "HW", "NA", "TBD", "A", "B", "C", "D", "E", "S", "M", "L", "XL", "SQL", "HTML",
    "CSS", "JSON", "XML", "REST", "HTTP", "HTTPS", "Wi-Fi", "WiFi", "LAN", "WAN", "QR", "PoC", "MSA",
    "Note", "Summary", "Total", "Yes", "No", "TOP", "Top", "Best", "Plan", "Before", "After",
))
#: A skill list in the material: 「기술: Java, Kotlin, AWS」.
SKILL_LINE = re.compile(
    r"^\s{0,8}[-*•]?\s{0,4}(?:보유\s{0,2})?(?:기술(?:\s{0,2}스택)?|스킬|사용\s{0,2}기술|역량|도구|툴|skills?|"
    r"tech(?:\s?stack)?)\s{0,2}[:：]", re.I)
#: The person saying they did the work: 「AWS 위에서 배포·운영을 수행했습니다」.
DID_WORK = re.compile(
    r"(?:수행|운영|설계|구축|배포|개발|도입|이관|적용|구현|활용|사용|담당|관리)\s{0,2}(?:했|하였|해\s{0,2}왔|하며|하여|해\s)")

PLACEHOLDER = {
    "number": "[수치]", "date": "[일자]", "month": "[시기]", "time": "[시각]", "person": "[이름]",
    "org": "[기관명]", "title": "[직함]", "product": "[제품명]", "claim": "",
}
KIND_WORDS = {
    "number": "수치", "date": "날짜", "month": "시기", "time": "시각", "person": "이름",
    "org": "기관·회사", "title": "직함", "product": "제품·도구", "claim": "자료에 없는 경력 문장",
}

# ── what the sources hold ─────────────────────────────────────────────────

_NATIVE = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5,
           "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_NATIVE_COUNT = re.compile(
    r"(?<![가-힣\d.,])(하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉|열)\s?(명|개|곳|군데|대|팀|시간|시|건|가지|번|주|달)(?![가-힣])")


def _native_digits(text: str) -> str:
    """「두 명」 → 「2명」: a count as people say it (source side only, where it can only excuse)."""
    word = {"군데": "곳", "번": "회", "달": "개월"}
    return _NATIVE_COUNT.sub(lambda m: f"{_NATIVE[m.group(1)]}{word.get(m.group(2), m.group(2))}", text)


def _family(unit: str | None) -> str:
    unit = (unit or "").strip()
    return FAMILY.get(unit, unit)


def _decimals(raw: str) -> int:
    digits = re.sub(r"[^\d.]", "", re.split(r"[조억만천]", raw)[0])
    return len(digits.split(".", 1)[1]) if "." in digits else 0


def near(value: float, known: float, places: int, known_places: int | None = None) -> bool:
    """`value` as written to `places` decimals is `known` rounded the way people round. A
    printed source (`known_places`) was itself rounded; a computed one (None) may be a step off
    in the last place shown (3.6134 − 3.6496 written −0.036)."""
    value, known = abs(value), abs(known)
    if abs(value - known) <= 1e-9 * max(1.0, known):
        return True
    if known_places is not None and known_places <= places:
        return False
    step = 10.0 ** -places
    # A computed whole number may be cut rather than rounded (42,120 ÷ 22 written 1,914).
    slack = 0.5 * step + (0.5 * 10.0 ** -known_places if known_places is not None
                          else (0.5 * step if places or value >= 100 else 0.0))
    return abs(value - known) <= slack + 1e-9 * max(1.0, known)


def arith(pool: list[tuple[float, str]], family: str) -> list[float]:
    """What plain arithmetic of numbers written together gives, for a value in `family`: a
    count or a length is a sum or a difference of its own kind (or a product with a plain
    number: 「8시간 × 2일」); a percent is any share or change; an amount is a sum, a gap, a
    price times a count, or a total over a count. Multiplying by 1 proves nothing."""
    pool = sorted({(abs(v), f) for v, f in pool if v and abs(v) != 1})[:60]
    out: set[float] = set()
    percent, money = family in ("%", "%p"), family in ("원", "달러")
    own = [v for v, f in pool if not family or not f or f == family]
    if len(own) >= 2:
        out.add(sum(own))
    for i, (a, fa) in enumerate(pool):
        for b, fb in pool[i + 1:]:
            same = not family or (fa in (family, "") and fb in (family, ""))
            mine = not family or family in (fa, fb)
            if percent or not family:
                out.update((b / a * 100, (b - a) / a * 100, a / b * 100, (b - a) / b * 100))
                if percent and fa in ("%", "") and fb in ("%", ""):
                    out.add(b - a)
            if same and not percent:
                out.update((a + b, b - a))
            if not percent and (mine or money) and (not family or fa != fb or not fa):
                out.update((a * b, b / a, a / b))
    return sorted(out)


def _hour(m: re.Match) -> int:
    hour = int(m.group("h"))
    if m.group("half") in ("오후", "저녁", "밤") and hour < 12:
        hour += 12
    return hour


_HEADER_UNIT = re.compile(
    r"\(\s{0,2}(?:단위\s{0,2}[:：]?\s{0,2})?(?P<u>천만\s?원|백만\s?원|억\s?원|만\s?원|천\s?원|원|명|건|개월|MM|M/M|%p|%|"
    r"시간|일|주|개|회|점|대|곳|팀|행|달러)\s{0,2}\)|(?P<w>인원|인력\s{0,2}수|금액|단가|비용|예산|가격|MM|M/M)\s{0,2}$")
_HEADER_WORD = {"인원": "명", "인력수": "명", "금액": "원", "단가": "원", "비용": "원", "예산": "원", "가격": "원",
                "M/M": "MM"}
_SCALE = {"천만원": 1e7, "백만원": 1e6, "억원": 1e8, "만원": 1e4, "천원": 1e3}


def column_units(lines: list[str]) -> dict[int, list[tuple[str, float]]]:
    """For each table row, the unit (family, scale) its header gives each column: a bare 「3.0」
    under 「투입(MM)」 is MM, not people; 「4,850」 under 「(만 원)」 is 48,500,000."""
    out: dict[int, list[tuple[str, float]]] = {}
    header: list[tuple[str, float]] | None = None
    for k, line in enumerate(lines):
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        if header is None:
            header = []
            for cell in cells:
                m = _HEADER_UNIT.search(cell.replace("*", ""))
                word = (m.group("u") or m.group("w")).replace(" ", "") if m else ""
                unit = _HEADER_WORD.get(word, word)
                header.append(("원", _SCALE[unit]) if unit in _SCALE else (_family(unit), 1.0))
            continue
        out[k] = header
    return out


def _column_of(line: str, start: int) -> int:
    return line[:start].count("|") - (1 if line.lstrip().startswith("|") else 0)


def _table_columns(text: str) -> list[float]:
    """Each column total of the material's own tables: 「합계 45,760명」 is the material's."""
    out: list[float] = []
    block: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                block.append(cells)
            continue
        if len(block) >= 3:
            for k in range(max(len(r) for r in block)):
                values = []
                for row in block[1:]:
                    cell = row[k] if k < len(row) else ""
                    if re.fullmatch(rf"\s*(?:{TERM})\s*", cell):
                        values.append(ko_number(cell))
                if len(values) >= 2:
                    out.append(sum(values))
        block = []
    return out


#: Calendar constants a document converts with: a year is 12 months, 52 weeks, 365 days.
_CONSTANT = {"개월": {12, 24, 36}, "주": {52}, "일": {7, 365}, "시간": {24}, "분": {60}}


class Sources:
    """What the output may say: the request, the material, the person's answers and what code
    worked out, read once."""

    def __init__(self, material: str, computed: list[str] | str | None = None, *,
                 today: dt.date | None = None, numbers: str = "all") -> None:
        computed_text = computed if isinstance(computed, str) else "\n".join(computed or [])
        text = f"{material or ''}\n{computed_text}"
        if today and re.search(r"(?<![가-힣])(?:오늘|금일)(?!까지)", material or ""):
            text += f"\n{today.isoformat()}"
        self.text = text
        self.lower = re.sub(r"\s+", "", text).lower()
        self.days: set[tuple[int | None, int, int]] = set()
        self.year_months: set[tuple[int, int]] = set()
        self.months: set[int] = set()
        self.hours: set[int] = set()
        for pattern in (FULL_DAY, COMPACT_DAY):
            for m in pattern.finditer(text):
                self._day(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        for m in MONTH_DAY.finditer(text):
            self._day(None, int(m.group("m")), int(m.group("d")))
        for m in re.finditer(r"(?<![\d./])(\d{1,2})\s{0,1}[/.]\s{0,1}(\d{1,2})(?![\d/])", text):
            self._day(None, int(m.group(1)), int(m.group(2)))
        for m in YEAR_MONTH.finditer(text):
            if 1 <= int(m.group("m")) <= 12:
                self.year_months.add((int(m.group("y")), int(m.group("m"))))
                self.months.add(int(m.group("m")))
        for m in MONTH_RANGE.finditer(text):
            y1, m1, m2 = int(m.group("y1")), int(m.group("m1")), int(m.group("m2"))
            y2 = int(m.group("y2") or y1)
            if 1 <= m1 <= 12 and 1 <= m2 <= 12 and 0 <= (y2 * 12 + m2) - (y1 * 12 + m1) <= 60:
                # Every month the span holds: a plan's months inside the stated period.
                for n in range(y1 * 12 + m1 - 1, y2 * 12 + m2):
                    self.year_months.add((n // 12, n % 12 + 1))
                    self.months.add(n % 12 + 1)
        # 「21.10~22.3」, 「22년 4월」, 「25.1부터」: a two-digit year with its month.
        for m in re.finditer(r"(?<![\d.])(\d{2})\s{0,2}(?:년\s{0,2}|\.)(\d{1,2})(?:\s{0,2}월)?(?![\d.])", text):
            if 1 <= int(m.group(2)) <= 12:
                self.year_months.add((2000 + int(m.group(1)), int(m.group(2))))
                self.months.add(int(m.group(2)))
        self.years = {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)}
        self.years |= {2000 + int(y) for y in re.findall(r"(?<![\d.])(\d{2})\s{0,2}년", text)}
        for m in re.finditer(r"(?<![\d.])(\d{1,2})\s{0,2}월", text):
            if 1 <= int(m.group(1)) <= 12:
                self.months.add(int(m.group(1)))
        for m in TIME.finditer(text):
            self.hours.update((_hour(m) % 24, int(m.group("h")) % 24))
        number_text = text if numbers == "all" else f"{_said_by_person(material)}\n{computed_text}"
        number_text = _native_digits(number_text)
        self.values: list[tuple[float, str, int]] = []
        rows = number_text.splitlines()
        heads = column_units(rows)
        for k, line in enumerate(rows):
            line = FULL_DAY.sub(lambda m: " " * len(m.group(0)), LIST_MARKER.sub(lambda m: " " * len(m.group(0)), line))
            for m in NUMBER.finditer(line):
                try:
                    value, unit, scale = ko_number(m.group("num")), _family(m.group("unit")), 1.0
                except ValueError:
                    continue
                if not unit and k in heads and 0 <= (col := _column_of(line, m.start())) < len(heads[k]):
                    unit, scale = heads[k][col]
                self.values.append((value * scale, unit, _decimals(m.group("num"))))
        pool = [v for v, _, _ in self.values]
        self.derived = _table_columns(number_text)
        figures = sorted({abs(v) for v in pool if abs(v) >= 10})
        if numbers == "all" and len(figures) <= 25:
            # A short material: the sums, gaps and percent changes of its own figures are its
            # too (a long one would excuse almost any number this way).
            self.derived += [x for i, a in enumerate(figures) for b in figures[i + 1:]
                             for x in (a + b, b - a, (b - a) / a * 100, a / b * 100, b / a)]
        if numbers == "all":
            # A price or a distance times a count: 「20만 원 × 2팀」, 「15,000km × 5대」.
            counts = sorted({abs(v) for v in pool if 1 < abs(v) < 100 and v == int(v)})
            self.derived += [a * c for a in figures for c in counts if a >= 1000]
        #: An attached data file's rows, each as (the cells that name it, its values): a value
        #: counts from a row only on a line that also names that row (「행 9 · 32201533 · 22시간」),
        #: so a mean cannot pass as a cell it happens to equal. And a rate the person gave times
        #: a computed value (「12시간 × 11,000원」) is the tool's result priced, not new arithmetic.
        #: Both only where numbers must come from the tools; otherwise the rows are material.
        self.rows: list[tuple[list[tuple[str, ...]], set[float]]] = []
        self.priced: set[float] = set()
        if numbers != "all":
            self.rows = _row_keys(_data_rows(material))
            rates = {abs(v) for v in self._person_values(material) if abs(v) >= 100}
            self.priced = {round(r * abs(x), 6) for r in rates for x in pool if x}
        #: The people an attached roster names (a payroll sheet's 성명 column), and each row ID's
        #: (학번 「32221045」) names: a name the answer puts in a table or a list must be one of
        #: them, and beside that ID, that ID's.
        self.roster, self.id_names = _roster(material)
        self.skill_only: set[str] = set()
        lines = text.splitlines()
        for line in lines:
            if not SKILL_LINE.search(line):
                continue
            for m in LATIN.finditer(line):
                word = m.group("w").lower()
                if re.search(r"[A-Za-z]\s$", line[:m.start()]):
                    continue
                if not any(word in other.lower() and not SKILL_LINE.search(other) for other in lines):
                    self.skill_only.add(word)

    @staticmethod
    def _person_values(material: str) -> list[float]:
        out = []
        for m in NUMBER.finditer(_native_digits(_said_by_person(material))):
            try:
                out.append(ko_number(m.group("num")))
            except ValueError:
                continue
        return out

    def _day(self, year: int | None, month: int, day: int) -> None:
        if 1 <= month <= 12 and 1 <= day <= 31:
            self.days.update(((year, month, day), (None, month, day)))
            self.months.add(month)
            if year:
                self.year_months.add((year, month))

    def match(self, value: float, unit: str, places: int) -> float | None:
        """The source value `value` was written from (more precise than the text may be), or
        None when no source holds it."""
        family = _family(unit)
        if family in _CONSTANT and value in _CONSTANT[family]:
            return value
        # 「5년」 written as 「60개월」; 「(메일 3, 14일)」 for a day the material dates.
        if family == "개월" and value % 12 == 0 and any(
                f == "년" and abs(v * 12 - value) < 1e-9 for v, f, _ in self.values):
            return value
        if family == "일" and value == int(value) and any(
                (None, m, int(value)) in self.days for m in range(1, 13)):
            return value
        for known, known_unit, kp in self.values:
            if (not known_unit or not family or known_unit == family) and near(value, known, places, kp):
                return known
        if round(abs(value), 6) in self.priced:
            return value
        if value >= 10 and any(near(value, d, places) for d in self.derived):
            return value
        return None

    def has_number(self, value: float, unit: str, places: int) -> bool:
        return self.match(value, unit, places) is not None

    def quoted(self, value: float, line: str) -> bool:
        """Whether `line` names a data row that holds `value` exactly as written."""
        compact = re.sub(r"\s+", "", line)
        return any(abs(value) in values and any(all(part in compact for part in key) for key in keys)
                   for keys, values in self.rows)

    def has_text(self, words: str) -> bool:
        return re.sub(r"\s+", "", words).lower() in self.lower


#: A thousands separator (「11,000」), not a field break.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _data_row(line: str) -> bool:
    """Whether a line is an attached data file's raw row (CSV, a tab-separated sheet row, a
    markdown table row) rather than something the person said. Prose commas (「주시고, 」) and
    thousands separators do not make a row."""
    stripped = line.strip()
    if stripped.startswith("|") or line.count("\t") >= 2:
        return True
    return len(re.findall(r",(?!\s)", _THOUSANDS.sub("", line))) >= 3


def _said_by_person(material: str) -> str:
    """The request and the person's answers without an attached data file's raw rows (their
    cells are data, not results)."""
    return "\n".join(line for line in (material or "").splitlines() if not _data_row(line))


def _data_rows(material: str) -> str:
    return "\n".join(line for line in (material or "").splitlines() if _data_row(line))


#: A cell that can name its row: an ID with letters and digits (「T0224」), a long number
#: (학번 「32201533」), a date, a year, or a word.
_KEY_CELL = re.compile(r"(?=.*[A-Za-z가-힣])[\w.-]{2,40}|\d{5,15}|(?:19|20)\d{2}(?:[-./]\d{1,2}){0,2}")


def _row_keys(rows: str) -> list[tuple[list[tuple[str, ...]], set[float]]]:
    """Each data row's naming cells and values. A cell names a row when at most two rows hold
    it; a common one (「공과대학」, 「Y」) names a row only with a second naming cell beside it."""
    table: list[list[str]] = []
    for line in rows.splitlines():
        if not re.sub(r"[\s:|-]", "", line):
            continue
        cut = "|" if line.strip().startswith("|") else ("\t" if "\t" in line else ",")
        table.append([re.sub(r"\s+", "", c.strip().strip('"')) for c in line.strip().strip("|").split(cut)])
    counts: dict[str, int] = {}
    for cells in table:
        for c in set(cells):
            counts[c] = counts.get(c, 0) + 1
    out: list[tuple[list[tuple[str, ...]], set[float]]] = []
    for cells in table:
        values: set[float] = set()
        for c in cells:
            if re.fullmatch(r"[+-]?\d[\d,]{0,15}(?:\.\d{1,10})?", c):
                values.add(abs(float(c.replace(",", ""))))
        keys = [c for c in cells if _KEY_CELL.fullmatch(c)]
        rare = [c for c in keys if counts[c] <= 2]
        if rare:
            out.append(([(c,) for c in rare], values))
        elif len(keys) >= 2:
            # Two common cells together: 「김민지」 with 「2026-09-03」.
            out.append(([(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]], values))
    return out


# ── people in a roster ────────────────────────────────────────────────────

#: Korean surnames, the first syllable of a bare name in a table cell or a list item.
SURNAMES = frozenset(
    "김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방"
    "공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부")
#: A column that lists people: 「성명」, 「이름」, 「학생」, 「담당자」.
NAME_HEADER = re.compile(r"^(?:성명|이름|학생\s?명?|학생\s?이름|직원\s?명?|근로자|담당자|참석자|대상자|"
                         r"name|student)$", re.I)
#: A row ID: a 학번·사번 of digits, maybe with a letter typed for a digit (「3222O781」).
ROW_ID = re.compile(r"(?<![\w.-])(?=[0-9A-Za-z]*\d{4})[0-9A-Za-z]{5,15}(?![\w.-])")
#: Words a name-shaped cell may hold that are not people: totals, notes, places.
NOT_PERSON = re.compile(
    r"^(?:합계|소계|총계|총합|평균|계|기타|비고|없음|전체|미상|미정|공란|누락|확인|해당\s?없음|중복|오류|"
    r"이상|정상|주간|야간|오전|오후|본인|공통|전원|남|여|이상치|최댓값|최솟값|최대값|최소값|평균값|"
    r"기준일|합계액|미입력|미기재|미확인)$"
    r"|(?:팀|과|실|관|부|처|청|원|센터|학과|학부|대학|본부|위원회|사무국|회|단)$")
#: An honorific or a role after a name in a cell: 「김민지 학생」, 「이준호 님」.
_AFTER_NAME = re.compile(r"\s*(?:학생|님|씨|조교|선생님?|\(.*\))\s*$")


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        return [c.strip() for c in stripped.strip("|").split("|")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return [c.strip().strip('"') for c in line.split(",")]


def _person(cell: str) -> str:
    """The name a cell holds alone (「 이준호」, 「**김민지** 학생」), or ''."""
    name = _AFTER_NAME.sub("", cell.replace("*", "").strip())
    if (re.fullmatch(r"[가-힣]{2,4}", name) and name[0] in SURNAMES
            and not NOT_PERSON.search(name)):
        return name
    return ""


def _roster(material: str) -> tuple[set[str], dict[str, set[str]]]:
    """The names an attached table lists — its name column's, or beside a row ID — and the
    names each ID's rows give. Empty unless at least two people are listed."""
    names: set[str] = set()
    by_id: dict[str, set[str]] = {}
    column = -1
    for line in (material or "").splitlines():
        if not _data_row(line):
            column = -1
            continue
        cells = _cells(line)
        heads = [k for k, c in enumerate(cells) if NAME_HEADER.match(c.replace(" ", ""))]
        if heads:
            column = heads[0]
            continue
        ids = [c for c in cells if ROW_ID.fullmatch(c)]
        if 0 <= column < len(cells):
            people = [p] if (p := _person(cells[column])) else []
        elif ids:
            people = [p for c in cells if (p := _person(c))][:1]
        else:
            people = []
        for person in people:
            names.add(person)
            for row_id in ids:
                by_id.setdefault(row_id.upper(), set()).add(person)
    return (names, by_id) if len(names) >= 2 else (set(), {})


#: A list item that starts with a name: 「- 김민지(32221045): 12시간」, 「1. **이준호** — 11.5시간」.
_LISTED = re.compile(
    r"^\s{0,8}(?:[-*+•]|\d{1,2}[.)])\s{1,4}(?:\*\*)?(?P<name>[가-힣]{2,4})(?:\*\*)?"
    r"(?=\s{0,2}(?:\(|:|：|—|–|-|,|·|/|\s학생|\s님|\s\d|$))")


def _listed_people(lines: list[str], skip: set[int], sources: Sources,
                   found: list[dict]) -> list[dict]:
    """Names in the answer's tables and lists that the attached roster does not hold, or that
    sit beside an ID the roster gives to someone else. Only when the material lists people;
    a name the person typed is in the material too."""
    if not sources.roster:
        return []
    out: list[dict] = []

    def wrong(name: str, line: str) -> bool:
        if not sources.has_text(name):
            return True
        ids = [m.group(0).upper() for m in ROW_ID.finditer(line)]
        return any(i in sources.id_names and name not in sources.id_names[i] for i in ids)

    def add(k: int, start: int, name: str) -> None:
        if any(f["line"] == k and f["start"] < start + len(name) and start < f["end"]
               for f in found):
            return
        out.append({"kind": "person", "text": name, "line": k, "start": start,
                    "end": start + len(name), "placeholder": PLACEHOLDER["person"]})

    column, start = -1, -1
    for k, line in enumerate(lines):
        stripped = line.strip()
        if k in skip or not stripped.startswith("|"):
            start = -1
            m = _LISTED.match(line) if k not in skip else None
            # A list item's lead word is a name only at three syllables or as the roster's
            # own (「정리:」, 「이상 행」 are not people).
            if m and _person(m.group("name")) and (
                    len(m.group("name")) == 3 or m.group("name") in sources.roster) and (
                    ROW_ID.search(line) or re.search(r"학번|사번|부서|소속", line)):
                if wrong(m.group("name"), line):
                    add(k, m.start("name"), m.group("name"))
            continue
        cells = stripped.strip("|").split("|")
        if start < 0:
            # A table's first row: its 성명 column, if it names one.
            start = k
            heads = [i for i, c in enumerate(cells)
                     if NAME_HEADER.match(re.sub(r"[\s*]", "", c))]
            column = heads[0] if heads else -1
            if heads or not any(c.strip() in sources.roster for c in cells):
                continue
        if re.fullmatch(r"[\s|:\-]+", stripped):
            continue
        if column < 0:
            # No 성명 header: the column where the roster's own names stand.
            column = next((i for i, c in enumerate(cells) if _person(c) in sources.roster), -1)
            if column < 0:
                continue
        if column >= len(cells) or not (name := _person(cells[column])):
            continue
        if wrong(name, line):
            at = line.index("|") + 1 + sum(len(c) + 1 for c in cells[:column])
            add(k, at + cells[column].index(name), name)
    return out


# ── finding ───────────────────────────────────────────────────────────────


def _masked(line: str) -> str:
    line = LIST_MARKER.sub(lambda m: " " * len(m.group(0)), line)
    return MASKED.sub(lambda m: " " * len(m.group(0)), line)


def _number_matches(masked: str) -> list[tuple[re.Match, float, str, bool]]:
    """`(match, value, unit, checked)` for each number: `checked` unless it is order, a length,
    a year, or a bare number (bare ones are checked only with `bare`, see `check`)."""
    out = []
    for m in NUMBER.finditer(masked):
        unit = m.group("unit") or ""
        raw = m.group("num")
        try:
            value = ko_number(raw)
        except ValueError:
            continue
        after = masked[m.end():]
        if not unit and ORDINAL_AFTER.match(after):
            continue
        if unit and re.match(r"\s?(?:차|째)", after):
            continue
        if re.match(r"\s?(?:자|바이트|byte)", after) or (unit == "년" and 1900 <= value <= 2100):
            continue
        if unit in ("부", "장") and value < 10:
            continue
        # A count of the people the document lists: 「참석 5명」.
        if unit == "명" and value <= 30 and re.search(r"참석|불참|참여자|출석|명단|인원", masked):
            continue
        # The document counting its own items: 「요구사항 6개」, 「3가지」, 「2줄」.
        if _family(unit) in ("개", "줄") and value <= 20 and value == int(value):
            continue
        grouped = bool(re.search(r"\d,\d{3}|[조억만]", raw))
        out.append((m, value, unit, bool(unit) or grouped))
    return out


def check(text: str, material: str, computed: list[str] | str | None = None,
          options: dict | None = None) -> list[dict]:
    """Every value in `text` that no source holds, as findings."""
    options = options or {}
    today = None
    if options.get("today"):
        try:
            today = dt.date.fromisoformat(str(options["today"])[:10])
        except ValueError:
            today = None
    sources = Sources(material, computed, today=today, numbers=str(options.get("numbers") or "all"))
    bare = bool(options.get("bare"))
    lines = (text or "").split("\n")
    skip: set[int] = set()
    # Code and diagrams are skipped; a 「```callout」 box is prose and is read.
    fenced, prose = False, False
    for k, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced, prose = not fenced, stripped[3:].strip().lower() == "callout" and not fenced
            skip.add(k)
        elif (fenced and not prose) or not stripped or stripped.startswith(("#", "![", "<")):
            skip.add(k)
    tables: dict[int, int] = {}
    table = -1
    for k, line in enumerate(lines):
        if line.strip().startswith("|") and k not in skip:
            if k - 1 not in tables:
                table += 1
            tables[k] = table
    found: list[dict] = []
    numbers: list[dict] = []
    heads = column_units(lines)
    for k, line in enumerate(lines):
        if k in skip:
            continue
        masked = _masked(line)
        taken = _other_values(line, masked, sources, k, found, options)
        for m, value, unit, checked in _number_matches(masked):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            if not unit and k in heads and 0 <= (col := _column_of(line, m.start())) < len(heads[k]):
                head_unit, scale = heads[k][col]
                if head_unit:
                    value, unit, checked = value * scale, head_unit, True
            numbers.append({"line": k, "match": m, "value": value, "unit": unit,
                            "checked": checked or (bare and ("." in m.group("num") or value >= 10)
                                                   and not (value == int(value) and 1900 <= value <= 2100)),
                            "known": sources.match(value, unit, _decimals(m.group("num")))})
            numbers[-1]["ok"] = numbers[-1]["known"] is not None or sources.quoted(
                ko_number(m.group("num")), line)
    # Plain arithmetic of grounded numbers written together (a line, a table) is grounded too,
    # and so are sums, differences and shares of the big figures anywhere (a total and its
    # change); a value already grounded elsewhere in the text may be repeated. A difference
    # of two totals needs a second round.
    # A share the material states (「등록금의 3분의 2」, 「수업료의 50퍼센트」) applied to a grounded
    # amount is arithmetic on the material's own figures too.
    shares = _shares("\n".join([material or "", *([computed] if isinstance(computed, str)
                                                     else list(computed or []))]))
    for _ in range(3):
        accepted = [n for n in numbers if n["ok"]]
        written = [(abs(a["value"]), _decimals(a["match"].group("num")), _family(a["unit"])) for a in accepted]
        big = sorted({(abs(a["value"]), _family(a["unit"])) for a in accepted if abs(a["value"]) >= 100})[:60]
        pools: dict[tuple, list[float]] = {}
        changed = False
        for n in numbers:
            if n["ok"]:
                continue
            places = _decimals(n["match"].group("num"))
            family = _family(n["unit"])
            if any((not wu or not family or wu == family) and near(n["value"], w, places, wp)
                   for w, wp, wu in written):
                n["ok"] = changed = True
                continue
            key = (("t", tables[n["line"]]) if n["line"] in tables else ("l", n["line"])) + (family,)
            if key not in pools:
                local = [(a["known"] if a.get("known") is not None else a["value"], _family(a["unit"]))
                         for a in accepted if (a["line"] in tables and tables[a["line"]] == key[1]
                                               if key[0] == "t" else a["line"] == key[1])]
                # The big figures anywhere: a total, its change and its share.
                spread = [(v, f) for v, f in big if f == family or not f or not family or family in ("%", "%p")]
                pools[key] = arith(local, family) + [
                    x for i, (a, _) in enumerate(spread) for b, _ in spread[i + 1:]
                    for x in ((a + b, b - a) if family not in ("%", "%p")
                              else (a / b * 100, (b - a) / a * 100, (b - a) / b * 100))]
            pool = pools[key]
            if shares and family in ("원", "달러", ""):
                grounded = {v for v, _ in big} | {abs(a["value"]) for a in accepted}
                pool = pool + [v * f for v in grounded for f in shares]
            if any(near(n["value"], d, places) for d in pool):
                n["ok"] = changed = True
                n["known"] = None
        if not changed:
            break
    for n in numbers:
        if n["ok"] or not n["checked"] or (n["value"] in (0, 1) and not bare):
            continue
        m = n["match"]
        start = m.start("num") if m.group("sign") is None else m.start()
        found.append(_finding("number", lines[n["line"]], n["line"], start, m.end()))
    found.extend(_claims(lines, skip, sources))
    found.extend(_listed_people(lines, skip, sources, found))
    return sorted(found, key=lambda f: (f["line"], f["start"]))


#: 「3분의 2」, 「50퍼센트」, 「50%」 — a share of something.
_SHARE = re.compile(r"(\d{1,3})\s{0,2}분의\s{0,2}(\d{1,3})|(\d{1,3}(?:\.\d{1,2})?)\s{0,1}(?:%|퍼센트)")


def _shares(text: str) -> list[float]:
    """The fractions a text states, as numbers between 0 and 1."""
    out: set[float] = set()
    for m in _SHARE.finditer(text or ""):
        if m.group(1):
            whole, part = int(m.group(1)), int(m.group(2))
            if 0 < part < whole:
                out.add(part / whole)
        else:
            value = float(m.group(3))
            if 0 < value < 100:
                out.add(value / 100)
    return sorted(out)[:24]


def _finding(kind: str, line: str, k: int, start: int, end: int) -> dict:
    # Spaces a pattern took at the end, or a masked span's blank (a 「[…]」 after a date), are
    # not part of the value.
    masked = _masked(line)
    while end > start and masked[end - 1].isspace():
        end -= 1
    return {"kind": kind, "text": line[start:end], "line": k, "start": start, "end": end,
            "placeholder": PLACEHOLDER[kind]}


def _other_values(line: str, masked: str, sources: Sources, k: int, found: list[dict],
                  options: dict) -> list[tuple[int, int]]:
    """Dates, months, times and names on one line; returns the spans they take."""
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in taken)

    def add(kind: str, start: int, end: int) -> None:
        taken.append((start, end))
        found.append(_finding(kind, line, k, start, end))

    for m in FULL_DAY.finditer(masked):
        taken.append(m.span())
        y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
        if 1 <= mo <= 12 and 1 <= d <= 31 and (y, mo, d) not in sources.days \
                and (None, mo, d) not in sources.days:
            found.append(_finding("date", line, k, *m.span()))
    for pattern in (MONTH_DAY, SHORT_DAY):
        for m in pattern.finditer(masked):
            if not free(*m.span()):
                continue
            taken.append(m.span())
            mo, d = int(m.group("m")), int(m.group("d"))
            if 1 <= mo <= 12 and 1 <= d <= 31 and (None, mo, d) not in sources.days:
                found.append(_finding("date", line, k, *m.span()))
    for m in YEAR_MONTH.finditer(masked):
        if not free(*m.span()):
            continue
        taken.append(m.span())
        y, mo = int(m.group("y")), int(m.group("m"))
        if 1 <= mo <= 12 and (y, mo) not in sources.year_months and not (
                y in sources.years and mo in sources.months):
            found.append(_finding("month", line, k, *m.span()))
    for m in VAGUE_MONTH.finditer(masked):
        if not free(*m.span()):
            continue
        mo, part = int(m.group("m")), m.group("part")
        taken.append(m.span())
        if not 1 <= mo <= 12:
            continue
        if part in ("초", "중순", "하순", "말"):
            days = {"초": range(1, 11), "중순": range(11, 21)}.get(part, range(21, 32))
            ok = sources.has_text(m.group(0)) or any((None, mo, d) in sources.days for d in days)
        else:
            ok = mo in sources.months
        if not ok:
            # The month and its part, not the 「까지」 after it.
            span = re.match(r"\d{1,2}\s{0,2}월\s{0,2}(?:초|중순|하순|말)?", m.group(0)).group(0)
            found.append(_finding("month", line, k, m.start(), m.start() + len(span)))
    for m in TIME.finditer(masked):
        if not free(*m.span()):
            continue
        tail = masked[m.end():m.end() + 6]
        if m.group("mm") is None and m.group("half") is None and m.group("min") is None and not re.match(
                r"\s{0,2}(?:까지|부터|에|이전|이후|전|후|경|~)", tail):
            continue
        taken.append(m.span())
        if _hour(m) % 24 not in sources.hours and int(m.group("h")) % 24 not in sources.hours:
            start = m.start("half") if m.group("half") else m.start("h")
            found.append(_finding("time", line, k, start, m.end()))
    for m in ORG.finditer(masked):
        name = PARTICLE.sub("", next(g for g in m.groups() if g))
        if free(*m.span()):
            taken.append(m.span())
            if not sources.has_text(name):
                found.append(_finding("org", line, k, *m.span()))
    for m in PERSON.finditer(masked):
        name = m.group("name")
        if not free(*m.span()) or NOT_A_NAME.match(name):
            continue
        if "○○" not in name and len(name) >= 3 and VERB_END.search(name):
            continue
        if "○○" in name:
            ok = sources.has_text(name)
        else:
            stem = re.sub(r"(?:이|가|은|는|을|를|의|와|과|께서|에게|께)$", "", name)
            ok = sources.has_text(name) or (len(stem) >= 2 and sources.has_text(stem)) or (
                m.group("title") is not None and sources.has_text(name + m.group("title")))
        taken.append(m.span())
        if not ok:
            found.append(_finding("person", line, k, m.start("name"), m.end("name")))
    for m in TITLE.finditer(masked) if options.get("titles") else ():
        if free(*m.span()) and not sources.has_text(m.group(0)) and _titled(masked, m):
            taken.append(m.span())
            found.append(_finding("title", line, k, *m.span()))
    for m in LATIN.finditer(masked) if options.get("products") else ():
        word = m.group("w")
        if not free(*m.span()) or word.lower() in COMMON_LATIN or re.search(r"[\d-]", word):
            continue
        # One word of an English phrase (「Mean Absolute Error」, 「Spring Boot」) is not a name.
        if re.search(r"[A-Za-z]\s$", masked[:m.start()]) or re.match(r"\s[A-Za-z]", masked[m.end():]):
            continue
        taken.append(m.span())
        if not sources.has_text(word):
            found.append(_finding("product", line, k, *m.span()))
    return taken


def _titled(masked: str, m: re.Match) -> bool:
    """A title held by someone: 「파트장(팀원 4명)」, 「대리로 승진」, 「박준영 대리」 — not
    「팀장님께 보고」 in a request-shaped sentence."""
    after = masked[m.end():m.end() + 8]
    return bool(re.match(r"\s{0,2}(?:\(|로\s|으로\s|직|직책|직급|을\s맡|를\s맡|역할|승진)", after))


def _claims(lines: list[str], skip: set[int], sources: Sources) -> list[dict]:
    """Sentences saying the person worked with a tool their material only lists as a skill."""
    if not sources.skill_only:
        return []
    out = []
    for k, line in enumerate(lines):
        if k in skip or line.strip().startswith("|"):
            continue
        pos = 0
        for part in re.split(f"({SENTENCE_BREAK.pattern})", line):
            if DID_WORK.search(part) and {m.group("w").lower() for m in LATIN.finditer(part)} & sources.skill_only:
                out.append({"kind": "claim", "text": part, "line": k, "start": pos, "end": pos + len(part),
                            "placeholder": ""})
            pos += len(part)
    return out


# ── repairing ─────────────────────────────────────────────────────────────


def _blank_cell(cell: str, items: list[dict], offset: int) -> str:
    """A cell holding the stray value and little else becomes the blank alone."""
    rest = cell
    for f in sorted(items, key=lambda f: -f["start"]):
        rest = rest[: f["start"] - offset] + rest[f["end"] - offset:]
    if not re.sub(r"[\s*_()~\-+,.%:/·→▲▼]|약|총|이내|이상|이하|까지|원|명|건|개", "", rest):
        return f" {items[0]['placeholder']} "
    for f in sorted(items, key=lambda f: -f["start"]):
        cell = cell[: f["start"] - offset] + f["placeholder"] + cell[f["end"] - offset:]
    return cell


def _fix_line(line: str, items: list[dict]) -> str | None:
    """The line with each finding blanked; `None` when nothing is left of it."""
    if line.lstrip().startswith("|"):
        cells, pos, out = line.split("|"), 0, []
        for cell in cells:
            start, end = pos, pos + len(cell)
            mine = [f for f in items if start <= f["start"] and f["end"] <= end and f["kind"] != "claim"]
            out.append(_blank_cell(cell, mine, start) if mine else cell)
            pos = end + 1
        return "|".join(out)
    pos, kept = 0, []
    for part in re.split(f"({SENTENCE_BREAK.pattern})", line):
        start = pos
        pos += len(part)
        mine = [f for f in items if start <= f["start"] < start + max(1, len(part))]
        if not mine:
            kept.append(part)
            continue
        if any(f["kind"] == "claim" for f in mine):
            kept.append("")
            continue
        rest = part
        for f in sorted(mine, key=lambda f: -f["start"]):
            rest = rest[: f["start"] - start] + rest[f["end"] - start:]
        # Invented values carrying the sentence alone: two or more, and no number left.
        if len(mine) >= 2 and not re.search(r"\d", rest):
            kept.append("")
            continue
        for f in sorted(mine, key=lambda f: -f["start"]):
            part = part[: f["start"] - start] + f["placeholder"] + part[f["end"] - start:]
        kept.append(part)
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", "".join(kept)).rstrip()
    return text if text.strip() else None


def fix(text: str, findings: list[dict]) -> str:
    """`text` with every finding blanked, or its sentence dropped (a claim, or a sentence of
    invented values only)."""
    by_line: dict[int, list[dict]] = {}
    for f in findings or []:
        by_line.setdefault(int(f["line"]), []).append(f)
    out = []
    for k, line in enumerate((text or "").split("\n")):
        if k not in by_line:
            out.append(line)
        elif (fixed := _fix_line(line, by_line[k])) is not None:
            out.append(fixed)
    return "\n".join(out)


def summary(findings: list[dict]) -> str:
    """「근거 없는 값 3개 → 빈칸 (수치 2 · 날짜 1): 「11월 초」, …」."""
    if not findings:
        return ""
    counts: dict[str, int] = {}
    for f in findings:
        word = KIND_WORDS[f["kind"]]
        counts[word] = counts.get(word, 0) + 1
    kinds = " · ".join(f"{word} {n}" for word, n in counts.items())
    shown = ", ".join(dict.fromkeys(f"「{f['text'].strip()[:24]}」" for f in findings if f["kind"] != "claim"))
    return f"근거 없는 값 {len(findings)}개 → 빈칸 ({kinds})" + (f": {shown[:300]}" if shown else "")


def run(options: dict) -> dict:
    op = options.get("op") or "repair"
    text = str(options.get("text") or "")
    if op == "fix":
        return {"ok": True, "op": op, "text": fix(text, list(options.get("findings") or []))}
    found = check(text, str(options.get("material") or ""), options.get("computed"),
                  dict(options.get("options") or {}))
    if op == "check":
        return {"ok": True, "op": op, "findings": found, "summary": summary(found)}
    if op == "repair":
        return {"ok": True, "op": op, "text": fix(text, found), "findings": found, "summary": summary(found)}
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
    if result.get("summary"):
        print(result["summary"])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
