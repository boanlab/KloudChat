"""Documents by purpose: the title block and the numbering each kind of document uses.

Prose rules live in `report._GENRES`. The report artifact carries the result as ``titleBlock``:

    {"format": "lab", "label": "실험 보고서", "subtitle": "",
     "fields": [["실험일", "2026-10-02"], ["과목·분반", ""], ...],
     "abstract": "", "keywords": [], "numbering": "decimal"}

An empty value is a blank the person fills in; a value is never invented. Numbering styles:

- ``decimal`` — 1. / 1.1 (h2 / h3)
- ``roman``   — I. / A.
- ``korean``  — 1. / 가. (and 1) for a third level)
- ``official``— 1. / 가. / 1) / 가) — Korean official documents
- ``none``    — headings unnumbered

``lists: "gaejo"`` sets list depth 1–4 as □ / ○ / - / ㆍ with hanging indents and a line
opening with ※ as a note (개조식); ``closing`` ends the document with 「끝.」 after the last
line of the body or the 붙임 list. Both are drawn by the renderers, never written by the model.

The web mirror is ``apps/web/src/components/report/docFormats.ts``; a test keeps them equal.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services import placeholders


def blank(label: str) -> str:
    """Shown where a head field is still blank: its own name in brackets, 「[작성자]」."""
    return placeholders.blank(label)


@dataclass(frozen=True)
class DocFormat:
    id: str
    label: str
    #: Matched on the person's instruction (not on pasted material).
    match: str
    fields: tuple[str, ...]
    numbering: str = "decimal"
    abstract: bool = False
    keywords: bool = False
    subtitle: bool = False
    #: How the title block is laid out: cover (a page of its own), header (a field table
    #: under the title), memo (field rows above the title, official/minutes), press
    #: (headline, subtitle, dateline), draft (간이기안문: register fields beside the 결재란,
    #: see `APPROVERS`, then the title).
    head: str = "header"
    citation: str = "apa"
    #: ``gaejo``: lists drawn as □ / ○ / - / ㆍ, ※ lines as notes. Empty: plain lists.
    lists: str = ""
    #: 「끝.」 after the body or the 붙임 list (시행규칙 제4조제5항).
    closing: bool = False
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
        "press", "보도자료", r"보도\s{0,3}자료|press\s{0,3}release|언론\s{0,3}배포",
        ("배포일", "보도 시점", "문의처"), numbering="none", subtitle=True, head="press",
    ),
    DocFormat(
        # 별지 제2호서식: 발신하지 않는 내부결재 문서만. A full 기안문·시행문 (수신·발신명의·
        # 결문) is a separate format, not this one.
        "internal_draft", "간이기안문",
        r"간이\s{0,3}기안|내부\s{0,3}결재|기안\s{0,3}(?:문|서)|기안해",
        ("생산등록번호", "등록일", "결재일", "공개 구분", "기안자", "검토자", "결재권자"),
        numbering="korean", head="draft", lists="gaejo", closing=True,
    ),
    DocFormat(
        "official", "공문·안내문", r"공문|시행\s{0,3}문|안내문|공지문|협조\s{0,3}요청",
        ("수신", "참조", "발신", "시행일"), numbering="official", head="memo",
    ),
    DocFormat(
        "incident", "장애 보고서",
        r"장애(?!인)|사고\s{0,3}보고|incident|포스트모템|post-?mortem",
        ("발생 일시", "영향 범위", "심각도", "작성자"), head="header",
    ),
    DocFormat(
        # 실험 결과 보고서 is `lab`, matched earlier.
        "result_report", "결과 보고서",
        r"결과\s{0,3}보고|출장\s{0,3}보고|실적\s{0,3}보고",
        ("보고일", "보고 부서", "보고자"),
        numbering="korean", head="memo", lists="gaejo", closing=True,
    ),
    DocFormat(
        "plan", "연구·조사 계획서",
        r"(?:연구|조사)\s{0,3}계획|연구\s{0,3}제안서",
        ("과제명", "연구자", "소속", "지도교수", "작성일"), head="cover",
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
        # Student capstone: tried before the client proposal so 「캡스톤 사업 제안서」 stays here.
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
        r"비교\s{0,3}[·ㆍ/]?\s{0,3}(?:분석|검토|평가)\s{0,3}(?:보고서|보고|문서)"
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
#: Formats retired with their agents (고객 맞춤 제안서, 자기소개서·경력기술서): an agent that
#: has one keeps it, nothing new chooses it. Documents that name one are still written in it.
RETIRED = frozenset({"client_proposal", "career_history"})
_COMPILED = [(f, re.compile(f.match, re.I)) for f in FORMATS]
#: Tried after every format: a bare 「계획서」 is a study plan unless an event plan matched,
#: and a bare 「제안서」 with no client named keeps the capstone form.
_FALLBACKS = [(BY_ID["plan"], re.compile(r"계획서")), (BY_ID["proposal"], re.compile(r"제안서"))]

#: The 결재란 columns of a `draft` head, in signing order; each field holds 직위와 성명.
APPROVERS = ("기안자", "검토자", "결재권자")

def _template_formats() -> dict[str, str]:
    """Render templates that fix the purpose: each declares `doc_format` in its own
    `template.toml` (read here directly: `design_templates` builds on this module)."""
    root = Path(__file__).resolve().parent.parent / "design_templates"
    out: dict[str, str] = {}
    for manifest in sorted(root.glob("*/template.toml")):
        fmt = str(tomllib.loads(manifest.read_text(encoding="utf-8")).get("doc_format") or "")
        if fmt:
            if fmt not in BY_ID:
                raise ValueError(f"{manifest.parent.name}: unknown doc_format {fmt!r}")
            out[manifest.parent.name] = fmt
    return out


#: Render templates that already fix the purpose, by template id.
TEMPLATE_FORMATS = _template_formats()


def detect(
    instruction: str, template_id: str | None = None, fixed: str | None = None
) -> DocFormat | None:
    """The template's format, else `fixed` (an agent's bound format), else the first format
    the instruction names; None otherwise."""
    if template_id and template_id in TEMPLATE_FORMATS:
        return BY_ID[TEMPLATE_FORMATS[template_id]]
    if fixed and fixed in BY_ID:
        return BY_ID[fixed]
    for fmt, pattern in (*_COMPILED, *_FALLBACKS):
        if pattern.search(instruction or ""):
            return fmt
    return None


# ── head fields read from the material by code ──────────────────────────

#: Labels the material writes a head field under, besides the field's own name.
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
}
#: Labels that name a person in a sentence: 「보고자는 시설팀 김도윤 주무관.」
_SAID_LABELS = ("작성자", "보고자", "이름", "보고일", "작성일", "보고 대상")
_DASH = re.compile(r"\s{1,3}[—–-]\s{1,3}")
_HEADER_LINE = re.compile(r"^\s{0,4}\[([^\[\]\n]{4,240})\]\s{0,4}$")
_DAY_TIME = re.compile(
    r"(?:20\d{2}|\d{2})\s{0,2}[-./년]\s{0,2}\d{1,2}\s{0,2}[-./월]\s{0,2}\d{1,2}\s{0,2}일?\.?"
    r"(?:\s{0,2}\([월화수목금토일]\))?(?:\s{0,2}\d{1,2}:\d{2}(?:\s{0,2}[~–-]\s{0,2}\d{1,2}:\d{2})?)?")
_MEETING_NAME = re.compile(r"회의|위원회|미팅|모임|세미나|워크숍|간담회|협의회")
_PLACE_WORD = re.compile(
    r"\d{2,4}\s{0,2}호|회의실|강당|세미나실|\d\s{0,2}층|홀$|센터$|관\s{0,2}\d")
#: 「작성자는 나(조교 정다은)로」: the name in the parentheses.
_SAID_ME = r"\s{0,2}(?:는|은)\s{0,2}(?:나|저)\s{0,2}\(([^()\n]{2,30})\)"
#: 「보고자는 시설팀 김도윤 주무관.」, 「보고자 학생지원팀.」
_SAID_VALUE = (r"(?:\s{0,2}(?:는|은)\s{0,2}|\s{1,2})([가-힣A-Za-z0-9○][^,.:\n]{1,30}?)\s{0,2}"
               r"(?:(?:으로|로)\s{0,2}(?:해|써|적어|넣어|부탁)|이야|입니다|이고|예요|[.\n]|$)")
_PROFILE = re.compile(r"경력|이력|자기소개|지원서|프로필|포트폴리오")
_PERSON = re.compile(r"[가-힣]{2,4}(?:○{1,2})?")


def _base(field: str) -> str:
    """「보고자(부서)」 → 「보고자」."""
    return re.sub(r"\s{0,2}\(.*?\)", "", field).strip()


def _segments(text: str) -> list[str]:
    """Comma-separated parts, a comma inside parentheses kept: 「김교수(위원장, 좌장), 박교수」."""
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
    """`label: value` pairs on one line; a value runs to the next label, 「]」, 「 / 」 or
    the end of the line."""
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


def head_values(fmt: DocFormat, text: str) -> dict[str, str]:
    """Head fields the request and material state, read by code: 「일시: …」 labels, a memo's
    bracketed header 「[메모 — 교육과정위원회, 2026-10-12(월) 15:00~16:10, 공학관 302호]」,
    「작성자는 나(조교 정다은)로」, 「[경력 정리 — 박준영]」. Lineup v1 left these blank though
    the material gave them (minutes-1/4, career-docs-2)."""
    wanted = {_base(f): f for f in fmt.fields}
    labels: dict[str, str] = {}
    for base, field_name in wanted.items():
        for alias in (base, *_HEAD_ALIASES.get(base, ())):
            labels.setdefault(alias, field_name)
    out: dict[str, str] = {}

    def put(field_name: str | None, value: str) -> None:
        value = " ".join((value or "").split()).strip(" ,;·")[:80]
        if field_name and value and field_name not in out:
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
        # A memo without an attendance line: the people it names (speakers, owners) are listed,
        # marked as read from the material.
        if len(people := named_people(text)) >= 2:
            put(wanted["참석자"], ", ".join(people[:8]) + NAMED_NOTE)
    return out


#: What follows attendees read from the names in a memo, not from an attendance line.
NAMED_NOTE = " (자료에 나온 이름 기준)"

#: Who a memo names: 「포스터 → 지민」, 「… 하자 (회장)」, 「수아가 하기로」
#: (gap-check reads the same).
_NAMED = (
    re.compile(r"(?:→|->|=>)\s{0,2}([가-힣]{2,4}|[가-힣]○○)"
               r"(?=\s{0,2}(?:[,(]|$)|(?:이|가|님|씨)?\s{0,2}(?:담당|맡))", re.M),
    re.compile(r"\((회장|부회장|위원장|부위원장|팀장|총무|간사|대표|사회자?|의장)\)\s{0,2}$",
               re.M),
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


def fill_prompt(fmt: DocFormat, request: str, title: str, material: str = "") -> str:
    """The question for the head fields: values only from the request and material, blanks
    otherwise."""
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
    """The artifact's ``titleBlock``; a value not found in `request` (the request and its
    material) becomes a blank. `read` holds the values code read (`head_values`), which win."""
    given = " ".join((request or "").split())
    squeezed = re.sub(r"\s+", "", given)
    raw = answer.get("fields") if isinstance(answer.get("fields"), dict) else {}

    def grounded(value: Any) -> str:
        text = " ".join(str(value or "").split())[:80]
        if not text:
            return ""
        # Every token of the value must appear in the request (the attendees' provenance note
        # is the code's own words).
        tokens = [t for t in re.split(r"[\s,·/()]+", text.removesuffix(NAMED_NOTE)) if t]
        return text if all(re.sub(r"\s+", "", t) in squeezed for t in tokens) else ""

    fields = [[name, grounded((read or {}).get(name)) or grounded(raw.get(name))]
              for name in fmt.fields]
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
    """The number before a heading, given the running counters ([h2, h3, h4])."""
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


# ── 공문서 표기 (행정업무운영 편람; 시행규칙 제2조) ──────────────────────────

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ("", "십", "백", "천")
_LARGE_UNITS = ("", "만", "억", "조")


def hangul_amount(amount: int) -> str:
    """113560 → 「일십일만삼천오백육십」: every digit read, 일 included, as on a 공문."""
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
    """「금113,560원(금일십일만삼천오백육십원)」 — an amount that is the subject of approval."""
    return f"금{amount:,}원(금{hangul_amount(amount)}원)"


def official_date(year: int, month: int, day: int) -> str:
    """「2026. 10. 7.」: the 연·월·일 dropped for points, no leading zero."""
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
    # 「3시」 may be 15:00 in speech: only a stated 오전·오후 or a 24-hour hour is certain.
    if half is None and hour < 13:
        return match.group(0)
    if half == "오후" and hour < 12:
        hour += 12
    if hour > 24 or minute > 59:
        return match.group(0)
    return f"{hour:02d}:{minute:02d}"


def official_notation(text: str) -> str:
    """Dates as 「2026. 10. 7.」 and times as 「14:00」 outside code fences."""
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


# ── 개조식 ───────────────────────────────────────────────────────────────

#: The marker of each list depth, 1 to 4.
GAEJO_MARKERS = ("□", "○", "-", "ㆍ")
#: A 개조식 보고's fixed sections, in order. The 핵심 두 줄 open the first as a callout;
#: detail tables go to 붙임, which the page limit does not count.
GAEJO_OUTLINE = ("현황", "문제점 및 검토", "향후 조치 및 결정 요청", "붙임")
#: Typed markers by the depth they stand for; 「ㅇ」 is how ○ is often typed.
_TYPED_DEPTH = {
    "□": 1, "■": 1, "○": 2, "◦": 2, "ㅇ": 2, "-": 3, "–": 3, "―": 3, "ㆍ": 4, "·": 4, "∙": 4,
}
_TYPED = re.compile(r"^(\s{0,12})(?:[-*+]\s{1,4})?([□■○◦ㅇ\-–―ㆍ·∙])\s{1,4}(\S.*)$")
_ITEM = re.compile(r"^(\s{0,24})([-*+]|\d{1,3}[.)])\s{1,4}(.*)$")
_ATTACH = re.compile(r"^\s{0,8}붙\s{0,2}임\s{0,4}:?[\s\u00a0]{0,6}(.*)$")
_NUMBERED = re.compile(r"^\s{0,24}\d{1,3}\.\s{1,4}\S")
#: Two non-breaking spaces: the 2타 before 「끝.」 and after 「붙임」, kept by every renderer.
GAP = "\u00a0\u00a0"


#: A line opening with a date's month and day (「12. 31. 만료)」, 「7.(월) 오픈)가」, 「12.」).
_DATE_TAIL = re.compile(
    r"^\d{1,2}\.(?P<more>\s{1,2}\d{1,2}\.(?:\([월화수목금토일]\))?|\([월화수목금토일]\))?(?=[\s)]|$)"
)
_DANGLING_NUMBER = re.compile(r"(?:^|[\s(])\d{1,4}\.$")


def _date_tail(line: str, before: str) -> bool:
    """Whether `line` is the rest of a date the line `before` was cut from."""
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
    """Whether the writer typed □/○ rather than nesting Markdown lists."""
    return any(
        (found := _TYPED.match(line)) and found.group(2) in "□■○◦ㅇ" for line in lines
    )


def gaejo_markdown(text: str) -> str:
    """A 개조식 body as one canonical Markdown: depth by nesting, two spaces a level.

    Typed markers (「□ 현황」, 「 ○ 세부」, 「ㅇ」 for ○) become list items at their depth;
    Markdown lists keep their nesting. A ※ line under an item becomes an item at that
    item's depth (the renderers draw ※ as its marker); elsewhere it stands apart as a
    note. A 붙임 run becomes one paragraph:
    「붙임  1. ○○ 1부.」 with the next items under it. Tables and fences pass through
    untouched.
    """
    lines = (text or "").split("\n")
    typed = _typed_markers(lines)
    out: list[str] = []
    #: Indents of the open Markdown list levels.
    indents: list[int] = []
    fenced = False
    attaching = False
    #: The depth of the last list item, while a ※ line may still stand under it.
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
            # A gap inside the 붙임 run (one paragraph per item, as the editor saves it)
            # does not end the run.
            if not attaching:
                indents.clear()
                out.append("")
            continue
        if attaching and _NUMBERED.match(line):
            if out[-1] == "붙임":
                out[-1] = f"붙임{GAP}{stripped}"
            else:
                # A hard break: the items stay one paragraph, a line each.
                out[-1] = out[-1].rstrip() + "  "
                out.append(stripped)
            continue
        if attaching:
            blank()
        attaching = False
        if _date_tail(stripped, out[-1] if out else ""):
            # A date cut at its dots (「연장(2026.」 / 「12. 31. 만료)」) is one line again.
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
                # A ※ under an item is a note at that item's depth: its own marker, no ○.
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
            # A marker typed inside a Markdown item names its depth.
            if (inner := _TYPED.match(body)) and inner.group(2) in "□■○◦ㅇㆍ·∙":
                depth, body = _TYPED_DEPTH[inner.group(2)], inner.group(3).strip()
            mark = "- " if item.group(2) in "-*+" else item.group(2) + " "
            out.append("  " * (depth - 1) + mark + body)
            item_depth = depth
            continue
        if out and out[-1].lstrip().startswith(("- ", "* ")) and indents:
            # Prose after a list: its own paragraph, not a lazy continuation of the item.
            blank()
        indents.clear()
        item_depth = 0
        out.append(stripped)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def attachment_body(text: str) -> str:
    """The 붙임 section's body under its 붙임 label: 「붙임」 put first when the writer left it
    off, and a label typed as a list item (「- 붙임」) made a line of its own."""
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


#: A typed 「끝.」: alone, after a gap of two, or after a sentence's end — not 「시작과 끝.」.
_CLOSING = re.compile(
    r"(?:^[\s\u00a0]{0,8}|[\s\u00a0]{2,8}|(?<=[.)」”])[\s\u00a0]{1,8})끝\s{0,2}\.\s{0,4}$"
)


def without_closing(line: str) -> str:
    """A line with a 「끝.」 the writer typed taken off; the renderers place their own."""
    return _CLOSING.sub("", line)


def wire() -> list[dict[str, Any]]:
    """The table the web mirror must equal."""
    return [
        {"id": f.id, "label": f.label, "fields": list(f.fields), "numbering": f.numbering,
         "head": f.head, "abstract": f.abstract, "keywords": f.keywords, "subtitle": f.subtitle,
         "lists": f.lists, "closing": f.closing}
        for f in FORMATS
    ]
