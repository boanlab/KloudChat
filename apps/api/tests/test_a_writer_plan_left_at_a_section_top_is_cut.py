"""A section the writer opened with its own plan — 「Structured plan / Purpose: … / Key facts
to use (source → fact): …」, or items telling what each part will hold (「본문: …로 도입」,
「표 후 … 1줄」) — keeps only the document's prose; labelled content of the document stays."""

from app.services import internal_words

PROSE = ("11월 둘째 주 영업팀은 4분기 매출 전망 보고서 초안 작성과 거래처 계약 갱신 협의를 "
         "중심으로 업무를 진행했습니다. 10월 매출은 목표를 5% 웃돌았습니다.")

PLAN = f"""Structured plan
Purpose: 주간 업무 현황을 팀장에게 보고
Structure (이 섹션 내부):
• 이번 주 주요 업무 요약
• 담당자별 진행 상황
Key facts to use (source → fact):
• [1] 회의: 임하준 11/17 초안
• [2] 실적표: 10월 매출 4.2억
Draft approach:
• 산문만, 표 없음

{PROSE}"""

PARTS = f"""• 본문: 회의 개요(일시·장소·참석)로 도입
• 본문: 안건별 핵심 발언 요약 후 결정 사항 정리
• 표 후 확인 필요 사항 1줄

{PROSE}"""


def test_an_english_plan_block_goes():
    out = internal_words.strip_work_notes(PLAN, document=True)
    assert out.strip() == PROSE
    assert internal_words.strip_plan_blocks("### 개요\n\n" + PLAN).startswith("### 개요")


def test_part_instructions_go():
    assert internal_words.strip_plan_blocks(PARTS).strip() == PROSE


def test_a_document_s_own_labels_stay():
    content = ("추진 방향: 고객 유지율 개선\n- 대상: 기존 거래처 40곳\n"
               "- 기간: 11월~12월\n\n" + PROSE)
    assert internal_words.strip_plan_blocks(content) == content
    # one label alone is no plan
    single = "작성 방향: 숫자로 정리\n\n" + PROSE
    assert internal_words.strip_plan_blocks(single) == single
    # an English document's own 「Purpose:」 line
    english = ("Purpose: This memo summarises the week.\nScope: sales team only.\n\n"
               "The team closed two renewals this week and drafted the Q4 forecast in full.")
    assert internal_words.strip_plan_blocks(english) == english
    # nothing but the plan: never cut down to nothing
    assert internal_words.strip_plan_blocks("Purpose: x\nStructure:\n• a") == \
        "Purpose: x\nStructure:\n• a"
