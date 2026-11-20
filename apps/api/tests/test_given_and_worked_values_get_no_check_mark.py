"""A value the person gave, or one worked from what they gave, gets no 「(확인 필요)」.

Shapes from the 11-25 benchmark: a fact restated from the prompt (「11월 17~20일」 written as
「11월 17일부터 20일까지 나흘」), a day the memo writes alone (「복귀가 24일이니까」), sums of
an attached CSV, a percentage worked from a pipe table without outer bars (「1,987」 is a
count, not a year), a formula's result used again, a value in another unit, and a slide title
(「마케팅 예산 15억 배분」). A value nobody gave or worked out is still marked."""

from __future__ import annotations

import datetime as dt

import pytest

from app.pipeline import check_stages, report_stages
from app.services import arithmetic, check_skills, value_guard

TODAY = dt.date(2026, 11, 20)

DELAY = ("거래처에 보낼 납품 지연 사과 메일을 써 줘.\n"
         "- 주문: PO-2026-1187, 브래킷 부품 BRK-220 12,000개, 원래 납기 11월 28일\n"
         "- 원인: 열처리 설비 고장으로 11월 17~20일 생산 중단\n"
         "- 새 일정: 11월 28일에 7,000개 먼저 납품, 나머지 5,000개는 12월 5일")

MEMO = ("[녹취 정리] 2026-11-19(목) 10:00~11:05\n"
        "오세훈: 보도자료는 홍지수 씨. 복귀가 24일이니까 제가 24일에 전달할게요.\n"
        "장우진: 계약서 초안을 다음 주 수요일, 25일까지 법무팀에 넘기겠습니다.")

ORDERS = ("첨부한 주문 데이터(CSV)를 분석해 줘. 순매출 = 수량 × 단가 × (1 − 할인율)로 계산해.\n"
          "주문번호,주문일,지역,제품,수량,단가,할인율(%)\n"
          "O-1,2026-07-01,서울,키보드,15,89000,10\n"
          "O-2,2026-07-08,대구,노트북,5,1290000,10\n"
          "O-3,2026-08-15,대구,노트북,4,1290000,0\n"
          "O-4,2026-08-22,서울,마우스,7,45000,0\n"
          "O-5,2026-10-01,부산,모니터,8,349000,5\n"
          "O-6,2026-10-15,대구,마우스,9,45000,10\n")

VOC = ("[2026년 3분기 고객 문의(VOC) 집계]\n"
       "유형 | 7월 | 8월 | 9월 | 3분기 합계\n"
       "배송 지연 | 412 | 538 | 701 | 1,651\n"
       "상품 불량 | 205 | 198 | 214 | 617\n"
       "환불·교환 절차 | 330 | 352 | 389 | 1,071\n"
       "앱 결제 오류 | 96 | 88 | 412 | 596\n"
       "기타 문의 | 260 | 249 | 271 | 780\n"
       "합계 | 1,303 | 1,425 | 1,987 | 4,715\n"
       "- 상담 인력: 22명(7~9월 변동 없음)")

BRIEF = ("- 제품: 무선 청소기 '클린윙 S2'\n"
         "- 개선점: 무게 1.9kg→1.6kg, 사용 시간 40분→60분\n"
         "- 마케팅 예산: 15억 원(디지털 9억, 오프라인 체험존 4억, 기타 2억)")


#: the model groups' trace settings: a self-hosted or small external writer, a strong one
GROUPS = pytest.mark.parametrize("group", [{}, {"strong": True}], ids=["self-hosted", "strong"])


def _chat(answer: str, material: str, group: dict | None = None) -> str:
    options = {**check_skills.options_for(TODAY, None, surface="chat"), "person": material,
               "requirements": True, **(group or {})}
    return check_skills.ground(("grounding-check",), answer, material=material, computed=[],
                               options=options, evidence=[material]).text


@GROUPS
def test_a_period_the_person_gave_is_no_unknown_day_when_restated(group):
    answer = ("열처리 설비가 고장 나 11월 17일부터 20일까지 나흘 동안 생산이 멈췄습니다. "
              "11월 17일부터 20일까지 4일간 생산이 중단되었습니다.")
    assert _chat(answer, DELAY, group) == answer
    # a day outside what the person gave still is
    assert "11월 15일(확인 필요)" in _chat("설비가 11월 15일부터 멈췄습니다.", DELAY, group)


def test_a_day_the_memo_writes_alone_is_that_day_of_the_memos_month():
    answer = ("| 담당 | 할 일 | 기한 |\n|---|---|---|\n"
              "| 오세훈 | 홍지수에게 보도자료 전달 | 11월 24일 |\n"
              "| 장우진 | 계약서 초안 법무팀 전달 | 11월 25일(수) |")
    assert value_guard.MARK not in _chat(answer, MEMO)
    assert value_guard.MARK in _chat("시안 리뷰는 11월 27일입니다.", MEMO)


@GROUPS
def test_sums_of_an_attached_table_stand_and_a_wrong_one_is_marked(group):
    # 대구: 5×1,290,000×0.9 + 4×1,290,000 + 9×45,000×0.9 = 11,329,500
    right = ("| 지역 | 순매출 |\n|---|---:|\n| 대구 | 11,329,500 |\n"
             "| 서울 | 1,516,500 |\n\n전체 순매출은 15,498,400원이고, 할인으로 깎인 총액은 "
             "958,600원입니다. 7월 순매출은 7,006,500원입니다.")
    assert _chat(right, ORDERS, group) == right
    wrong = "| 지역 | 순매출 |\n|---|---:|\n| 대구 | 11,933,500 |"
    assert "11,933,500(확인 필요)" in _chat(wrong, ORDERS, group)


def test_a_percentage_worked_from_a_table_without_outer_bars_stands():
    text = ("9월 문의는 1,987건으로 8월(1,425건) 대비 약 39.4% 증가했습니다. 배송 지연은 3분기 "
            "합계 1,651건으로 전체 문의의 35.0%를 차지합니다. 9월에는 전체 1,987건의 20.7%가 "
            "앱 결제 오류였습니다.")
    marked = report_stages._unbacked_marked(text, [VOC], computed="", grounded=True)
    assert marked == text
    made_up = report_stages._unbacked_marked("앱 결제 오류는 전체 문의의 47.3%입니다.", [VOC],
                                             computed="", grounded=True)
    assert "47.3%(확인 필요)" in made_up


def test_a_formula_on_given_figures_backs_its_result_in_later_sections():
    earlier = "9월 문의는 8월보다 1,987건 - 1,425건 = 562건 많습니다."
    text = ("증가분 562건 가운데 앱 결제 오류 증가분 324건이 324건 / 562건 = 57.65%를 "
            "차지합니다.\n\n두 유형을 합친 증가분 487건은 전체 증가분 562건의 86.65%입니다. "
            "324건 + 163건 = 487건입니다.")
    assert report_stages._unbacked_marked(text, [VOC], computed="", grounded=True,
                                          worked=earlier) == text
    wrong = "앱 결제 오류 증가분이 324건 / 562건 = 67.65%를 차지합니다."
    assert "67.65%(확인 필요)" in report_stages._unbacked_marked(
        wrong, [VOC], computed="", grounded=True, worked=earlier)


def test_a_rate_per_person_listed_after_its_first_item_is_no_fractional_count():
    text = ("상담 인력 22명 기준으로 7월은 1인당 59.2건, 8월은 64.8건, 9월은 90.3건입니다. "
            "9월 문의량 대비 인력 규모는 1,987 ÷ 22 = 90.32건/명입니다.")
    assert report_stages._unbacked_marked(text, [VOC], computed="", grounded=True) == text


def test_the_arithmetic_check_reads_a_whole_formula_and_a_share_in_percent():
    assert arithmetic.findings("(1,987 − 1,425) ÷ 1,425 × 100 = 39.4%") == []
    assert arithmetic.findings("324건 / 562건 = 57.65%") == []
    assert arithmetic.findings("324건 / 562건 = 67.65%")


def test_a_value_in_another_unit_is_the_same_value():
    evidence = value_guard.Evidence.of(["회의실 사용 합계: 120분", "예산 1,500,000,000원"])
    assert evidence.supports_number(2, "시간", "2")
    assert evidence.supports_number(15e8, "", "15억")
    assert not evidence.supports_number(3, "시간", "3")


def test_a_slide_title_is_never_split_by_a_mark():
    slide = {"id": "s", "layout": "chart-pie", "title": "마케팅 예산 15억 배분",
             "notes": "디지털 9억, 오프라인 체험존 4억, 기타 2억을 배분했습니다."}
    options = {**check_skills.options_for(TODAY, surface="deck"), "person": BRIEF,
               "requirements": True}
    out, _ = check_stages.ground_slides([slide], material=BRIEF, computed=[""], options=options)
    assert out[0]["title"] == "마케팅 예산 15억 배분"
    # a title value nobody gave keeps its words; the notes name it
    marked = [{**slide, "title": "마케팅 예산 18억(확인 필요) 배분"}]
    fixed = check_stages.titles_unmarked(marked)[0]
    assert fixed["title"] == "마케팅 예산 18억 배분"
    assert "「18억」" in fixed["notes"] and fixed["notes"].startswith("디지털 9억")


def test_a_heading_in_a_chat_answer_keeps_its_words():
    answer = "## 2027년 예산 18억 원 계획\n\n예산은 15억 원입니다."
    out = _chat(answer, BRIEF)
    assert out.split("\n")[0] == "## 2027년 예산 18억 원 계획"


def test_a_conditioned_example_is_no_claim():
    material = "오늘 09:00~18:00을 30분 단위 칸으로 보여 줘. 회의실별 예약 시간 합계를 보여 줘."
    answer = "합계는 예약 칸 수 × 0.5시간으로 계산합니다. 4칸이면 2시간입니다."
    assert _chat(answer, material) == answer


SPECS = ("- 제품: 무선 청소기 '클린윙 S2' (전작 S1, 누적 판매 8만 2천 대)\n"
         "- 개선점: 사용 시간 40분→60분\n"
         "- 경쟁: A사 '에어킥 7'(54만 원, 1.8kg, 50분), B사 '스톰라이트'(39만 원, 2.1kg, 45분)")


def test_another_named_items_figure_on_a_slide_is_still_marked():
    slides = [{"id": "a", "layout": "bullets", "title": "S2 개선점",
               "bullets": ["S2 사용 시간 50.0분으로 확대", "S1 누적 판매 8만 2천 대",
                           "에어킥 7은 50분, 스톰라이트는 45분"]}]
    out, count = check_stages.mark_misbound(slides, SPECS)
    assert count == 1
    assert out[0]["bullets"] == ["S2 사용 시간 50.0분(확인 필요)으로 확대",
                                 "S1 누적 판매 8만 2천 대", "에어킥 7은 50분, 스톰라이트는 45분"]
