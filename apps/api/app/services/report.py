"""Report writing: outline, one-shot draft split by heading, per-section fallback and rewrite."""

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
    diagrams,
    figures,
    grounding,
    hangul,
    imagegen,
    pictures,
    ratelimit,
    research,
    richtext,
    settings_store,
    thinking,
)
from app.services import outline as plan_rules
from app.services.context import build_document_messages

log = logging.getLogger(__name__)

#: Section-count bounds for the outline.
_MIN_SECTIONS = 3
_MAX_SECTIONS = 12


def requested_sections(request: str) -> int | None:
    """An explicit section count; page counts do not imply a number of sections."""
    return plan_rules.requested_count(request, ("섹션", "절"), maximum=_MAX_SECTIONS)


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
- 판단을 구하는 문서(검토·의사결정·제안·타당성)는 첫 절이 「요약」, 마지막 절이
  「권고안과 다음 단계」다. 결론과 권고는 그 마지막 절 하나에만 있다. **현황·주간
  보고, 회의록, 장애 보고서, 실험 보고서, 안내문은 판단을 구하는 문서가 아니다** —
  요약·권고안 절을 붙이지 말고 그 장르의 항목이 목차다.
{genre}
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
  바꾸지 마라. 축이 둘이면 — 「안건별로 결정·반론·조건·담당을 나눠」 — **한 축만
  절로 삼는다**(안건마다 절 하나, 그 안에서 결정·반론·조건·담당을 소제목으로).
  안건별 절과 항목별 절을 둘 다 만들면 같은 말이 두 번 나온다.
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
{genre}

규칙:
- "{heading}" 에 해당하는 내용만 써라. 보고서 전체를 이 절에 넣지 마라. 다른
  절의 몫(비교, 일정, 권고)은 그 절이 쓴다.
- 제목 줄은 쓰지 마라 — 굵은 글씨로도. 본문만. 최상위 제목(#)도 쓰지 마라.
- 앞에서 한 말과 앞에서 그린 표·블록을 되풀이하지 마라.
- 줄글이 기본이다. 한 절은 보통 문단 두셋에서 넷이고, 문단은 이어지는 문장으로
  쓴다. 「- **1번 항목**:」 같은 번호 목록으로 절을 채우지 마라.
- **수치는 위의 「쓸 수 있는 수치」 목록에 있는 것과 그것으로 계산한 값만 쓴다.**
  단위와 자릿수까지 그대로 — 연 단위를 만 단위로 옮기거나 자릿수를 바꾸지 마라.
  계산한 값은 식을 함께 적어라(「월 비용 × 12개월 = 연 비용」 꼴로). 목록에
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
{genre}

규칙:
- 자료에 없는 원인·이유·평가를 보태지 마라 — 「민감한 정보가 포함되어 있어」「~때문으로
  판단됩니다」는 자료가 말하지 않았으면 쓰지 않는다. 모르는 것은 그냥 쓰지 않는다;
  「(자료에 없음)으로 처리해야 할 부분은 …」처럼 무엇이 없는지 설명하는 문장은 문서에
  넣지 않는다. 「기반을 마련했습니다」「긍정적인 성과」처럼 사실을 더하지 않는 문장도
  쓰지 않는다.
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
  대안, 행은 기준이다. **기준은 요청이 말한 것** — 기법 비교면 메모리·성능·학습
  파라미터 수처럼 그 분야의 잣대이고, 비용을 따지는 결정 문서일 때만 첫해 비용·3년 총비용(식과 함께)·장애 위험·결정
  뒤 남는 문제가 들어간다. 표 위에 이름표를 붙이지 말고 바로 표를 그린 뒤, 아래
  문단에서 표가 무엇을 말하는지 풀어 쓴다 — 열 이름을 하나씩 풀이하는 「비교표
  설명」 목록은 쓰지 마라. 표가 말하는 결론 한두 문장이면 된다. **같은 표를 다른 절에 다시 그리지
  마라** — 문서에 비교표는 하나다.
- 줄글이 기본이다. 비교·항목별 값은 표로 쓰되(행 사이 빈 줄 없이, 3~5행), 문서
  전체에 표는 두 개까지. 표 앞에 무엇을 견주는지, 뒤에 그래서 무엇인지 한 문장씩.
  절의 결론이 되는 숫자 둘셋은 문서 전체에 한 번만 ```kpi 블록(`값 | 이름` 한 줄씩,
  최대 4개)으로. 틀리면 뒤가 무너지는 전제 하나는 문서 전체에 한 번만 ```callout
  블록(첫 줄 제목, 다음 줄 내용)으로. 그 밖의 블록은 쓰지 마라.
- **요청에 측정 데이터 표가 있으면 결과 절에 그 표를 다시 싣는다** — 요청의 열
  그대로에, 그 값으로 계산한 열(이득, dB, 이론값, 오차 %)을 더해서. 표 없이 값을
  줄글에 흩어 놓은 실험 보고서는 읽는 사람이 표를 다시 만들어야 한다. 이 표는 표
  두 개 한도에 든다.
- **차이를 말하려면 두 수를 계산해 나란히 적어라.** 「고주파에서 위상이 이론값보다
  다소 크다」처럼 계산하지 않은 편차를 말하지 마라 — 이론값을 식으로 구해 측정값
  옆에 두고, 차이가 1% 안이면 「일치한다」고 쓴다. 「3회 반복 측정」처럼 요청에 없는
  절차도 지어내지 마라.
- **수치는 위 「쓸 수 있는 수치」에 있는 것과 그것으로 계산한 값만 쓴다.** 요청에
  적힌 표기 그대로 아라비아 숫자로 — 자릿수를 옮기거나(만 단위를 백만 단위로)
  한자 숫자로 바꾸지 마라. **이 규칙 안의 보기 숫자는 이 문서의 수치가 아니다.**
  계산은 식과 결과를 함께 적고 나눗셈은 소수 첫째 자리까지 쓴다(「월 비용 ×
  12개월 = 연 비용」, 「초기 비용 ÷ 연 비용 ≈ 회수 기간(년)」 꼴로). 목록에 없는 수치 —
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


_TABLE = re.compile(r"(?m)^\|.+\|\s*\n\|[\s:|-]+\|\s*\n(?:^\|.+\|\s*\n?)+")
_RESULTS_HEADING = re.compile(r"결과|측정|데이터|분석")


def _carry_table(request: str, headings: list[str], drafted: dict[str, str]) -> dict[str, str]:
    """Puts the request's own data table into the results section when the draft left it out."""
    found = _TABLE.search(request)
    if not found or not drafted:
        return drafted
    if any(_TABLE.search(text) for text in drafted.values()):
        return drafted
    target = next((h for h in headings if _RESULTS_HEADING.search(h) and h in drafted), None)
    if target is None:
        return drafted
    table = found.group(0).strip()
    drafted[target] = f"측정 데이터는 다음과 같습니다.\n\n{table}\n\n{drafted[target]}"
    return drafted


def _split_draft(draft: str, headings: list[str]) -> dict[str, str]:
    """The draft cut into sections by its `## ` lines, matched loosely; unwritten headings are absent."""

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
            # A stray sub-heading is kept as bold text.
            if current is not None:
                found[current].append(f"**{m.group(1).strip()}**")
                continue
        if current is not None:
            found[current].append(line)
    return {h: "\n".join(lines).strip() for h, lines in found.items() if "\n".join(lines).strip()}


#: Block syntax per kind; a section is shown only the blocks it may use.
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


#: Writer rule for a bare form: 있는 자료로 진행 was answered to a question the
#: document could not be written without.
_FRAME_RULE = (
    "**이 문서는 자료 없이 틀만 쓴다.** 요청에 없는 사실·논의·결정·이름·수치를 어떤 것도 "
    "지어내지 마라. 절마다 그 절에 무엇을 적어야 하는지 한두 문장으로 안내하고, 채울 "
    "자리는 「(여기에: 결정된 사항과 근거)」처럼 괄호 빈칸으로 둔다. 표는 머리글 행과 "
    "빈 칸만. 비용 계산·대안 비교처럼 요청에 없던 절이나 표를 보태지 마라. "
    "공식과 일반 원리는 써도 된다."
)

#: Opens a document written without a single web source, when one was looked for.
_UNVERIFIED_NOTE = (
    "_이 문서는 웹 검색에서 쓸 만한 자료를 얻지 못해 기억을 바탕으로 썼습니다. "
    "수치·연구명·서지는 확인이 필요합니다._"
)

#: A request that weighs options — the only kind the cost-table advice fits.
_DECISION = re.compile(r"대안|결정|권고|비용|타당성|선택")

#: Genre shape rules, matched on the request and given to the outline and the draft.
_GENRES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"주간|월간|업무 ?보고|진행 ?상황|현황 ?보고|진척"),
        "장르: 현황·주간 보고. 한 쪽에 읽힌다 — 절마다 짧은 문장의 항목 서넛, 문단은 둘을 넘기지 "
        "않는다. 「요약」 절 대신 첫 절 첫 줄에 이번 주의 결론 한 문장. 권고안 절을 만들지 않는다; "
        "결정이 필요한 것은 「이슈」에 질문 형태로 적는다. 자료에 없는 원인·해석·전망("
        "「~때문으로 판단됩니다」「달성 가능성 높음」)을 보태지 않는다.",
    ),
    (
        re.compile(r"회의록|녹취|미팅 ?메모|회의 ?메모"),
        "장르: 회의록. 결정·조치·미결은 표로(항목·담당·기한). 발언은 요약하되 판단을 보태지 "
        "않고, 요약·권고 절을 만들지 않는다. 자료에 없는 담당자·기한은 「미정」.",
    ),
    (
        re.compile(r"장애|사고|incident|포스트모템|post-?mortem"),
        "장르: 장애 보고서. 시각열은 시각·사건·조치의 표로 싣는다. 영향은 누가·얼마나·얼마 동안을 "
        "숫자로, 원인은 확인된 것과 추정을 갈라, 재발 방지는 원인과 짝지은 표(조치·담당·기한). "
        "대응 절은 시각열에 없는 것(왜 그 결정을 했는지, 임시 조치)만 적고 시각열을 되풀이하지 "
        "않는다. 자료의 날짜에 연도가 없으면 연도를 붙이지 않는다. 책임을 묻는 문장을 쓰지 "
        "않는다.",
    ),
    (
        re.compile(r"실험|측정|시험 결과"),
        "장르: 실험 보고서. 측정 데이터는 표로, 계산한 열을 더해서. 차이는 이론값과 나란히 계산해 "
        "말하고, 요청에 없는 절차(반복 횟수, 장비 설정)를 지어내지 않는다.",
    ),
    (
        re.compile(r"안내문|공지|가이드라인|규정|정책"),
        "장르: 안내·정책 문서. 항목마다 원칙·허용·금지·예시를 짧게. 권고안·요약 절을 만들지 않고, "
        "시행일·문의처를 끝에 둔다.",
    ),
)


_OWN_GENRES = re.compile(
    r"주간|월간|업무 ?보고|진행 ?상황|현황 ?보고|회의록|녹취|장애|사고|실험|측정"
)


def _own_material(request: str) -> bool:
    """Whether the request is about the person's own material and carries it; no web search then."""
    return bool(_OWN_GENRES.search(request)) and _carries_material(request)


def _genre_rule(request: str) -> str:
    """The shape rule for the request's genre, or `""` for a document with none."""
    for pattern, rule in _GENRES:
        if pattern.search(request):
            return rule
    return ""


_MONEY = re.compile(
    r"(?<![\d,.])\d[\d,]{0,15}(?:\.\d{1,3})?\s*(?:억|만|천|백)?\s*원(?!인|리|칙|자)"
)


def _without_invented_money(text: str) -> str:
    """Every sum of money replaced by (미정), for a document with no figures to draw on."""
    return _MONEY.sub("(미정)", text)


_OWNER_HEADER = re.compile(r"담당|책임|owner", re.I)
_DUE_HEADER = re.compile(r"기한|마감|완료일|납기|due|deadline", re.I)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
_MONTH_DAY = re.compile(r"(\d{1,2})\s*[/.월]\s*(\d{1,2})\s*일?")
_ISO_DAY = re.compile(r"\d{4}-(\d{2})-(\d{2})")
_BLANK_CELLS = {"", "미정", "(미정)", "-", "—", "tbd", "n/a", "없음"}


def _cell_sourced(cell: str, compact: str) -> bool:
    """Whether an owner or due-date cell names something the request or material names.

    A date matches by month and day in any of the usual spellings (9/10 · 9월 10일 ·
    2026-09-10); a person or team by any word of two characters or more.
    """
    flat = re.sub(r"\s+", "", cell)
    if flat.lower().strip("*_`") in _BLANK_CELLS or flat in compact:
        return True
    days = [(int(m), int(d)) for m, d in _MONTH_DAY.findall(flat)] + [
        (int(m), int(d)) for m, d in _ISO_DAY.findall(flat)
    ]
    for month, day in days:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        spellings = (
            f"{month}/{day}",
            f"{month:02d}/{day:02d}",
            f"{month}월{day}일",
            f"{month:02d}월{day:02d}일",
            f"{month}.{day}",
            f"-{month:02d}-{day:02d}",
        )
        if any(sp in compact for sp in spellings):
            return True
    if days:
        return False
    words = [w.strip("*_`()") for w in re.split(r"[\s,·/()]+", cell)]
    return any(len(w) >= 2 and w in compact for w in words)


def _unsourced_owner_dates(text: str, source: str) -> str:
    """In a table with a 담당 or 기한 column, a cell the request and material never
    mention becomes 「미정」.

    The action-item table is where a writer fills blanks with a plausible team and a
    plausible date; the surfaces promise 「담당자나 기한이 나오지 않았으면 미정」, and a
    promise kept only in the prose is a table nobody can act on.
    """
    compact = re.sub(r"\s+", "", source)
    if not compact or "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            guarded = [
                k for k, h in enumerate(header) if _OWNER_HEADER.search(h) or _DUE_HEADER.search(h)
            ]
            out.extend((line, lines[i + 1]))
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if guarded:
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    for k in guarded:
                        if k < len(cells) and not _cell_sourced(cells[k], compact):
                            cells[k] = "미정"
                    out.append("| " + " | ".join(cells) + " |")
                else:
                    out.append(lines[i])
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _facts_line(request: str, sources: list[dict[str, Any]]) -> str:
    """The closed list of numbers the document may use, read off the request and the sources."""
    found: list[str] = []
    for text in [request, *[str(s.get("quote") or "") for s in sources]]:
        for match in _NUMBER.finditer(text):
            token = re.sub(r"\s+", "", match.group(0))
            if len(token) < 2 or token.isdigit() and len(token) > 4:
                continue
            if token not in found:
                found.append(token)
    if not found:
        line = (
            "쓸 수 있는 수치: 없다. 요청과 자료에 수치가 하나도 없다 — **금액·기간·인원·"
            "퍼센트·측정값·부품값·성능 향상률을 어떤 것도 쓰지 마라.** 값이 들어갈 자리는 "
            "「(미정)」「(측정값)」으로 비워 두고, 값을 셈하거나 지어내는 대신 무엇을 어떻게 "
            "측정·확인해야 하는지를 적는다. 공식과 일반 원리(f_c = 1/(2πRC) 같은 것)는 써도 "
            "된다."
        )
        if _DECISION.search(request):
            line += (
                " 「비용 계산의 전제」 절은 견적, 예산 한도, 대상 인원처럼 확인할 것을 적고, "
                "비교표의 행은 비용 대신 「필요한 것」 「위험」 「되돌릴 수 있는가」로."
            )
        return line
    line = "쓸 수 있는 수치(요청과 자료에 있는 것 전부): " + ", ".join(found[:40])
    if derived := derived_values(request):
        # Arithmetic the request implies is done here, once, so the writer copies a
        # finished figure instead of multiplying in prose (and getting it wrong).
        line += (
            "\n계산된 값(다시 셈하지 마라; 비용·비교처럼 그 값이 필요한 절에서 한 번만 쓰고 다른"
            " 절에서 되풀이하지 마라; 표 셀에는 값만 적고 식은 머리글이나 문장에 한 번만): "
            + "; ".join(derived)
        )
    return line


#: 「월 470만 원」 「월 약 690만 원」 — a monthly amount in 만 원.
_MONTHLY_WON = re.compile(r"월\s*(?:약\s*)?([\d,]+(?:\.\d+)?)\s*만\s*원")
_HORIZON_MONTHS = re.compile(r"(\d{1,3})\s*개월")
_HORIZON_YEARS = re.compile(r"(\d)\s*년\s*(?:TCO|총|치|간|동안|기준)")
_ANNUAL_BUDGET = re.compile(r"연\s*(?:간\s*)?예산\s*([\d,]+(?:\.\d+)?)\s*만\s*원")
_ADDED_MONTHLY = re.compile(r"추가\s{0,8}\(?\s{0,8}월\s{0,8}(?:약\s{0,8})?([\d,]{1,15}(?:\.\d{1,15})?)\s{0,8}만\s{0,8}원")


def _won(value: float) -> str:
    """A 만 원 figure the way a Korean reader writes it: 「5,640만 원」, 「1억 6,920만 원」."""
    whole = round(value)
    if abs(value - whole) > 1e-6:
        return f"{value:,.1f}만 원"
    if whole >= 10000:
        eok, man = divmod(whole, 10000)
        return f"{eok}억 {man:,}만 원" if man else f"{eok}억 원"
    return f"{whole:,}만 원"


def _label_before(text: str, end: int) -> str:
    """The words naming the amount: the clause in front of it, up to a delimiter."""
    head = text[max(0, end - 40):end]
    head = re.split(r"[,:;\n•·()]|\s(?:와|과|및)\s", head)[-1]
    return re.sub(r"\s{0,8}(?:은|는|이|가|의)?\s{0,8}월\s{0,8}(?:약\s{0,8})?$", "", head).strip(" -–—")[:24]


def derived_values(text: str) -> list[str]:
    """Finished arithmetic the request calls for, as 「식 = 값」 lines; `[]` when none.

    Monthly amounts are carried to the horizons the text names (a year when a yearly
    budget or 「연」 appears, 「36개월」, 「3년 TCO」), an added monthly cost is folded into
    the current one, and yearly totals are set against the yearly budget. Nothing here
    goes beyond what the words ask for: a model asked for a three-year table was
    writing 「470 × 36 = 16,200」 and 「1,692,0600,000」."""
    monthly: list[tuple[str, float]] = []
    for match in _MONTHLY_WON.finditer(text):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        label = _label_before(text, match.start())
        if all(abs(value - v) > 1e-9 or label != lab for lab, v in monthly):
            monthly.append((label, value))
    if not monthly:
        return []
    horizons: list[int] = []
    budget = None
    if m := _ANNUAL_BUDGET.search(text):
        budget = float(m.group(1).replace(",", ""))
    if budget is not None or re.search(r"연\s*(?:간|비용|예산)|1년|연간", text):
        horizons.append(12)
    for m in _HORIZON_MONTHS.finditer(text):
        n = int(m.group(1))
        if 2 <= n <= 120 and n not in horizons:
            horizons.append(n)
    for m in _HORIZON_YEARS.finditer(text):
        n = int(m.group(1)) * 12
        if n not in horizons:
            horizons.append(n)
    added = [float(m.group(1).replace(",", "")) for m in _ADDED_MONTHLY.finditer(text)]
    lines: list[str] = []
    for label, value in monthly[:6]:
        if any(abs(value - a) < 1e-9 for a in added):
            continue  # the added cost is shown folded into the base below
        name = f"{label} " if label else ""
        for h in horizons:
            lines.append(f"{name}{_won(value)} × {h}개월 = {_won(value * h)}")
    if added and monthly:
        base_label, base = monthly[0]
        for a in added:
            for h in horizons:
                lines.append(
                    f"({_won(base).replace(' 원', '')} + {_won(a).replace(' 원', '')}) × {h}개월"
                    f" = {_won((base + a) * h)}"
                )
    if budget is not None:
        for label, value in monthly[:6]:
            if any(abs(value - a) < 1e-9 for a in added):
                continue
            annual = value * 12
            gap = annual - budget
            verdict = f"예산보다 {_won(abs(gap))} {'초과' if gap > 0 else '여유'}" if gap else "예산과 같음"
            lines.append(f"{label + ' ' if label else ''}연 {_won(annual)} vs 연 예산 {_won(budget)}: {verdict}")
    return lines[:14]


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
    """`(role, block_rules)` for a section; callout and kpi are offered once per document."""
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


#: Placeholder for an empty source list; an empty block makes the model invent citations.
_NO_REFS = "(없음. 번호 인용을 쓰지 마라.)"


#: Seconds between retries of a rate-limited call when the gateway names no reset time.
#: Five rounds, about seventy seconds in all: past a token-per-minute window.
_BACKOFF = (2.0, 6.0, 12.0, 20.0, 30.0)


async def _complete(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    max_tokens: int,
) -> tuple[str, dict]:
    """One non-streaming call. Returns `(text, usage)`. Retries a 429."""
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
                    # Off where the provider allows; `thinking.switch` learns where not.
                    **thinking.switch(model),
                },
            )
            if thinking.refused(model, response):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": max_tokens},
                )
            if response.status_code != 429 or attempt == len(_BACKOFF):
                break
            # A token-per-minute 429 names when its window resets; wait for that.
            delay = ratelimit.retry_delay(response.text, dict(response.headers), _BACKOFF[attempt])
            log.info("report call rate limited, retrying in %.0fs", delay)
            await asyncio.sleep(delay)
        response.raise_for_status()
        payload = response.json()

    # A reasoning model can spend the whole ceiling thinking; re-ask with a bigger one.
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
                    **thinking.switch(model),
                },
            )
            if again.status_code >= 400:
                # A gateway that rejects `reasoning` refuses the whole call.
                again = await client.post(
                    "/v1/chat/completions",
                    json={"model": model, "messages": messages, "max_tokens": bigger},
                )
        if again.status_code == 200:
            retried = again.json()
            spent = retried.get("usage") or {}
            first = payload.get("usage") or {}
            # Both calls are billed, so both are counted.
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
    """The look the outline chose, or `""`. Read by regex so a partial answer keeps its style."""
    match = re.search(r'"style"\s*:\s*"([^"]+)"', text)
    return _STYLES.get((match.group(1).strip() if match else ""), "")


#: The planner's stated subject, checked against the request — see grounding.
_subject_missing = grounding.subject_missing


#: Documents about the person's own work that carry nothing without it.
_RESULTS = re.compile(r"결과|시험|실험|측정|캡스톤|제안서|설계 변경|기획서|연구 ?계획")
#: Documents whose facts all come from outside — nothing to write without a search.
_FROM_THE_WEB = re.compile(
    r"동향|조사해|문헌|선행 ?연구|최근 (?:\d+ ?년|연구)|연구를 정리|현황을 조사|비교표|인용|"
    r"참고문헌|시카고|APA|출처를"
)


def _from_the_web(request: str) -> bool:
    return bool(_FROM_THE_WEB.search(request))


#: Words that point at a thing the person has and did not attach.
_MATERIAL = re.compile(
    r"녹취|녹음|피드백|기록을|표가 있|파일|첨부|메모를|학위논문|논문 ?\d+ ?장"
    r"|(?:표|자료)를 (?:붙|드립|줍|보냅|첨부)"
)


_PAGES = re.compile(r"(?<!\d)(\d{1,4})\s{0,3}(?:장|쪽|페이지|p)\s{0,3}(?:이상|분량|짜리|내외|정도)")


def _long_form(request: str) -> bool:
    """Whether eight or more pages were asked for; past that every section is written on its own."""
    m = _PAGES.search(request)
    return bool(m) and int(m.group(1)) >= 8


def _without_own_heading(body: str, heading: str) -> str:
    """The section's text minus a repeat of its own heading on the first line."""
    lines = body.lstrip().split("\n", 1)
    first = re.sub(r"^#{1,6}\s*|\*+", "", lines[0]).strip()
    # A lone bold line at the top with no sentence in it is a heading whatever it says
    # (「**위험과 대응**」 over a section called 「위험」); the body starts below it.
    if (
        re.fullmatch(r"\*\*[^*\n]{1,40}\*\*\s*", lines[0])
        and not re.search(r"[.!?。]", lines[0])
        and len(lines) > 1
    ):
        return lines[1].lstrip()
    if first != heading.strip() and (
        re.match(r"^#{1,6}\s+", lines[0]) or re.fullmatch(r"\*\*.+\*\*\s*", lines[0])
    ):
        # Numbered headings may repeat the outline; plain numbered lists remain body text.
        first = re.sub(r"^\d+[.)]\s+", "", first)
    if first and first == heading.strip():
        return lines[1].lstrip() if len(lines) > 1 else ""
    # A plain title-like first line (short, no sentence mark, a blank line after it) that
    # contains the heading, or is contained in it, is the heading under a new name — the
    # revision wrote 「위험과 대응」 over a section still called 「위험」.
    if (
        len(lines) > 1
        and len(first) <= 40
        and not re.search(r"[.!?。:|]", first)
        and lines[1].startswith("\n")
    ):
        a, b = _heading_grams(first), _heading_grams(heading)
        if a and b and (len(a & b) / len(b) >= 0.8 or len(a & b) / len(a) >= 0.8):
            return lines[1].lstrip()
    return body


def _heading_grams(text: str) -> set[str]:
    plain = re.sub(r"[^가-힣A-Za-z0-9]", "", text)
    return {plain[i : i + 2] for i in range(len(plain) - 1)} if len(plain) > 1 else {plain}


def _carries_material(request: str) -> bool:
    """Whether the request carries its own material: long enough, or with a table in it."""
    return len(request) >= 300 or "|" in request


def _results_without_data(request: str, attached: list[str]) -> bool:
    """Whether the request names or needs material (녹취, 표, 파일, results) that nothing attached carries."""
    if any(block.strip() for block in attached) or _carries_material(request):
        return False
    if _MATERIAL.search(request):
        return True
    if not _RESULTS.search(request):
        return False
    return not deck_rules.has_numbers(request, [])


def _fold_alternatives(headings: list[str], alternatives: list[str]) -> list[str]:
    """Drops sections named after one alternative and keeps a single 대안 비교 section."""
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
        # After the calculation section if there is one, else after the situation.
        at = next((i + 1 for i, h in enumerate(keep) if "계산" in h or "전제" in h), None)
        keep.insert(min(len(keep), 2) if at is None else at, "대안 비교")
    return keep


def _parse_outline(text: str) -> tuple[str, list[str]]:
    """`(title, headings)` from whatever the model wrapped its JSON in; the title may be empty."""
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
    # `.get` throughout: a hand-typed source carries only a title and a url.
    return "\n".join(
        f"[{s.get('ordinal', i + 1)}] {s.get('title') or s.get('url') or ''}"
        f" ({s.get('publisher') or ''})\n{s.get('quote') or ''}"
        for i, s in enumerate(sources)
    )


async def _draw(figure: dict, image_model: dict | None, api_key: str) -> dict | None:
    """One picture as a stored figure, or `None` when it could not be drawn.

    Embedded as a `data:` URI so exported and mailed copies keep it. Never raises.
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
            aspect="4:3",
        )
    except Exception as exc:  # noqa: BLE001 — a missing figure is not a failed report
        log.warning("figure could not be drawn: %s", exc)
        return None
    return {
        # `encode` returns the whole `data:` URI.
        "src": pictures.encode(made.mime, made.data),
        "caption": str(figure.get("caption") or ""),
        "width": made.width,
        "height": made.height,
        # Popped by the caller into the turn's usage.
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


#: The two blocks that are nothing but figures, drawn large.
_FIGURE_FENCE = re.compile(r"^```(?:kpi|chart)\b.*?^```\s*$", re.S | re.M)


def _grounded_figures(text: str, grounded: bool) -> str:
    """Figure blocks removed from a section with no material to draw them from; the prose stays."""
    if not grounded:
        return _FIGURE_FENCE.sub("", text).strip()
    return re.sub(r"\n{3,}", "\n\n", _KPI_FENCE.sub(_kpi_or_nothing, text)).strip()


_KPI_FENCE = re.compile(r"^```kpi\b.*?^```\s*$", re.S | re.M)
_UNDETERMINED = re.compile(r"미정|확인 필요|해당 없음|N/A|TBD|\?", re.I)


def _kpi_or_nothing(match: re.Match[str]) -> str:
    """A kpi block removed whole when any of its values is a placeholder rather than a number."""
    body = match.group(0).strip("`").split("\n", 1)[1] if "\n" in match.group(0) else ""
    lines = [
        line for line in body.split("\n") if line.strip() and not line.strip().startswith("```")
    ]
    if any(
        _UNDETERMINED.search(line.split("|")[0]) or not re.search(r"\d", line.split("|")[0])
        for line in lines
    ):
        return ""
    return match.group(0)


async def write(
    *,
    request: str,
    model: str,
    api_key: str,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    #: Model that plans, when an administrator named one; empty plans with `model`.
    outline_model: str = "",
    #: The approved 목차. Absent: plan, emit `proposal` (or `needs`) and stop.
    #: Present: write exactly it.
    approved_plan: dict[str, Any] | None = None,
    #: Whether this pass may stop to ask. False on the pass after 있는 자료로
    #: 진행, or the same question loops.
    may_ask: bool = True,
    #: Approved pictures to draw. `None` on the planning pass; `[]` means 그림 없이.
    figures_plan: list[dict] | None = None,
    #: Model that draws them. Empty disables the figure proposal.
    image_model: dict | None = None,
    #: Whether to research through `services.research` before planning.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streams `step`, `section` and one final `usage` event.

    The caller owns persistence, billing and the artifact — this only writes.
    """
    # Outline tokens are counted apart: planning may run on another model.
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "outlineInputTokens": 0,
        "outlineOutputTokens": 0,
    }

    # Research runs before the outline so the 목차 is planned on the sources.
    findings = research.Findings()
    # 제 자료로 쓰는 문서는 검색하지 않는다.
    if web_search and _own_material(request):
        web_search = False
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
    # Copied: project citations must not be appended into the caller's list.
    findings.sources = list(findings.sources)
    web_selected = len(findings.sources)
    if (
        may_ask
        and approved_plan is None
        and web_search
        and findings.searched
        and web_selected == 0
        and _from_the_web(request)
        and not _carries_material(request)
        and not any(block.strip() for block in untrusted_context or [])
    ):
        # 검색이 빈손이면 동향·문헌 문서는 쓰지 않고 묻는다.
        yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
        yield {
            "type": "needs",
            "questions": [
                grounding.Question(
                    id="sources",
                    question=(
                        "웹 검색이 쓸 만한 자료를 찾지 못했습니다(검색 엔진이 응답하지 "
                        "않거나 무관한 결과뿐). 참고할 자료를 붙이거나 잠시 뒤 다시 시도해 "
                        "주세요. 「있는 자료로 진행」이면 기억으로 쓰되 확인이 필요한 "
                        "항목을 그렇게 표시합니다."
                    ),
                    options=[],
                ).wire()
            ],
        }
        yield {"type": "usage", **usage}
        return
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

    # Stored by the report artifact as its research log.
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
    # Switched off needs no disclaimer; could not run and found nothing each get one.
    research_rule = ""
    if web_search and not findings.searched:
        research_rule = research.UNRESEARCHED_RULE
    elif web_search and web_selected == 0:
        research_rule = research.EMPTY_RULE
    # Research pages go after the attached files, as their own labelled block.
    document_context = list(untrusted_context or [])
    if project_reference_lines:
        trusted_context = list(trusted_context or []) + [
            "# 프로젝트 자료 인용 번호\n"
            "프로젝트 자료에서 가져온 사실을 사용한 문장 끝에는 아래 번호를 정확히 붙이세요. "
            "목록에 없는 번호를 만들지 마세요.\n" + "\n".join(project_reference_lines)
        ]
    #: Whether the material carries any numbers; judged once, by the deck's test.
    grounded = deck_rules.has_numbers(request, document_context)
    if block := research.context_block(findings):
        document_context.append(block)

    if approved_plan is None:
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
        wanted = requested_sections(request)

        async def ask(nudge: str = "") -> tuple[str, dict[str, int]]:
            return await _complete(
                outline_model or model,
                build_document_messages(
                    SessionKind.report,
                    _OUTLINE_PROMPT.format(
                        ask_rule=grounding.ASK_RULE if may_ask else grounding.PROCEED_RULE,
                        lo=wanted or _MIN_SECTIONS,
                        hi=wanted or _MAX_SECTIONS,
                        genre=_genre_rule(request),
                        request=request[:2000],
                    )
                    + nudge,
                    request=request,
                    trusted_context=trusted_context,
                    untrusted_context=document_context,
                    research_rule=research_rule,
                ),
                api_key,
                400,
            )

        try:
            text, spent = await ask()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("report outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
            yield {"type": "usage", **usage}
            return

        plan_rules.count(usage, spent, planned_apart=bool(outline_model))
        # A question instead of a 목차 — see `grounding.ASK_RULE`.
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
                            "어떤 연구입니까? 제안 방법의 이름과 핵심 아이디어, 있으면 "
                            "수식과 결과를 적어 주세요. 없으면 「있는 자료로 진행」— 내용 "
                            "자리는 (미정)으로 비워 둔 틀을 씁니다."
                            if re.search(r"논문", request)
                            else "바탕이 될 자료(녹취, 표, 측정값, 파일)를 붙이거나 적어 "
                            "주세요. 없으면 「있는 자료로 진행」— 내용 자리는 (미정)으로 "
                            "비워 둔 틀을 씁니다."
                        ),
                        options=[],
                    ).wire()
                ],
            }
            yield {"type": "usage", **usage}
            return
        if may_ask and _subject_missing(text, request, "\n".join(untrusted_context or [])):
            # 주제가 없는 요청은 묻는다.
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
        # Carried on the plan so the writing pass, a separate request, writes a
        # form — see `_FRAME_RULE`.
        frame = not may_ask and (
            _results_without_data(request, list(untrusted_context or []))
            or grounding.subject_missing(text, request, "\n".join(untrusted_context or []))
        )
        # Written from memory; carried on the plan so the document opens by saying so.
        unverified = (
            not may_ask
            and web_search
            and findings.searched
            and web_selected == 0
            and _from_the_web(request)
        )
        title, headings = _parse_outline(text)
        if wanted and len(headings) != wanted:
            try:
                retry_text, retry_spent = await ask(
                    f"\n\n앞선 목차는 {len(headings)}절이었다. 요청한 전체 목차는 정확히 "
                    f"{wanted}절이다. 요청한 항목을 빠뜨리지 말고 그 수에 맞춰 다시 구성하라."
                )
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.warning("report outline size retry failed: %s", exc)
            else:
                plan_rules.count(usage, retry_spent, planned_apart=bool(outline_model))
                retry_title, retry_headings = _parse_outline(retry_text)
                if len(retry_headings) == wanted:
                    title, headings = retry_title or title, retry_headings
            if len(headings) != wanted:
                yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
                yield {
                    "type": "error",
                    "message": f"요청한 {wanted}절에 맞는 목차를 만들지 못했습니다. 다시 시도해 주세요.",
                }
                yield {"type": "usage", **usage}
                return
        if len(headings) < (1 if wanted else _MIN_SECTIONS):
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
        # Planning stops here: the caller stores the plan, shows it, and calls
        # back with it approved. Figures are proposed now and asked about on a
        # separate card — see `services.figures`.
        plan: dict[str, Any] = {
            "title": title[:200],
            "sections": headings,
            # The request's own style wins; the outline's choice fills the default.
            "visualStyle": (
                design.visual_style_for(request)
                if design.visual_style_for(request) != "editorial"
                else (_outline_style(text) or "editorial")
            ),
        }
        if frame:
            plan["frame"] = True
        if unverified:
            plan["unverified"] = True
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
    frame = bool(approved_plan.get("frame"))
    unverified = bool(approved_plan.get("unverified"))
    if not headings:
        yield {"type": "error", "message": "승인된 개요가 비어 있습니다."}
        yield {"type": "usage", **usage}
        return
    # Emitted only when the model produced one, so the caller keeps its fallback.
    if title:
        yield {"type": "title", "title": hangul.tidy_spacing(title)[:200]}

    # The same sources the outline was planned on.
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

    # The whole document is drafted in one call so numbers and conclusions stay
    # consistent across sections, then cut along its headings. Sections the
    # draft missed are written on their own below; that pass also serves rewrites.
    drafted: dict[str, str] = {}
    long_form = _long_form(request)
    if long_form:
        # 긴 문서는 절마다 따로 쓴다 — see `_long_form`.
        yield {"type": "step", "id": "draft", "label": "절마다 길게 쓰는 중", "status": "done"}
    else:
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "running"}
    try:
        if long_form:
            raise ValueError("long form")
        draft_text, spent = await _complete(
            model,
            build_document_messages(
                SessionKind.report,
                _DRAFT_PROMPT.format(
                    outline="\n".join(f"## {h}" for h in headings),
                    refs=refs,
                    facts=_FRAME_RULE if frame else _facts_line(request, sources),
                    genre=_genre_rule(request),
                    request=request[:1500],
                ),
                request=request,
                trusted_context=trusted_context,
                untrusted_context=document_context,
                research_rule=research_rule,
            ),
            api_key,
            max_tokens=min(11000, 1400 * len(headings)),
        )
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        drafted = _carry_table(request, headings, _split_draft(draft_text, headings))
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "done"}
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        if not long_form:
            log.warning("report draft failed, writing section by section: %s", exc)
            yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "error"}
    #: Approved pictures by section index.
    wanted_figures = {int(f.get("section", -1)): f for f in (figures_plan or []) if f.get("prompt")}

    for index, section in enumerate(sections):
        # The position lives in `progress` only; the surface renders it.
        label = str(section["heading"])
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
                            facts=_FRAME_RULE if frame else _facts_line(request, sources or []),
                            genre=_genre_rule(request),
                        )
                        + (
                            # Told before writing so the section can refer to its figure.
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
                        )
                        + (
                            "\n\n이 문서는 긴 분량으로 요청되었다. 이 절은 문단 다섯에서 "
                            "여덟, 1,500자 이상으로 — 자료의 수치를 근거로 풀어 쓰되 같은 말을 "
                            "되풀이해 채우지 마라."
                            if long_form
                            else ""
                        ),
                        request=request,
                        trusted_context=trusted_context,
                        untrusted_context=document_context,
                        research_rule=research_rule,
                    ),
                    api_key,
                    2400 if long_form else 1200,
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
        # Stray ideographs are read back into Hangul and table gaps closed once,
        # before storing, so every reader sees the same text.
        body = unwrap_json_prose(body)
        if placeholder_heavy(body) and any(re.search(r"\d", m or "") for m in document_context):
            # 「(미정)」 cells under an attached table of numbers: the model wrote the frame
            # and left the data out. Asked once more, with the data in front of it.
            try:
                refilled, more = await _complete(
                    model,
                    build_document_messages(
                        SessionKind.report,
                        _FILL_PROMPT.format(heading=section["heading"], body=body[:6000]),
                        request=request,
                        trusted_context=trusted_context,
                        untrusted_context=document_context,
                        research_rule=research_rule,
                    ),
                    api_key,
                    1600,
                )
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                log.info("placeholder refill of %r failed: %s", section["heading"], exc)
            else:
                usage["inputTokens"] += more["inputTokens"]
                usage["outputTokens"] += more["outputTokens"]
                refilled = unwrap_json_prose(refilled)
                if refilled.strip() and not placeholder_heavy(refilled):
                    body = refilled
        # An editor's trim, by hand: a sentence that only restates numbers already given
        # in an earlier section, or a filler sentence, is cut. No model rewrites here — a
        # model asked to 「say it differently」 fills the gap with things nobody said.
        earlier = [str(s.get("content") or "") for s in sections[:index]]
        body, trimmed = trim_restatements(body, earlier)
        if trimmed:
            log.info("trimmed %d sentence(s) from %r: %s", len(trimmed), section["heading"], trimmed[0][:80])
        clean, _ = hangul.read_back(_without_own_heading(body, section["heading"]))
        clean = hangul.tidy_spacing(clean)
        clean = drop_redundant_kpi(drop_repeated_figures(clean))
        if not grounded:
            clean = _without_invented_money(clean)
        # Owners and dates come from the request, the material or a source — never the pen.
        clean = _unsourced_owner_dates(
            clean,
            "\n".join(
                [request, *document_context, *[str(s.get("quote") or "") for s in sources or []]]
            ),
        )
        if unverified and index == 0:
            # 첫 절 머리에 밝힌다.
            clean = _UNVERIFIED_NOTE + "\n\n" + clean
        # 「A × B = C」 in the draft is checked by arithmetic before it is kept.
        section["content"] = richtext.tidy_tables(
            _grounded_figures(
                trim_table_echo(
                    fix_point_units(
                        undouble_words(
                            fix_percent_formulas(
                                fix_ledger_magnitudes(
                                    fix_value_then_product(fix_products(clean)), request
                                )
                            )
                        )
                    )
                ),
                grounded,
            )
        )

        # Drawn after the prose so a failed drawing leaves no dangling reference.
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
                # Appended to the body as Markdown: the panel, the page view and
                # the exporters all read the body.
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

    # Structure, flow, comparison and concept figures the report draws for itself as
    # mermaid; the editor renders and stores them. Planned on the written sections, so the
    # planner reads what is actually there; a section with an approved picture keeps it.
    eligible = [
        i
        for i, section in enumerate(sections)
        if i not in wanted_figures and str(section.get("content") or "").strip()
    ]
    planned, spent = await diagrams.plan(
        parts=[(str(s["heading"]), str(s.get("content") or "")) for s in sections],
        eligible=eligible,
        request=request,
        model=model,
        api_key=api_key,
        complete=_complete,
        slide=False,
        wrap=lambda prompt: build_document_messages(
            SessionKind.report,
            prompt,
            request=request,
            trusted_context=trusted_context,
            untrusted_context=document_context,
            research_rule=research_rule,
        ),
    )
    usage["inputTokens"] += spent["inputTokens"]
    usage["outputTokens"] += spent["outputTokens"]
    if not planned:
        log.info("report figures: none planned for %d eligible sections", len(eligible))
    for row in planned:
        section = sections[row.index]
        name = row.caption or diagrams.FIGURES[row.figure]
        progress = {"current": row.index + 1, "total": len(sections)}
        yield {
            "type": "step",
            "id": f"dia{row.index}",
            "label": f"{name} 그리는 중",
            "status": "running",
            "progress": progress,
        }
        try:
            made, spent = await diagrams.make(row, model=model, api_key=api_key, slide=False)
        except Exception as exc:  # noqa: BLE001 — a section without its figure is still a section
            log.warning("report figure %r not drawn: %s", name, exc)
            yield {
                "type": "step",
                "id": f"dia{row.index}",
                "label": name,
                "status": "error",
                "progress": progress,
            }
            continue
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        # A mermaid fence in the body: the panel renders it, stores the raster, and the
        # exporters place that raster by key.
        section["content"] = f"{section['content'].rstrip()}\n\n{diagrams.fence(made)}"
        yield {
            "type": "step",
            "id": f"dia{row.index}",
            "label": name,
            "status": "done",
            "progress": progress,
        }
        yield {
            "type": "section",
            "sectionId": section["id"],
            "heading": section["heading"],
            "content": section["content"],
            "done": True,
        }

    sections = enforce_sentence_counts(trim_leading_conclusion(sections), request)
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
    material: list[str] | None = None,
) -> tuple[str, dict]:
    """Rewrites one section with the rest of the document as context.

    `material` is the request's own data — attached files, pasted tables — carried
    again so the rewrite draws its numbers from where the original did.
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
        # The shelf keeps the document's citation numbers valid.
        refs=_refs_block(sources or []),
        request=request[:1500],
        role=role,
        blocks=blocks,
        others=_others_line([str(x.get("heading") or "") for x in sections], position),
        # Numbers already in the document, and in the material it was written from, are allowed too.
        facts=_facts_line(
            "\n".join(
                [request, *(material or []), *[str(s.get("content") or "") for s in sections]]
            ),
            sources or [],
        ),
        genre=_genre_rule(request),
    )
    if note.strip():
        # Last and labelled, or it reads as part of the original request.
        prompt += (
            f"\n\n이번에 다시 쓰는 이유(반드시 반영):\n{note.strip()[:600]}\n"
            "이유가 형식을 말하면(번호 목록 셋, 표 하나, 세 문장) 그 형식 그대로 쓴다. "
            "이유에 없는 표·블록을 새로 보태지 말고, 다른 절에 이미 있는 표를 다시 그리지 마라."
        )
    body, spent = await _complete(
        model,
        build_document_messages(
            SessionKind.report, prompt, request=request, untrusted_context=material
        ),
        api_key,
        1200,
    )
    # The same normalisation `write` applies.
    body = hangul.tidy_spacing(hangul.read_back(_without_own_heading(body, heading))[0])
    target = next((s for s in sections if s.get("id") == target_id), {})
    others = [str(s.get("content") or "") for s in sections if s.get("id") != target_id]
    return _without_borrowed_tables(body, target.get("content") or "", others, note), spent


def _without_borrowed_tables(body: str, before: str, others: list[str], note: str) -> str:
    """The rewrite's tables, minus any another section draws or the section never had and the note did not ask for."""
    if not _TABLE.search(body):
        return body
    elsewhere = {_table_key(m.group(0)) for text in others for m in _TABLE.finditer(text)}
    had_table = bool(_TABLE.search(before)) or bool(re.search(r"표", note))

    def keep_or_drop(m: re.Match[str]) -> str:
        if _table_key(m.group(0)) in elsewhere or not had_table:
            return ""
        return m.group(0)

    return re.sub(r"\n{3,}", "\n\n", _TABLE.sub(keep_or_drop, body)).strip()


def _table_key(table: str) -> str:
    """A table's header row, spacing and alignment marks removed."""
    head = table.strip().split("\n", 1)[0]
    return re.sub(r"[\s:|-]+", "", head)


_PRODUCT = re.compile(
    r"(?P<a>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ua>만|억|천)?\s{0,8}(?P<unit>원|명|건|개|대|시간|분)?\s{0,8}"
    r"[×x\*]\s{0,8}(?P<b>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<ub>개월|년|개|명|대|회|일|주|배|시간)?\s{0,8}=\s{0,8}"
    r"(?P<c>\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?P<uc>만|억|천)?\s{0,8}(?P<unitc>원|명|건|개|대|시간|분)?"
)


def _as_number(text: str) -> float:
    return float(text.replace(",", ""))


#: 「16억 9,200만 원 (470만 원 × 36개월)」 — a total written first, its formula after it.
_VALUE_THEN_PRODUCT = re.compile(
    r"(?P<c>\d[\d,]*(?:\.\d+)?\s*억(?:\s*\d[\d,]*\s*만)?|\d[\d,]*(?:\.\d+)?\s*만)\s*원?\s*"
    r"\(\s*(?P<a>\d[\d,]*(?:\.\d+)?)\s*만\s*원?\s*[×x\*]\s*(?P<b>\d[\d,]*)\s*(?:개월|년|개|명|대|회)\s*\)"
)


_AMOUNT_WON = re.compile(
    r"(?<![\d,.])((?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억\s{0,8})?(?:\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}만)|\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}억)\s{0,8}원"
)


def fix_ledger_magnitudes(text: str, request: str) -> str:
    """An amount that is a ledger result off by a power of ten (「169,200만 원」 for the
    「1억 6,920만 원」 the request's arithmetic gives) is that result: the writer slipped a
    digit, it did not compute a new figure."""
    results: list[float] = []
    for line in derived_values(request or ""):
        if " = " not in line:
            continue
        right = line.rsplit(" = ", 1)[1]
        match = _AMOUNT_WON.search(right)
        value = _to_man(match.group(1)) if match else None
        if value:
            results.append(value)
    if not results:
        return text

    def fix(m: re.Match) -> str:
        value = _to_man(m.group(1))
        if value is None or any(abs(value - r) < 0.5 for r in results):
            return m.group(0)
        for r in results:
            for factor in (10, 100, 0.1, 0.01):
                if abs(value - r * factor) < 0.5:
                    return _won(r)
        return m.group(0)

    return _AMOUNT_WON.sub(fix, text)


def _to_man(text: str) -> float | None:
    """「16억 9,200만」 → 169200, 「1억」 → 10000, 「5,640만」 → 5640; `None` when unreadable."""
    plain = text.replace(",", "").replace(" ", "")
    match = re.fullmatch(r"(?:(\d{1,15}(?:\.\d{1,15})?)억)?(?:(\d{1,15}(?:\.\d{1,15})?)만)?", plain)
    if not match or not (match.group(1) or match.group(2)):
        return None
    return float(match.group(1) or 0) * 10000 + float(match.group(2) or 0)


#: The same noun twice in a row under different particles — 「방향으로 방향을 잡는다」 —
#: a writer that restarted its phrase mid-sentence. Code and URLs are left alone.
#: The first half must be an adverbial (「방향으로」, 「서울에서」); a subject followed by
#: the same noun as object (「비용이 비용을 낳는다」) is a sentence, not a restart.
_DOUBLED = re.compile(
    r"(?<![가-힣])([가-힣]{2,6})(으로|로|에서|에게|에|의|와|과|도)\s+"
    r"\1(으로|로|에서|에게|에|을|를|이|가|은|는|의|와|과|도)(?![가-힣])"
)


def undouble_words(text: str) -> str:
    """「방향으로 방향을」 → 「방향을」: the restarted phrase keeps its second, finished half.
    Table rows and fenced code are left as written."""
    out = []
    fenced = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("|"):
            out.append(line)
            continue
        out.append(_DOUBLED.sub(lambda m: f"{m.group(1)}{m.group(3)}", line))
    return "\n".join(out)


#: A difference worked out in prose (「0.521 - 0.345 = 0.176」) is scratch work; a cost
#: working (「470만 원 × 36개월 = …」) is asked for by the prompt and stays.
_SHOWN_ARITHMETIC = re.compile(
    r"(?<![\d.])\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}[-−–]\s{0,8}\d[\d,]{0,15}(?:\.\d{1,15})?\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)"
)
_NUMBER_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s{1,8}|(?<=다\.)\s{0,8}(?=[가-힣A-Za-z(「\d])")


def _number_keys(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER_TOKEN.finditer(text)}


def _collapse_arithmetic(body: str) -> str:
    """「0.521 - 0.345 = 0.176으로 측정되어」 → 「0.176으로 측정되어」 outside tables and code:
    a report states a result; the working belongs in a table note, if anywhere."""
    out = []
    fenced = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("|"):
            out.append(line)
        else:
            out.append(_SHOWN_ARITHMETIC.sub(r"\1", line))
    return "\n".join(out)


def trim_table_echo(body: str) -> str:
    """A section that puts its numbers in a table does not also read them out.

    「8에서 16으로 갈 때 0.176 올랐고, 128에서 256은 0.018…」 above or below a table with
    those very cells is the table twice. A sentence whose numbers (two or more) are all
    cells of a table in the same section goes; a sentence with a judgement and at most one
    table number stays. Shown arithmetic (「0.521 - 0.345 = 0.176」) becomes its result. A
    section never loses its last prose sentence.
    """
    lines = body.split("\n")
    table_cells: set[str] = set()
    for line in lines:
        if line.lstrip().startswith("|"):
            for cell in line.strip().strip("|").split("|"):
                table_cells |= _number_keys(cell)
    if len(table_cells) < 2:
        # No table to echo; shown arithmetic in prose still becomes its result.
        return _collapse_arithmetic(body)
    out: list[str] = []
    fenced = False
    prose_kept = 0
    for line in lines:
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or line.lstrip().startswith(("|", "#", "-", "*", ">")) or not line.strip():
            out.append(line)
            continue
        line = _SHOWN_ARITHMETIC.sub(r"\1", line)
        kept = []
        for sentence in _SENTENCE_END.split(line):
            if not sentence.strip():
                continue
            numbers = _number_keys(sentence)
            if len(numbers) >= 2 and numbers <= table_cells:
                continue
            kept.append(sentence.strip())
        prose_kept += len(kept)
        if kept:
            out.append(" ".join(kept))
    if prose_kept == 0:
        return _SHOWN_ARITHMETIC.sub(r"\1", body)
    return "\n".join(out)


_POINT_UNIT = re.compile(r"(?<![\d.])(\d{1,15}(?:\.\d{1,15})?)\s{0,8}(?:%p|퍼센트\s{0,8}포인트|포인트)(?![\w가-힣])")
_BARE_RATIO = re.compile(r"(?<![\d.%])0\.\d{1,15}(?![\d.%])")


def fix_point_units(text: str) -> str:
    """「0.853에서 0.912로 0.059%p 증가」: a difference of two ratios is a ratio, not
    percentage points. The unit goes when the sentence's other numbers are bare ratios
    (0.xxx with no %) and nothing in it is a percentage; a sentence about percentages
    (「48%에서 52%로 4%p」) keeps its points."""
    out = []
    for sentence in re.split(r"(?<=[.!?。])\s{1,8}", text):
        if _POINT_UNIT.search(sentence) and len(_BARE_RATIO.findall(sentence)) >= 2 and "%" not in (
            _POINT_UNIT.sub(r"\1", sentence)
        ):
            sentence = _POINT_UNIT.sub(r"\1", sentence)
        out.append(sentence)
    return " ".join(out)


_PERCENT_FORMULA = re.compile(
    r"\(?\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:-|−|–)\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}\)?\s{0,8}(?:÷|/)\s{0,8}"
    r"(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:×|x|\*)\s{0,8}100\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}%"
)
_RATIO_FORMULA = re.compile(
    r"(?<![\d.)])(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:÷|/)\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}(?:×|x|\*)\s{0,8}100"
    r"\s{0,8}=\s{0,8}(\d[\d,]{0,15}(?:\.\d{1,15})?)\s{0,8}%"
)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _pct(value: float) -> str:
    rounded = round(value, 1)
    return f"{rounded:.1f}".rstrip("0").rstrip(".") + "%"


def fix_percent_formulas(text: str) -> str:
    """「(14-3) ÷ 14 × 100 = 1,400%」 is checked, not trusted: the result becomes 78.6%, and
    the wrong figure is corrected wherever else the section repeats it."""
    wrong: dict[str, str] = {}

    def diff(m: re.Match) -> str:
        a, b, c, shown = _num(m.group(1)), _num(m.group(2)), _num(m.group(3)), m.group(4)
        if c == 0:
            return m.group(0)
        right = _pct((a - b) / c * 100)
        if right != _pct(_num(shown)):
            wrong[shown + "%"] = right
            return m.group(0)[: m.start(4) - m.start()] + right
        return m.group(0)

    def ratio(m: re.Match) -> str:
        a, b, shown = _num(m.group(1)), _num(m.group(2)), m.group(3)
        if b == 0:
            return m.group(0)
        right = _pct(a / b * 100)
        if right != _pct(_num(shown)):
            wrong[shown + "%"] = right
            return m.group(0)[: m.start(3) - m.start()] + right
        return m.group(0)

    out = _PERCENT_FORMULA.sub(diff, text)
    out = _RATIO_FORMULA.sub(ratio, out)
    for bad, good in wrong.items():
        out = out.replace(bad, good)
    return out


_LEADING_CONCLUSION = re.compile(r"결론|요약|제언|권고|핵심\s*요약|executive|summary", re.I)


def _section_text(section: dict) -> str:
    return re.sub(r"<[^>]+>", " ", str(section.get("content") or ""))


def trim_leading_conclusion(sections: list[dict]) -> list[dict]:
    """A conclusion moved to the front (「결론을 맨 앞으로」) does not re-list the numbers
    the sections after it give: its sentences that only restate those numbers go, as a
    later section's would against the ones before it. Never emptied."""
    if len(sections) < 2 or not _LEADING_CONCLUSION.search(str(sections[0].get("heading") or "")):
        return sections
    body = str(sections[0].get("content") or "")
    if str(sections[0].get("format") or "markdown") != "markdown":
        return sections
    trimmed, cut = trim_restatements(body, [_section_text(s) for s in sections[1:]])
    if cut and trimmed.strip():
        sections[0] = {**sections[0], "content": trimmed}
    return sections


_KO_COUNT = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6}
_SENTENCE_ASK = re.compile(
    r"(요약|결론|서론|개요|배경|제언|권고|도입)[은는이가도]?\s{0,8}(?:절|부분|문단)?[은는이가도]?\s{0,8}"
    r"(한|두|세|네|다섯|여섯|\d{1,15})\s{0,8}문장(?:\s{0,8}(?:으로|이내|이하|안에|까지|만))?"
)


def requested_sentence_counts(request: str) -> dict[str, int]:
    """「요약은 세 문장」 「결론 두 문장으로」 → {"요약": 3, "결론": 2}."""
    out: dict[str, int] = {}
    for m in _SENTENCE_ASK.finditer(request or ""):
        count = _KO_COUNT.get(m.group(2)) or int(m.group(2)) if m.group(2) else None
        if count:
            out[m.group(1)] = count
    return out


def enforce_sentence_counts(sections: list[dict], request: str) -> list[dict]:
    """A section the request sized in sentences keeps that many: the first N prose
    sentences stay, later ones go; tables, lists and fenced blocks are not sentences and
    stay where they are."""
    wanted = requested_sentence_counts(request)
    if not wanted:
        return sections
    for index, section in enumerate(sections):
        heading = str(section.get("heading") or "")
        count = next((n for word, n in wanted.items() if word in heading), None)
        if not count or str(section.get("format") or "markdown") != "markdown":
            continue
        body = str(section.get("content") or "")
        out, seen, fenced = [], 0, False
        for line in body.split("\n"):
            if line.strip().startswith("```"):
                fenced = not fenced
            if fenced or not line.strip() or line.lstrip().startswith(("|", "#", "-", "*", ">", "!")):
                out.append(line)
                continue
            kept = []
            for sentence in _SENTENCE_END.split(line):
                if not sentence.strip():
                    continue
                if seen < count:
                    kept.append(sentence.strip())
                    seen += 1
            if kept:
                out.append(" ".join(kept))
        if seen > count or out != body.split("\n"):
            sections[index] = {**section, "content": "\n".join(out).strip() + "\n"}
    return sections


def fix_value_then_product(text: str) -> str:
    """A total written before its own formula is recomputed from the formula; a wrong total
    is replaced where it stands and wherever the section repeats it. The ledger gives
    「1억 6,920만 원」; a model reading it as 「16억 9,200만 원」 is caught here."""
    out = text or ""
    corrections: list[tuple[str, str]] = []
    for match in reversed(list(_VALUE_THEN_PRODUCT.finditer(out))):
        written = _to_man(match.group("c"))
        try:
            product = _as_number(match.group("a")) * _as_number(match.group("b"))
        except ValueError:
            continue
        if written is None or product <= 0 or abs(product - written) <= max(0.5, product * 0.005):
            continue
        right = _won(product).replace(" 원", "")
        out = out[: match.start("c")] + right + out[match.end("c") :]
        corrections.append((match.group("c").strip(), right))
    for wrong, right in corrections:
        out = re.sub(rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.])", right, out)
    return out


def _format_like(value: float, sample: str) -> str:
    """`value` written the way `sample` was: thousands separators if it had them, no
    needless decimals."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        rounded = int(round(rounded))
    text = f"{rounded:,}" if "," in sample or (isinstance(rounded, int) and rounded >= 1000) else f"{rounded}"
    return text


def fix_products(text: str) -> str:
    """Every 「A × B = C」 in `text` recomputed: a wrong C is replaced, everywhere in the
    text, by the right one.

    A model asked for a 36-month total writes 「470만 원 × 36개월 = 169,200만 원」, ten
    times the truth, and repeats the wrong figure in the table below. The sentence is
    the proof of its own error; the product is checked and the figure corrected where
    it stands and wherever else the section repeats it. Only plain products with the
    same scale on both sides are touched."""
    out = text or ""
    corrections: list[tuple[str, str, str, str]] = []
    # The equations first, each result replaced in its own span (right to left so
    # earlier offsets stay valid).
    for match in reversed(list(_PRODUCT.finditer(out))):
        if (match.group("ua") or "") != (match.group("uc") or ""):
            continue
        try:
            a = _as_number(match.group("a"))
            b = _as_number(match.group("b"))
            c = _as_number(match.group("c"))
        except ValueError:
            continue
        product = a * b
        if product <= 0 or abs(product - c) <= max(0.5, abs(product) * 0.005):
            continue
        right = _format_like(product, match.group("c"))
        out = out[: match.start("c")] + right + out[match.end("c") :]
        corrections.append(
            (match.group("c"), right, match.group("uc") or "", match.group("unitc") or "")
        )
    # Then the same wrong figure wherever the text repeats it — a table cell below the
    # sentence — matched as a whole number with its scale and unit, never inside another
    # number: 「169,200만 원」 goes, 「2,169,200」 and 「169,2001」 stay.
    for wrong, right, scale, unit in corrections:
        tail = (rf"\s*{re.escape(scale)}" if scale else "") + (rf"\s*{re.escape(unit)}" if unit else "")
        pattern = re.compile(rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.]){tail}" if (scale or unit)
                             else rf"(?<![\d,.]){re.escape(wrong)}(?![\d,.])")
        def _swap(m: re.Match[str], wrong: str = wrong, right: str = right) -> str:
            return m.group(0).replace(wrong, right, 1)

        out = pattern.sub(_swap, out)
    return out


_PLACEHOLDER = re.compile(r"\((?:미정|측정값|값|TBD|TODO|n/a|추후\s*기입|확인\s*필요)\)", re.I)
_FILL_PROMPT = """아래는 보고서의 "{heading}" 절 초안이다. 표와 문장에 (미정)·(측정값) 같은 빈자리가 남아
있다. 첨부 자료(참고 데이터)에 그 값들이 들어 있다. 빈자리를 자료의 실제 값으로 채워 절을
다시 써라. 자료에 없는 값은 만들지 말고 그 칸은 「—」로 둔다. 절 제목 없이 본문만, 마크다운으로
답하라. JSON 으로 싸지 마라.

{body}"""


#: Sentences that carry no fact: the model's way of saying it has nothing to add.
_FILLER = re.compile(
    r"(명시되어 있지 않습니다|확인되지 않았습니다|자료에 (?:다른|별도의) [^.]*없습니다|"
    r"고려할 필요가 있습니다|중요한 역할을 합니다|할 수 있을 것으로 보입니다|"
    r"필요할 것으로 판단됩니다|주목할 만합니다|의미가 있다고 할 수 있습니다)"
)
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")
#: A number with the unit glued to it (「742명」 「6.1만 건」 「14분」 「4.1점」 「7%」).
_NUMBER_FACT = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*(?:\s*(?:만|억|천))?[가-힣%]?")


def _sentences(text: str) -> list[str]:
    plain = re.sub(r"```.*?```", " ", text, flags=re.S)
    plain = re.sub(r"^\|.*$", " ", plain, flags=re.M)
    return [s.strip() for s in _SENTENCE_END.split(plain) if len(s.strip()) >= 12]


def _grams(sentence: str) -> set[str]:
    """Character bigrams without spaces or punctuation: particles and spacing differ
    between two tellings of the same fact, the letters mostly do not."""
    plain = re.sub(r"[^가-힣A-Za-z0-9%]", "", sentence)
    return {plain[i : i + 2] for i in range(len(plain) - 1)}


def editing_issues(body: str, earlier: list[str]) -> list[str]:
    """What an editor would flag in `body` against the sections before it; `[]` when
    nothing. Each item names the sentence, so the rewrite can be asked for precisely."""
    issues: list[str] = []
    own = _sentences(body)
    if not own:
        return issues
    seen = [_grams(s) for text in earlier for s in _sentences(text)]
    repeated = 0
    for sentence in own:
        grams = _grams(sentence)
        if len(grams) >= 12 and any(
            2 * len(grams & g) / (len(grams) + len(g)) >= 0.6 for g in seen if g
        ):
            repeated += 1
            if repeated <= 2:
                issues.append(f"앞 절과 같은 말: 「{sentence[:60]}」")
    # The same facts told again in new words: a sentence whose every number (with its
    # unit) already appeared in an earlier section is a restatement, however phrased.
    earlier_numbers = set(_NUMBER_FACT.findall(" ".join(earlier)))
    if earlier_numbers:
        restated = 0
        for sentence in own:
            numbers = set(_NUMBER_FACT.findall(sentence))
            if len(numbers) >= 2 and numbers <= earlier_numbers:
                restated += 1
                if restated <= 2 and not any(sentence[:60] in i for i in issues):
                    issues.append(f"앞 절에 이미 있는 사실을 다시 말함: 「{sentence[:60]}」")
    for sentence in own:
        if _FILLER.search(sentence):
            issues.append(f"빈말: 「{sentence[:60]}」")
            break
    endings = [s[-4:] for s in own if s.endswith(".")]
    if len(endings) >= 5 and len(set(endings)) == 1:
        issues.append("모든 문장이 같은 말로 끝남")
    return issues


_PARTICLE = re.compile(
    r"(되었습니다|였습니다|이었습니다|했습니다|합니다|됩니다|입니다|습니다|되었고|이었고|였고|"
    r"되었|되어|하였|하여|했다|한다|하는|하고|하며|이며|으며|에서는|에서|으로|은|는|이|가|을|를|"
    r"의|에|로|와|과|도|된|한|며)$"
)
#: Words that carry no fact of their own; a restatement dressed in them is still one.
_CONNECTIVES = frozenset(
    "이번 총 동안 기간 대상 기준 통해 위해 경우 또한 그리고 이후 당시 전체 각각 모두 해당 "
    "진행 운영 실시 수행 소속 포함 전제 결과 과정 중 및 등".split()
)


def _stems(text: str) -> set[str]:
    """Content words with the particle or ending cut off, so 「대상은」 and 「대상」 agree;
    one-letter stems and connective words are not counted as content."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z]{2,}", text):
        stem = _PARTICLE.sub("", word) or word
        if len(stem) >= 2 and stem not in _CONNECTIVES:
            out.add(stem)
    return out


def trim_restatements(body: str, earlier: list[str]) -> tuple[str, list[str]]:
    """`(body without pure restatements and filler, the sentences cut)`.

    A sentence is cut only when it is safe to lose: every number in it (with its unit)
    already appears in an earlier section and it carries no word the earlier sections
    lack, or it is a filler sentence. Tables and blocks are never touched, and a section
    is never emptied."""
    if not body.strip():
        return body, []
    # With no earlier sections the pool starts empty and grows sentence by sentence, so
    # a section that says 「64에서 128로 갈 때 0.853에서 0.912로」 twice says it once.
    earlier_text = " ".join(earlier)
    earlier_numbers = set(_NUMBER_FACT.findall(earlier_text))
    earlier_words = _stems(earlier_text)
    has_table = any(line.strip().startswith("|") for line in body.split("\n"))
    cut: list[str] = []
    out_lines: list[str] = []
    in_block = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_block = not in_block
        if line.strip().startswith("|"):
            # Prose after a table that only reads the table back is a restatement too.
            cells = " ".join(
                c for c in line.split("|") if c.strip() and not set(c.strip()) <= set("-: ")
            )
            earlier_numbers |= set(_NUMBER_FACT.findall(cells))
            earlier_words |= _stems(cells)
        if in_block or line.strip().startswith("|") or line.strip().startswith("#") or not line.strip():
            out_lines.append(line)
            continue
        sentences = [x for x in _SENTENCE_END.split(line) if x.strip()]
        kept: list[str] = []
        for sentence in sentences:
            text = sentence.strip()
            numbers = set(_NUMBER_FACT.findall(text))
            words = _stems(text)
            # A ratio drawn from the numbers already given (「64에서 128로 2배가 될 때」) is
            # not a new fact.
            given = {n for n in numbers if n in earlier_numbers or re.fullmatch(r"\d{1,15}배", n)}
            # Two numbers already given, or one number with nothing else new to say.
            restated = (
                bool(numbers)
                and numbers <= given
                and (
                    len(numbers) >= 2
                    and len(words - earlier_words) <= max(1, len(words) // 5)
                    # Three numbers already given together is the same statement, even
                    # when a few generic words (「데이터에서 확인되는」) changed.
                    or len(numbers) >= 3
                    and len(words - earlier_words) <= len(words) // 2
                    or len(numbers) == 1
                    and len(text) >= 8
                    and not (words - earlier_words)
                )
            )
            dangling = (
                not has_table
                and re.search(r"(?:위|아래|다음|이)\s*표(?:는|에서|를|가|의)", text)
                and re.search(r"(?:보여|나타내|정리|제시|요약)", text)
            )
            if restated or dangling or _FILLER.search(text):
                cut.append(text)
                continue
            kept.append(sentence)
            # What this sentence said is now said: a later sentence repeating it goes.
            earlier_numbers |= numbers
            earlier_words |= words
        if sentences and not kept and not any(x.strip() for x in out_lines):
            # A section is never emptied: its first paragraph keeps its first sentence.
            kept = [sentences[0]]
            cut.remove(sentences[0].strip())
        if kept or not sentences:
            out_lines.append(" ".join(k.strip() for k in kept) if kept else line)
    if not cut:
        return body, []
    return "\n".join(out_lines).strip() + "\n", cut


def edit_keeps_facts(before: str, after: str) -> bool:
    """An edit is kept only when every number of the draft survives, nothing new was
    numbered, and the text did not shrink by more than half."""
    after = after.strip()
    # Cutting a repeated and a filler sentence from a short section can take a third
    # of it away; losing more than that means the editor rewrote, not trimmed.
    if not after or len(after) < len(before.strip()) * 0.4:
        return False
    numbers_before = set(re.findall(r"\d[\d,.]*", before))
    numbers_after = set(re.findall(r"\d[\d,.]*", after))
    return numbers_after <= numbers_before and not placeholder_heavy(after)


_FIGURE_BLOCK = re.compile(r"```(?:mermaid|chart)\b.*?```\n?(?:\*[^\n]*\*\n?)?", re.S)
_FIGURE_KEYWORDS = {"graph", "flowchart", "LR", "TD", "TB", "RL", "style", "fill", "classDef",
                    "class", "subgraph", "end", "direction", "br", "hot", "mermaid", "chart"}


def _figure_words(block: str) -> set[str]:
    """The words a diagram shows, without its syntax: node labels, whatever the shape."""
    body = re.sub(r"^```\w*|```$", "", block.strip(), flags=re.M)
    body = re.sub(r"\*[^\n]*\*\s*$", "", body)
    return {w for w in re.findall(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{1,}", body) if w not in _FIGURE_KEYWORDS}


def drop_repeated_figures(text: str) -> str:
    """A section that draws the same thing twice — the writer's own diagram and then the
    planned figure of the same nodes — keeps one: the captioned one if there is one,
    else the first. Two blocks are the same drawing when four fifths of the words of the
    smaller appear in the larger."""
    blocks = list(_FIGURE_BLOCK.finditer(text))
    if len(blocks) < 2:
        return text
    words = [_figure_words(m.group(0)) for m in blocks]
    captioned = [bool(re.search(r"\*[^\n]*\*\s*$", m.group(0).strip())) for m in blocks]
    drop: set[int] = set()
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a, b = words[i], words[j]
            if not a or not b:
                continue
            small, large = (a, b) if len(a) <= len(b) else (b, a)
            if len(small & large) / len(small) < 0.8:
                continue
            # Same drawing: keep the captioned one, else the first.
            loser = i if (captioned[j] and not captioned[i]) else j
            drop.add(loser)
    if not drop:
        return text
    out = text
    for index in sorted(drop, reverse=True):
        m = blocks[index]
        out = out[: m.start()] + out[m.end():]
    return re.sub(r"\n{3,}", "\n\n", out).strip() + ("\n" if text.endswith("\n") else "")


def drop_redundant_kpi(text: str) -> str:
    """A ```kpi block whose every value already sits in a table of the same section says
    the numbers twice; the table stays."""
    match = re.search(r"```kpi\n(.*?)```\n?", text, flags=re.S)
    if not match:
        return text
    values = [line.split("|")[0].strip() for line in match.group(1).splitlines() if "|" in line]
    table_cells = " ".join(line for line in text.splitlines() if line.strip().startswith("|"))
    if values and all(v and v in table_cells for v in values):
        return (text[: match.start()] + text[match.end():]).strip() + "\n"
    return text


def placeholder_heavy(text: str) -> bool:
    """Three or more 「(미정)」-style holes: a frame the data never reached."""
    return len(_PLACEHOLDER.findall(text or "")) >= 3


def unwrap_json_prose(text: str) -> str:
    """A section body the model wrapped as a one-key JSON object (`{"결과의 섹션 본문":
    "..."}`) is that string; anything else comes back as it was."""
    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return text
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(data, dict):
        values = [v for v in data.values() if isinstance(v, str) and v.strip()]
        if len(values) == 1 and len(data) <= 2:
            return values[0]
        if values and all(isinstance(v, str) for v in data.values()):
            return "\n\n".join(values)
    return text


def word_count(sections: list[dict]) -> int:
    return sum(len((s.get("content") or "").split()) for s in sections)


def to_markdown(title: str, sections: list[dict]) -> str:
    parts = [f"# {title}"]
    for section in sections:
        parts.append(f"\n## {section['heading']}\n\n{section.get('content') or ''}")
    return "\n".join(parts).strip() + "\n"
