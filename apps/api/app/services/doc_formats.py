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

The web mirror is ``apps/web/src/components/report/docFormats.ts``; a test keeps them equal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Shown where a head field is still blank.
BLANK = "(기입)"


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
    #: (headline, subtitle, dateline).
    head: str = "header"
    citation: str = "apa"
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
        r"실험\s{0,3}(?:결과\s{0,3})?보고서|결과\s{0,3}보고서|예비\s{0,3}보고서|실험\s{0,3}레포트",
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
        "official", "공문·안내문", r"공문|시행\s{0,3}문|안내문|공지문|협조\s{0,3}요청",
        ("수신", "참조", "발신", "시행일"), numbering="official", head="memo",
    ),
    DocFormat(
        "incident", "장애 보고서", r"장애|사고\s{0,3}보고|incident|포스트모템|post-?mortem",
        ("발생 일시", "영향 범위", "심각도", "작성자"), head="header",
    ),
    DocFormat(
        "status", "업무·현황 보고",
        r"주간\s{0,3}보고|월간\s{0,3}보고|업무\s{0,3}보고|현황\s{0,3}보고|진척\s{0,3}보고|진행\s{0,3}상황",
        ("보고일", "보고자", "대상 기간", "보고 대상"), numbering="korean", head="memo",
    ),
    DocFormat(
        "plan", "연구·조사 계획서",
        r"연구\s{0,3}계획서|조사\s{0,3}계획서|연구\s{0,3}제안서|계획서",
        ("과제명", "연구자", "소속", "지도교수", "작성일"), head="cover",
    ),
    DocFormat(
        "proposal", "프로젝트 제안서",
        r"캡스톤|프로젝트\s{0,3}제안서|사업\s{0,3}제안서|제안서|기획서",
        ("팀명", "팀원", "지도교수", "제출일"), head="cover",
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
_COMPILED = [(f, re.compile(f.match, re.I)) for f in FORMATS]

#: Render templates that already fix the purpose.
TEMPLATE_FORMATS = {
    "doc-lab": "lab", "doc-minutes": "minutes", "doc-incident": "incident",
    "doc-notice": "official", "doc-proposal": "proposal", "doc-project-brief": "proposal",
    "doc-term-paper": "term", "doc-survey": "plan", "doc-brief": "biz",
}


def detect(instruction: str, template_id: str | None = None) -> DocFormat | None:
    """The template's format, else the first format the instruction names; None otherwise."""
    if template_id and template_id in TEMPLATE_FORMATS:
        return BY_ID[TEMPLATE_FORMATS[template_id]]
    for fmt, pattern in _COMPILED:
        if pattern.search(instruction or ""):
            return fmt
    return None


def fill_prompt(fmt: DocFormat, request: str, title: str) -> str:
    """The question for the head fields: values only from the request, blanks otherwise."""
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
        "규칙: 요청에 **그대로 적힌 값만** 옮겨 적는다. 요청에 없는 이름·학번·날짜·장소·"
        "기관은 지어내지 말고 빈 문자열로 둔다. 「팀원 4명」처럼 수만 있으면 그 말 그대로.\n"
        + (f"그 밖에: {'; '.join(extra)}\n" if extra else "")
        + '\nJSON 객체 하나로만 답하라. 예: {"fields": {"과목": "회로실험", "이름": ""}'
        + (', "subtitle": ""' if fmt.subtitle else "")
        + (', "keywords": []' if fmt.keywords else "")
        + "}\n\n요청:\n"
        + request[:3000]
    )


def title_block(fmt: DocFormat, answer: dict[str, Any], request: str) -> dict[str, Any]:
    """The artifact's ``titleBlock``; a value not found in the request becomes a blank."""
    given = " ".join((request or "").split())
    squeezed = re.sub(r"\s+", "", given)
    raw = answer.get("fields") if isinstance(answer.get("fields"), dict) else {}

    def grounded(value: Any) -> str:
        text = " ".join(str(value or "").split())[:80]
        if not text:
            return ""
        # Every token of the value must appear in the request.
        tokens = [t for t in re.split(r"[\s,·/()]+", text) if t]
        return text if all(re.sub(r"\s+", "", t) in squeezed for t in tokens) else ""

    fields = [[name, grounded(raw.get(name))] for name in fmt.fields]
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


def wire() -> list[dict[str, Any]]:
    """The table the web mirror must equal."""
    return [
        {"id": f.id, "label": f.label, "fields": list(f.fields), "numbering": f.numbering,
         "head": f.head, "abstract": f.abstract, "keywords": f.keywords, "subtitle": f.subtitle}
        for f in FORMATS
    ]
