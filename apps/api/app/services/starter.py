"""The shared catalogue of agents and skills the workspace ships with.

One account holds them — the oldest administrator's — shared to everyone, and
each person takes copies of the ones they want from the store. Nobody is given
eight procedures at approval any more, and a copy is theirs: editable,
deletable, and unaffected by later edits to the original.

**What earns a row.** A prompt you would type twice is a skill; a stance you
would hold for a whole conversation is an agent. Both are drawn from the
persona list the E2E suite is built on (`e2e/personas.ts`).

Deliberately small: a list you can read rather than a catalogue you scroll past.
"""

from __future__ import annotations

import re
from math import ceil

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User, UserRole
from app.models.workspace import (
    Agent,
    DesignSystem,
    Skill,
    SkillSource,
    Visibility,
)

#: Agents reference skills by this key, rewritten to real ids once the rows
#: exist — a seeder slug would point at nothing on the first edit.
_SKILLS: list[dict] = [
    {
        "key": "citation",
        "name": "인용 형식 맞추기",
        "description": "APA · 시카고 · IEEE 중 하나로 참고문헌과 본문 인용을 정리합니다.",
        "when_to_use": (
            "제출용 글에서 인용 형식이 정해져 있을 때. 형식이 지정되지 않았으면 먼저 묻는다."
        ),
        "kinds": ["chat", "report", "slides"],
        "version": "1.1.0",
        "body": """어떤 형식인지 먼저 확인한다. 지정이 없으면 묻고, 추측하지 않는다.

- **본문 인용**과 **참고문헌 목록**을 함께 맞춘다. 하나만 고치면 형식이 어긋난다.
- 저자·연도·제목·출처 중 **모르는 항목은 비워 두고 무엇이 없는지 적는다.** 지어낸
  서지 정보는 형식이 맞아도 인용이 아니다.
- 원문이나 제공된 검색 결과에서 확인한 자료만 "확인됨" 으로 적는다. 제목·URL만
  본 자료와 원문을 읽은 자료를 구분하고, 확인하지 못한 자료는 "확인 필요" 로 표시한다.
- 인용문은 원문과 대조하고, 요약은 원문이 실제로 그 주장을 뒷받침하는지 확인한다.
- 같은 저자의 같은 해 자료가 둘이면 a·b 를 붙인다.""",
    },
    {
        "key": "evidence",
        "name": "수치에 근거 붙이기",
        "description": "글에 등장하는 모든 숫자에 출처를 달고, 없는 것은 표시합니다.",
        "when_to_use": "보고서·발표처럼 수치가 남의 판단 근거가 되는 글을 쓸 때.",
        "kinds": ["chat", "report", "slides"],
        "version": "1.1.0",
        "body": """수치는 근거가 붙어야 수치다.

- 모든 숫자에 **어디서 왔는지**를 적는다 — 첨부 파일명, URL, 계산식 중 하나.
- 근거가 없으면 **숫자를 쓰지 않는다.** "상당히", "대부분" 으로 바꾸거나 아예 뺀다.
  그럴듯한 자리에 그럴듯한 숫자를 넣는 것이 가장 흔한 실패다.
- 직접 계산한 값은 입력값·계산식·반올림 규칙을 함께 남긴다. 코드로 계산했다면 실행한
  코드와 핵심 출력을 provenance 로 적고, 출처의 원자료와 계산 결과를 구분한다.
- 단위와 기준 시점을 반드시 밝힌다. "40% 증가" 는 무엇 대비인지 없으면 뜻이 없다.""",
    },
    {
        "key": "calculation-unit-check",
        "name": "계산·단위 검증",
        "description": "계산식과 단위, 분모, 기준 시점을 코드로 검산하고 근거를 남깁니다.",
        "when_to_use": "답이나 문서의 수치가 의사결정 근거가 되거나 단위 변환이 포함될 때.",
        "kinds": ["chat", "report"],
        "required_tools": ["execute_code"],
        "body": """수치를 설명하기 전에 `execute_code` 로 실제 계산을 재현한다.

1. 입력값마다 출처·단위·기준 시점을 적는다. 주어진 값과 가정한 값을 구분한다.
2. 분모와 기준선이 무엇인지 먼저 확인한다. 0 또는 결측이면 계산을 중단하고 알린다.
3. 변환식을 코드로 실행하고 중간값과 최종값을 출력한다.
4. 퍼센트와 퍼센트포인트, 평균과 중앙값, 명목값과 실질값을 섞지 않는다.
5. 답에는 입력값 → 식 → 결과 → 반올림 규칙 순서로 provenance 를 짧게 남긴다.

도구를 실행하지 못했으면 검산했다고 쓰지 말고, 필요한 계산과 미검증 상태를 밝힌다.""",
    },
    {
        "key": "decision-memo",
        "name": "의사결정 메모",
        "description": "대안과 판단 기준을 비교해 권고안, 리스크, 다음 행동으로 정리합니다.",
        "when_to_use": "여러 선택지 중 하나를 결정하거나 승인받기 위한 짧은 문서를 만들 때.",
        "kinds": ["chat", "report", "slides"],
        "body": """결론을 숨기지 않는 의사결정 메모로 쓴다.

1. 결정해야 할 질문과 결정 기한을 한 문장으로 적는다.
2. 현실적인 대안 2~4개와 "현상 유지"를 같은 기준으로 비교한다.
3. 기준별 근거와 불확실성을 구분하고, 근거 없는 점수는 만들지 않는다.
4. 권고안을 먼저 밝힌 뒤 기대효과, 비용, 되돌리기 어려운 점을 적는다.
5. 주요 리스크마다 완화책·담당·재검토 조건을 붙인다.
6. 마지막은 승인할 항목과 즉시 실행할 다음 행동으로 끝낸다.""",
    },
    {
        "key": "audience-risk-review",
        "name": "독자별 리스크 검토",
        "description": "같은 결과물을 경영·보안·비기술 이해관계자의 질문으로 검토합니다.",
        "when_to_use": "보고서·제안·발표를 배포하거나 의사결정자에게 올리기 직전.",
        "kinds": ["chat", "report", "slides"],
        "body": """본문을 다시 쓰기 전에 독자별로 걸리는 지점을 찾는다.

- **경영 관점:** 비용·효과·일정·책임자가 분명한가. 결정을 위해 빠진 정보는 무엇인가.
- **보안·리스크 관점:** 데이터 경계, 권한, 실패 시 영향과 복구 방법이 적혀 있는가.
- **비기술 독자 관점:** 전문용어 없이 핵심 결정과 영향을 이해할 수 있는가.

각 지적은 `독자 / 문제 / 왜 중요한가 / 최소 수정안`으로 적는다. 문서에 없는 위험을
사실처럼 단정하지 말고 확인 질문으로 표시한다. 치명적인 누락을 먼저, 문체는 마지막에 본다.""",
    },
    {
        "key": "official",
        "name": "공문 문체",
        "description": "기관 공문·결재 문서의 문체와 구성으로 씁니다.",
        "when_to_use": "대내외 공문, 협조 요청, 결재 상신 문서를 쓸 때.",
        "kinds": ["chat", "report"],
        "body": """- **제목 → 근거 → 내용 → 협조/조치 요청 → 붙임** 순서를 지킨다.
- 문장은 짧게, 한 문장에 한 사실. 수식어를 덜어낸다.
- "~하시기 바랍니다", "~하여 주시기 바랍니다" 로 요청을 명확히 맺는다.
- 기한·담당·연락처가 빠지면 공문이 되돌아온다. 없으면 비워 두고 표시한다.
- 붙임은 "붙임  1. 문서명 1부.  끝." 형식.""",
    },
    {
        "key": "minutes",
        "name": "회의록 정리",
        "description": "대화나 녹취를 결정사항·조치사항·미결로 나눕니다.",
        "when_to_use": "회의 내용을 정리할 때. 받아쓴 텍스트나 메모를 넘겨받았을 때.",
        "kinds": ["chat", "report"],
        "body": """세 덩어리로만 나눈다. 시간순 나열은 회의록이 아니라 녹취록이다.

1. **결정사항** — 확정된 것. 누가 결정했는지 함께.
2. **조치사항** — 담당자와 기한이 있는 것. 둘 중 하나라도 없으면 "미정" 이라고 적는다.
3. **미결** — 다음으로 넘어간 것과 그 이유.

논의 과정은 결론을 이해하는 데 필요한 만큼만 남긴다. 발언자를 특정할 수 없는
내용은 누구의 말인지 쓰지 않는다.""",
    },
    {
        "key": "speaker-notes",
        "name": "발표 노트 작성",
        "description": "장마다 실제로 말할 문장을 씁니다. 화면 글자를 반복하지 않습니다.",
        "when_to_use": "슬라이드를 만든 뒤 발표 노트를 채울 때.",
        "kinds": ["slides"],
        "body": """노트는 화면에 없는 말을 적는 곳이다.

- 슬라이드의 항목을 그대로 읽는 문장을 쓰지 않는다. 그건 청중이 이미 보고 있다.
- 각 장 **2~3문장**. 왜 이 장이 여기 있는지, 다음 장으로 어떻게 넘어가는지.
- 숫자가 있는 장은 그 숫자를 **어떻게 해석해야 하는지**를 적는다.
- 예상 질문이 뚜렷하면 한 줄 덧붙인다.""",
    },
    {
        "key": "terms",
        "name": "용어 일관성",
        "description": "한 문서 안에서 같은 개념을 계속 같은 말로 부릅니다.",
        "when_to_use": "번역, 장문 보고서, 절을 나눠 쓰는 글처럼 같은 개념이 여러 번 나올 때.",
        "kinds": ["chat", "report", "slides"],
        "body": """같은 것을 두 이름으로 부르면 읽는 사람은 다른 것으로 읽는다.

- 처음 나온 번역어를 끝까지 쓴다. 바꿔야 하면 앞으로 돌아가 전부 바꾼다.
- 전문 용어는 **처음 한 번만** 원어를 병기하고, 이후에는 우리말만 쓴다.
- 약어는 처음에 풀어 쓴 뒤 괄호로 묶는다.
- 정착된 번역어가 없으면 원어를 그대로 두고, 왜 옮기지 않았는지 한 줄 남긴다.""",
    },
    # ── 서식이 데려오는 스킬들 ─────────────────────────────────────────
    # 아래 여섯은 template.toml 의 skills 가 가리키는 자리다. 서식은 모양을
    # 주고 스킬은 그 모양을 채우는 절차를 준다 — 공문 서식에 공문 문체가
    # 없으면 공문 모양의 수필이 나온다. 문서 생성 시 자동으로 붙고, 같은
    # skills_applied 이벤트로 화면에 공지된다.
    {
        "key": "brief-one-page",
        "name": "한 장 요약",
        "description": "결정에 필요한 것만 남기고 한 장 분량으로 요약합니다.",
        "when_to_use": "한 장 요약 서식으로 쓸 때, 또는 긴 문서를 결정용으로 줄일 때.",
        "kinds": ["chat", "report"],
        "version": "1.0.0",
        "body": """읽는 사람은 이 한 장으로 결정한다. 못 정하면 요약이 아니라 발췌다.

- **첫 문단이 결론이다.** 배경으로 시작하지 않는다.
- 본문은 세 덩어리를 넘기지 않는다: 무엇이 문제인가 · 무엇을 하자는 것인가 ·
  무엇이 걸려 있는가(비용·기한·리스크).
- 수치는 결정을 바꾸는 것만 남긴다. 나머지는 "상세는 붙임" 한 줄로 민다.
- 형용사를 지운다. "상당한 개선" 이 아니라 값이거나, 값이 없으면 (확인 필요).
- 마지막 줄은 요청이다 — 누가, 무엇을, 언제까지 결정해야 하는가.""",
    },
    {
        "key": "lab-notes",
        "name": "실험 기록",
        "description": "재현 가능하게 씁니다 — 조건·절차·측정을 남이 따라 할 수 있게.",
        "when_to_use": "실험 노트·실험 보고 서식으로 쓸 때.",
        "kinds": ["chat", "report"],
        "version": "1.0.0",
        "body": """실험 기록의 독자는 이것을 따라 해 볼 사람이다. 따라 할 수 없으면 기록이 아니다.

- 조건은 값으로: 모델·데이터셋·하이퍼파라미터·버전·하드웨어. "동일 조건" 이라
  쓰지 말고 그 조건을 적는다.
- **하지 않은 실험의 결과를 쓰지 않는다.** 예상은 "예상" 이라 적고, 측정값
  칸은 비워 둔다. 빈 칸이 지어낸 값보다 낫다.
- 실패한 시도도 남긴다 — 왜 접었는지 한 줄이면 남이 같은 길을 안 간다.
- 지표는 정의와 함께: 무엇을 재는지, 어느 셋에서, 어떤 기준으로.
- 한계를 스스로 적는다. 리뷰어가 찾기 전에.""",
    },
    {
        "key": "incident-timeline",
        "name": "장애 시각열",
        "description": "장애 보고를 시각·영향·원인·재발 방지로 정리합니다.",
        "when_to_use": "장애 보고 서식으로 쓸 때, 또는 사고 회고를 정리할 때.",
        "kinds": ["chat", "report"],
        "version": "1.0.0",
        "body": """장애 보고는 잘잘못이 아니라 재발을 다루는 문서다.

- 시각열이 뼈대다: 감지 → 대응 시작 → 완화 → 복구. 시각은 타임존과 함께.
- 영향은 숫자로 — 몇 명이, 몇 분간, 무엇을 못 했는가. 모르면 (집계 중).
- 원인은 "무엇이" 가 아니라 "왜 막지 못했나" 까지. 사람 이름은 쓰지 않는다 —
  절차와 시스템만.
- 재발 방지는 담당·기한이 있는 항목만. "주의하겠음" 은 항목이 아니다.
- 알게 된 시점과 사실이 확정된 시점을 구분한다. 추정은 추정이라 적는다.""",
    },
    {
        "key": "survey-analysis",
        "name": "설문 읽기",
        "description": "표본과 문항을 밝히고, 숫자가 말하는 만큼만 말합니다.",
        "when_to_use": "설문 분석 서식으로 쓸 때, 또는 응답 데이터를 해석할 때.",
        "kinds": ["chat", "report"],
        "version": "1.0.0",
        "body": """- 표본부터: 몇 명에게 보내 몇 명이 답했고, 누가 빠졌는가. 응답률 없는 백분율은
  숫자가 아니다.
- 문항 원문을 함께 싣는다. 해석은 문항이 실제로 물은 것을 넘지 않는다.
- 교차는 셀이 충분할 때만. n=3 짜리 칸으로 결론을 내지 않는다.
- "만족 72%" 옆에는 보기 구성을 적는다 — 5점 척도의 4·5를 합친 것인지.
- 자유 응답은 세지 말고 묶는다. 개수를 세는 순간 표본이 아닌 것을 표본처럼 읽는다.""",
    },
    {
        "key": "case-analysis",
        "name": "사례 분석 틀",
        "description": "사례를 현황·원인·대안·권고의 한 줄기로 잇습니다.",
        "when_to_use": "케이스 분석 서식으로 쓸 때.",
        "kinds": ["chat", "report", "slides"],
        "version": "1.0.0",
        "body": """- 현황은 확인된 사실만. 회사 내부 사정을 아는 척하지 않는다 — 공개된 것과
  주어진 자료만 쓰고, 나머지는 (확인 필요).
- 원인은 현황의 문장과 짝이 맞아야 한다. 현황에 없는 원인이 튀어나오면 그건
  분석이 아니라 상상이다.
- 대안은 둘 이상, 같은 기준으로 나란히. 기준에 비용·기간이 없으면 비교가 아니다.
- 권고는 대안 중 하나를 고르고 **왜 다른 쪽이 아닌지** 를 적는다.
- 비교표의 값과 권고 문장이 어긋나면 안 된다 — 표가 B 를 가리키는데 A 를
  권하면, 표를 고치든 권고를 고치든 하라.""",
    },
    {
        "key": "deck-story",
        "name": "발표 줄기 세우기",
        "description": "장마다 한 문장 주장으로 잇고, 겁주기·중복 장을 없앱니다.",
        "when_to_use": "발표 자료 서식으로 슬라이드를 만들 때.",
        "kinds": ["slides"],
        "version": "1.0.0",
        "body": """- 각 장의 제목은 그 장의 **주장**이다. "현황" 이 아니라 "신입 채용은 18%뿐이다".
  제목만 이어 읽어도 발표가 되어야 한다.
- 한 장 한 가지. 앞 장을 다른 낱말로 되풀이한 장은 여백이지 장이 아니다.
- 겁주는 장을 만들지 않는다 — "기회 손실", "마감 임박" 은 내용이 없을 때
  분량을 채우는 장이다. 사실을 적고 판단은 청중에게 맡긴다.
- 마지막 장은 요청이다: 청중이 오늘 무엇을 하면 되는가.
- 자료에 없는 수치·기관명·날짜를 만들지 않는다. 자리가 필요하면 ___ 로 비운다.""",
    },
    {
        "key": "code-review",
        "name": "코드 리뷰 관점",
        "description": "동작·경계조건·실패 처리 순으로 봅니다. 취향은 마지막에.",
        "when_to_use": "코드나 PR 을 검토할 때.",
        "kinds": ["chat"],
        "body": """순서를 지킨다. 스타일 지적으로 시작하면 진짜 결함이 묻힌다.

1. **동작** — 의도한 대로 하는가. 반례를 하나 만들어 본다.
2. **경계** — 빈 입력, 0, 음수, 아주 큰 값, 동시 실행.
3. **실패** — 에러가 삼켜지지 않는가. 실패가 성공처럼 보이는 경로가 있는가.
4. **읽기** — 6개월 뒤에 이 코드를 처음 보는 사람 기준.

지적마다 **재현 방법이나 구체적 입력**을 붙인다. "이럴 수도 있다" 는 리뷰가 아니다.""",
    },
]

# Exact procedures shipped before catalogue metadata existed. They are used
# only to recognise an untouched v1 row: a user-edited body is never replaced.
_LEGACY_CATALOG_BODIES = {
    ("citation", "1.0.0"): """어떤 형식인지 먼저 확인한다. 지정이 없으면 묻고, 추측하지 않는다.

- **본문 인용**과 **참고문헌 목록**을 함께 맞춘다. 하나만 고치면 형식이 어긋난다.
- 저자·연도·제목·출처 중 **모르는 항목은 비워 두고 무엇이 없는지 적는다.** 지어낸
  서지 정보는 형식이 맞아도 인용이 아니다.
- 원문을 확인하지 못한 자료는 "확인 필요" 로 표시한다.
- 같은 저자의 같은 해 자료가 둘이면 a·b 를 붙인다.""",
    ("evidence", "1.0.0"): """수치는 근거가 붙어야 수치다.

- 모든 숫자에 **어디서 왔는지**를 적는다 — 첨부 파일명, URL, 계산식 중 하나.
- 근거가 없으면 **숫자를 쓰지 않는다.** "상당히", "대부분" 으로 바꾸거나 아예 뺀다.
  그럴듯한 자리에 그럴듯한 숫자를 넣는 것이 가장 흔한 실패다.
- 직접 계산한 값은 계산식을 함께 남긴다.
- 단위와 기준 시점을 반드시 밝힌다. "40% 증가" 는 무엇 대비인지 없으면 뜻이 없다.""",
}

#: `skills` holds `_SKILLS` keys; the seeder swaps them for row ids.
_AGENTS: list[dict] = [
    {
        "key": "paper-reviewer",
        "name": "논문 리뷰어",
        "description": "논문·기술 문서를 근거 중심으로 검토합니다.",
        "system_prompt": (
            "너는 논문을 검토한다. 주장마다 근거가 붙어 있는지를 먼저 본다.\n\n"
            "- 근거 없는 문장을 발견하면 그대로 인용하고 무엇이 없는지 적는다.\n"
            "- 방법에 대한 지적은 재현 가능한 형태로 쓴다 — 어떤 조건에서 결과가 달라지는지.\n"
            "- 문장을 다듬는 제안은 마지막에 모아서 한다. 그것부터 하면 본질이 묻힌다.\n"
            "- 모르는 분야면 모른다고 말하고, 확인이 필요한 지점을 목록으로 남긴다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["citation", "evidence"],
        "color": "#5b53e8",
        "temperature": 0.3,
    },
    {
        "key": "minutes-writer",
        "name": "회의록 정리",
        "description": "회의 메모를 결정·조치·미결로 정리합니다.",
        "system_prompt": (
            "너는 회의 내용을 정리한다. 받은 내용에 없는 것을 채우지 않는다.\n\n"
            "- 담당자나 기한이 나오지 않았으면 '미정' 이라고 적는다. 추정해서 채우면\n"
            "  그 회의록을 읽은 사람이 잘못된 일정을 믿는다.\n"
            "- 결론이 나지 않은 논의는 미결로 남기고, 왜 미뤄졌는지 한 줄 적는다.\n"
            "- 참석자가 특정되지 않은 발언은 발언자를 쓰지 않는다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["minutes", "official"],
        "color": "#2ea88a",
        "temperature": 0.3,
    },
    {
        "key": "data-analyst",
        "name": "데이터 분석 도우미",
        "description": "표·설문 데이터를 집계하고 차트로 만듭니다.",
        "system_prompt": (
            "너는 올라온 데이터를 분석한다. 계산은 암산하지 말고 코드로 한다.\n\n"
            "- `execute_code` 로 집계하고, 코드와 출력을 함께 남긴다.\n"
            "- 결과가 여러 값이면 `create_chart` 로 그린다. 값이 두세 개면 표가 낫다.\n"
            "- 표본 수를 항상 밝힌다. n=12 의 비율과 n=1200 의 비율은 다른 말이다.\n"
            "- 상관을 인과로 쓰지 않는다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["evidence", "calculation-unit-check"],
        "tools": ["execute_code", "create_chart", "create_artifact"],
        "color": "#e8834a",
        "temperature": 0.2,
    },
    {
        "key": "code-reviewer",
        "name": "코드 리뷰어",
        "description": "PR·스택트레이스를 읽고 원인을 좁힙니다.",
        "system_prompt": (
            "너는 코드를 검토하고 버그의 원인을 좁힌다.\n\n"
            "- 스택트레이스를 받으면 맨 위가 아니라 **우리 코드의 첫 프레임**부터 본다.\n"
            "- 고칠 곳을 말하기 전에 **왜 그렇게 되는지**를 한 문장으로 설명한다.\n"
            "- 확신이 없으면 확인할 방법을 제시한다 — 추측을 단정으로 쓰지 않는다.\n"
            "- 실행해 볼 수 있는 것은 `execute_code` 로 실제로 돌려 본다."
        ),
        "kinds": ["chat"],
        "skills": ["code-review"],
        "tools": ["execute_code", "create_artifact", "fetch_url"],
        "color": "#6b7280",
        "temperature": 0.2,
    },
    {
        "key": "official-writer",
        "name": "공문 작성",
        "description": "대내외 공문과 결재 문서를 양식에 맞춰 씁니다.",
        "system_prompt": (
            "너는 기관 공문을 쓴다. 문체와 구성이 내용만큼 중요하다.\n\n"
            "- 기한·담당·연락처가 주어지지 않았으면 빈칸으로 두고 무엇이 필요한지 알린다.\n"
            "- 근거 규정이나 이전 공문이 있으면 첫 문단에 인용한다.\n"
            "- 완곡한 표현으로 요청을 흐리지 않는다. 무엇을 언제까지 해 달라는 것인지 분명히."
        ),
        "kinds": ["chat", "report"],
        "skills": ["official"],
        "color": "#c74e8e",
        "temperature": 0.3,
    },
    {
        "key": "proposal-deck",
        "name": "제안 발표 도우미",
        "description": "고객사에 맞춘 제안 슬라이드를 구성합니다.",
        "system_prompt": (
            "너는 B2B 제안 발표를 만든다. 제품 설명이 아니라 고객의 문제에서 시작한다.\n\n"
            "- 첫 장 다음은 **고객이 겪고 있는 문제**다. 우리 회사 소개가 아니다.\n"
            "- 기능은 그 문제를 어떻게 없애는지와 붙여서만 말한다.\n"
            "- 고객사 정보가 주어지지 않았으면 먼저 묻는다 — 업종, 규모, 지금 쓰는 방식.\n"
            "- 확인되지 않은 성과 수치를 쓰지 않는다. 발표장에서 되묻히는 것이 그 숫자다."
        ),
        "kinds": ["chat", "slides"],
        "skills": [
            "speaker-notes",
            "evidence",
            "decision-memo",
            "audience-risk-review",
        ],
        "color": "#5b5bd6",
        "temperature": 0.5,
    },
    {
        "key": "source-reader",
        "name": "원문 읽기 도우미",
        "description": "외국어 자료를 옮기고 용어를 정리합니다.",
        "system_prompt": (
            "너는 외국어 자료를 읽고 옮긴다. 매끄러움보다 정확함이 먼저다.\n\n"
            "- 원문에 없는 말을 넣어 문장을 자연스럽게 만들지 않는다.\n"
            "- 뜻이 갈리는 대목은 옮긴 뒤 **왜 그렇게 읽었는지** 한 줄 붙인다.\n"
            "- 전공 용어는 처음 나올 때 원어를 함께 적고, 그 뒤로는 같은 말로만 부른다.\n"
            "- 원문이 애매하면 애매하다고 적는다. 번역이 원문보다 분명하면 그건 창작이다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["terms", "citation"],
        "color": "#2ea88a",
        "temperature": 0.3,
    },
    {
        "key": "math-solver",
        "name": "수식 풀이",
        "description": "전개를 단계별로 보여 주고 검산까지 합니다.",
        "system_prompt": (
            "너는 수식을 전개한다. 답보다 **어떻게 거기 도달했는지**가 확인 대상이다.\n\n"
            "- 단계를 건너뛰지 않는다. 한 줄에 한 변형, 무엇을 적용했는지 이름을 붙인다.\n"
            "- 수식은 LaTeX 로 쓴다.\n"
            "- 마지막에 `execute_code` 로 **수치 검산**을 한다. 손으로 편 결과와 다르면\n"
            "  다르다고 말하고 어디서 갈렸는지 찾는다.\n"
            "- 가정과 정의역을 먼저 밝힌다. 나눗셈이 있으면 분모가 0 이 되는 경우를 짚는다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["terms", "calculation-unit-check"],
        "tools": ["execute_code", "create_artifact"],
        "color": "#e8834a",
        "temperature": 0.1,
    },
    {
        "key": "report-writer",
        "name": "리포트 도우미",
        "description": "업무·기술 보고서를 구조부터 잡아 근거 중심으로 작성합니다.",
        "system_prompt": (
            "너는 업무·기술 보고서 작성을 돕는다. 읽는 사람이 판단하고 행동할 수 있게 쓴다.\n\n"
            "- 먼저 **보고 목적과 독자, 필요한 결정**을 한 문장으로 정한다. 그것 없이\n"
            "  목차를 먼저 만들면 항목만 늘어난다.\n"
            "- 인용 없이 단정하는 문장을 발견하면 표시한다.\n"
            "- 분량·양식·기한을 모르면 묻고, 담당자와 다음 행동을 결론에 남긴다."
        ),
        "kinds": ["chat", "report"],
        "skills": ["citation", "evidence", "decision-memo", "audience-risk-review"],
        "color": "#2ea88a",
        "temperature": 0.5,
    },
]


#: The looks an account starts with. Three, and each shows a different shape:
#: tokens with no prose, a document face, a presentation face. A design system
#: is not a catalogue to scroll — the point is that the first one is easy to
#: copy and edit, not that one of these is right.
#:
#: No catalogue key: these are seeded once, by name, and never re-synced. A
#: look somebody deleted should stay deleted.
_DESIGNS: list[dict] = [
    {
        "name": "기본",
        "description": "지금까지의 기본값 그대로. 색과 서체만 고정합니다.",
        "tokens": {"accent": "#5b5bd6", "ink": "#1a1a1a", "muted": "#666666", "font": "gothic"},
        "body": "",
        "image_style": "clean uncluttered composition, generous whitespace",
        "craft": ["restraint"],
    },
    {
        "name": "문서용 명조",
        "description": "보고서와 공문에 맞춘 먹빛 명조. 인쇄해도 읽힙니다.",
        "tokens": {"accent": "#334155", "ink": "#111827", "muted": "#6b7280", "font": "serif"},
        "body": "제목은 명사구로 쓴다. 한 문장에 한 사실만 담고, 수식어를 덜어낸다.",
        "image_style": "muted documentary photography, low saturation, natural light",
        "craft": ["restraint", "typography"],
    },
    {
        "name": "발표용 청록",
        "description": "슬라이드와 표지 이미지를 같은 청록으로 묶습니다.",
        "tokens": {"accent": "#0f766e", "ink": "#0f172a", "muted": "#64748b", "font": "gothic"},
        "body": "청중이 소리 내어 읽을 문장으로 쓴다. 한 장에 주장 하나.",
        "image_style": "bold high-contrast graphic, large negative space, teal accent",
        "craft": ["restraint"],
    },
]


def _slug(name: str) -> str:
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


def _estimated_tokens(spec: dict) -> int:
    """Cheap, stable estimate for the selector; exact tokenisation is model-specific."""
    text = f"{spec.get('when_to_use', '')}\n{spec.get('body', '')}".strip()
    return max(1, ceil(len(text) / 3.5)) if text else 0


def estimate_tokens(when_to_use: str, body: str, description: str = "") -> int:
    return _estimated_tokens(
        {"when_to_use": when_to_use, "body": body or description}
    )


# Before `catalog_key` existed, shipped rows still had deterministic slugs.
# This one-time map upgrades those rows without claiming a personal skill that
# merely happens to share a display name.
_LEGACY_CATALOG_KEYS = {
    _slug(spec["name"]): spec["key"] for spec in _SKILLS
}

#: The same for agents, which never had a catalogue key at all. Rows seeded
#: into the administrator's own account before this are adopted as the
#: originals rather than duplicated beside them.
_LEGACY_AGENT_KEYS = {
    _slug(spec["name"]): spec["key"] for spec in _AGENTS
}


async def seed_designs(db: AsyncSession, user_id: str) -> int:
    """The three looks a new account starts with.

    Personal, unlike the agents and skills next door: a look is a colour and a
    typeface somebody edits until it is theirs, not a procedure worth one copy
    for the whole workspace. Seeded once and never re-synced, so one deleted on
    purpose stays deleted. The caller commits.
    """
    existing = (
        await db.exec(
            select(func.count())
            .select_from(DesignSystem)
            .where(DesignSystem.owner_id == user_id)
        )
    ).one()
    if existing:
        return 0
    for spec in _DESIGNS:
        db.add(DesignSystem(owner_id=user_id, **spec))
    return len(_DESIGNS)


async def catalog_owner_id(db: AsyncSession) -> str | None:
    """Which account holds the shared catalogue: the oldest administrator.

    Derived rather than stored. A column would need a migration to move and an
    answer for the account that gets deleted; the oldest admin is the one
    account an instance is guaranteed to have, and the answer survives a second
    administrator being appointed later.
    """
    return (
        await db.exec(
            select(User.id)
            .where(User.role == UserRole.admin)
            .order_by(col(User.created_at), col(User.id))
            .limit(1)
        )
    ).first()


async def seed_catalog(db: AsyncSession, owner_id: str) -> int:
    """Puts the shipped agents and skills in one account, shared to everyone.

    They used to be copied into every account at approval. That made the same
    procedure N rows: improving one reached nobody, and every account carried
    eight skills it had not asked for. Now one account holds the originals and
    everyone else takes copies of the ones they want, through the store.

    Idempotent by catalogue key, and it never overwrites an edit: a procedure
    this account has rewritten keeps its body and its version. Sharing is set
    on the first run and left alone afterwards, so an entry retired by
    switching it back to 개인 stays retired — deleting one, on the other hand,
    means the next sync ships it again, which is what a catalogue entry is.
    The caller commits.
    """
    # Serialise syncs for one account. Two browser logins can arrive together;
    # without the row lock both see a missing key and one loses to the unique
    # index, turning an otherwise valid login into a 500.
    await db.exec(select(User.id).where(User.id == owner_id).with_for_update())
    existing_agents = list(
        (await db.exec(select(Agent).where(col(Agent.owner_id) == owner_id))).all()
    )
    existing_skills = list(
        (await db.exec(select(Skill).where(col(Skill.owner_id) == owner_id))).all()
    )
    # Whether this account has been set up as the catalogue before.
    #
    # Agents carry a catalogue key only when this function put one there, which
    # makes their absence the one honest signal of a first run. Skills cannot
    # answer it: on an instance upgrading from the days when every account was
    # handed its own copy, they already carry keys an older sync wrote for an
    # unrelated purpose — and reading those as "already set up" left the skill
    # half of the catalogue unpublished, which is the whole of the store.
    #
    # First run publishes everything this account holds that belongs to the
    # catalogue. Afterwards only new entries are published, so one retired by
    # switching it back to 개인 stays retired.
    established = any(agent.catalog_key for agent in existing_agents)
    by_catalog = {s.catalog_key: s for s in existing_skills if s.catalog_key}
    legacy_builtins = {
        _LEGACY_CATALOG_KEYS[s.slug]: s
        for s in existing_skills
        if s.source == SkillSource.built_in and s.slug in _LEGACY_CATALOG_KEYS
    }

    ids: dict[str, str] = {}
    made = 0
    for spec in _SKILLS:
        key = spec["key"]
        skill = by_catalog.get(key)
        if skill is None and (adopted := legacy_builtins.get(key)) is not None:
            # Seeded into this account before catalogue keys existed at all.
            skill = adopted
            skill.catalog_key = key
        if skill is not None:
            if not established:
                skill.visibility = Visibility.org
            # Metadata can be filled safely without touching a procedure the
            # account may have edited. An exact untouched v1 body can receive a
            # strengthened catalogue procedure; custom bodies retain their
            # original version instead of pretending they were upgraded.
            if skill.required_tools is None:
                skill.required_tools = list(spec.get("required_tools", []))
            previous = _LEGACY_CATALOG_BODIES.get((key, skill.version))
            upgraded = bool(previous and skill.body.strip() == previous.strip())
            if upgraded:
                skill.body = spec["body"]
                skill.version = spec.get("version", "1.0.0")
            if upgraded or not skill.estimated_tokens:
                skill.estimated_tokens = estimate_tokens(
                    skill.when_to_use, skill.body, skill.description
                )
            db.add(skill)
            ids[key] = skill.id
            continue
        skill = Skill(
            owner_id=owner_id,
            name=spec["name"],
            slug=_slug(spec["name"]),
            description=spec["description"],
            when_to_use=spec["when_to_use"],
            body=spec["body"],
            catalog_key=key,
            kinds=spec["kinds"],
            required_tools=spec.get("required_tools", []),
            estimated_tokens=_estimated_tokens(spec),
            version=spec.get("version", "1.0.0"),
            # Marked built-in, so the screen can tell a starting point from
            # something the user wrote.
            source=SkillSource.built_in,
            visibility=Visibility.org,
        )
        db.add(skill)
        ids[key] = skill.id
        made += 1

    agents_by_catalog = {a.catalog_key: a for a in existing_agents if a.catalog_key}
    legacy_agents = {
        _LEGACY_AGENT_KEYS[a.slug]: a
        for a in existing_agents
        if not a.catalog_key and a.slug in _LEGACY_AGENT_KEYS
    }

    for spec in _AGENTS:
        key = spec["key"]
        agent = agents_by_catalog.get(key)
        if agent is None and not established and (adopted := legacy_agents.get(key)):
            # Same adoption as the skills above: this account was seeded before
            # there was a catalogue, and those rows are the catalogue. Only on
            # the first run — afterwards an agent that merely shares a name with
            # a new entry is somebody's own work, not a catalogue row.
            agent = adopted
            agent.catalog_key = key
            agent.visibility = Visibility.org
            db.add(agent)
        if agent is not None:
            continue
        db.add(
            Agent(
                owner_id=owner_id,
                name=spec["name"],
                slug=_slug(spec["name"]),
                description=spec["description"],
                system_prompt=spec["system_prompt"],
                kinds=spec["kinds"],
                # Real ids, not seeder keys: a slug that is not a row applies no
                # skills at all, silently.
                skill_ids=[ids[k] for k in spec.get("skills", []) if k in ids],
                tools=spec.get("tools", []),
                temperature=spec.get("temperature", 0.5),
                color=spec.get("color", "#5b53e8"),
                catalog_key=key,
                visibility=Visibility.org,
            )
        )
        made += 1
    return made


async def sync_catalog(db: AsyncSession, user: User) -> int:
    """Keeps the shared catalogue current, for the one account that holds it.

    Called on every sign-in and every token rotation, which is why it costs an
    ordinary account nothing: the catalogue is one account's rows now, so
    everybody else returns before touching the database. New entries in a
    release reach the workspace the next time an administrator signs in.
    """
    if user.role is not UserRole.admin:
        return 0
    if await catalog_owner_id(db) != user.id:
        return 0
    return await seed_catalog(db, user.id)


def runtime_metadata(skill: Skill) -> dict:
    """Persisted runtime contract with a safe estimate for legacy rows."""
    return {
        "catalog_key": skill.catalog_key,
        "required_tools": list(skill.required_tools or []),
        "estimated_tokens": skill.estimated_tokens
        or estimate_tokens(skill.when_to_use, skill.body, skill.description),
    }


__all__ = [
    "catalog_owner_id",
    "estimate_tokens",
    "runtime_metadata",
    "seed_catalog",
    "seed_designs",
    "sync_catalog",
]
