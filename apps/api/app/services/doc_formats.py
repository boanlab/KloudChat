"""Per-purpose document title blocks and heading numbering; mirrored in web `docFormats.ts`."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services import placeholders


def blank(label: str) -> str:
    """Blank head field placeholder: 「[작성자]」."""
    return placeholders.blank(label)


@dataclass(frozen=True)
class DocFormat:
    id: str
    label: str
    #: Matched on the instruction, not pasted material.
    match: str
    fields: tuple[str, ...]
    numbering: str = "decimal"
    abstract: bool = False
    keywords: bool = False
    subtitle: bool = False
    #: Title block layout: cover, header, memo, press, draft (결재란 + title).
    head: str = "header"
    citation: str = "apa"
    #: ``gaejo``: □ / ○ / - / ㆍ lists, ※ notes; empty = plain.
    lists: str = ""
    #: 「끝.」 after body or 붙임 (시행규칙 제4조제5항).
    closing: bool = False
    #: Fields shown only when the material gives them.
    optional: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


FORMATS: tuple[DocFormat, ...] = (
    DocFormat(
        "review", "피어리뷰",
        r"피어\s{0,3}리뷰|peer\s{0,3}review|리뷰어|심사\s{0,3}(?:의견|보고)|메타\s{0,3}리뷰",
        ("심사 대상", "심사 의견", "확신도"), numbering="none", head="header",
    ),
    DocFormat(
        "paper", "학술 논문",
        r"논문\s{0,3}초안|논문을\s{0,3}써|투고|학회\s{0,3}논문|워크숍\s{0,3}논문|paper\s{0,3}draft",
        ("저자", "소속", "교신저자"), numbering="roman", abstract=True, keywords=True,
        head="paper", citation="ieee",
    ),
    DocFormat(
        "lab", "실험 보고서",
        r"실험\s{0,3}(?:결과\s{0,3})?보고서|예비\s{0,3}보고서|실험\s{0,3}레포트",
        ("실험일", "과목·분반", "조", "학번·이름", "공동실험자", "제출일"), head="cover",
    ),
    DocFormat(
        "minutes", "회의록", r"회의록|미팅\s{0,3}메모|회의\s{0,3}메모|녹취\s{0,3}정리",
        ("회의명", "일시", "장소", "참석자", "작성자"), numbering="none", head="memo",
    ),
    DocFormat(
        # After minutes: 「회의록」 wins when both are named.
        "agenda", "회의 안건",
        r"회의\s{0,3}안건(?!별)|안건\s{0,3}(?:지|목록)|안건\s{0,3}표(?!결)|어젠다|(?<![A-Za-z])agenda(?![A-Za-z])",
        ("회의명", "일시", "장소", "참석자", "작성자"), numbering="none", head="memo",
    ),
    DocFormat(
        "press", "보도자료", r"보도\s{0,3}자료|press\s{0,3}release|언론\s{0,3}배포",
        ("배포일", "보도 시점", "문의처"), numbering="none", subtitle=True, head="press",
    ),
    DocFormat(
        # 별지 제2호서식: 내부결재 only; full 기안문·시행문 is separate.
        "internal_draft", "간이기안문",
        r"간이\s{0,3}기안|내부\s{0,3}결재|기안\s{0,3}(?:문|서)|기안해",
        ("생산등록번호", "등록일", "결재일", "공개 구분", "기안자", "검토자", "결재권자"),
        numbering="korean", head="draft", lists="gaejo", closing=True,
    ),
    DocFormat(
        "official", "공문·안내문", r"공문|시행\s{0,3}문|안내문|공지문|협조\s{0,3}요청",
        ("수신", "참조", "발신", "시행일"), numbering="official", head="memo", closing=True,
    ),
    DocFormat(
        "incident", "장애 보고서",
        r"장애(?!인)|사고\s{0,3}보고|incident|포스트모템|post-?mortem",
        ("발생 일시", "영향 범위", "심각도", "작성자"), head="header",
    ),
    DocFormat(
        "result_report", "결과 보고서",
        r"결과\s{0,3}보고|출장\s{0,3}보고|실적\s{0,3}보고",
        ("보고일", "보고 부서", "보고자"),
        numbering="korean", head="memo", lists="gaejo", closing=True,
    ),
    DocFormat(
        # Before `plan`, so bare-계획서 fallback doesn't apply.
        "grant", "공모·지원사업 계획서",
        r"공모\s{0,3}(?:사업)?[^\n]{0,30}?(?:계획|신청)"
        r"|(?:지원|보조|위탁|공모)\s{0,3}사업[^\n]{0,30}?(?:계획서|신청서)"
        r"|(?:계획서|신청서)[^\n]{0,40}?(?:공모|지원\s{0,3}사업|보조\s{0,3}사업|보조금|심사\s{0,3}위원)"
        r"|(?:공모|지원\s{0,3}사업|보조\s{0,3}사업|보조금)[^\n]{0,40}?(?:계획서|신청서)",
        ("사업명", "신청 기관", "사업 기간", "작성일"), head="cover",
        optional=("사업 기간",),
    ),
    DocFormat(
        "plan", "연구·조사 계획서",
        r"(?:연구|조사)\s{0,3}계획|연구\s{0,3}제안서",
        ("과제명", "연구자", "소속", "연구 기간", "지도교수", "작성일"), head="cover",
        optional=("연구 기간", "지도교수"),
    ),
    DocFormat(
        "event_plan", "추진 계획(안)",
        r"추진\s{0,3}계획|운영\s{0,3}계획|계획\s{0,2}\(안\)|(?<!사업)(?<!사업\s)계획안(?!내)"
        r"|(?:행사|교육|연수|워크숍|세미나|오리엔테이션|(?<![A-Za-z])OT(?![A-Za-z])|캠프|축제"
        r"|설명회|간담회|발대식|대회|체험)[^\n.]{0,12}?계획",
        ("작성일", "작성 부서", "작성자"),
        numbering="korean", head="memo", lists="gaejo", closing=True,
    ),
    DocFormat(
        "gaejo", "개조식 보고서", r"개조식",
        ("보고일", "보고자(부서)", "보고 대상"),
        numbering="korean", head="memo", lists="gaejo", closing=True,
    ),
    DocFormat(
        "status", "업무·현황 보고",
        r"주간\s{0,3}보고|월간\s{0,3}보고|업무\s{0,3}보고|현황\s{0,3}보고|진척\s{0,3}보고|진행\s{0,3}상황",
        ("보고일", "보고자", "대상 기간", "보고 대상"), numbering="korean", head="memo",
    ),
    DocFormat(
        # Before client proposal so 「캡스톤 사업 제안서」 stays here.
        "proposal", "프로젝트 제안서",
        r"캡스톤|프로젝트\s{0,3}제안서|기획서",
        ("팀명", "팀원", "지도교수", "제출일"), head="cover",
    ),
    DocFormat(
        "client_proposal", "고객 제안서",
        r"(?:고객|사업|영업|입찰|용역)\s{0,3}(?:맞춤\s{0,3})?제안서"
        r"|(?:고객사|RFP|제안\s{0,3}요청서|발주처|입찰)[^\n]{0,60}?제안서"
        r"|제안서[^\n]{0,60}?(?:고객사|RFP|제안\s{0,3}요청서|발주처|입찰)",
        ("고객사", "사업명", "제안사", "제안일", "담당자", "연락처", "유효기간"), head="cover",
    ),
    DocFormat(
        "comparison", "비교·분석 보고서",
        r"비교\s{0,3}[·ㆍ/]?\s{0,3}(?:분석|검토|평가)(?:하는|한)?\s{0,3}(?:보고서|보고|문서)"
        r"|대안\s{0,3}(?:비교|평가)|(?:업체|제품|솔루션|서비스|입지|공급사|도입)\s{0,3}(?:비교|선정)"
        r"\s{0,3}(?:보고서|보고|검토|분석)",
        ("검토 대상", "판단 기준", "작성일", "작성자"), head="header",
    ),
    DocFormat(
        "career_history", "경력기술서",
        r"경력\s{0,3}기술서|커리어\s{0,3}기술서|career\s{0,3}(?:history|description)",
        ("이름", "연락처", "이메일", "지원 회사", "지원 직무", "작성일"), numbering="none",
        head="header",
    ),
    DocFormat(
        "biz", "기획안·업무 문서", r"기획안|품의|업무\s{0,3}제안|검토\s{0,3}보고|결과\s{0,3}보고",
        ("작성 부서", "작성자", "작성일"), numbering="korean", head="memo",
    ),
    DocFormat(
        "term", "대학 과제 리포트",
        r"리포트|레포트|기말\s{0,3}(?:과제|보고서)|중간\s{0,3}(?:과제|보고서)|과제\s{0,3}보고서|에세이",
        ("과목", "담당교수", "학과", "학번", "이름", "제출일"), head="cover",
    ),
)

BY_ID: dict[str, DocFormat] = {f.id: f for f in FORMATS}
#: Retired formats: kept by existing agents, never newly chosen.
RETIRED = frozenset({"client_proposal", "career_history"})
_COMPILED = [(f, re.compile(f.match, re.I)) for f in FORMATS]
#: Last-resort matches for bare 「계획서」 / 「제안서」; 「사업계획서」 stays a plain report.
_FALLBACKS = [
    (BY_ID["plan"], re.compile(r"(?<!사업)(?<!사업\s)(?<!사업\s\s)계획서")),
    (BY_ID["proposal"], re.compile(r"제안서")),
]

#: `draft` 결재란 columns in signing order.
APPROVERS = ("기안자", "검토자", "결재권자")

def _template_formats() -> dict[str, str]:
    """`doc_format` from each render template's `template.toml`."""
    root = Path(__file__).resolve().parent.parent / "design_templates"
    out: dict[str, str] = {}
    for manifest in sorted(root.glob("*/template.toml")):
        fmt = str(tomllib.loads(manifest.read_text(encoding="utf-8")).get("doc_format") or "")
        if fmt:
            if fmt not in BY_ID:
                raise ValueError(f"{manifest.parent.name}: unknown doc_format {fmt!r}")
            out[manifest.parent.name] = fmt
    return out


#: Purpose-fixing render templates by id.
TEMPLATE_FORMATS = _template_formats()


def detect(
    instruction: str, template_id: str | None = None, fixed: str | None = None
) -> DocFormat | None:
    """Template format, else `fixed`, else first named in instruction; else None."""
    if template_id and template_id in TEMPLATE_FORMATS:
        return BY_ID[TEMPLATE_FORMATS[template_id]]
    if template_id and template_id.startswith("ut-"):
        # User templates carry their own format.
        from app.services import design_templates

        template = design_templates.get(template_id)
        if template is not None and template.doc_format_def is not None:
            return template.doc_format_def
    if fixed and fixed in BY_ID:
        return BY_ID[fixed]
    for fmt, pattern in (*_COMPILED, *_FALLBACKS):
        if pattern.search(instruction or ""):
            return fmt
    return None


#: Extra material labels per head field.
_HEAD_ALIASES: dict[str, tuple[str, ...]] = {
    "회의명": ("회의명",),
    "일시": ("일시", "회의 일시", "일자", "날짜"),
    "장소": ("장소", "회의 장소"),
    "참석자": ("참석자", "참석"),
    "작성자": ("작성자", "기록자", "서기"),
    "이름": ("이름", "성명"),
    "연락처": ("연락처", "전화", "휴대폰"),
    "이메일": ("이메일", "e-mail", "email"),
    "보고자": ("보고자", "담당자", "담당", "작성자"),
    "보고 대상": ("보고 대상", "보고대상"),
    "고객사": ("고객사", "발주처", "발주 기관"),
    "사업명": ("사업명", "용역명"),
    "신청 기관": ("신청 기관", "신청기관", "주관 기관", "수행 기관"),
    "사업 기간": ("사업 기간", "사업기간"),
}
#: Labels naming a person in a sentence.
_SAID_LABELS = ("작성자", "보고자", "이름", "보고일", "작성일", "보고 대상")
_DASH = re.compile(r"\s{1,3}[—–-]\s{1,3}")
_HEADER_LINE = re.compile(r"^\s{0,4}\[([^\[\]\n]{4,240})\]\s{0,4}$")
_DAY_TIME = re.compile(
    r"(?:20\d{2}|\d{2})\s{0,2}[-./년]\s{0,2}\d{1,2}\s{0,2}[-./월]\s{0,2}\d{1,2}\s{0,2}일?\.?"
    r"(?:\s{0,2}\([월화수목금토일]\))?(?:\s{0,2}\d{1,2}:\d{2}(?:\s{0,2}[~–-]\s{0,2}\d{1,2}:\d{2})?)?")
_MEETING_NAME = re.compile(r"회의|위원회|미팅|모임|세미나|워크숍|간담회|협의회")
_PLACE_WORD = re.compile(
    r"\d{2,4}\s{0,2}호|회의실|강당|세미나실|\d\s{0,2}층|홀$|센터$|관\s{0,2}\d")
#: 「작성자는 나(조교 정다은)로」 → parenthesised name.
_SAID_ME = r"\s{0,2}(?:는|은)\s{0,2}(?:나|저)\s{0,2}\(([^()\n]{2,30})\)"
#: 「보고자는 시설팀 김도윤 주무관.」
_SAID_VALUE = (r"(?:\s{0,2}(?:는|은)\s{0,2}|\s{1,2})([가-힣A-Za-z0-9○][^,.:\n]{1,30}?)\s{0,2}"
               r"(?:(?:으로|로)\s{0,2}(?:해|써|적어|넣어|부탁)|이야|입니다|이고|예요|[.\n]|$)")
_PROFILE = re.compile(r"경력|이력|자기소개|지원서|프로필|포트폴리오")
_PERSON = re.compile(r"[가-힣]{2,4}(?:○{1,2})?")


def _base(field: str) -> str:
    return re.sub(r"\s{0,2}\(.*?\)", "", field).strip()


def _segments(text: str) -> list[str]:
    """Comma split, parenthesised commas kept."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += ch in "(（"
        depth -= ch in ")）" and depth > 0
        if ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return [s for s in out if s]


def _labelled(line: str, labels: dict[str, str]) -> dict[str, str]:
    """One-line `label: value` pairs."""
    names = sorted(labels, key=len, reverse=True)
    rx = re.compile(r"(?:^|(?<=[\s\[(,/·—-]))(" + "|".join(re.escape(n) for n in names)
                    + r")\s{0,2}[:：]\s{0,3}")
    hits = list(rx.finditer(line))
    out: dict[str, str] = {}
    for k, m in enumerate(hits):
        end = hits[k + 1].start() if k + 1 < len(hits) else len(line)
        value = re.split(r"\]|\s/\s", line[m.end():end], maxsplit=1)[0]
        value = " ".join(value.split()).strip(" ,;·")[:80]
        field_name = labels[m.group(1)]
        if value and field_name not in out:
            out[field_name] = value
    return out


#: a reply sentence, not a field value: 「…빈칸으로 두세요.」, 「…전부입니다.」
_SENTENCE_VALUE = re.compile(r"(?:니다|세요|어요|아요|해요|하세요|주세요|[다요])[.!]?\s*$")


def field_value(name: str, value: str) -> bool:
    """Whether `value` can fill head field `name`: a date field holds a digit, no field a
    whole reply sentence."""
    value = (value or "").strip()
    if not value:
        return True
    if re.search(r"일시|일자|날짜|기간|시간", name) and not re.search(r"\d|\[", value):
        return False
    return not (len(value) > 20 and _SENTENCE_VALUE.search(value))


def head_values(fmt: DocFormat, text: str) -> dict[str, str]:
    """Head fields read by code from request and material."""
    wanted = {_base(f): f for f in fmt.fields}
    labels: dict[str, str] = {}
    for base, field_name in wanted.items():
        for alias in (base, *_HEAD_ALIASES.get(base, ())):
            labels.setdefault(alias, field_name)
    out: dict[str, str] = {}

    def put(field_name: str | None, value: str) -> None:
        value = " ".join((value or "").split()).strip(" ,;·")[:80]
        if field_name and value and field_name not in out and field_value(field_name, value):
            out[field_name] = value

    for line in (text or "").splitlines():
        for field_name, value in _labelled(line, labels).items():
            put(field_name, value)
        for base in _SAID_LABELS:
            field_name = wanted.get(base) or labels.get(base)
            if not field_name or field_name in out:
                continue
            said = (re.search(re.escape(base) + _SAID_ME, line)
                    or re.search(re.escape(base) + _SAID_VALUE, line))
            if said and not said.group(1).startswith(("나", "저")):
                put(field_name, said.group(1))
        header = _HEADER_LINE.match(line)
        if not header:
            continue
        parts = _DASH.split(header.group(1), maxsplit=1)
        kind, rest = (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])
        if "이름" in wanted and _PROFILE.search(kind) and _PERSON.fullmatch(rest.strip()):
            put(wanted["이름"], rest.strip())
        if "일시" not in wanted:
            continue
        for k, seg in enumerate(_segments(rest)):
            if re.search(r"[가-힣]\s{0,2}[:：]", seg):
                continue
            if day := _DAY_TIME.search(seg):
                put(wanted.get("일시"), day.group(0))
            elif k == 0 and "회의명" in wanted and _MEETING_NAME.search(seg):
                put(wanted["회의명"], seg)
            elif "장소" in wanted and _PLACE_WORD.search(seg):
                put(wanted["장소"], seg)
    listed = re.search(r"참석|출석|참가자|참여자", text or "")
    if "참석자" in wanted and wanted["참석자"] not in out and not listed:
        # Memo without attendance line: named people listed, marked as from material.
        if len(people := named_people(text)) >= 2:
            put(wanted["참석자"], ", ".join(people[:8]) + NAMED_NOTE)
    return out


#: Attendees-from-names note.
NAMED_NOTE = " (자료에 나온 이름 기준)"

#: Memo name patterns (shared with gap-check).
_NAMED = (
    re.compile(r"(?:→|->|=>)\s{0,2}([가-힣]{2,4}|[가-힣]○○)"
               r"(?=\s{0,2}(?:[,(]|$)|(?:이|가|님|씨)?\s{0,2}(?:담당|맡))", re.M),
    re.compile(r"\((회장|부회장|위원장|부위원장|팀장|총무|간사|대표|사회자?|의장)\)\s{0,2}$",
               re.M),
    re.compile(r"(?<![가-힣])([가-힣]{2,3})(?:이|가)\s{0,2}(?:하기로|맡기로|맡는|담당하기로)"),
)


def named_people(text: str) -> list[str]:
    """Names/roles the memo assigns, in first-mention order."""
    found: list[tuple[int, str]] = []
    for pattern in _NAMED:
        found += [(m.start(1), m.group(1)) for m in pattern.finditer(text or "")]
    out: list[str] = []
    for _, name in sorted(found):
        if name not in out and name not in ("누가", "누구"):
            out.append(name)
    return out


def fill_prompt(fmt: DocFormat, request: str, title: str, material: str = "") -> str:
    """Head-field prompt: values from request/material only, else blanks."""
    extra = []
    if fmt.subtitle:
        extra.append('"subtitle": 제목 아래 한 줄(40자 이내)')
    if fmt.abstract:
        extra.append('"abstract": 빈 문자열(초록은 본문을 쓴 뒤 채운다)')
    if fmt.keywords:
        extra.append('"keywords": 핵심어 3~5개 목록')
    fields = ", ".join(f'"{name}"' for name in fmt.fields)
    return (
        f"「{fmt.label}」의 머리 정보를 채워라. 문서 제목: {title}\n\n"
        f"항목: {fields}\n"
        "규칙: 요청·자료에 **그대로 적힌 값만** 옮겨 적는다. 없는 이름·학번·날짜·장소·"
        "기관은 지어내지 말고 빈 문자열로 둔다. 「팀원 4명」처럼 수만 있으면 그 말 그대로.\n"
        + (f"그 밖에: {'; '.join(extra)}\n" if extra else "")
        + '\nJSON 객체 하나로만 답하라. 예: {"fields": {"과목": "회로실험", "이름": ""}'
        + (', "subtitle": ""' if fmt.subtitle else "")
        + (', "keywords": []' if fmt.keywords else "")
        + "}\n\n요청:\n"
        + request[:3000]
        + (f"\n\n자료(여기 적힌 값도 옮긴다):\n{material[:6000]}" if material.strip() else "")
    )


def title_block(
    fmt: DocFormat, answer: dict[str, Any], request: str, read: dict[str, str] | None = None
) -> dict[str, Any]:
    """Artifact ``titleBlock``; values not in `request` blanked, code-read `read` wins."""
    given = " ".join((request or "").split())
    squeezed = re.sub(r"\s+", "", given)
    raw = answer.get("fields") if isinstance(answer.get("fields"), dict) else {}

    def grounded(value: Any) -> str:
        text = " ".join(str(value or "").split())[:80]
        if not text:
            return ""
        # Every value token must appear in the request.
        tokens = [t for t in re.split(r"[\s,·/()]+", text.removesuffix(NAMED_NOTE)) if t]
        return text if all(re.sub(r"\s+", "", t) in squeezed for t in tokens) else ""

    fields = [[name, grounded((read or {}).get(name)) or grounded(raw.get(name))]
              for name in fmt.fields]
    fields = [[name, value if field_value(name, value) else ""] for name, value in fields]
    fields = [[name, value] for name, value in fields if value or name not in fmt.optional]
    block: dict[str, Any] = {
        "format": fmt.id,
        "label": fmt.label,
        "head": fmt.head,
        "numbering": fmt.numbering,
        "fields": fields,
    }
    if fmt.subtitle:
        block["subtitle"] = " ".join(str(answer.get("subtitle") or "").split())[:60]
    if fmt.abstract:
        block["abstract"] = ""
    if fmt.keywords:
        words = answer.get("keywords") if isinstance(answer.get("keywords"), list) else []
        block["keywords"] = [" ".join(str(w).split())[:24] for w in words if str(w).strip()][:6]
    if fmt.lists:
        block["lists"] = fmt.lists
    if fmt.closing:
        block["closing"] = True
    return block


def number_heading(numbering: str, level: int, counters: list[int]) -> str:
    """Heading number from running counters ([h2, h3, h4])."""
    if numbering == "none":
        return ""
    korean = "가나다라마바사아자차카타파하"
    a, b, c = (counters + [0, 0, 0])[:3]
    if numbering == "roman":
        romans = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
        return f"{romans[a - 1]}." if level == 1 else f"{chr(64 + b)}."
    if numbering in ("korean", "official"):
        if level == 1:
            return f"{a}."
        if level == 2:
            return f"{korean[(b - 1) % len(korean)]}."
        return f"{c})"
    return f"{a}." if level == 1 else f"{a}.{b}"


_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ("", "십", "백", "천")
_LARGE_UNITS = ("", "만", "억", "조")


def hangul_amount(amount: int) -> str:
    """113560 → 「일십일만삼천오백육십」 (공문 style)."""
    if amount <= 0:
        return "영"
    groups: list[str] = []
    for index, unit in enumerate(_LARGE_UNITS):
        part = (amount // 10 ** (4 * index)) % 10_000
        if not part:
            continue
        words = "".join(
            _DIGITS[digit] + _SMALL_UNITS[place]
            for place in range(3, -1, -1)
            if (digit := (part // 10**place) % 10)
        )
        groups.append(words + unit)
    return "".join(reversed(groups))


def official_amount(amount: int) -> str:
    """「금113,560원(금일십일만삼천오백육십원)」."""
    return f"금{amount:,}원(금{hangul_amount(amount)}원)"


def official_date(year: int, month: int, day: int) -> str:
    """「2026. 10. 7.」."""
    return f"{year}. {month}. {day}."


_FENCE_LINE = re.compile(r"^\s*```")
_KO_DATE = re.compile(r"(?<!\d)(\d{4})\s{0,2}년\s{0,2}(\d{1,2})\s{0,2}월\s{0,2}(\d{1,2})\s{0,2}일")
_ISO_DATE = re.compile(r"(?<![\d.-])(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?![\d-])\.?")
_KO_TIME = re.compile(
    r"(?:(오전|오후)\s{0,2})?(?<!\d)(\d{1,2})\s{0,2}시(?:\s{0,2}(\d{1,2})\s{0,2}분|\s{0,2}(반))?"
    r"(?![간각기점대작설행])"
)


def _date_text(match: re.Match[str]) -> str:
    year, month, day = (int(group) for group in match.group(1, 2, 3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return match.group(0)
    return official_date(year, month, day)


def _time_text(match: re.Match[str]) -> str:
    half, hour = match.group(1), int(match.group(2))
    minute = 30 if match.group(4) else int(match.group(3) or 0)
    # 「3시」 ambiguous: only stated 오전·오후 or 24-hour is certain.
    if half is None and hour < 13:
        return match.group(0)
    if half == "오후" and hour < 12:
        hour += 12
    if hour > 24 or minute > 59:
        return match.group(0)
    return f"{hour:02d}:{minute:02d}"


def official_notation(text: str) -> str:
    """공문 date/time notation outside code fences."""
    out: list[str] = []
    fenced = False
    for line in (text or "").split("\n"):
        if _FENCE_LINE.match(line):
            fenced = not fenced
        elif not fenced:
            line = _KO_DATE.sub(_date_text, line)
            line = _KO_TIME.sub(_time_text, _ISO_DATE.sub(_date_text, line))
        out.append(line)
    return "\n".join(out)


#: Markers per list depth 1–4.
GAEJO_MARKERS = ("□", "○", "-", "ㆍ")
#: 개조식 보고 fixed sections; tables to uncounted 붙임.
GAEJO_OUTLINE = ("현황", "문제점 및 검토", "향후 조치 및 결정 요청", "붙임")
#: Typed markers by depth; 「ㅇ」 = ○.
_TYPED_DEPTH = {
    "□": 1, "■": 1, "○": 2, "◦": 2, "ㅇ": 2, "-": 3, "–": 3, "―": 3, "ㆍ": 4, "·": 4, "∙": 4,
}
_TYPED = re.compile(r"^(\s{0,12})(?:[-*+]\s{1,4})?([□■○◦ㅇ\-–―ㆍ·∙])\s{1,4}(\S.*)$")
_ITEM = re.compile(r"^(\s{0,24})([-*+]|\d{1,3}[.)])\s{1,4}(.*)$")
_ATTACH = re.compile(r"^\s{0,8}붙\s{0,2}임\s{0,4}:?[\s\u00a0]{0,6}(.*)$")
_NUMBERED = re.compile(r"^\s{0,24}\d{1,3}\.\s{1,4}\S")
#: Two NBSPs (2타) before 「끝.」 / after 「붙임」.
GAP = "\u00a0\u00a0"


#: Line starting with a date's month/day remainder.
_DATE_TAIL = re.compile(
    r"^\d{1,2}\.(?P<more>\s{1,2}\d{1,2}\.(?:\([월화수목금토일]\))?|\([월화수목금토일]\))?(?=[\s)]|$)"
)
_DANGLING_NUMBER = re.compile(r"(?:^|[\s(])\d{1,4}\.$")


def _date_tail(line: str, before: str) -> bool:
    """`line` continues a date split from `before`."""
    found = _DATE_TAIL.match(line)
    if not found or not before.strip() or before.lstrip().startswith(("|", "#", "```", "~~~")):
        return False
    return bool(
        _DANGLING_NUMBER.search(before.rstrip())
        or found.group("more")
        or found.end() == len(line)
        or line.count(")") > line.count("(")
    )


def _typed_markers(lines: list[str]) -> bool:
    """Writer typed □/○ instead of nested lists."""
    return any(
        (found := _TYPED.match(line)) and found.group(2) in "□■○◦ㅇ" for line in lines
    )


#: List item holding a table row.
_ITEM_ROW = re.compile(r"^\s{0,24}(?:[-*+]|[□■○◦ㅇㆍ·∙])\s{1,4}(\|.*\|)\s*$")
#: List item that is a table caption.
_ITEM_CAPTION = re.compile(
    r"^\s{0,24}(?:[-*+]|[□■○◦ㅇㆍ·∙])\s{1,4}((?:\*\*)?표\s{0,2}(?:\d{1,3}\.|:).*)$")


def _tables_out_of_items(lines: list[str]) -> list[str]:
    """List-item table rows restored as a captioned table."""
    rows = [bool(_ITEM_ROW.match(line)) for line in lines]
    if sum(rows) < 2:
        return lines
    out: list[str] = []
    for k, line in enumerate(lines):
        if rows[k]:
            if not (k and rows[k - 1]):
                caption = _ITEM_CAPTION.match(out[-1]) if out else None
                if caption:
                    out[-1] = caption.group(1)
                if out and out[-1].strip():
                    out.append("")
            out.append(_ITEM_ROW.match(line).group(1))
            if k + 1 < len(lines) and not rows[k + 1] and lines[k + 1].strip():
                out.append("")
            continue
        out.append(line)
    return out


#: 공문 item line.
_OFFICIAL_ITEM = re.compile(r"^\s{0,12}(?:\d{1,2}[.)]|[가-하][.)]|\(\d{1,2}\)|※|붙\s{0,2}임)\s")


def official_body(text: str) -> str:
    """공문 body, one paragraph per item; tables and fences pass through."""
    out: list[str] = []
    fenced = False

    def table(line: str) -> bool:
        return line.lstrip().startswith("|")

    for line in (text or "").split("\n"):
        if _FENCE_LINE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if _OFFICIAL_ITEM.match(line):
            line = stripped
        # Blank line between lines unless both table rows.
        if out and out[-1].strip() and not (table(out[-1]) and table(line)):
            out.append("")
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


def tables_out_of_items(text: str) -> str:
    """`text` with list-item tables restored."""
    return "\n".join(_tables_out_of_items((text or "").split("\n")))


def gaejo_markdown(text: str) -> str:
    """개조식 body as canonical nested Markdown; tables and fences untouched."""
    lines = _tables_out_of_items((text or "").split("\n"))
    typed = _typed_markers(lines)
    out: list[str] = []
    #: Open list-level indents.
    indents: list[int] = []
    fenced = False
    attaching = False
    #: Last item depth, for a following ※ line.
    item_depth = 0

    def blank() -> None:
        if out and out[-1].strip():
            out.append("")

    for line in lines:
        if _FENCE_LINE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        stripped = line.strip()
        if fenced or stripped.startswith("|"):
            out.append(line)
            attaching = False
            item_depth = 0
            continue
        if not stripped:
            # Gap inside 붙임 run doesn't end it.
            if not attaching:
                indents.clear()
                out.append("")
            continue
        if attaching and _NUMBERED.match(line):
            if out[-1] == "붙임":
                out[-1] = f"붙임{GAP}{stripped}"
            else:
                # Hard break: one paragraph, a line each.
                out[-1] = out[-1].rstrip() + "  "
                out.append(stripped)
            continue
        if attaching:
            blank()
        attaching = False
        if _date_tail(stripped, out[-1] if out else ""):
            # Rejoin a date split at its dots.
            out[-1] = out[-1].rstrip() + " " + stripped
            continue
        if found := _ATTACH.match(line):
            blank()
            rest = found.group(1).strip()
            out.append(f"붙임{GAP}{rest}" if rest else "붙임")
            attaching = True
            indents.clear()
            item_depth = 0
            continue
        if stripped.startswith("※"):
            note = "※ " + stripped[1:].strip()
            if item_depth:
                # ※ under an item: note at that depth.
                while out and not out[-1].strip():
                    out.pop()
                out.append("  " * (item_depth - 1) + "- " + note)
                continue
            blank()
            out.append(note)
            out.append("")
            indents.clear()
            continue
        typed_item = _TYPED.match(line) if typed else None
        if typed_item:
            depth = _TYPED_DEPTH[typed_item.group(2)]
            out.append("  " * (depth - 1) + "- " + typed_item.group(3).strip())
            item_depth = depth
            continue
        if item := _ITEM.match(line):
            indent = len(item.group(1).expandtabs(4))
            while indents and indent < indents[-1]:
                indents.pop()
            if not indents or indent > indents[-1]:
                indents.append(indent)
            depth = min(len(indents), len(GAEJO_MARKERS))
            body = item.group(3).strip()
            if (inner := _TYPED.match(body)) and inner.group(2) in "□■○◦ㅇㆍ·∙":
                depth, body = _TYPED_DEPTH[inner.group(2)], inner.group(3).strip()
            mark = "- " if item.group(2) in "-*+" else item.group(2) + " "
            out.append("  " * (depth - 1) + mark + body)
            item_depth = depth
            continue
        if out and out[-1].lstrip().startswith(("- ", "* ")) and indents:
            # Prose after a list: own paragraph.
            blank()
        indents.clear()
        item_depth = 0
        out.append(stripped)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def attachment_body(text: str) -> str:
    """붙임 section body: label prepended if missing, list-item label split out."""
    lines = (text or "").split("\n")
    at = next((i for i, line in enumerate(lines) if line.strip()), None)
    if at is None:
        return text
    first = lines[at]
    if (item := _ITEM.match(first)) and item.group(2) in "-*+" and _ATTACH.match(item.group(3)):
        lines[at] = item.group(3)
    elif not _ATTACH.match(first):
        lines.insert(at, "붙임")
    return "\n".join(lines)


#: Typed 「끝.」, not 「시작과 끝.」.
_CLOSING = re.compile(
    r"(?:^[\s\u00a0]{0,8}|[\s\u00a0]{2,8}|(?<=[.)」”])[\s\u00a0]{1,8})끝\s{0,2}\.\s{0,4}$"
)


def without_closing(line: str) -> str:
    """Strip typed 「끝.」; renderers add their own."""
    return _CLOSING.sub("", line)


def wire() -> list[dict[str, Any]]:
    """Table the web mirror must equal."""
    return [
        {"id": f.id, "label": f.label, "fields": list(f.fields), "numbering": f.numbering,
         "head": f.head, "abstract": f.abstract, "keywords": f.keywords, "subtitle": f.subtitle,
         "lists": f.lists, "closing": f.closing}
        for f in FORMATS
    ]
