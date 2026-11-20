"""A report says of each option only what the material says of it, and names who owns a task.

The 11-25 benchmark's 사내 메신저 report gave option C (하이웍스) 「국내 데이터센터」 in its
comparison table: the material says that of A (없음) and B, never of C. Code sees it — the
cell's words are words the material says only of other items — and the verify stage sends
the row back; a cell still standing says 「자료 없음」. The same reports left 「[담당자]」 in
their owner cells; that blank now goes back to be named by department."""

from __future__ import annotations

import pytest

from app.services import checks, item_claims
from app.services.verify import Doc

MATERIAL = (
    "- 후보 3개(연간 비용은 260명 기준 견적):\n"
    "  A. 글로벌 협업툴 '팀링크': 1인 월 8,000원, 연 2,496만 원. 기능 풍부, 한국어 지원 보통, "
    "국내 데이터센터 없음.\n"
    "  B. 국산 메신저 '온톡 비즈': 1인 월 5,500원, 연 1,716만 원. 국내 데이터센터, 관리자 "
    "기능(퇴사자 일괄 차단, 파일 외부전송 차단) 강함, 연동 앱 적음.\n"
    "  C. 그룹웨어 내장 메신저(현재 쓰는 그룹웨어 '하이웍스' 추가 모듈): 1인 월 3,000원, 연 "
    "936만 원. 모바일 앱 평가 낮음(앱스토어 2.9점), 파일 용량 제한 100MB.\n"
    "- 현장 요구: 물류센터 팀장 인터뷰 – 사진으로 파손 보고를 빨리 올릴 수 있어야")

ROW = "| 데이터 저장 위치 | 국내 데이터센터 없음 | 국내 데이터센터 | 국내 데이터센터 |"
TABLE = ("| 기준 | A. 팀링크 | B. 온톡 비즈 | C. 하이웍스 추가 모듈 |\n|---|---|---|---|\n"
         "| 연간 비용 | 2,496만 원 | 1,716만 원 | 936만 원 |\n" + ROW + "\n"
         "| 모바일 | 기능 풍부 | 연동 앱 적음 | 모바일 앱 평가 낮음 |\n")


def test_the_material_lists_its_items_with_what_it_says_of_each() -> None:
    items = item_claims.items_of(MATERIAL)
    assert [sorted(i.names) for i in items] == [["팀링크"], ["온톡 비즈"], ["하이웍스"]]
    assert "국내 데이터센터 없음" in items[0].body


def test_a_cell_borrowing_another_items_attribute_is_found() -> None:
    found = item_claims.misbound([{"heading": "후보 비교", "content": TABLE}], MATERIAL)
    assert [(f.item, f.phrase, f.column) for f in found] == [("하이웍스", "국내 데이터센터", 3)]
    assert "팀링크" in found[0].owners and "온톡 비즈" in found[0].owners


def test_a_sentence_giving_one_item_anothers_attribute_is_found() -> None:
    text = "현장 사용성에서 온톡 비즈는 한국어 지원이 원활하고 모바일 환경에 적합합니다."
    found = item_claims.misbound([{"heading": "비교", "content": text}], MATERIAL)
    assert [(f.item, f.phrase) for f in found] == [("온톡 비즈", "한국어 지원")]


def test_what_the_material_says_of_the_item_itself_stands() -> None:
    own = ("팀링크는 국내 데이터센터가 없습니다. 하이웍스 모듈은 파일 용량 제한이 100MB입니다. "
           "온톡 비즈는 국내 데이터센터를 씁니다.")
    assert item_claims.misbound([{"heading": "비교", "content": own}], MATERIAL) == []
    said = "하이웍스 모듈의 데이터센터 위치는 자료에 없어 확인이 필요합니다."
    assert item_claims.misbound([{"heading": "비교", "content": said}], MATERIAL) == []


def test_a_material_with_no_item_lines_finds_nothing() -> None:
    assert item_claims.items_of("임직원 260명, 예산 2,000만 원") == []


def test_a_cell_standing_after_the_repair_says_the_material_lacks_it() -> None:
    sections, count = item_claims.settled([{"heading": "후보 비교", "content": TABLE}], MATERIAL)
    assert count == 1
    assert "| 국내 데이터센터 없음 | 국내 데이터센터 | 자료 없음 |" in sections[0]["content"]


@pytest.mark.asyncio
async def test_the_verify_stage_sends_the_row_back_once() -> None:
    doc = Doc([{"heading": "후보 비교", "content": TABLE}], MATERIAL, "", given=MATERIAL)
    found, _ = await checks.item_attributes(doc)
    assert [f.sentence for f in found] == [ROW]
    assert found[0].code == "item_attribute" and "자료 없음" in found[0].why
    fixed = "| 데이터 저장 위치 | 국내 데이터센터 없음 | 국내 데이터센터 | 자료 없음 |"
    assert found[0].still(ROW) and not found[0].still(fixed)


@pytest.mark.asyncio
async def test_an_owner_left_as_a_blank_goes_back_to_be_named() -> None:
    plan = ("| 위험 | 대응 | 담당 |\n|---|---|---|\n"
            "| 현장 저항 | 시범 운영 피드백 반영 | [담당자] |\n"
            "결제 모듈 개선은 [담당 부서]가 맡습니다. 서명은 [작성자]가 합니다.")
    found, _ = await checks.owner_cells(Doc([{"heading": "위험", "content": plan}], ""))
    assert [f.sentence for f in found] == [
        "| 현장 저항 | 시범 운영 피드백 반영 | [담당자] |",
        "결제 모듈 개선은 [담당 부서]가 맡습니다."]
    assert "부서" in found[0].why and found[0].still("| a | b | [담당자] |")
    assert not found[0].still("| 현장 저항 | 시범 운영 피드백 반영 | 물류센터 운영팀 |")


@pytest.mark.asyncio
async def test_a_deadline_left_as_a_blank_goes_back_to_the_materials_schedule() -> None:
    plan = ("| 단계 | 담당 | 기한 |\n|---|---|---|\n| 시범 운영 | IT 담당 부서 | [기한] |\n"
            "| 전사 적용 | [담당자] | [기한] |")
    found, _ = await checks.owner_cells(Doc([{"heading": "일정", "content": plan}], ""))
    assert [f.code for f in found] == ["owner_blank", "due_blank"]
    both = next(f for f in found if f.code == "owner_blank")
    assert "부서" in both.why and "일정" in both.why
    assert both.still("| 전사 적용 | IT 담당 부서 | [기한] |")
    assert not both.still("| 전사 적용 | IT 담당 부서 | 4월 전사 적용 전 |")


def test_a_row_about_one_items_attribute_is_not_answered_for_the_others() -> None:
    table = ("| 기준 | A. 팀링크 | B. 온톡 비즈 | C. 하이웍스 모듈 |\n|---|---|---|---|\n"
             "| 한국어 지원 | 보통 | 국산으로 자연스러움 | 자료 없음 |\n"
             "| 데이터 저장 위치 | 국내 데이터센터 없음 | 국내 데이터센터 "
             "| 국내(그룹웨어 인프라) |\n"
             "| 기능·연동 | 기능 풍부 | 연동 앱 적음 | 파일 용량 제한 100MB |\n")
    found = item_claims.misbound([{"heading": "후보 비교", "content": table}], MATERIAL)
    assert sorted((f.item, f.phrase) for f in found) == [("온톡 비즈", "한국어"),
                                                         ("하이웍스", "데이터")]
