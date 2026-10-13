"""문서의 수치·날짜·시각·이름·기관·직함·제품명이 자료나 계산값에 있는지 대조하고, 없는 값은 빈칸으로 바꿉니다."""

from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import sys

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
#: A number with an optional unit; not inside an identifier (「REQ-03」, 「p95」) nor glued to a number.
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

#: 「오후 5~8시」, 「5시~8시」: a span of hours
TIME_RANGE = re.compile(
    r"(?P<half>오전|오후|저녁|밤|새벽|아침)?\s{0,2}(?<![\d:])(?P<h1>\d{1,2})\s{0,2}(?:시\s{0,2})?[~∼〜-]"
    r"\s{0,2}(?P<h2>\d{1,2})\s{0,2}시(?!간)")


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
#: A linking verb ending before a title-like word: 「달라」, 「달라서」, 「맡아」, 「하며」.
_LINKING_END = re.compile(r"(?:라|아|어|서|고|며|면|게|지|해|여|러|워)$")
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

#: Something the reader must bring or hand in: 「신분증 지참」, 「서류를 제출해 주세요」.
REQUIRE = re.compile(
    r"(?P<obj>[가-힣A-Za-z0-9]{2,15})\s{0,2}(?:(?:꼭|반드시|미리|함께|각자|직접|모두|필히|별도로)\s{1,2})?"
    r"(?P<verb>지참|제출|휴대|착용|구비|챙기|챙겨|가져오|가져와|준비해|준비하)"
    r"(?!\s{0,2}드리)")  # 「준비해 드리겠습니다」: an offer, not a requirement
#: Objects any notice may name: 「서류를 제출」, 「준비물을 챙겨」.
GENERIC_OBJECTS = frozenset((
    "서류", "자료", "준비물", "물품", "소지품", "개인", "본인", "것", "이것", "그것", "해당", "관련", "필요",
    "내용", "사항", "결과", "결과물", "의견", "답안", "답변", "회신", "미리", "사전", "함께", "꼭",
))
#: An object word that is not one: 「10월 5일까지 제출」, 「신청 후 제출」, 「완료한 뒤 제출」.
NOT_OBJECT = re.compile(r"\d|(?:까지|부터|에게|에서|에|로|으로|은|는|이|가|의|후|뒤|전|때|시|내|한|된|할|하여|하고|해서|면)$")
#: A verb told as what happens, not asked of the reader: 「제출됩니다」, 「제출이 가능」, 「제출된」.
DESCRIBED = re.compile(
    r"\s{0,2}(?:되(?!어\s{0,2}야)|됩|된|됐|돼(?!야)|이\s{0,2}(?:가능|완료|마감|끝))")
#: Findings that drop their whole sentence rather than leave a blank.
DROPPED = frozenset({"claim", "requirement"})

PLACEHOLDER = {
    "number": "[수치]", "date": "[일자]", "month": "[시기]", "time": "[시각]", "person": "[이름]",
    "org": "[기관명]", "title": "[직함]", "product": "[제품명]", "claim": "", "requirement": "",
    "contact": "(확인 필요)", "dose": "(확인 필요)", "consequence": " (확인 필요)",
}
KIND_WORDS = {
    "number": "수치", "date": "날짜", "month": "시기", "time": "시각", "person": "이름",
    "org": "기관·회사", "title": "직함", "product": "제품·도구", "claim": "자료에 없는 경력 문장",
    "requirement": "자료에 없는 준비·제출 요구",
    "contact": "전화번호", "dose": "용량", "consequence": "법적 결과",
}
#: The mark an advice finding gets where it stands (「1393(확인 필요)」), never blanked or dropped.
MARK = "(확인 필요)"


def _marks(finding: dict) -> bool:
    return finding["placeholder"].strip() == MARK


#: `advice` (legal, tax, financial, medical chat answers): numbers checked are money and rates;
#: hotlines, doses and legal consequences are checked too; agency names are not.
ADVICE_FAMILIES = frozenset({"원", "달러", "%", "%p"})
#: A phone number: full, or a 15xx-style line.
_PHONE = re.compile(r"(?<![\d.,-])(?:0\d{1,2}-\d{3,4}-\d{4}|1[5-8]\d{2}-\d{4})(?![\d-])")
#: A short line (「109」 「1393」), read as one only beside a call cue.
_SHORT_LINE = re.compile(
    r"(?<![\d.,-])1\d{2,3}(?![\d.,-])(?!\s?(?:%|퍼센트|년|월|일|원|만|억|천|개|명|시간|분|초|세|살|"
    r"회|kg|cm|mg|km|달러|가지|주|층|호|차|위|점|배|건|곳|단계|쪽))")
_CALL_CUE = re.compile(
    r"전화|상담|핫라인|번호|콜\s?센터|☎|신고|긴급|문의|다이얼|직통|"
    r"hotline|helpline|crisis|call|dial|phone", re.I)
#: A dose: 「500mg」 「2정」 「10 mL」.
_DOSE = re.compile(
    r"(?<![\d.,])(?P<n>\d+(?:[.,]\d+)?)\s?(?P<u>mg|㎎|mcg|㎍|μg|mL|ml|㎖|IU|캡슐|"
    r"정(?![가-힣])|알(?![가-힣]))(?![A-Za-z])")
#: A legal consequence: 「형사처벌」 「벌금」 「면허 취소」.
_PENALTY = re.compile(
    r"형사\s?처벌|형사\s?책임|형사\s?입건|처벌|징역|벌금|과태료|구속|전과|면허\s?(?:취소|정지)|"
    r"criminal(?:ly)?\s+(?:charge\w*|liab\w*|prosecut\w*)|jail|prison", re.I)
_MARK_AFTER = re.compile(r"\s?\(\s?확인\s?필요\s?\)")
_VALUE_MASK = re.compile(
    r"https?://\S{1,300}|`[^`\n]{0,200}`|\[[^\[\]\n]{0,40}\](?:\([^)\n]{0,300}\))?")


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
    """`value` at `places` decimals is `known` rounded; a computed `known` may be one off in the last place."""
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
    """Plain arithmetic of nearby numbers giving a `family` value: sums, gaps, shares, price × count."""
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
#: 「(단위: 천원)」: unit for the line's bare figures (alone on a line: the block below).
_LINE_UNIT = re.compile(r"단위\s{0,2}[:：]?\s{0,2}(?P<u>천만\s?원|백만\s?원|억\s?원|만\s?원|천\s?원|원|명|건|개)(?![가-힣])")


def line_units(lines: list[str]) -> dict[int, tuple[str, float]]:
    """For each line, the unit (family, scale) a 「단위 ○○」 note gives its bare figures."""
    out: dict[int, tuple[str, float]] = {}
    carry: tuple[str, float] | None = None
    for k, line in enumerate(lines):
        m = _LINE_UNIT.search(line)
        if m:
            word = m.group("u").replace(" ", "")
            unit = ("원", _SCALE[word]) if word in _SCALE else (_family(word), 1.0)
            out[k] = unit
            # A note alone on its line (「(단위: 만원)」) covers the block below it.
            carry = unit if not re.search(r"\d", _LINE_UNIT.sub("", line)) else None
            continue
        if not line.strip():
            carry = None
        elif carry:
            out[k] = carry
    return out


def column_units(lines: list[str]) -> dict[int, list[tuple[str, float]]]:
    """Per table row, each column's unit from its header (「(만 원)」, 「투입(MM)」)."""
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


#: A day offset: 「30일 전」, 「14일 전까지」, 「2주 뒤」 (not 영업일, which skips days off).
_OFFSET = re.compile(r"(?<![\d영업])(\d{1,3})\s{0,2}(일|주일|주)\s{0,2}(?:전|후|뒤|이내|이전|이후|안에)")
#: A unit price in the person's words: 「시간당 11,000원」, 「1인당 2만 원」, 「건당」, 「개당」, 「단가」.
_RATE = re.compile(r"[가-힣]{0,4}당\s{0,2}\d|단가|요율|시급|일급|월급")

#: Calendar constants a document converts with: a year is 12 months, 52 weeks, 365 days.
_CONSTANT = {"개월": {12, 24, 36}, "주": {52}, "일": {7, 365}, "시간": {24}, "분": {60}}


class Sources:
    """What the output may say: request, material, answers and code results, read once."""

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
        #: Day offsets the sources state (「30일 전」, 「2주 뒤」), as counts of days.
        self.today = today
        self.offsets = {int(n) * (7 if u.startswith("주") else 1)
                        for n, u in _OFFSET.findall(text) if 0 < int(n) <= 400}
        self.years = {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)}
        self.years |= {2000 + int(y) for y in re.findall(r"(?<![\d.])(\d{2})\s{0,2}년", text)}
        for m in re.finditer(r"(?<![\d.])(\d{1,2})\s{0,2}월", text):
            if 1 <= int(m.group(1)) <= 12:
                self.months.add(int(m.group(1)))
        for m in TIME.finditer(text):
            self.hours.update((_hour(m) % 24, int(m.group("h")) % 24))
        for m in TIME_RANGE.finditer(text):
            # 「오후 5~8시」: the start is a time too, in the same half of the day
            pm = 12 if m.group("half") in ("오후", "저녁", "밤") else 0
            for h in (int(m.group("h1")), int(m.group("h2"))):
                self.hours.update((h % 24, (h + pm) % 24 if h < 12 else h % 24))
        # 「시차 9시간」: the other side's clock for every time given
        for shift in {int(n) for n in re.findall(r"시차\s{0,3}(?:는|가)?\s{0,2}(\d{1,2})\s{0,2}시간", text)}:
            self.hours |= {(h + d) % 24 for h in set(self.hours) for d in (shift, -shift)}
        number_text = text if numbers == "all" else f"{_said_by_person(material)}\n{computed_text}"
        number_text = _native_digits(number_text)
        self.values: list[tuple[float, str, int]] = []
        rows = number_text.splitlines()
        heads = column_units(rows)
        noted = line_units(rows)
        for k, line in enumerate(rows):
            line = FULL_DAY.sub(lambda m: " " * len(m.group(0)), LIST_MARKER.sub(lambda m: " " * len(m.group(0)), line))
            for m in NUMBER.finditer(line):
                try:
                    value, unit, scale = ko_number(m.group("num")), _family(m.group("unit")), 1.0
                except ValueError:
                    continue
                if not unit and k in heads and 0 <= (col := _column_of(line, m.start())) < len(heads[k]):
                    unit, scale = heads[k][col]
                if not unit and k in noted:
                    # 「식음료 870 …, 단위 만원」: 870 is 8,700,000원, and still the bare 870 too.
                    self.values.append((value * noted[k][1], noted[k][0], _decimals(m.group("num"))))
                self.values.append((value * scale, unit, _decimals(m.group("num"))))
        pool = [v for v, _, _ in self.values]
        self.derived = _table_columns(number_text)
        figures = sorted({abs(v) for v in pool if abs(v) >= 10})
        if numbers == "all" and len(figures) <= 25:
            # Short material only: its own figures' sums, gaps and changes count too.
            self.derived += [x for i, a in enumerate(figures) for b in figures[i + 1:]
                             for x in (a + b, b - a, (b - a) / a * 100, a / b * 100, b / a)]
        if numbers == "all":
            # A price or a distance times a count: 「20만 원 × 2팀」, 「15,000km × 5대」.
            counts = sorted({abs(v) for v in pool if 1 < abs(v) < 100 and v == int(v)})
            self.derived += [a * c for a in figures for c in counts if a >= 1000]
        #: Sorted magnitudes: a long attachment holds thousands of values and derives millions.
        self.derived_sorted = sorted({abs(d) for d in self.derived})
        self.values_sorted = sorted((abs(v), k) for k, (v, _, _) in enumerate(self.values))
        #: Data rows (naming cells, values): a value counts only on a line naming its row; rate ×
        #: computed value is priced tool output. Tools-only numbers mode; else rows are material.
        self.rows: list[tuple[list[tuple[str, ...]], set[float]]] = []
        self.priced: set[float] = set()
        #: Person-stated unit prices (「시간당 11,000원」): grounded quantity × price is grounded.
        self.rates = sorted({abs(v) for v in self._person_values(material)
                             if abs(v) >= 100})[:12] if _RATE.search(material or "") else []
        if numbers != "all":
            self.rows = _row_keys(_data_rows(material))
            rates = {abs(v) for v in self._person_values(material) if abs(v) >= 100}
            self.priced = {round(r * abs(x), 6) for r in rates for x in pool if x}
        #: Roster names and each row ID's names: listed names must be in it and match their ID.
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
        """The source value `value` was written from, or None."""
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
        # `near` allows at most one in the last place plus half a unit of the source's
        v = abs(value)
        tol = 1.0 + 10.0 ** -places + 1e-6 * max(1.0, v)
        lo = bisect.bisect_left(self.values_sorted, (v - tol, -1))
        hi = bisect.bisect_right(self.values_sorted, (v + tol, len(self.values)))
        for k in sorted(k for _, k in self.values_sorted[lo:hi]):
            known, known_unit, kp = self.values[k]
            if (not known_unit or not family or known_unit == family) and near(value, known, places, kp):
                return known
        if round(abs(value), 6) in self.priced:
            return value
        if value >= 10 and self._near_derived(value, places):
            return value
        return None

    def _near_derived(self, value: float, places: int) -> bool:
        # `near` never allows more than one step in the last place
        v = abs(value)
        tol = 10.0 ** -places + 1e-6 * max(1.0, v)
        lo = bisect.bisect_left(self.derived_sorted, v - tol)
        hi = bisect.bisect_right(self.derived_sorted, v + tol)
        return any(near(value, d, places) for d in self.derived_sorted[lo:hi])

    def counted(self, year: int | None, month: int, day: int) -> bool:
        """Whether the day is a source date moved by a source-stated offset (「3월 2일」 + 「30일 전」)."""
        if not self.offsets or (year, month, day) in self.days or (None, month, day) in self.days:
            return False
        years = {year} if year else (self.years | {self.today.year, self.today.year + 1}
                                     if self.today else self.years)
        bases = []
        for by, bm, bd in self.days:
            for y in ({by} if by else {y for x in years for y in (x - 1, x, x + 1)}):
                try:
                    bases.append(dt.date(y, bm, bd))
                except ValueError:
                    continue
        for y in years:
            try:
                target = dt.date(y, month, day)
            except ValueError:
                continue
            if any(abs((target - base).days) in self.offsets for base in bases):
                return True
        return False

    def as_written(self, raw: str, unit: str) -> float | None:
        """Leading figure of 「20,000천원」 when sources state amounts in 천원 (만원, 억원), else None."""
        m = re.fullmatch(r"\s*(\d[\d,]*(?:\.\d+)?)\s?(천|만|억)\s*", raw)
        if not m or _family(unit) not in ("원", "") or not re.search(
                rf"\(\s{{0,2}}(?:단위\s{{0,2}}[:：]?\s{{0,2}})?{m.group(2)}\s?원\s{{0,2}}\)"
                rf"|단위\s{{0,2}}[:：]?\s{{0,2}}{m.group(2)}\s?원", self.text):
            return None
        return float(m.group(1).replace(",", ""))

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
    """Whether a line is a raw data-file row (CSV, TSV, markdown table), not prose."""
    stripped = line.strip()
    if stripped.startswith("|") or line.count("\t") >= 2:
        return True
    return len(re.findall(r",(?!\s)", _THOUSANDS.sub("", line))) >= 3


def _said_by_person(material: str) -> str:
    """Request and answers without raw data-file rows (cells are data, not results)."""
    return "\n".join(line for line in (material or "").splitlines() if not _data_row(line))


def _data_rows(material: str) -> str:
    return "\n".join(line for line in (material or "").splitlines() if _data_row(line))


#: A cell that can name its row: alnum ID (「T0224」), long number (학번), date, year or word.
_KEY_CELL = re.compile(r"(?=.*[A-Za-z가-힣])[\w.-]{2,40}|\d{5,15}|(?:19|20)\d{2}(?:[-./]\d{1,2}){0,2}")


def _row_keys(rows: str) -> list[tuple[list[tuple[str, ...]], set[float]]]:
    """Each data row's naming cells and values; a common cell names a row only with a second one."""
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
    """Names an attached table lists, and each ID's names; empty unless 2+ people."""
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
    """Listed names missing from the roster or beside another person's ID."""
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
            # A list item's lead word is a name only at 3 syllables or if in the roster.
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


def _masked(line: str) -> str:
    line = LIST_MARKER.sub(lambda m: " " * len(m.group(0)), line)
    return MASKED.sub(lambda m: " " * len(m.group(0)), line)


def _number_matches(masked: str) -> list[tuple[re.Match, float, str, bool]]:
    """`(match, value, unit, checked)` per number; order, lengths, years, bare (unless `bare`) unchecked."""
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
    # Code and diagrams skipped; 「```callout」 prose and 「```kpi」 rows are read.
    fenced, prose = False, False
    for k, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced, prose = not fenced, stripped[3:].strip().lower() in ("callout", "kpi") \
                and not fenced
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
            places = _decimals(m.group("num"))
            # Figure as written, before column/own 「천」 scaling: a copied sheet cell is the cell.
            written = sources.as_written(m.group("num"), unit)
            if not unit and k in heads and 0 <= (col := _column_of(line, m.start())) < len(heads[k]):
                head_unit, scale = heads[k][col]
                if head_unit:
                    if scale != 1:
                        written = ko_number(m.group("num"))
                    value, unit, checked = value * scale, head_unit, True
            known = sources.match(value, unit, places)
            if known is None and written is not None:
                known = sources.match(written, "원", places)
            numbers.append({"line": k, "match": m, "value": value, "unit": unit,
                            "checked": checked or (bare and ("." in m.group("num") or value >= 10)
                                                   and not (value == int(value) and 1900 <= value <= 2100)),
                            "known": known})
            numbers[-1]["ok"] = numbers[-1]["known"] is not None or sources.quoted(
                ko_number(m.group("num")), line) or (written is not None and sources.quoted(written, line))
    # Arithmetic of grounded numbers (line, table, big figures, material shares) is grounded;
    # a difference of two totals needs a second round.
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
            # A line figure ± a same-kind figure anywhere (「22시간을 제외하면 49.5시간」).
            mine = [abs(a["value"]) for a in accepted if a["line"] == n["line"]
                    and (_family(a["unit"]) == family or not a["unit"])]
            kin = {abs(a["value"]) for a in accepted if family and _family(a["unit"]) == family}
            pool = pool + [x for a in mine for b in kin for x in (a + b, abs(b - a))]
            given = sorted({abs(v) for v, u, _ in sources.values if _family(u) == family})
            if _COMPARED.search(lines[n["line"]]) and family and len(given) <= 12:
                # 「실험반이 대조반보다 0.5점 높았다」: the gap of two figures the person gave,
                # from a short material only (a long one has a gap for anything)
                pool = pool + [b - a for i, a in enumerate(given) for b in given[i + 1:]]
            if n["line"] in tables:
                # a column's total or a run of it (a subtotal)
                pool = pool + _column_sums(n, accepted, tables, lines)
            # A rate the person gave times a grounded quantity of another kind: 49.5시간 × 11,000원.
            if family in ("원", "달러", "") and sources.rates:
                quantities = {abs(a["value"]) for a in accepted
                              if a["unit"] and _family(a["unit"]) not in ("원", "달러", "%", "%p")}
                pool = pool + [r * q for r in sources.rates for q in quantities]
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
        if options.get("advice") and _family(n["unit"]) not in ADVICE_FAMILIES:
            # advice's durations and counts are guidance, not facts to source
            continue
        m = n["match"]
        start = m.start("num") if m.group("sign") is None else m.start()
        found.append(_finding("number", lines[n["line"]], n["line"], start, m.end()))
    found.extend(_claims(lines, skip, sources))
    if options.get("requirements"):
        found.extend(_requirements(lines, skip, sources))
    found.extend(_listed_people(lines, skip, sources, found))
    if options.get("advice"):
        # advice: agency names are not checked, and a value is marked where it stands
        found = [_as_mark(f, lines) for f in found if f["kind"] not in ("org", "person")]
        found = [f for f in found if f is not None]
        found.extend(_advice_values(lines, skip, sources, found))
    return sorted(found, key=lambda f: (f["line"], f["start"]))


def _as_mark(finding: dict, lines: list[str]) -> dict | None:
    """A blank-type finding as a mark after its value; None when the mark is there already."""
    if finding["kind"] in DROPPED:
        return finding
    line, end = lines[finding["line"]], finding["end"]
    if _MARK_AFTER.match(line, end):
        return None
    return {**finding, "start": end, "end": end, "placeholder": MARK}


def _digits_in(sources: Sources, digits: str) -> bool:
    joined = re.sub(r"(?<=\d)[-\s.](?=\d)", "", sources.text)
    return bool(re.search(rf"(?<!\d){re.escape(digits)}(?!\d)", joined))


def _advice_values(lines: list[str], skip: set[int], sources: Sources,
                   found: list[dict]) -> list[dict]:
    """Hotlines, doses and legal consequences an advice answer gives with no source."""
    out: list[dict] = []
    for k, line in enumerate(lines):
        if k in skip:
            continue
        masked = _VALUE_MASK.sub(lambda m: " " * len(m.group(0)), line)
        taken = [(f["start"], f["end"]) for f in found if f["line"] == k]

        def free(start: int, end: int, taken: list = taken) -> bool:
            return not any(s < end and start < e for s, e in taken)

        def mark(kind: str, m: re.Match, taken: list = taken, line: str = line,
                 k: int = k) -> None:
            taken.append(m.span())
            if not _MARK_AFTER.match(line, m.end()):
                out.append({"kind": kind, "text": m.group(0), "line": k, "start": m.end(),
                            "end": m.end(), "placeholder": PLACEHOLDER[kind]})

        for m in _PHONE.finditer(masked):
            if free(*m.span()) and not _digits_in(sources, re.sub(r"\D", "", m.group(0))):
                mark("contact", m)
        for m in _SHORT_LINE.finditer(masked):
            near_text = masked[max(0, m.start() - 16):m.end() + 16]
            if free(*m.span()) and _CALL_CUE.search(near_text) \
                    and not _digits_in(sources, m.group(0)):
                mark("contact", m)
        for m in _DOSE.finditer(masked):
            written = re.sub(r"\s+", "", m.group(0)).lower()
            if free(*m.span()) and written not in sources.lower:
                mark("dose", m)
        if line.strip().startswith("|"):
            continue
        pos = 0
        for part in re.split(f"({SENTENCE_BREAK.pattern})", line):
            start, pos = pos, pos + len(part)
            if "확인 필요" in part:
                continue
            hit = next((m for m in _PENALTY.finditer(part)
                        if re.sub(r"\s+", "", m.group(0)).lower() not in sources.lower), None)
            if hit is None:
                continue
            # the mark goes at the sentence's end, before its closing punctuation
            end = start + len(part.rstrip())
            while end > start and line[end - 1] in ".!?。)*":
                end -= 1
            out.append({"kind": "consequence", "text": hit.group(0), "line": k, "start": end,
                        "end": end, "placeholder": PLACEHOLDER["consequence"]})
    return out


#: words of a comparison: a gap between two figures may be stated
_COMPARED = re.compile(r"높|낮|많|적|크|작|차이|증가|감소|늘|줄|상승|하락|앞서|뒤지|%p|포인트|격차")


def _column_sums(n: dict, accepted: list[dict], tables: dict[int, int],
                 lines: list[str]) -> list[float]:
    """Sums of every run of the accepted figures above `n` in its table column."""
    col = _column_of(lines[n["line"]], n["match"].start())
    above = [abs(a["value"]) for a in sorted(accepted, key=lambda a: a["line"])
             if a["line"] in tables and tables[a["line"]] == tables[n["line"]]
             and a["line"] < n["line"] and _column_of(lines[a["line"]], a["match"].start()) == col]
    above = above[-40:]
    return [sum(above[i:j]) for i in range(len(above)) for j in range(i + 2, len(above) + 1)]


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
    # Trailing spaces or a masked blank are not part of the value.
    masked = _masked(line)
    while end > start and masked[end - 1].isspace():
        end -= 1
    return {"kind": kind, "text": line[start:end], "line": k, "start": start, "end": end,
            "placeholder": PLACEHOLDER[kind]}


def person_days(text: str) -> dict[tuple[int | None, int, int], str]:
    """Days the person wrote (request and files, not search pages), each as written."""
    out: dict[tuple[int | None, int, int], str] = {}
    for pattern in (FULL_DAY, MONTH_DAY, SHORT_DAY):
        for m in pattern.finditer(text or ""):
            y = int(m.group("y")) if "y" in m.groupdict() and m.group("y") else None
            mo, d = int(m.group("m")), int(m.group("d"))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.setdefault((y, mo, d), m.group(0).strip())
    # 「2027. 2. 3. ~ 2. 5.」: a range's short end is in its start's year
    for m in FULL_DAY.finditer(text or ""):
        if end := _RANGE_END.match(text, m.end()):
            mo, d = int(end.group("m")), int(end.group("d"))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.setdefault((int(m.group("y")), mo, d), end.group(0).strip(" ~∼〜-"))
    return out


_RANGE_END = re.compile(
    r"\s{0,3}[~∼〜-]\s{0,3}(?P<m>\d{1,2})\s{0,2}(?:월|[-./])\s{0,2}(?P<d>\d{1,2})(?!\d)\s{0,2}(?:일|\.)?")


#: The words a deadline ends on: 「11. 6.(금) 18:00까지」.
_DEADLINE_CUE = re.compile(r"[^.\n]{0,16}?(?=까지|마감|기한)")
#: What a garbled copy of a deadline's time leaves between the day and 「까지」: 「 00」, 「 00(:00)」.
_TIME_DEBRIS = re.compile(r"[\s\d:()시분]*")


def person_deadlines(text: str) -> dict[tuple[int | None, int], list[str]]:
    """Deadlines the person wrote, by (year, month), each as written up to 「까지」: 「2026. 11. 6.(금) 18:00」."""
    out: dict[tuple[int | None, int], list[str]] = {}
    for pattern in (FULL_DAY, MONTH_DAY, SHORT_DAY):
        for m in pattern.finditer(text or ""):
            cue = _DEADLINE_CUE.match(text, m.end())
            if cue is None or not _TIME_DEBRIS.fullmatch(cue.group(0)):
                continue
            y = int(m.group("y")) if "y" in m.groupdict() and m.group("y") else None
            phrase = text[m.start():cue.end()].strip()
            for key in {(y, int(m.group("m"))), (None, int(m.group("m")))}:
                if phrase not in out.setdefault(key, []):
                    out[key].append(phrase)
    return out


#: 「00시 2214분」: minutes no clock has — another figure (an extension) run into a time.
_BROKEN_TIME = re.compile(r"(?<![\d:])\d{1,2}\s{0,2}시\s{0,2}\d{3,}\s{0,2}분")


def _a_misread_day(y: int | None, mo: int, d: int, own: dict, near_deadline: bool) -> str | None:
    """None for the person's own day (or undated month); else their one day that month, or a blank."""
    if (y, mo, d) in own or (None, mo, d) in own:
        return None
    if y is None and any(key[1:] == (mo, d) for key in own):
        # 「2. 3.」 for the person's 「2027-02-03」: the year left out, the day theirs
        return None
    same = {key: text for key, text in own.items()
            if key[1] == mo and (y is None or key[0] is None or key[0] == y)}
    if not same:
        return None
    days = {key[1:] for key in same}
    if near_deadline and len(days) == 1:
        return max(same.values(), key=len)
    return PLACEHOLDER["date"]


def _other_values(line: str, masked: str, sources: Sources, k: int, found: list[dict],
                  options: dict) -> list[tuple[int, int]]:
    """Dates, months, times and names on one line; returns the spans they take."""
    taken: list[tuple[int, int]] = []
    own = person_days(str(options.get("person") or ""))

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in taken)

    def add(kind: str, start: int, end: int) -> None:
        taken.append((start, end))
        found.append(_finding(kind, line, k, start, end))

    def day(m: re.Match, y: int | None, mo: int, d: int) -> None:
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return
        if sources.counted(y, mo, d):
            return
        if (y is None or (y, mo, d) not in sources.days) and (None, mo, d) not in sources.days:
            # A garbled copy of the person's one deadline that month goes back to it whole.
            cue = _DEADLINE_CUE.match(masked, m.end())
            given = person_deadlines(str(options.get("person") or "")).get((y, mo), [])
            if cue is not None and len(given) == 1 and _TIME_DEBRIS.fullmatch(cue.group(0)):
                end = m.end() + len(cue.group(0).rstrip())
                taken.append((m.start(), end))
                found.append({**_finding("date", line, k, m.start(), end), "placeholder": given[0]})
                return
            found.append(_finding("date", line, k, *m.span()))
            return
        deadline = bool(re.match(r"[^.\n]{0,16}?(?:까지|마감|기한)", masked[m.end():]))
        if (instead := _a_misread_day(y, mo, d, own, deadline)) is not None:
            found.append({**_finding("date", line, k, *m.span()), "placeholder": instead})

    for m in _BROKEN_TIME.finditer(masked):
        add("time", *m.span())
    for m in FULL_DAY.finditer(masked):
        if not free(*m.span()):
            continue
        taken.append(m.span())
        day(m, int(m.group("y")), int(m.group("m")), int(m.group("d")))
    for pattern in (MONTH_DAY, SHORT_DAY):
        for m in pattern.finditer(masked):
            if not free(*m.span()):
                continue
            taken.append(m.span())
            day(m, None, int(m.group("m")), int(m.group("d")))
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
        if "○○" not in name and name[0] not in SURNAMES and _LINKING_END.search(name):
            # 「담당 교수가 달라 교수 효과와」: a verb running on, not a name
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
    """A title held by someone (「박준영 대리」), not 「팀장님께 보고」 in a request."""
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


def _required(part: str, sources: Sources) -> bool:
    """Whether `part` asks the reader to bring or hand in something no source names."""
    for m in REQUIRE.finditer(part):
        if DESCRIBED.match(part, m.end()):
            # 「마감에 맞춰 제출됩니다」, 「통합 제출이 가능합니다」: how it goes, not what to bring
            continue
        obj = re.sub(r"(?:을|를|도|만|등|및)$", "", m.group("obj"))
        if len(obj) < 2 or obj in GENERIC_OBJECTS or NOT_OBJECT.search(obj):
            continue
        if obj.lower() not in sources.lower:
            return True
    return False


def _requirements(lines: list[str], skip: set[int], sources: Sources) -> list[dict]:
    """Sentences telling the reader to bring or submit an item the material never names."""
    out = []
    for k, line in enumerate(lines):
        if k in skip or line.strip().startswith("|"):
            continue
        pos = 0
        for part in re.split(f"({SENTENCE_BREAK.pattern})", line):
            if _required(part, sources):
                out.append({"kind": "requirement", "text": part, "line": k, "start": pos,
                            "end": pos + len(part), "placeholder": ""})
            pos += len(part)
    return out


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


_TERM_OR_BLANK = (r"(?:\[수치\]|\d[\d,]*(?:\.\d+)?\s?(?:천만\s?원|백만\s?원|억\s?원|만\s?원|천\s?원|원|개월|명|대|건|개|%|시간|일|회)?"
                  r"(?:/(?:대|월|명|개|건|년|일|인|회|팀|시간|가구)){0,3})")
#: A worked calculation (「[수치] ÷ 30 ÷ 10개월 = 0만원/대/월」): one blank leaves the rest wrong.
_CHAIN = re.compile(rf"{_TERM_OR_BLANK}(?:\s{{0,2}}[÷×*+=−]\s{{0,2}}{_TERM_OR_BLANK}){{1,8}}")


def _collapse_chain(part: str) -> str:
    """A calculation holding a blank becomes one blank."""
    def one(m: re.Match) -> str:
        chain = m.group(0)
        if "[수치]" not in chain or ("=" not in chain and len(re.findall(r"[÷×*+−]", chain)) < 2):
            return chain
        return "[수치]"
    return _CHAIN.sub(one, part)


#: a heading or a bold item title: kept whole when its values are blanked
_TITLE_LINE = re.compile(r"\s{0,3}(?:#{1,6}\s|\*\*|(?:[-*+•]|\d{1,2}[.)])\s{1,4}\*\*)")


def _fix_line(line: str, items: list[dict]) -> str | None:
    """The line with each finding blanked; `None` when nothing is left of it."""
    if line.lstrip().startswith("|"):
        cells, pos, out = line.split("|"), 0, []
        for cell in cells:
            start, end = pos, pos + len(cell)
            mine = [f for f in items if start <= f["start"] and f["end"] <= end and f["kind"] not in DROPPED]
            out.append(_blank_cell(cell, mine, start) if mine else cell)
            pos = end + 1
        return "|".join(out)
    pos, kept = 0, []
    for part in re.split(f"({SENTENCE_BREAK.pattern})", line):
        start = pos
        pos += len(part)
        # a mark after a line's last sentence sits at its very end
        mine = [f for f in items if start <= f["start"] < start + max(1, len(part))
                or (_marks(f) and f["start"] == pos and part.strip())]
        if not mine:
            kept.append(part)
            continue
        if any(f["kind"] in DROPPED for f in mine):
            kept.append("")
            continue
        rest = part
        for f in sorted(mine, key=lambda f: -f["start"]):
            rest = rest[: f["start"] - start] + rest[f["end"] - start:]
        # Invented values carrying the sentence alone: two or more, and no number left. A
        # heading or bold list title (「**2. 등록금 납부 — …**」) keeps its words.
        if len(mine) >= 2 and not re.search(r"\d", rest) and not _TITLE_LINE.match(line) and all(
                f["placeholder"] in PLACEHOLDER.values() and not _marks(f) for f in mine):
            kept.append("")
            continue
        for f in sorted(mine, key=lambda f: -f["start"]):
            part = part[: f["start"] - start] + f["placeholder"] + part[f["end"] - start:]
        kept.append(_collapse_chain(part))
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", "".join(kept)).rstrip()
    if line.count("**") % 2 == 0 and text.count("**") % 2:
        # a dropped piece took a bold pair's closing half
        text += "**"
    if any(f["kind"] == "requirement" for f in items):
        # a dropped first notice keeps the line's indent and list marker
        lead = re.match(r"\s*(?:[-*+•]\s+|\d{1,2}[.)]\s+)?", line).group(0)
        text = lead + (text[len(lead):] if text.startswith(lead) else text).lstrip()
        if text.strip() == lead.strip():
            return None
    return text if text.strip() else None


_KO_DIGIT = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_KO_SMALL = {"십": 10, "백": 100, "천": 1000}
_KO_BIG = {"만": 10**4, "억": 10**8, "조": 10**12}
#: 「금 3,200,000원(금삼백이십만원)」, 「금[수치](금이백십이만원)」: an amount and its words
_SPELLED = re.compile(
    r"(?P<num>\d[\d,]*|\[수치\]|\[금액\])\s{0,2}(?:원)?\s{0,2}"
    r"\(\s{0,2}금\s{0,2}(?P<ko>[영일이삼사오육칠팔구십백천만억조]+)\s{0,2}원\s{0,2}(?:정)?\s{0,2}\)")


def ko_spelled(words: str) -> int | None:
    """「이백십이만」 → 2120000; None when not a numeral."""
    total = section = digit = 0
    for ch in words:
        if ch in _KO_DIGIT:
            digit = _KO_DIGIT[ch]
        elif ch in _KO_SMALL:
            section += (digit or 1) * _KO_SMALL[ch]
            digit = 0
        elif ch in _KO_BIG:
            total += ((section + digit) or 1) * _KO_BIG[ch]
            section = digit = 0
        else:
            return None
    return total + section + digit


def spell_ko(value: int) -> str:
    """2120000 → 「이백십이만」 (공문서 금액 표기)."""
    if value == 0:
        return "영"
    digits = "영일이삼사오육칠팔구"
    out = ""
    for unit, size in (("조", 10**12), ("억", 10**8), ("만", 10**4), ("", 1)):
        part, value = divmod(value, size)
        if not part:
            continue
        words = ""
        for name, step in (("천", 1000), ("백", 100), ("십", 10), ("", 1)):
            d, part = divmod(part, step)
            if d:
                words += ("" if d == 1 and name else digits[d]) + name
        out += words + unit
    return out


def _spelled_amounts(text: str) -> str:
    """Words of an amount follow its figure: dropped with a blanked figure, rewritten when
    they say another amount."""
    def one(m: re.Match) -> str:
        whole, ko = m.group(0), m.group("ko")
        head = whole[: m.start("ko") - m.start()]
        if m.group("num").startswith("["):
            return whole[: whole.index("(")].rstrip()
        value = int(m.group("num").replace(",", ""))
        said = ko_spelled(ko)
        if said is None or said == value:
            return whole
        return head + spell_ko(value) + whole[m.end("ko") - m.start():]
    return _SPELLED.sub(one, text or "")


def fix(text: str, findings: list[dict]) -> str:
    """`text` with findings blanked, or sentences dropped (claims, requirements, invented-only)."""
    by_line: dict[int, list[dict]] = {}
    for f in findings or []:
        by_line.setdefault(int(f["line"]), []).append(f)
    out = []
    for k, line in enumerate((text or "").split("\n")):
        if k not in by_line:
            out.append(line)
        elif (fixed := _fix_line(line, by_line[k])) is not None:
            out.append(fixed)
    return _spelled_amounts("\n".join(out))


def summary(findings: list[dict]) -> str:
    """「근거 없는 값 3개 → 확인 필요 (수치 2 · 날짜 1): 「11월 초」, …」."""
    if not findings:
        return ""
    counts: dict[str, int] = {}
    for f in findings:
        word = KIND_WORDS[f["kind"]]
        counts[word] = counts.get(word, 0) + 1
    kinds = " · ".join(f"{word} {n}" for word, n in counts.items())
    shown = ", ".join(dict.fromkeys(f"「{f['text'].strip()[:24]}」" for f in findings if f["kind"] != "claim"))
    return f"근거 없는 값 {len(findings)}개 → 확인 필요 ({kinds})" + (f": {shown[:300]}" if shown else "")


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
