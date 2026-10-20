"""A limit the memo only asks about stays an ask whatever else the pipeline put next to the
memo; an item the memo gives no day is not put on one."""

from __future__ import annotations

import json

import pytest

from app.pipeline import revise_stages
from app.pipeline.bundle import Bundle, Material, Part, Request
from app.pipeline.report_stages import _doc, mark_policy
from app.services import checks, stated_days, stated_policy, verify

MEMO = (
    "아래 이번 주 메모로 팀장님께 올릴 주간 업무 보고서를 만들어줘.\n"
    "- 월: 신규 거래처 '대성유통' 1차 미팅. 월 발주 예상 3,200만원. 단가 협상 잔여(5% 인하 요구).\n"
    "- 화: 기존 거래처 '한솔마트' 재고 클레임 2건 처리 완료(반품 420만원).\n"
    "- 금: 영업 CRM 데이터 정리 중 중복 거래처 37건 발견, 병합 작업 60% 진행.\n"
    "- 이슈: 영남 출고 지연 재발 우려(물류팀 확인 필요), CRM 병합 완료는 다음주 수요일 예정.\n"
    "- 다음주 계획: 대성유통 단가 최종 협상(목요일 2차 미팅), 한솔마트 재고 점검 방문, CRM 병합 "
    "마무리, 4분기 목표 초안 작성(금요일까지).\n"
    "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), 물류팀 출고 SLA 협의 자리 마련."
)

#: the settled-figures block the pipeline puts beside the memo, under a model-made name
SETTLED = "# 확정 수치\n- 단가 인하 허용 상한: 4%\n- 월 발주 예상: 3,200만원"

#: the T1 report as written: a limit the memo asks about, and a request put on Friday
WEEKLY = [
    {"heading": "주간 결론", "content": (
        "```callout\n대성유통 단가 협상의 전제\n신규 거래처 대성유통의 월 발주 예상액은 "
        "3,200만원이며, 5% 인하를 요구하고 있습니다. 내부 허용 범위는 최대 4%까지입니다.\n```\n\n"
        "| 안 | 금액(월) | 조건 |\n|---|---|---|\n"
        "| A: 5% 인하 수용 | 3,040만원 (3,200 × 0.95) | 고객 요구 충족, 내부 허용 초과 |\n"
        "| B: 4% 인하 수용 | 3,072만원 (3,200 × 0.96) | 내부 허용 상한선, 고객 재요구 가능성 |")},
    {"heading": "다음 주 계획", "content": (
        "| 일정(요일) | 내용 |\n|---|---|\n"
        "| 화요일 | 한솔마트 재고 점검 방문 |\n"
        "| 수요일 | CRM 병합 마무리(37건 중 잔여 40% 처리) |\n"
        "| 목요일 | 대성유통 단가 최종 협상(2차 미팅) |\n"
        "| 금요일 | 4분기 목표 초안 작성, 물류팀 출고 SLA 협의(예정) |\n\n"
        "목요일 대성유통 2차 미팅 전까지 단가 인하 폭 결재를 요청합니다. CRM 병합은 수요일까지 "
        "마무리되며, 이는 금요일 4분기 목표 초안 작성의 데이터 기반으로 활용됩니다. 물류팀과의 SLA "
        "협의는 금요일 오후에 진행합니다. 10월 23일 금요일에는 주간 회의가 있습니다.")},
]


def _copy(sections: list[dict]) -> list[dict]:
    return json.loads(json.dumps(sections))


def test_an_item_the_memo_gives_no_day_is_found_on_the_day_it_was_put():
    found = stated_days.findings(WEEKLY, MEMO)
    row, sentence = "| 금요일 | 4분기 목표 초안 작성, 물류팀 출고 SLA 협의(예정) |", (
        "물류팀과의 SLA 협의는 금요일 오후에 진행합니다")
    assert [(piece, items) for _, piece, items in found] == [
        (row, ["물류팀 출고 SLA 협의(예정)"]), (f"{sentence}.", [sentence])]
    # a day the memo gives, a cited sentence and a weekday beside a date are left alone
    assert not stated_days.untraced("| 목 | 대성유통 2차 미팅 |", MEMO)
    assert not stated_days.untraced("금요일에는 SLA를 정한다 [2].", MEMO)
    assert not stated_days.untraced("주간 회의는 10/23(금)에 연다.", MEMO)
    assert stated_days.untraced("| 금 | 협의 |", "자료 없음 금요일") == []


@pytest.mark.asyncio
async def test_a_limit_the_memo_only_asks_about_is_found_beside_a_settled_block_and_web_pages():
    doc = verify.Doc(_copy(WEEKLY), f"{MEMO}\n{SETTLED}", MEMO,
                     [{"ordinal": 1, "quote": "할인 허용 범위와 상한은 회사마다 다르다"}],
                     given=MEMO)
    pieces = {f.sentence for f in (await checks.stated_rules(doc))[0]}
    assert "내부 허용 범위는 최대 4%까지입니다." in pieces
    assert any("내부 허용 초과" in p for p in pieces)
    assert any("내부 허용 상한선" in p for p in pieces)
    # a sentence citing the page may say what the page says
    assert not stated_policy.untraced("할인 허용 범위는 회사마다 다르다 [1].", MEMO,
                                      "할인 허용 범위와 상한은 회사마다 다르다")


def test_what_the_person_gave_leaves_out_the_pipeline_s_own_blocks():
    bundle = Bundle(request=Request(text=MEMO, model="local/m", api_key="k"),
                    material=Material(given=MEMO, parts=[
                        Part(SETTLED, "settled"), Part("## memo.txt\n결재 규정 없음", "attachment"),
                        Part("# 작성 메모(내부)\n허용 범위 규정", "note")]))
    given = _doc(bundle).given
    assert "memo.txt" in given and "확정 수치" not in given and "작성 메모" not in given


def test_what_stands_after_the_repair_is_marked_whatever_check_found_it_first():
    doc = verify.Doc(_copy(WEEKLY), f"{MEMO}\n{SETTLED}", MEMO, given=MEMO)
    # the arithmetic check found the rows first: their findings carry its code
    still = [verify.Finding(0, "| A: 5% 인하 수용 | 3,040만원 (3,200 × 0.95) | 고객 요구 충족, "
                               "내부 허용 초과 |", "arithmetic", "계산")]
    marked = mark_policy(doc.sections, still, doc)
    first, plan = marked[0]["content"], marked[1]["content"]
    assert "내부 허용 범위는 최대 4%까지입니다 (확인 필요)." in first
    assert "| 고객 요구 충족, 내부 허용 초과 (확인 필요) |" in first
    assert "| 내부 허용 상한선, 고객 재요구 가능성 (확인 필요) |" in first
    assert "| 4분기 목표 초안 작성, 물류팀 출고 SLA 협의(예정) (확인 필요) |" in plan
    assert "| 화요일 | 한솔마트 재고 점검 방문 |" in plan
    assert "단가 인하 폭 결재를 요청합니다." in plan


@pytest.mark.asyncio
async def test_the_day_goes_back_to_the_writer_and_a_rewrite_without_it_is_kept():
    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        items = json.loads(messages[0]["content"].rsplit("Items:\n", 1)[1])
        return json.dumps([
            "| 금요일 | 4분기 목표 초안 작성 |" if item["sentence"].startswith("| 금요일")
            else "물류팀과의 SLA 협의 일정은 아직 정해지지 않았습니다."
            for item in items], ensure_ascii=False), {}

    doc = verify.Doc(_copy(WEEKLY[1:]), MEMO, MEMO, given=MEMO)
    sections, still, _ = await verify.verify_and_repair(
        doc, [checks.stated_dates], complete=complete, model="m", api_key="k")
    assert not still
    assert "| 금요일 | 4분기 목표 초안 작성 |" in sections[0]["content"]


def test_an_edit_that_puts_an_item_on_a_day_is_marked():
    body = "| 요일 | 내용 |\n|---|---|\n| 금 | 물류팀 출고 SLA 협의 |"
    marked = revise_stages._policy_marked(body, MEMO, [], {})
    assert "| 물류팀 출고 SLA 협의 (확인 필요) |" in marked
