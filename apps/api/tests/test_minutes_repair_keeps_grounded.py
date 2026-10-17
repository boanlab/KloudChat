"""Minutes repair that keeps memo-grounded values and tables."""

from __future__ import annotations

from app.services import report_checks, richtext

MEMO = (
    "아래 메모로 회의록 작성해 줘. 결정 사항과 할 일(담당·기한)이 한눈에 보이게.\n\n"
    "[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의\n"
    "참석: 박정민 팀장, 이수진 차장, 김도윤 대리, 최하늘 주임 (정현우 과장 출장으로 불참)\n"
    "1. 2학기 AI 활용 교수법 워크숍 — 신청 42명(정원 40). 이수진: 대기자 2명은 온라인 동시 "
    "송출로 수용하자고 제안. 팀장 OK. 강사 섭외 완료(외부 강사 1명, 강사료 50만원). "
    "장소 미래관 201호, 10/22(목) 15:00.\n"
    "2. LMS 개편 — 업체(한결소프트) 1차 시안 10/15 도착 예정. 김도윤: 모바일 화면 검수 "
    "체크리스트 만들어서 다음 회의 때 공유하겠다. 학생 테스트 그룹 모집은 최하늘이 학생회 "
    "통해 진행, 10명 목표.\n"
    "3. 우수 강의 사례집 — 원고 12편 중 9편 접수. 미제출 3편은 교수님들께 10/16까지 "
    "재요청(최하늘). 디자인 업체 견적 2곳: A사 380만원, B사 420만원. B사가 납기 1주 빠름. "
    "결론 못 냄 → 정현우 과장 복귀 후 다음 회의에서 결정.\n"
    "4. 기타: 재무팀에서 10/30까지 11월 예산 집행계획 제출 요청 옴. 팀장이 각자 담당 사업 "
    "집행계획을 10/23까지 본인에게 달라고 함.\n"
    "다음 회의 10/15(목) 14시 같은 장소\n"
)


def _fix(content: str) -> tuple[str, list[str]]:
    fixed, codes = report_checks.minutes_grounding([{"heading": "h", "content": content}], MEMO)
    return fixed[0]["content"], codes


def test_the_next_meeting_sentence_survives_a_counted_agenda():
    text = ("회의는 총 4개의 안건으로 구성되었으며 다음 회의는 10월 15일(목) 14시 같은 장소에서 "
            "열립니다.")
    out, codes = _fix(text)
    assert out == text and codes == []


def test_a_difference_of_two_quotes_is_grounded():
    text = "B사의 견적은 A사보다 40만원 비싸지만 납기가 1주 빠릅니다."
    out, codes = _fix(text)
    assert out == text and codes == []


def test_a_bare_number_the_memo_writes_without_its_unit_is_kept_in_a_cell():
    table = ("| 항목 | 내용 | 담당 | 기한 | 비고 |\n|---|---|---|---|---|\n"
             "| 대기자 수용 방식 | 온라인 동시 송출로 2명 수용 | 이수진 | 미정 "
             "| 정원 40명, 신청 42명 |\n")
    out, codes = _fix(table)
    assert "정원 40명, 신청 42명" in out and codes == []


def test_only_the_clause_with_a_made_up_number_goes():
    text = "부스에는 냉장고 3대를 두며 다음 회의는 10월 15일(목) 14시 같은 장소에서 열립니다."
    out, codes = _fix(text)
    assert out == "다음 회의는 10월 15일(목) 14시 같은 장소에서 열립니다."
    assert codes == ["minutes_spec"]


def test_a_made_up_number_in_the_last_clause_is_blanked_not_the_sentence_dropped():
    text = "다음 회의는 10월 15일(목) 14시에 열리며 의자는 9개를 둡니다."
    out, codes = _fix(text)
    assert out.startswith("다음 회의는 10월 15일(목) 14시에 열리며")
    assert "9개" not in out and codes == ["minutes_spec"]


def test_a_table_with_an_empty_corner_cell_is_not_split():
    text = ("표: 디자인 업체 견적 비교\n\n| | A사 | B사 |\n|---|---|---|\n"
            "| 견적 금액 | 380만원 | 420만원 |")
    assert richtext.detach_tables(text) == text
    glued = "업체 견적은 아래와 같습니다. | | A사 | B사 |\n|---|---|---|"
    assert richtext.detach_tables(glued).split("\n")[2] == "| | A사 | B사 |"
