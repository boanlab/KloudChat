"""Writing a report, section by section.

Two passes:

* **Outline.** One cheap call returning headings only. This is what makes the
  progress readout honest — six pending sections means six are coming.
* **Sections.** One call each, carrying the outline and everything written so
  far, so section four does not repeat section two.

A failed section is marked and the rest continues: five sections and a gap beat
nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.models.chat import SessionKind
from app.services import deck as deck_rules
from app.services import (
    design,
    figures,
    grounding,
    hangul,
    imagegen,
    pictures,
    research,
    richtext,
    settings_store,
    thinking,
)
from app.services import outline as plan_rules
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Each section is its own model call, so this is the multiplier on the bill.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 8

_OUTLINE_PROMPT = """다음 요청에 맞는 보고서의 제목과 목차를 만들어라.

규칙:
- 제목은 문서의 표지에 적힐 한 줄이다. 요청 문장을 그대로 옮기지 말고,
  주제를 가리키는 명사구로 써라. 마침표와 "~에 대한 보고서" 같은 군말은 빼라.
- **요청에 없는 소재를 지어내지 마라.** 요청이 문서의 쓰임만 말하고 무엇에 대한
  것인지는 말하지 않았으면 그 쓰임을 가리키는 제목을 쓰고, 목차는 그 쓰임이
  요구하는 뼈대로 잡아라. 요청에 없던 분야나 연도를 골라 채운 보고서는 읽는
  사람의 것이 아니어서 그대로 쓸 수 없다.
- 섹션 {lo}~{hi}개.
- 각 섹션은 서로 겹치지 않고, 순서대로 읽으면 하나의 글이 되어야 한다. 절마다
  **서로 다른 물음 하나**에 답한다 — 「비용 비교」와 「옵션 비교 분석」처럼 같은
  물음을 둘로 쪼개지 마라.
- 판단을 구하는 문서(보고·검토·의사결정·제안)는 첫 절이 「요약」, 마지막 절이
  「권고안과 다음 단계」다. 결론과 권고는 그 마지막 절 하나에만 있다.
- **대안이 여럿인 결정 문서는 대안마다 절을 만들지 마라.** 「A의 경제적 분석」
  「B의 경제적 분석」은 표 하나의 열을 절로 쪼갠 것이다. 대신 「대안 비교」 절
  하나에서 같은 기준으로 견준다. **절 제목에 특정 대안의 이름(클라우드, 교체,
  연장)을 넣지 마라** — 대안 이름이 제목에 있으면 그 절은 절이 아니라 표의 열이다.
  그런 문서의 뼈대는 대개 이렇다: 요약 → 현황과 결정할 사안 → 비용 계산의 전제 →
  대안 비교 → 위험과 남는 문제 → 권고안과 다음 단계. 계산이 비교보다 앞이다 —
  표의 숫자는 그 앞 절에서 식으로 구한 값을 옮겨 적는 것이지 표에서 새로 셈하는
  것이 아니다.
  **그 뼈대는 대안을 고르는 문서에만 쓴다.** 회의록·실험 보고서·안내문·동향 분석에
  「비용 계산의 전제」「대안 비교」를 넣지 마라.
- **요청이 항목을 말했으면 그 항목이 목차다.** 「결정 사항, 반론, 다음 발표자와
  기한을 나눠」라고 했으면 절은 그 셋(과 필요한 머리말)이고, 「목적·이론·장치·절차·
  결과·오차 분석」이라고 했으면 그 여섯이다. 요청한 항목을 빼거나 다른 이름으로
  바꾸지 마라.
- 섹션은 제목만. 내용은 쓰지 마라.
- style 은 이 문서가 어디에 쓰이는지에 맞는 인상이다. 셋 중 하나만 골라라:
  · 편집형 — 보고·검토·계획처럼 읽어서 판단하는 문서. 선과 넓은 여백.
  · 포스터형 — 안내·홍보처럼 눈길을 먼저 잡아야 하는 문서. 강한 색면.
  · 미니멀 — 논문·심사 자료처럼 절제가 예의인 문서. 옅은 색과 작은 제목.
  요청에 인상이 적혀 있으면 그것을 따르고, 없으면 주제에서 골라라.
{ask_rule}
- 참고할 자료에 양식·서식 문서가 있으면 그 문서의 항목 순서를 그대로 목차로 써라.
  개수도 그 양식을 따르고, 일반적인 보고서 목차로 바꾸지 마라.

JSON 객체로만 답하라. "subject" 에는 이 문서가 무엇에 대한 것인지를 **요청에 적힌
말 그대로** 적어라 — 요청에 주제가 없으면 빈 문자열. 요청이 대안 여럿 가운데
고르는 것이면 "alternatives" 에 그 대안들의 짧은 이름을 적어라(없으면 빈 배열).
예: {{"title": "전이학습의 소량 데이터 효율성", "style": "미니멀", "subject": "전이학습",
     "sections": ["요약", "배경", "방법", "결과", "한계", "결론"], "alternatives": []}}
예: {{"title": "학과 서버 교체 여부 결정", "style": "편집형", "subject": "학과 서버 교체",
     "sections": ["요약", "현황과 결정할 사안", "비용 계산의 전제", "대안 비교",
                  "위험과 남는 문제", "권고안과 다음 단계"],
     "alternatives": ["교체", "1년 연장", "클라우드 이전"]}}
예: {{"title": "9월 학과 세미나 회의록", "style": "편집형", "subject": "학과 세미나",
     "sections": ["회의 개요", "결정 사항", "반론과 남은 쟁점", "다음 발표자와 기한"],
     "alternatives": []}}

요청: {request}"""

_SECTION_PROMPT = """너는 아래 보고서의 "{heading}" 섹션만 쓰고 있다.

전체 목차:
{outline}

앞 섹션에서 이미 쓴 내용:
{written}

참고 자료:
{refs}

이 절의 역할: {role}
{others}
{facts}

규칙:
- "{heading}" 에 해당하는 내용만 써라. 보고서 전체를 이 절에 넣지 마라. 다른
  절의 몫(비교, 일정, 권고)은 그 절이 쓴다.
- 제목 줄은 쓰지 마라 — 굵은 글씨로도. 본문만. 최상위 제목(#)도 쓰지 마라.
- 앞에서 한 말과 앞에서 그린 표·블록을 되풀이하지 마라.
- 줄글이 기본이다. 한 절은 보통 문단 두셋에서 넷이고, 문단은 이어지는 문장으로
  쓴다. 「- **1번 항목**:」 같은 번호 목록으로 절을 채우지 마라.
- **수치는 위의 「쓸 수 있는 수치」 목록에 있는 것과 그것으로 계산한 값만 쓴다.**
  단위와 자릿수까지 그대로 — 「연 380만 원」은 「3,800만 원」도 「38백만 원」도
  아니다. 계산한 값은 식을 함께 적어라(「62만 원 × 12개월 = 744만 원」). 목록에
  없는 수치 — 사용자 수, 비율, 장애 원인, 피해 규모, 다른 비용, 연도 — 는 만들지
  말고, 필요하면 「(미정)」 「(확인 필요)」 로 적어라. 장애의 원인이나 경위처럼
  요청에 없는 사정을 지어내지 마라.
- 문체는 문서 전체가 하나다. 앞 절이 「~합니다」로 썼으면 이 절도 그렇게 쓴다.
  첫 절이면 「~합니다」 로 쓴다.
- **자료에 없는 고유한 값을 지어내지 마라.** 금액, 날짜, 기관 이름, 사람 이름,
  계약 상대가 그렇다. 결정해야 할 자리라면 값을 채우지 말고 무엇을 정해야
  하는지를 적어라 — "예산 2억 원" 이 아니라 "예산 규모(미정)", "A社·B社" 가
  아니라 "협약 기업(선정 필요)" 이다.
- 참고 자료에서 가져온 사실은 그 자료의 번호를 문장 끝에 [1] 처럼 붙여라.
  목록에 없는 번호는 절대 쓰지 마라. 참고 자료가 없으면 번호도 쓰지 마라.
- 이 규칙 문장들을 본문에 옮겨 적지 마라. 읽는 사람에게 규칙은 보이지 않는다.
{blocks}

원래 요청: {request}"""

_DRAFT_PROMPT = """아래 목차대로 보고서 전체를 한 번에 써라.

목차(이 제목을 이 순서로, `## 제목` 줄로 그대로 쓴다):
{outline}

참고 자료:
{refs}

{facts}

규칙:
- 절마다 `## 제목` 줄로 시작한다. 목차에 없는 절을 만들지 말고, 목차의 절을
  빼지도 마라. 최상위 제목(#)은 쓰지 마라 — 제목은 표지에 따로 붙는다.
- 첫 절이 「요약」이면: 무엇을 결정해야 하는지, 권고가 무엇인지, 근거 둘을 문단
  하나에서 둘로. 표·목록 없이 줄글로만, 200자 안팎.
- 마지막 절이 「권고안」이나 「다음 단계」이면: 권고 하나를 분명히 말하고 그 근거,
  그리고 할 일을 순서대로. 결론과 권고는 이 절에만 있다 — 가운데 절들은 사실과
  비교를 말하고 판단은 여기로 미룬다.
- 가운데 절은 각각 다른 물음에 답한다. **한 절은 문단 둘 이상, 문단은 문장
  서너 개.** 한 문단짜리 절은 절이 아니라 메모다 — 무엇이 그런지, 왜 그런지,
  그래서 읽는 사람에게 무엇이 달라지는지를 쓰면 문단 둘은 나온다. 절 안에
  「결론」 「요약」 「다음 단계」 같은 소제목을 만들지 마라. 「- **1번 항목**:」
  같은 번호 목록으로 절을 채우지 마라. 절 제목을 본문에 굵은 글씨로 다시 쓰지 마라.
- 대안이 둘 이상인 문서는 비교하는 절 첫머리에 표 하나를 반드시 둔다. 열은
  대안, 행은 기준이고, 기준에는 첫해 비용·3년 총비용(식과 함께)·장애 위험·결정
  뒤 남는 문제가 들어간다. 표 위에 이름표를 붙이지 말고 바로 표를 그린 뒤, 아래
  문단에서 표가 무엇을 말하는지 풀어 쓴다 — 열 이름을 하나씩 풀이하는 「비교표
  설명」 목록은 쓰지 마라. 표가 말하는 결론 한두 문장이면 된다. **같은 표를 다른 절에 다시 그리지
  마라** — 문서에 비교표는 하나다.
- 줄글이 기본이다. 비교·항목별 값은 표로 쓰되(행 사이 빈 줄 없이, 3~5행), 문서
  전체에 표는 두 개까지. 표 앞에 무엇을 견주는지, 뒤에 그래서 무엇인지 한 문장씩.
  절의 결론이 되는 숫자 둘셋은 문서 전체에 한 번만 ```kpi 블록(`값 | 이름` 한 줄씩,
  최대 4개)으로. 틀리면 뒤가 무너지는 전제 하나는 문서 전체에 한 번만 ```callout
  블록(첫 줄 제목, 다음 줄 내용)으로. 그 밖의 블록은 쓰지 마라.
- **수치는 위 「쓸 수 있는 수치」에 있는 것과 그것으로 계산한 값만 쓴다.** 요청에
  적힌 표기 그대로 아라비아 숫자로 — 「연 380만 원」은 「3,800만 원」도 「38백만
  원」도 「3백8십만 원」도 아니다.
  계산은 식과 결과를 함께 적고 나눗셈은 소수 첫째 자리까지 쓴다(「62만 원 ×
  12개월 = 744만 원」, 「2,400만 원 ÷ 744만 원 ≈ 3.2년」). 목록에 없는 수치 —
  사용자 수, 비율, 기간, 다른 비용, 잔존 가치, 연도, 영업일 — 는 만들지 말고
  「(미정)」「(확인 필요)」로 적는다. 장애 원인이나 경위처럼 요청에 없는 사정을
  지어내지 마라. 큰 수와 작은 수를 견줄 때는 두 수를 나란히 적어 방향을 확인한다.
- 참고 자료의 사실은 자료 번호를 문장 끝에 [1] 처럼 붙인다. 목록에 없는 번호,
  [설계도면] 같은 이름표는 쓰지 마라. 자료가 없으면 번호도 없다.
- 문체는 문서 전체가 「~합니다」 하나다. 1인칭(「저는」「우리는」)을 쓰지 않는다 —
  「클라우드 이전을 권고합니다」이지 「저는 … 권고합니다」가 아니다.
- 비교표의 숫자는 앞 절에서 식으로 구한 값을 그대로 옮긴다. 표에서 새로 셈하지
  말고, 표와 본문의 같은 항목이 다른 값을 갖지 않게 한다.
- 이 규칙 문장을 본문에 옮겨 적지 마라.

원래 요청: {request}"""


def _split_draft(draft: str, headings: list[str]) -> dict[str, str]:
    """The draft, cut into its sections by the `## ` lines it was asked to write.

    Matched loosely — a heading the model wrote with a stray number, a trailing
    colon or different spacing still lands in its section. A heading it did not
    write at all is simply absent, and the caller writes that one on its own.
    """

    def key(text: str) -> str:
        text = re.sub(r"^[\d.\s]+", "", text.strip())
        return re.sub(r"[\s:：.]+", "", text).lower()

    wanted = {key(h): h for h in headings}
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in draft.splitlines():
        m = re.match(r"^\s*#{1,3}\s+(.+?)\s*$", line)
        if m:
            k = key(m.group(1))
            if k in wanted:
                current = wanted[k]
                found[current] = []
                continue
            # A heading that is not one of ours: a stray sub-heading inside a
            # section. Kept as bold text so the structure stays the outline's.
            if current is not None:
                found[current].append(f"**{m.group(1).strip()}**")
                continue
        if current is not None:
            found[current].append(line)
    return {h: "\n".join(lines).strip() for h, lines in found.items() if "\n".join(lines).strip()}


#: 절의 역할과 그 절에서 쓸 수 있는 블록. 제목의 낱말로 고른다.
#:
#: 블록 여섯 종을 보기와 함께 한꺼번에 주자 모델은 절마다 여섯을 다 썼다 —
#: 요약 절에 표·절차·강조 수치·카드·강조 상자가 전부 들어가고, 다음 절이 그것을
#: 되풀이했다. 보기는 틀이 된다. 그래서 절마다 그 절이 쓸 수 있는 것만 말한다.
_BLOCK_SYNTAX = {
    "table": (
        "- 비교·항목별 값은 표로 쓴다. 행 사이에 빈 줄을 넣지 마라. 3~5행이 알맞고,\n"
        "  표 앞에 무엇을 견주는지, 뒤에 그래서 무엇인지 한 문장씩 둔다.\n"
        "      | 기준 | 대안 A | 대안 B |\n"
        "      | --- | --- | --- |\n"
        "      | 초기 비용 | 0원 | 약 3억 원 |"
    ),
    "kpi": (
        "- 절의 결론이 되는 숫자가 둘셋이면 ```kpi 블록에 `값 | 이름` 을 한 줄씩(최대 4개).\n"
        "  표에 있는 값을 다시 넣지 마라. 그 숫자의 뜻은 본문이 말한다.\n"
        "      ```kpi\n      32% | 오탐 감소\n      1.4초 | 평균 응답 시간\n      ```"
    ),
    "steps": (
        "- 차례대로 하는 일은 ```steps 블록에 `이름 | 설명` 을 한 줄씩(최대 8단계).\n"
        "      ```steps\n      자료 수집 | 공개 데이터와 내부 로그를 모은다\n"
        "      정제 | 중복과 결측을 걸러낸다\n      ```"
    ),
    "cards": (
        "- 서너 갈래를 같은 무게로 나란히 놓을 때(이해관계자·산출물·목표)는 ```cards 블록에\n"
        "  `## 카드 제목` 아래 `- 줄` 을 붙인다(최대 6장, 장마다 다섯 줄 안쪽).\n"
        "      ```cards\n      ## 산출물\n      - 네트워크 전면 교체\n      ## 목표\n"
        "      - 8개월 안에 완료\n      ```"
    ),
    "callout": (
        "- 틀리면 뒤가 다 무너지는 전제·경고·기한 하나는 ```callout 블록에 첫 줄 제목,\n"
        "  다음 줄 내용으로. 문서 전체에 하나까지.\n"
        "      ```callout\n      승인 없이는 시작하지 않는다\n"
        "      9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다.\n      ```"
    ),
    "chart": (
        "- 한 항목이 시점에 따라 변하는 값(시점 4개 이상)은 표가 아니라 ```chart 블록.\n"
        "  첫 줄 `종류 | 단위`, 둘째 줄 `가로축 이름 | 계열 이름들`, 나머지가 값이다.\n"
        "  가로축 8개, 계열 2개까지. 값이 빈 줄은 통째로 빠진다. 지어낸 수치를 쓰지 마라.\n"
        "      ```chart\n      bar | 건\n      분기 | 처리 건수 | 반려 건수\n"
        "      1분기 | 120 | 8\n      2분기 | 210 | 11\n      ```"
    ),
}

_NUMBER = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:억|만|천|백)?\s*(?:원|%|퍼센트|시간|분|초|일|주|개월|년|회|건|명|대|장|쪽|GB|TB|MB|kg|km|m|건수)?",
)


def _facts_line(request: str, sources: list[dict[str, Any]]) -> str:
    """The numbers the document may use, read off the request and the shelf.

    A decision report the person seeded with 「연 380만 원」 came back saying
    3,800만 원 in one section and 380만 원 in the next, with a network
    migration cost of 2,400만 원 that nobody had mentioned. The rule 「자료에
    없는 값을 지어내지 마라」 was in the prompt the whole time. A closed list is
    something a model can be held to; a principle is not.
    """
    found: list[str] = []
    for text in [request, *[str(s.get("quote") or "") for s in sources]]:
        for match in _NUMBER.finditer(text):
            token = re.sub(r"\s+", "", match.group(0))
            if len(token) < 2 or token.isdigit() and len(token) > 4:
                continue
            if token not in found:
                found.append(token)
    if not found:
        return (
            "쓸 수 있는 수치: 없다. 요청과 자료에 수치가 하나도 없다 — **금액·기간·인원·"
            "퍼센트·측정값·부품값·성능 향상률을 어떤 것도 쓰지 마라.** 비용 칸은 「(미정)」, "
            "측정 결과 칸은 「(측정값)」으로 비워 두고, 「비용 계산의 전제」「결과」 같은 절은 "
            "값을 셈하거나 지어내는 대신 무엇을 어떻게 측정·확인해야 하는지(견적, 예산 한도, "
            "대상 인원, 측정 조건)를 적는다. 비교표의 행은 비용 대신 「필요한 것」 「위험」 "
            "「되돌릴 수 있는가」로. 공식과 일반 원리(f_c = 1/(2πRC) 같은 것)는 써도 된다."
        )
    return "쓸 수 있는 수치(요청과 자료에 있는 것 전부): " + ", ".join(found[:40])


def _others_line(headings: list[str], index: int) -> str:
    """What the other sections own, so this one does not write them."""
    others = [h for i, h in enumerate(headings) if i != index and h.strip()]
    if not others:
        return ""
    return (
        "다른 절의 몫(여기서 쓰지 마라): "
        + " / ".join(others)
        + ". 결론·권고·다음 단계는 그 이름을 가진 절에서만 쓴다."
    )


_SUMMARY_WORDS = ("요약", "개요", "핵심", "결론", "제언", "executive", "summary")
_COMPARE_WORDS = ("비교", "분석", "비용", "대안", "옵션", "검토", "평가", "현황", "결과", "이력")
_PLAN_WORDS = ("일정", "계획", "추진", "실행", "절차", "단계", "로드맵", "방법")
_PEOPLE_WORDS = ("이해관계자", "역할", "산출물", "목표", "담당", "체계", "조직")
_TREND_WORDS = ("추이", "추세", "변화", "월별", "연도별", "분기별", "시계열")


def _section_role(heading: str, index: int, total: int, written: str) -> tuple[str, str]:
    """What this section is for, and the blocks it may use.

    Returns `(role, block_rules)`. The summary gets no blocks and a length
    cap; a comparison gets the table; a plan gets steps; people get cards.
    Callout and kpi are document-wide singletons, offered only while the
    document has not used them yet.
    """
    name = heading.lower()
    has = lambda words: any(w in name for w in words)  # noqa: E731
    allowed: list[str] = []
    if has(_SUMMARY_WORDS) and index == 0:
        role = (
            "보고서를 읽지 않을 사람을 위한 요약. 무엇을 결정해야 하는지, 권고가 무엇인지, "
            "그 근거 둘을 문단 하나에서 둘로 쓴다. 표·블록·목록 없이 줄글로만. 200자 안팎."
        )
        return role, ""
    if has(_SUMMARY_WORDS) and index == total - 1:
        role = (
            "결론. 앞에서 말한 것 가운데 남는 한 가지와 다음에 할 일을 문단 하나로. 표·블록 없이."
        )
        return role, ""
    if has(_TREND_WORDS):
        allowed.append("chart")
    if has(_COMPARE_WORDS):
        allowed.append("table")
        if "```kpi" not in written:
            allowed.append("kpi")
    if has(_PLAN_WORDS):
        allowed.append("steps")
        allowed.append("table")
    if has(_PEOPLE_WORDS):
        allowed.append("cards")
    if not allowed:
        allowed.append("table")
    if "```callout" not in written and index >= total - 2:
        allowed.append("callout")
    seen: list[str] = []
    for one in allowed:
        if one not in seen:
            seen.append(one)
    role = "본문 절. 이 절의 제목이 약속한 것을 쓴다. 블록은 아래 허용된 것만, 한 절에 하나까지."
    rules = "\n".join(_BLOCK_SYNTAX[one] for one in seen)
    return role, "- 이 절에서 쓸 수 있는 블록:\n" + rules


#: Placeholder for an empty shelf. An empty block reads as withheld material and
#: the model invents citations.
_NO_REFS = "(없음. 번호 인용을 쓰지 마라.)"


#: Waits between retries of a rate-limited call, in seconds.
_BACKOFF = (2.0, 6.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429.

    One call per section against a shared limit; a transient refusal would leave
    a hole in the document.
    """
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        for attempt in range(len(_BACKOFF) + 1):
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    # No thinking on a call whose whole answer is one JSON
                    # object — see `thinking.NO_REASONING` for the measurements.
                    # Safe to send everywhere: the proxy runs `drop_params`, so
                    # a provider that has never heard of it never sees it.
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            log.info("report call rate limited, retrying in %ss", _BACKOFF[attempt])
            await asyncio.sleep(_BACKOFF[attempt])
        response.raise_for_status()
        payload = response.json()

    # A reasoning model can spend the whole ceiling thinking and return an
    # empty answer with `finish_reason: "length"`. See `services/thinking.py` —
    # this is the one place that can tell that apart from a model with nothing
    # to say, because it is the only place holding the raw payload.
    if bigger := thinking.starved(payload, max_tokens):
        log.info("%s: answer starved by reasoning, re-asking with %s tokens", model, bigger)
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
        ) as client:
            again = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": bigger,
                    "reasoning": thinking.NO_REASONING,
                },
            )
            if again.status_code >= 400:
                # A gateway that does not know `reasoning` refuses the whole
                # call. The ceiling alone still helps every model that does not
                # scale its thinking to it.
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # Both calls are charged, so both are counted. A budget that hid
            # the first attempt would under-report what the turn cost.
            payload = retried
            payload["usage"] = {
                "prompt_tokens": int(first.get("prompt_tokens") or 0)
                + int(spent.get("prompt_tokens") or 0),
                "completion_tokens": int(first.get("completion_tokens") or 0)
                + int(spent.get("completion_tokens") or 0),
            }

    text = (payload["choices"][0]["message"]["content"] or "").strip()
    raw = payload.get("usage") or {}
    return text, {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


#: 문서가 입는 인상. 프롬프트는 한국어로 묻고, 저장은 렌더러가 아는 영어로 한다.
_STYLES = {"편집형": "editorial", "포스터형": "poster", "미니멀": "minimal"}


def _outline_style(text: str) -> str:
    """The look the outline chose, or `""` when it named none.

    Read with its own regex rather than off the parsed object, so an outline
    that was salvaged from a partial answer still keeps the look it picked.
    """
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


#: The planner's stated subject, checked against the request — see grounding.
_subject_missing = grounding.subject_missing


_RESULTS = re.compile(r"결과|시험|실험|측정")
#: Words that point at a thing the person has and did not attach.
_MATERIAL = re.compile(r"녹취|녹음|원고|초안|피드백|기록을|표가 있|표를|파일|첨부|자료를|메모를")


def _results_without_data(request: str, attached: list[str]) -> bool:
    """A report asked for on material that is not here.

    「신규 소재 적용 타당성 검토 — 시험 방법, 결과, 위험, 권고」 with no
    material, no numbers, no file came back with 「압축 강도와 피로 수명이
    12%~15% 향상」: a result nobody measured, in a document whose whole point is
    the measurement. 「세미나 녹취를 회의록으로」 with no 녹취 came back as a
    decision brief about adopting a minutes system. A frame with (미정) in it
    is honest and a made-up result is not, so a request that names its
    material (녹취, 표, 파일) or asks for results, and carries no figure and no
    attachment, is asked for the material first — 있는 자료로 진행 still writes
    the frame.
    """
    if any(block.strip() for block in attached):
        return False
    if _MATERIAL.search(request):
        return True
    if not _RESULTS.search(request):
        return False
    return not deck_rules.has_numbers(request, [])


def _fold_alternatives(headings: list[str], alternatives: list[str]) -> list[str]:
    """One comparison section instead of one section per alternative.

    Told in the outline prompt, in bold, not to make a section per option,
    the planner made 「기존 서버 1년 연장 사용」「전체 서버 교체」「클라우드
    마이그레이션」 anyway — three sections that are the columns of one table,
    each repeating the same facts, and then a fourth section with the table.
    The planner names the alternatives it saw; this drops any section named
    after one of them and makes sure a single comparison section remains.
    """
    if not alternatives or not headings:
        return headings
    names = [a for a in alternatives if len(a) >= 2]
    keep: list[str] = []
    dropped = 0
    for h in headings:
        compact = h.replace(" ", "")
        if any(n.replace(" ", "") in compact for n in names) and "비교" not in h:
            dropped += 1
            continue
        keep.append(h)
    if dropped and not any("비교" in h for h in keep):
        # After the section that works the numbers out, if there is one, so
        # the table copies computed values rather than computing its own;
        # otherwise after the situation, before the rest.
        at = next((i + 1 for i, h in enumerate(keep) if "계산" in h or "전제" in h), None)
        keep.insert(min(len(keep), 2) if at is None else at, "대안 비교")
    return keep


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from whatever the model wrapped its JSON in.

    The title is optional throughout: a bare array, or an object missing the
    key, still yields a usable outline and the caller falls back to the request.
    """
    title = ""
    obj = re.search(r"\{.*\}", text, re.S)
    if obj:
        try:
            data = json.loads(obj.group(0))
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                items = data.get("sections") or []
                headings = [str(x).strip() for x in items if str(x).strip()]
                alternatives = [
                    str(x).strip() for x in (data.get("alternatives") or []) if str(x).strip()
                ]
                headings = _fold_alternatives(headings, alternatives)
                if headings:
                    return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            items = json.loads(match.group(0))
            headings = [str(x).strip() for x in items if str(x).strip()]
            if headings:
                return title, headings[:_MAX_SECTIONS]
        except json.JSONDecodeError:
            pass
    # A model that ignored the format usually still produced a list.
    lines = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line).strip(" #").strip()
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
    ]
    return title, [line for line in lines if line][:_MAX_SECTIONS]


def _refs_block(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return _NO_REFS
    # `.get` throughout. A source a person typed by hand, or one a test
    # seeded, carries a title and a url and nothing else — and one missing
    # `publisher` was a KeyError that failed every rewrite of every section
    # of that document, reported as 「고치지 못했습니다」.
    return "\n".join(
        f"[{s.get('ordinal', i + 1)}] {s.get('title') or s.get('url') or ''}"
        f" ({s.get('publisher') or ''})\n{s.get('quote') or ''}"
        for i, s in enumerate(sources)
    )


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """One picture as a stored figure, or `None` when it could not be drawn.

    Embedded as a `data:` URI rather than a file reference. A report is
    exported and mailed, and a picture that lives at a URL is a picture that is
    missing by the time somebody opens the attachment — the same reason the
    document editor embeds what a person pastes in.

    Never raises. A drawing that fails leaves the section without a figure,
    which the reader can see; a turn that dies takes the whole document.
    """
    if not image_model:
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            prompt=imagegen.compose_prompt(str(figure.get("prompt") or ""), aspect="4:3", style=""),
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed report
        log.warning("figure could not be drawn: %s", exc)
        return None
    return {
        # `encode` already returns the whole `data:` address; wrapping it
        # in `data_uri` again produced `data:image/png;base64,data:…`,
        # which every reader of it silently refused.
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "width": made.width,
        "height": made.height,
        # Popped by the caller into the turn's usage. Carried on the dict
        # because the drawing is billed to the same turn the prose is.
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


#: The two blocks that are nothing but figures, drawn large.
_FIGURE_FENCE = re.compile(r"^```(?:kpi|chart)\b.*?^```\s*$", re.S | re.M)


def _grounded_figures(text: str, grounded: bool) -> str:
    """Figure blocks removed from a section with nothing to draw them from.

    The same rule the deck applies to its `chart` and `metrics` slides, and for
    the same reason. Asked for a 검토 보고서 on a topic with no material, the
    writer filled three sections with `kpi` blocks — 6개월, 80%, 90%, 4명, 30%,
    100%, 0일, 8주, 40명, 80점 — every one of them invented, every one of them
    set large on the page where a figure is read as the most factual thing in
    the section.

    The prose around them survives. A sentence saying the programme runs in a
    compressed cycle is a claim somebody can weigh; the same claim as `8주`
    beside a heading is a measurement, and there was no measuring.
    """
    if grounded:
        return text
    return _FIGURE_FENCE.sub("", text).strip()


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: The model that plans, when an administrator has named one. A report's
    #: 목차 is the same kind of decision a deck's layouts are: one call that
    #: every call after it is written against. Empty plans with `model`.
    outline_model: str = "",
    #: The 목차 somebody has already seen and approved.
    #:
    #: Absent, this plans and stops: it emits `proposal` — or `needs`, when the
    #: material cannot carry the request — and writes nothing. Present, it
    #: skips planning and writes exactly what was approved, because planning
    #: again would produce a different report from the one agreed to.
    approved_plan: dict[str, Any] | None = None,
    #: Whether this pass may stop to ask.
    #:
    #: False on the pass that follows "있는 자료로 진행" — the button whose whole
    #: promise is that it will not be asked again. Without it the answer folds
    #: back into a request identical to the one that raised the question, the
    #: planner asks it again, and the button loops for as long as somebody
    #: keeps pressing it. Only this one pass is silenced; a later request that
    #: genuinely cannot be grounded is still allowed to say so.
    may_ask: bool = True,
    #: The pictures somebody agreed to on the second card, ready to draw.
    #:
    #: `None` on the planning pass, which is where they are *proposed*. `[]`
    #: means the card was answered with 그림 없이, and the difference matters:
    #: a section told a figure is coming writes 아래 그림과 같이, and one told
    #: nothing does not. That is why the question is asked before the writing
    #: rather than after it.
    figures_plan: list[dict] | None = None,
    #: Model that draws them, and the key to draw with. Empty disables the
    #: proposal entirely — no image model configured, no card.
    image_model: dict | None = None,
    #: Whether to research this report before writing it.
    #:
    #: The shelf this used to build was six titles and six 300-character
    #: snippets, from one search on the request typed verbatim. That is enough
    #: to print a reference list and not enough to correct a single thing the
    #: model misremembers — which is how a report cites four real sources
    #: underneath a paragraph none of them support. With this on, the pass runs
    #: through `services.research`: the queries are planned off the request,
    #: and the top pages are read in full before a heading is chosen.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    """
    # Planning is counted apart from writing, because it can run on another
    # model — and a call billed at the wrong model's price is a ledger that
    # says the wrong thing about where the money went. Empty when the same
    # model does both, which is the shape every caller already handles.
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }

    # Before the outline, not after it. A 목차 chosen from memory commits the
    # whole document to that memory's shape — every section after it is written
    # to fill a heading that was already wrong. Researching first costs one
    # planning call and buys an outline that knows what it is about.
    findings = research.Findings()
    # Checked before the step is drawn, not inside `run`. A deployment with no
    # search backend would otherwise open every document with 자료 찾는 중 and
    # close it with 참고할 자료 없음 — a step that reports the deployment's
    # configuration as though it were this document's result.
    if web_search and await research.available():
        yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
        findings = await research.run(request, model=outline_model or model, api_key=api_key)
        usage["outlineInputTokens" if outline_model else "inputTokens"] += findings.usage[
            "inputTokens"
        ]
        usage["outlineOutputTokens" if outline_model else "outputTokens"] += findings.usage[
            "outputTokens"
        ]
        yield {
            "type": "step",
            "id": "sources",
            "label": f"자료 {len(findings.sources)}건" if findings.sources else "참고할 자료 없음",
            "status": "done",
            "detail": findings.detail,
        }
    # `research.run` normally owns this list, but test doubles and connector
    # adapters may reuse a Findings object. Project citations belong to this
    # run only, so never append into the caller's shelf in place.
    findings.sources = list(findings.sources)
    web_selected = len(findings.sources)
    project_selected = 0
    project_excluded = 0
    project_reference_lines: list[str] = []
    for item in project_sources or []:
        if item.get("state") not in ("included", "truncated"):
            project_excluded += 1
            continue
        ordinal = len(findings.sources) + 1
        title = str(item.get("name") or "프로젝트 자료")[:200]
        url = str(item.get("sourceUrl") or "")
        findings.sources.append(
            {
                "id": str(item.get("id") or f"project-{ordinal}"),
                "ordinal": ordinal,
                "title": title,
                "publisher": research._publisher(url) if url else "프로젝트 파일",
                "url": url,
                "origin": "web" if url else "file",
                "originLabel": "프로젝트 웹 자료" if url else "프로젝트 파일",
                "quote": (
                    " · ".join(str(v) for v in (item.get("locations") or []))
                    or (
                        "전체 내용 전달됨"
                        if item.get("state") == "included"
                        else "일부 내용만 전달됨"
                    )
                ),
            }
        )
        project_reference_lines.append(f"- [{ordinal}] {title}")
        project_selected += 1

    # Keep the investigation legible after the progress row disappears.  A
    # source shelf proves what was cited; it does not prove what was searched,
    # whether search actually ran, or how much irrelevant material was
    # rejected.  The report artifact stores this event as its research log.
    yield {
        "type": "research",
        "research": {
            "enabled": web_search,
            "searched": findings.searched,
            "queries": findings.queries,
            "selected": len(findings.sources),
            "excluded": findings.dropped,
            "webSelected": web_selected,
            "projectSelected": project_selected,
            "projectExcluded": project_excluded,
        },
    }
    # Three states, and the writer is told which one it is in. A toggle
    # somebody switched off is a choice and needs no disclaimer; a search that
    # could not run and a search that found nothing are both worth saying, and
    # they do not mean the same thing to a reader.
    research_rule = ""
    if web_search and not findings.searched:
        research_rule = research.UNRESEARCHED_RULE
    elif web_search and web_selected == 0:
        research_rule = research.EMPTY_RULE
    # The pages read, as their own reference block. Appended rather than
    # substituted: an attached file is still the better source for what it
    # covers, and the two are labelled so the writer can tell them apart.
    document_context = list(untrusted_context or [])
    if project_reference_lines:
        trusted_context = list(trusted_context or []) + [
            "# 프로젝트 자료 인용 번호\n"
            "프로젝트 자료에서 가져온 사실을 사용한 문장 끝에는 아래 번호를 정확히 붙이세요. "
            "목록에 없는 번호를 만들지 마세요.\n" + "\n".join(project_reference_lines)
        ]
    #: Whether a figure could honestly have come from anywhere. Judged once for
    #: the run, by the same test the deck uses — a saved memory about who the
    #: user is is material and is not a measurement.
    grounded = deck_rules.has_numbers(request, document_context)
    if block := research.context_block(findings):
        document_context.append(block)

    if approved_plan is None:
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
        try:
            text, spent = await _complete(
                outline_model or model,
                build_document_messages(
                    SessionKind.report,
                    _OUTLINE_PROMPT.format(
                        ask_rule=grounding.ASK_RULE if may_ask else grounding.PROCEED_RULE,
                        lo=_MIN_SECTIONS,
                        hi=_MAX_SECTIONS,
                        request=request[:2000],
                    ),
                    trusted_context=trusted_context,
                    untrusted_context=document_context,
                    research_rule=research_rule,
                ),
                api_key,
                400,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
            yield {"type": "usage", **usage}
            return

        plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        # A question instead of a 목차 — see `grounding.ASK_RULE`. Only when the
        # request names material the sources do not carry; a bare topic is still
        # planned without anybody being asked about it.
        if may_ask and (asked := grounding.parse_needs(text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield {"type": "usage", **usage}
            return
        if may_ask and _results_without_data(request, list(untrusted_context or [])):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {
                "type": "needs",
                "questions": [
                    grounding.Question(
                        id="data",
                        question=(
                            "바탕이 될 자료(녹취, 표, 측정값, 파일)를 붙이거나 적어 주세요. "
                            "없으면 「있는 자료로 진행」— 내용 자리는 (미정)으로 비워 둔 "
                            "틀을 씁니다."
                        ),
                        options=[],
                    ).wire()
                ],
            }
            yield {"type": "usage", **usage}
            return
        if may_ask and _subject_missing(text, request):
            # 주제가 없는 요청은 묻는다. Checked here rather than trusted to the
            # prompt: the planner planned a made-up subject with the rule in
            # front of it, and a decision brief about a decision nobody named
            # is worse than no brief.
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {
                "type": "needs",
                "questions": [
                    grounding.Question(
                        id="subject",
                        question="무엇에 대한 문서입니까? 결정할 사안이나 주제를 적어 주세요.",
                        options=[],
                    ).wire()
                ],
            }
            yield {"type": "usage", **usage}
            return
        title, headings = _parse_outline(text)
        if len(headings) < _MIN_SECTIONS:
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {
                "type": "error",
                "message": "보고서 개요를 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
            }
            yield {"type": "usage", **usage}
            return

        yield {
            "type": "step",
            "id": "outline",
            "label": f"개요 {len(headings)}개 섹션",
            "status": "done",
            "detail": " · ".join(headings),
        }
        # Planned, and that is where this stops. The 목차 is offered rather
        # than written against: the caller stores it, shows it, and calls back
        # with it approved. Nothing has been written, which is what keeps the
        # report already on screen safe from a run nobody confirmed.
        #
        # The pictures are proposed here too and asked about separately, once
        # the outline is agreed — see `services.figures`. Proposed now because
        # the planner has the outline in front of it; asked later because two
        # decisions on one card is how somebody approves an expensive one by
        # accident.
        plan: dict[str, Any] = {
            "title": title[:200],
            "sections": headings,
            # 말한 사람이 먼저다 — `deck` 과 같은 규칙. `visual_style_for`
            # answers only when the request says so, and its `editorial`
            # default reached every document nobody had described, so a 논문
            # 초안 and a 사내 안내문 came out wearing the same face.
            "visualStyle": (
                design.visual_style_for(request)
                if design.visual_style_for(request) != "editorial"
                else (_outline_style(text) or "editorial")
            ),
        }
        if image_model:
            drawn = await figures.propose(
                request=request,
                title=title,
                parts=headings,
                model=outline_model or model,
                api_key=api_key,
                image_model=image_model,
            )
            usage["outlineInputTokens" if outline_model else "inputTokens"] += drawn.usage[
                "inputTokens"
            ]
            usage["outlineOutputTokens" if outline_model else "outputTokens"] += drawn.usage[
                "outputTokens"
            ]
            if drawn.figures:
                plan["figures"] = drawn.wire()
        yield {"type": "proposal", "plan": plan}
        yield {"type": "usage", **usage}
        return

    title = str(approved_plan.get("title") or "")
    headings = [str(h).strip() for h in (approved_plan.get("sections") or []) if str(h).strip()]
    if not headings:
        yield {"type": "error", "message": "승인된 개요가 비어 있습니다."}
        yield {"type": "usage", **usage}
        return
    # Emitted only when the model produced one, so the caller keeps its fallback.
    if title:
        yield {"type": "title", "title": title[:200]}

    # One shelf for every section, and the same one the outline was chosen
    # from. Researched above — before this, the search ran here, which meant
    # the 목차 was planned with nothing under it.
    sources = findings.sources
    yield {"type": "sources", "sources": sources}
    refs = _refs_block(sources)

    sections = [
        {"id": f"s{i}_{uuid.uuid4().hex[:6]}", "heading": h, "level": 1}
        for i, h in enumerate(headings)
    ]
    # Announced up front so the panel can show the whole shape.
    for section in sections:
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": "",
            "done": False,
        }

    outline_text = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headings))
    written: list[str] = []

    # 한 번에 쓴다.
    #
    # Section by section, each call saw the outline, a 4,000-character tail of
    # what came before, and the same list of allowed numbers — and still wrote
    # 3,800만 원 in one section and 380만 원 in the next, turned a maintenance
    # cost into a residual value two sections later, and recommended the cloud
    # in one section and the replacement in another. A writer who cannot see
    # the whole document cannot keep it consistent, and a model of this size
    # does not hold a document in its head across calls.
    #
    # So the whole document is drafted in one call, with every number and
    # every section in one context, and then cut into sections along the
    # headings it was asked to write. A section the draft failed to write is
    # written on its own below, the old way. The per-section pass stays for
    # rewrites, where one section is the whole job.
    drafted: dict[str, str] = {}
    yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "running"}
    try:
        draft_text, spent = await _complete(
            model,
            build_document_messages(
                SessionKind.report,
                _DRAFT_PROMPT.format(
                    outline="\n".join(f"## {h}" for h in headings),
                    refs=refs,
                    facts=_facts_line(request, sources),
                    request=request[:1500],
                ),
                trusted_context=trusted_context,
                untrusted_context=document_context,
                research_rule=research_rule,
            ),
            api_key,
            max_tokens=min(9000, 1400 * len(headings)),
        )
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        drafted = _split_draft(draft_text, headings)
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "done"}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("report draft failed, writing section by section: %s", exc)
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "error"}
    #: Approved pictures by the index of the section they belong to. Empty when
    #: the figure card was answered 그림 없이, and then nothing below mentions a
    #: figure — which is the whole point of asking before the writing.
    wanted_figures = {int(f.get("section", -1)): f for f in (figures_plan or []) if f.get("prompt")}

    for index, section in enumerate(sections):
        # The position lives in `progress`, not in the text: spelled into both,
        # the surface renders "3/9 도입 (3/9)".
        label = str(section["heading"])
        # The outline lands before any section is written, so each step can say
        # where it sits in it — which is the only figure that answers "how much
        # of this is left" while the document builds.
        progress = {"current": index + 1, "total": len(sections)}
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "running",
            "progress": progress,
        }
        try:
            if section["heading"] in drafted:
                body, spent = drafted[section["heading"]], {"inputTokens": 0, "outputTokens": 0}
            else:
                body, spent = await _complete(
                    model,
                    build_document_messages(
                        SessionKind.report,
                        _SECTION_PROMPT.format(
                            heading=section["heading"],
                            outline=outline_text,
                            # Tail only: the whole document would crowd out the
                            # instruction by section six.
                            written="\n\n".join(written)[-4000:] or "(아직 없음)",
                            refs=refs,
                            request=request[:1500],
                            role=_section_role(
                                section["heading"], index, len(sections), "\n".join(written)
                            )[0],
                            blocks=_section_role(
                                section["heading"], index, len(sections), "\n".join(written)
                            )[1],
                            others=_others_line(headings, index),
                            facts=_facts_line(request, sources or []),
                        )
                        + (
                            # Told before the prose is written, so the section can
                            # refer to its figure. A picture added afterwards is a
                            # picture nobody mentioned.
                            "\n\n"
                            + figures.note_for(
                                figures.Figure(
                                    section=index,
                                    caption=str(wanted_figures[index].get("caption") or ""),
                                    prompt="",
                                )
                            )
                            if index in wanted_figures
                            else ""
                        ),
                        trusted_context=trusted_context,
                        untrusted_context=document_context,
                        research_rule=research_rule,
                    ),
                    api_key,
                    1200,
                )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report section %r failed: %s", section["heading"], exc)
            yield {
                "type": "step",
                "id": section["id"],
                "label": label,
                "status": "error",
                "progress": progress,
            }
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": "_이 섹션을 쓰지 못했습니다._",
                "done": True,
            }
            section["content"] = ""
            continue

        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        # Models write a table with a blank line between every row, which is
        # not a table to any renderer. Closed here, once, so the panel, the
        # page view and the three exporters all read the same thing.
        # Stray ideographs read back into Hangul before anything is stored —
        # `services/hangul.py`. The deck and the page tracks did this at their
        # own doors and the report did not, so a 보고서 came out carrying 培育,
        # 劣势 and 書類 while a deck on the same subject did not. One product,
        # one answer.
        clean, _ = hangul.read_back(body)
        clean = hangul.tidy_spacing(clean)
        section["content"] = richtext.tidy_tables(_grounded_figures(clean, grounded))

        # The picture, if this section is one of the ones somebody paid for.
        # Drawn after the prose rather than before it so a failed drawing
        # leaves a section with no figure rather than a section that refers to
        # one — the prompt already told the writer a figure was coming, and
        # that sentence is the thing a missing picture makes wrong.
        if (drawing := wanted_figures.get(index)) is not None:
            yield {
                "type": "step",
                "id": f"fig{index}",
                "label": drawing.get("caption") or "그림 그리는 중",
                "status": "running",
                "progress": progress,
            }
            picture = await _draw(drawing, image_model, api_key)
            if picture is None:
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림",
                    "status": "error",
                    "progress": progress,
                }
            else:
                usage["inputTokens"] += picture.pop("_in", 0)
                usage["outputTokens"] += picture.pop("_out", 0)
                # Into the prose, not onto the section.
                #
                # `section["images"]` was the writer's own channel and the
                # exporters read it — but nothing on screen did, so a figure
                # somebody paid for sat in the downloaded file and was
                # invisible in the panel. The body is the one place every
                # reader looks.
                #
                # As Markdown rather than as HTML, because the body *is*
                # Markdown here: the web view renders it, the page view turns
                # it into an `<img>` the editor can move, and the exporters
                # read it through the same `![…](…)` the document editor
                # produces when somebody pastes a picture in themselves.
                caption = str(picture.get("caption") or "").replace("]", " ")
                section["content"] = (
                    f"{section['content'].rstrip()}\n\n![{caption}]({picture['src']})"
                )
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림",
                    "status": "done",
                    "progress": progress,
                }
        written.append(f"## {section['heading']}\n{body}")
        yield {
            "type": "step",
            "id": section["id"],
            "label": label,
            "status": "done",
            "progress": progress,
        }
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": body,
            "done": True,
        }

    yield {"type": "report", "sections": sections}
    yield {"type": "usage", **usage}


async def rewrite_section(
    *,
    request: str,
    heading: str,
    sections: list[dict],
    target_id: str,
    model: str,
    api_key: str,
    note: str = "",
    sources: list[dict] | None = None,
) -> tuple[str, dict]:
    """Rewrites one section, with the rest of the document as context.

    Everything but the target is passed as written, so the new text does not
    repeat section two — the same guard the first pass uses.
    """
    outline = "\n".join(f"{i + 1}. {s.get('heading') or ''}" for i, s in enumerate(sections))
    written = "\n\n".join(
        f"## {s.get('heading')}\n{s.get('content') or ''}"
        for s in sections
        if s.get("id") != target_id and (s.get("content") or "").strip()
    )
    position = next((i for i, s in enumerate(sections) if s.get("id") == target_id), 0)
    role, blocks = _section_role(heading, position, len(sections), written)
    prompt = _SECTION_PROMPT.format(
        heading=heading,
        outline=outline,
        written=written[-4000:] or "(아직 없음)",
        # The document already carries numbered citations, so a rewrite without
        # the shelf would renumber them against nothing.
        refs=_refs_block(sources or []),
        request=request[:1500],
        role=role,
        blocks=blocks,
        others=_others_line([str(x.get("heading") or "") for x in sections], position),
        facts=_facts_line(request, sources or []),
    )
    if note.strip():
        # Last and labelled: an unlabelled sentence appended to a prompt reads
        # as part of the original request.
        prompt += f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}"
    return await _complete(
        model,
        build_document_messages(SessionKind.report, prompt),
        api_key,
        1200,
    )


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
