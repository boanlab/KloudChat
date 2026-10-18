"""Checks editing only what code decides; model judgments marked or handed back."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.pipeline import chat_stages, report_stages
from app.pipeline.bundle import Bundle, Request
from app.pipeline.check_stages import AnswerGrounding
from app.pipeline.stage import Context
from app.routers import sessions
from app.services import (
    copy_check,
    internal_words,
    model_tier,
    post_checks,
    report_checks,
    skill_scripts,
    superlatives,
    units,
    value_guard,
    verify,
)

SMALL, STRONG = "local/qwen3.8-27b", "deepseek/deepseek-v4-pro-0813"
TIERS = pytest.mark.parametrize("writer", [SMALL, STRONG])


def test_the_tier_table_is_the_shared_one():
    assert model_tier.strong(STRONG) and model_tier.strong("qwen/qwen3.8-max-0902")
    assert model_tier.strong("z-ai/glm-5.3")
    assert not model_tier.strong(SMALL) and not model_tier.strong("google/gemini-3.1-flash")
    assert not model_tier.strong("strict-local/anthropic/x")
    assert Request(text="", model=STRONG, api_key="").strong
    # the model that actually answered decides: a privacy route's local twin is small
    assert not Request(text="", model=STRONG, api_key="", actual_model=SMALL).strong
    assert Request(text="", model=STRONG, api_key="", outline_model="o").reviser == STRONG
    assert Request(text="", model=SMALL, api_key="", outline_model="o").reviser == "o"


# ── a pasted client profile, an exercise plan ──────────────────────────

TRAINER_REQUEST = (
    "퍼스널 트레이너가 되어 줘. 더 건강하고 튼튼해지고 싶은 사람의 정보를 줄 테니, "
    "현재 체력·목표·생활 습관에 맞는 최선의 계획을 짜 줘. 운동과학, 영양 지식 등 관련 "
    "지식을 써서.\n\n"
    "고객 정보:\n- 나이: 34세 / 성별: 여성\n- 직업: UX 디자이너(재택근무)\n"
    "- 키 165cm, 몸무게 72kg, 혈액형 A형\n- 목표: 체지방 감량\n"
    "- 운동 제약: 아파트라 점프 동작 어려움, 집에 덤벨 한 쌍(5kg)만 있음\n"
    "- 특별한 고민: 거북목, 오래 앉아 있으면 허리 아픔\n- 선호: 하루 30분 이내 홈트레이닝\n"
    "- 보충제: 먹어 볼 의향 있음\n\n다음을 포함한 종합 계획을 짜 줘.\n"
    "1. 주 4일 운동 계획 — 구체적 운동, 세트, 횟수, 휴식 시간\n"
    "2. 목표를 뒷받침하고 고객의 혈액형을 고려한, 지속 가능한 영양 계획\n"
    "3. 알맞은 보충제 추천\n4. 거북목과 허리 통증을 다루는 기법과 운동\n"
    "5. 재택근무자가 오래 앉아 있는 걸 상쇄할 일상 움직임·가동성 전략\n"
    "6. 진행 상황을 볼 간단한 측정 지표\n\n"
    "재택근무자 일상에 맞는 실천 방법을 주고, 지속 가능성·바른 자세·부상 예방을 강조해 "
    "줘.")
#: the plan's Monday and Friday as written before the check (the final answer kept only
#: 「허리를 바닥에 붙인 채 …」 and 「 덤벨을 골반 위에 올려도 됩니다.」 of these)
TRAINER_PLAN = (
    "**월요일 — 하체 + 코어 (약 28분)**\n"
    "고블릿 스쿼트 3세트 × 12회, 휴식 60초. 덤벨 한 개를 가슴 앞에 세워 들고 앉습니다. "
    "불가리안 스플릿 스쿼트 3세트 × 각 다리 10회, 휴식 60초. 뒤쪽 발을 소파나 의자에 "
    "올립니다. 데드버그 3세트 × 각 10회, 휴식 45초. 허리를 바닥에 붙인 채 반대쪽 팔·다리를 "
    "뻗습니다.\n\n"
    "**금요일 — 하체 + 등 + 코어 (약 28분)**\n"
    "글루트 브릿지 3세트 × 15회, 휴식 45초. 덤벨을 골반 위에 올려도 됩니다. "
    "스모 스쿼트 3세트 × 12회, 휴식 60초. 발을 어깨 너비보다 넓게 벌립니다. "
    "슈퍼맨 3세트 × 12회, 휴식 45초. 엎드려 팔·다리를 동시에 듭니다.\n\n"
    "머리가 2.5cm 앞으로 나갈 때마다 목에 걸리는 무게가 약 4.5kg 늘어납니다.")


def test_a_pasted_client_profile_is_not_material_to_trace():
    assert not sessions._memo_pasted(TRAINER_REQUEST)
    assert not sessions._pasted_in([{"role": "user", "content": TRAINER_REQUEST}])


def _ground(answer: str, request: str, *, writer: str, traced: bool, advice: str = "") -> str:
    bundle = chat_stages.bundle_for(
        answer=answer, request=request, model={"id": writer}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=request,
        messages=[{"role": "user", "content": request}], material=request, check_keys=(),
        check_material="", tool_outputs=[], judge="", kept_steps=[], usage={}, traced=traced,
        advice=advice)

    async def go():
        return [e async for e in AnswerGrounding().run(bundle, Context())]

    asyncio.run(go())
    return bundle.document.answer


@TIERS
@pytest.mark.parametrize("advice", ["medical", ""])
def test_the_prescriptions_stay_whole(writer, advice):
    out = _ground(TRAINER_PLAN, TRAINER_REQUEST, writer=writer, traced=True, advice=advice)
    for sentence in ("데드버그 3세트 × 각 10회, 휴식 45초.",
                     "글루트 브릿지 3세트 × 15회, 휴식 45초.", "슈퍼맨 3세트 × 12회, 휴식 45초."):
        assert sentence in out
    # a mark never splits a value from its unit
    assert "2.5(확인 필요)cm" not in out


# ── the judge's notes on a landing page's feature blocks ───────────────

LANDING_PAGE_REQUEST = (
    "「한빛워크」 랜딩 페이지 문구 써 줘. 히어로(헤드라인·서브), 기능 3개 블록, 고객 사례 "
    "한 줄, CTA까지.\n"
    "제품 정보(한빛소프트, 가상 — 아래 자료가 전부임):\n"
    "- 공공기관·중견기업용 협업툴: 메신저, 화상회의(최대 50명), 문서 공동편집, 결재 알림 연동\n"
    "- 설치형·클라우드형 둘 다 제공, 설치형은 망분리 환경 지원")
LANDING_PAGE_ANSWER = (
    "**메신저와 화상회의**\n화상회의에는 최대 50명이 한자리에 모일 수 있어, 부서 회의나 전 직원 "
    "회의를 별도 도구 없이 진행할 수 있습니다.\n\n"
    "**문서 공동편집**\n여러 사람이 같은 문서를 동시에 고치므로, 파일을 주고받으며 버전을 맞출 "
    "필요가 없습니다.\n\n"
    "**쓸 때 주의**\n게시 전에 그 수치의 산정 기준(집계 기간·대상)을 확인하세요.")
LANDING_PAGE_JUDGED = [
    {"quote": "부서 회의나 전 직원 회의를 별도 도구 없이 진행할 수 있습니다",
     "why": "자료에 언급 없음"},
    {"quote": "여러 사람이 같은 문서를 동시에 고치므로, 파일을 주고받으며 버전을 맞출 필요가 "
              "없습니다", "why": "근거가 자료에 없음"},
    {"quote": "게시 전에 그 수치의 산정 기준(집계 기간·대상)을 확인하세요", "why": "언급되지 않음"},
]


class _Writer:
    """Judge, confirmer and writer by prompt; the writer hands back `reply`."""

    def __init__(self, judged: list[dict], reply: str) -> None:
        self.judged, self.reply, self.prompts = judged, reply, []

    async def __call__(self, model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        self.prompts.append((model, prompt))
        if prompt.startswith("You check an answer"):
            return json.dumps({"unsupported": self.judged}, ensure_ascii=False), {}
        if prompt.startswith("Below are the material an answer was written from and statements"):
            return json.dumps({"verdicts": []}), {}
        if prompt.startswith("You check marketing copy"):
            return json.dumps({"unsupported": self.judged, "items": []}, ensure_ascii=False), {}
        return self.reply, {}


def _post(writer: str, model: _Writer) -> post_checks.Outcome:
    return asyncio.run(post_checks.run(
        LANDING_PAGE_ANSWER, keys=["generic-language-killer"], request="랜딩 페이지 문구 써 줘",
        material=LANDING_PAGE_REQUEST, attached="", computed=[], today="2026-10-13", complete=model,
        model=writer, judge="flash", api_key="k", strong=model_tier.strong(writer)))


@TIERS
def test_the_answer_s_own_suggestion_is_never_a_finding(writer):
    model = _Writer(LANDING_PAGE_JUDGED, LANDING_PAGE_ANSWER)
    out = _post(writer, model)
    sent = [row["text"] for row in out.sent]
    assert not any("산정 기준" in quote for quote in sent)
    assert "게시 전에 그 수치의 산정 기준(집계 기간·대상)을 확인하세요." in out.text


def test_a_strong_writer_weighs_the_notes_itself_and_keeps_what_it_kept():
    model = _Writer(LANDING_PAGE_JUDGED, LANDING_PAGE_ANSWER)  # the writer keeps its sentences
    out = _post(STRONG, model)
    # no weaker model reads or rewrites it
    assert {m for m, _ in model.prompts} == {STRONG}
    revise = model.prompts[-1][1]
    assert "it may be wrong" in revise and "a listed feature's plain use" in revise
    # what it kept stays, shown in the step only
    assert out.text == LANDING_PAGE_ANSWER and not out.dropped
    assert any("작성 모델이 근거를 보고 남김" in row["why"] for row in out.flags)


def test_a_small_writer_s_judged_statement_is_marked_unless_code_confirms_it():
    # a judge's 「자료에 없음」 is no reason to cut; code finds no figure or name to look
    # up in 「부서 회의나 전 직원 회의를 …」, so it is marked where it stands
    model = _Writer(LANDING_PAGE_JUDGED, LANDING_PAGE_ANSWER)
    out = _post(SMALL, model)
    assert [m for m, _ in model.prompts][:2] == ["flash", "flash"]
    assert "별도 도구 없이 진행할 수 있습니다(확인 필요)." in out.text and not out.dropped
    # a statement with a figure the material lacks still goes
    judged = [{"quote": "화상회의에는 최대 300명이 한자리에 모일 수 있습니다",
               "why": "자료는 50명"}]
    answer = LANDING_PAGE_ANSWER.replace(
        "최대 50명이 한자리에 모일 수 있어, 부서 회의나 전 직원 회의를 "
        "별도 도구 없이 진행할 수 있습니다.",
        "최대 300명이 한자리에 모일 수 있습니다.")
    out = asyncio.run(post_checks.run(
        answer, keys=["generic-language-killer"], request="랜딩 페이지 문구 써 줘",
        material=LANDING_PAGE_REQUEST, attached="", computed=[], today="2026-10-13",
        complete=_Writer(judged, answer), model=SMALL, judge="flash", api_key="k"))
    assert "300명" not in out.text and out.dropped


def test_what_a_judge_took_out_is_not_brought_back_by_the_guard():
    # the narration the judge took out came back marked
    before = ("검색 결과는 실제 2027년 개편안을 확인해 주지 못합니다.\n\n## 요약\n- 개편안 발표")
    after = "## 요약\n- 개편안 발표"
    ev = value_guard.Evidence.of(["개편안 발표"])
    kept = value_guard.settle(before, after, ev)
    left = value_guard.settle(before, after, ev, keep_out=[
        "검색 결과는 실제 2027년 개편안을 확인해 주지 못합니다"])
    assert "확인해 주지 못합니다" in kept.text and "확인해 주지 못합니다" not in left.text


def test_a_strong_writer_s_revision_stands_as_written():
    before = "첫 문단입니다.\n\n둘째 문단은 지웠습니다."
    out = value_guard.settle(before, "첫 문단입니다.", value_guard.Evidence.of([]), writer=True)
    assert out.text == "첫 문단입니다."


def test_a_cell_that_said_check_needed_is_marked_once():
    # 「확인 필요 (확인 필요)」
    before = "| 항목 | 가격 |\n|---|---|\n| 기본 | 확인 필요 |"
    after = "| 항목 | 가격 |\n|---|---|\n| 기본 | 확인 필요 |".replace("| 확인 필요 |", "|  |")
    out = value_guard.settle(before, after, value_guard.Evidence.of([]))
    assert "확인 필요 (확인 필요)" not in out.text and "확인 필요" in out.text


@TIERS
def test_copy_notes_go_to_the_writer_of_a_strong_answer_only(writer):
    answer = "결재 진행 상황을 따로 물어보지 않아도 됩니다. 같은 문서를 여러 명이 함께 고칩니다."
    judged = [{"quote": "결재 진행 상황을 따로 물어보지 않아도 됩니다", "why": "자료에 없음"}]
    model = _Writer(judged, answer)
    out = asyncio.run(copy_check.run(
        answer, request="랜딩 페이지 문구", material=LANDING_PAGE_REQUEST, complete=model,
        model=writer,
        judge="flash", api_key="k", strong=model_tier.strong(writer)))
    readers = [m for m, p in model.prompts if p.startswith("You check marketing copy")]
    if model_tier.strong(writer):
        assert set(readers) == {STRONG}
        assert "a claim note may be wrong" in model.prompts[1][1]
        # kept by the writer: marked where it stands, never cut
        assert "결재 진행 상황을 따로 물어보지 않아도 됩니다 (확인 필요)" in out.text
    else:
        assert set(readers) == {"flash"}


# ── the comparison pre-pass offers data ───────────────────────────────

COLLAB = ("우리 팀(교직원 40명) 업무 협업 도구 두 가지 안을 비교·검토하는 보고서 써 줘.\n\n"
          "A안: 사내 메신저 유지 + 화상회의 라이선스 추가\n"
          "- 화상회의 라이선스 1인당 월 8,000원 × 40명\n\n"
          "B안: 통합 협업툴 전환\n- 1인당 월 14,000원 × 40명, 연 단위 계약 시 15% 할인\n"
          "- 초기 전환 컨설팅·데이터 이전 1회 300만원\n\n판단 기준: 연간 비용(첫해·이듬해)")
#: One reading: the monthly items with the comparison's 12 months
COLLAB_DSP = {"period": "1년", "years": 1, "options": [
    {"name": "A안", "items": [
        {"name": "화상회의 라이선스", "unit_price": 8000, "count": 40, "per": "month",
         "months": 12}]},
    {"name": "B안", "items": [
        {"name": "구독료", "unit_price": 14000, "count": 40, "per": "month", "months": 12},
        {"name": "연 단위 계약 할인", "unit_price": 14000, "count": 40, "per": "month",
         "months": 12, "rate": 0.15, "sign": -1, "when": "연 단위 계약 시"},
        {"name": "전환 컨설팅", "unit_price": 3000000, "count": 1, "per": "once"}]}]}


def _collab():
    numbers = report_checks._material_numbers(COLLAB) + report_checks._calendar(COLLAB)
    return report_checks.option_costs(COLLAB_DSP, numbers)


def test_next_year_s_monthly_fees_are_not_zero():
    costs = _collab()
    assert (costs["options"]["A안"]["first_year"], costs["options"]["A안"]["next_year"]) == (
        3_840_000, 3_840_000)
    assert costs["options"]["B안"]["next_year"] == 6_720_000
    annual = next(sc for sc in costs["scenarios"] if sc["name"] == "연 단위 계약 시")["options"]
    assert annual["B안"]["next_year"] == 5_712_000


def test_a_strong_writer_gets_the_totals_as_data_not_a_verdict():
    costs = _collab()
    settled = report_checks._cost_block(costs)
    data = report_checks._cost_block(costs, data=True)
    assert "가장 적은 대안" in settled and "2년 연속" in settled
    assert "가장 적은 대안" not in data and "settled" not in data and "2년 연속" not in data
    assert "is your judgment" in data
    # both the base and the conditional totals, side by side
    for figure in ("9,720,000원", "8,712,000원", "5,712,000원", "3,840,000원"):
        assert figure in data


LAPTOP_QUOTES = (
    "[견적 2] 다솜시스템\n- 단가 1,390,000원(부가세 포함) × 30대\n"
    "- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)\n"
    "- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)\n"
    "[견적 1] 가온정보통신\n- 단가 1,320,000원(부가세 별도) × 30대\n"
    "조건: 예산 4,800만원(부가세 포함), 보증 3년 필수")


def test_a_warranty_extension_is_added_never_swapped_for_the_laptops():
    reading = {"period": "구매", "years": 1, "options": [
        {"name": "가온정보통신", "items": [
            {"name": "노트북", "unit_price": 1320000, "count": 30, "per": "once", "vat": "excl"}]},
        {"name": "다솜시스템", "items": [
            {"name": "노트북", "unit_price": 1390000, "count": 30, "per": "once", "vat": "incl"},
            {"name": "보증 3년 연장", "unit_price": 45000, "count": 30, "per": "once",
             "vat": "incl", "when": "3년 연장 시"},
            {"name": "기존 기기 수거", "unit_price": 10000, "count": 30, "per": "once",
             "vat": "incl"}]}]}
    costs = report_checks.option_costs(reading, report_checks._material_numbers(LAPTOP_QUOTES),
                                       material=LAPTOP_QUOTES)
    # 42,000,000원 in a block: the extension left out of the base
    assert costs["options"]["다솜시스템"]["total"] == 43_350_000


RECOMMENDATION_FINDING = verify.Finding(
    1, "따라서 가온정보통신을 권고합니다.", "recommend_mismatch", "조건 미확인", auto=True)
RECOMMENDATION_SECTIONS = [
    {"heading": "요약", "content": "결론은 가온정보통신입니다."},
    {"heading": "권고", "content": "따라서 가온정보통신을 권고합니다. 누리컴보다 약 3,003,000원 "
                                 "저렴하고 납기도 11월 2일(월)로 약 2주 빠릅니다."},
]
RECOMMENDATION_MUSTS = {"musts": ["예산"], "options": {
    "가온정보통신": {"status": "조건 미확인", "failed": [], "unknown": ["예산"]},
    "누리컴": {"status": "조건 미확인", "failed": [], "unknown": ["예산"]}}}


def test_settling_the_verdict_never_deletes_a_later_sentence():
    second = verify.Finding(1, "누리컴보다 약 3,003,000원 저렴하고 납기도 11월 2일(월)로 약 2주 "
                               "빠릅니다.", "recommend_mismatch", "x", auto=True)
    sections, still = report_checks.settle_verdict(RECOMMENDATION_SECTIONS,
                                                   [RECOMMENDATION_FINDING, second],
                                                   RECOMMENDATION_MUSTS)
    assert "누리컴보다 약 3,003,000원 저렴하고" in sections[1]["content"]
    assert second in still


@TIERS
def test_the_verdict_overrides_only_a_small_writer(writer, monkeypatch):
    async def found(doc, checks, **kwargs):
        found.model = kwargs["model"]
        return doc.sections, [RECOMMENDATION_FINDING], {}

    monkeypatch.setattr(report_stages.verify, "verify_and_repair", found)
    bundle = Bundle(request=Request(text="노트북 견적 비교 보고서", model=writer, api_key="k",
                                    outline_model="flash"))
    bundle.document.sections = [dict(s) for s in RECOMMENDATION_SECTIONS]
    bundle.computed.prepass = report_checks.Prepass(musts=RECOMMENDATION_MUSTS)
    asyncio.run(report_stages.Verify().apply(bundle, Context()))
    body = bundle.document.sections[1]["content"]
    if model_tier.strong(writer):
        # the writer repairs its own sentences; its recommendation stands, the finding shown
        assert found.model == STRONG
        assert "따라서 가온정보통신을 권고합니다." in body and bundle.findings.still
    else:
        assert found.model == "flash"
        assert "조건이 확인되면 권고합니다" in body


# ── the person asked for unconfirmed points to be marked ───────────────

def test_unsettled_web_findings_are_marked_when_the_person_asked():
    request = "사실마다 출처를 달고, 확인 못 한 건 확인 필요로 표시해 줘."
    assert report_stages._MARKS_ASKED.search(request)
    sentence = "행안부는 2026년 10월 CSP 대상 설명회를 개최했습니다 [3]."
    sections = [{"heading": "정책", "content": f"{sentence} 다음 문장입니다."}]
    web = verify.Finding(0, sentence, "web_claim", "근거는 계획 발표", auto=False)
    out = report_stages.mark_unsettled(sections, [web])
    assert "개최했습니다 [3] (확인 필요)." in out[0]["content"]
    assert report_stages.mark_unsettled(out, [web]) == out


# ── answer tidying: code fixes only what it computes ──────────────────────────

def test_a_mixed_scale_amount_is_read_whole():
    # became 「8.21만 2,080명 … = 2억 4,710만 원/년」
    text = ("410만 4천 명 × 2% = 8만 2,080명, 8만 2,080명 × 9,900원 × 12개월 = 97억 5,110만 "
            "원/년입니다.")
    assert units.fix_written_sums(units.fix_written_arithmetic(text)) == text


def test_a_result_run_on_into_a_word_is_left_alone():
    # 「= 104,000박당 104,000원」
    text = "이 경우 80,000원 + 24,000원 = 1박당 104,000원까지 인정되어"
    assert units.fix_written_sums(text) == text


def test_the_tail_of_a_longer_sum_is_not_the_sum():
    # the last terms read as the whole sum, 2,000만원 → 3,200만원
    text = ("시상금 계: 500만원×1 + 300만원×2 + 100만원×4 + 50만원×6 + 100만원×1 + 50만원×2 "
            "+ 50만원×2 + 50만원×2 = 2,200만원")
    assert units.fix_written_sums(units.fix_written_arithmetic(text)) == text
    # a wrong short sum is still fixed
    fixed = units.fix_written_sums("합계 100만원 + 200만원 = 400만원")
    assert fixed == "합계 100만원 + 200만원 = 300만원"


def test_a_true_claim_about_the_table_s_one_extreme_cell_stands():
    # 「전 단과대학·전 문항 중」 became 「전 단과대학·교과내용·시설·… 문항 중」
    text = ("| 단과대학 | 응답 | 교과내용 | 교수법 | 시설 | 온라인학습 | 전반만족 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 사회과학대학 | 24 | 4.38 | 3.92 | 3.92 | 3.83 | 4.17 |\n"
            "| 공과대학 | 32 | 3.66 | 3.56 | 3.69 | 3.41 | 3.59 |\n"
            "| 예술대학 | 18 | 3.22 | 3.61 | 2.89 | 3.06 | 3.39 |\n\n"
            "예술대학의 시설 평균이 2.89점으로 전 단과대학·전 문항 중 가장 낮습니다.")
    assert superlatives.narrow_sweeping_claims(text) == text


@pytest.mark.parametrize("before, after", [
    ("날씨 도구는 잘못 호출됐네요. 이 여행은 9월 하순 계획이라 날씨 조회가 필요 없으니, 바로 "
     "계획을 짜 드리겠습니다.\n\n먼저 가정을 정리합니다. 예산 600유로는 두 사람 합계입니다.",
     "먼저 가정을 정리합니다. 예산 600유로는 두 사람 합계입니다."),
    ("2026-11-04는 수요일로 확인됐습니다. 사용자가 준 요일(수요일)과 일치합니다.\n\n이제 두 "
     "문서를 작성합니다. 청구서 발송일 변경(1일→3일)은 12월 청구분부터 처음 적용됩니다.\n\n"
     "## 사내 공지",
     "청구서 발송일 변경(1일→3일)은 12월 청구분부터 처음 적용됩니다.\n\n## 사내 공지"),
    ("두 버전으로 작성합니다.\n\n## 고객용", "## 고객용"),
    ("이제 소재를 정리해 드리겠습니다.\n\n| 소재 | 문구 |\n|---|---|",
     "| 소재 | 문구 |\n|---|---|"),
    ("점검 결과: 콜드메일 check 0 block(제목 3안은 택1), 카피 claims 0 block, 「30일 무료」는 "
     "근거 있음으로 판정. 남은 `[전화번호]`만 채우시면 됩니다.",
     "남은 `[전화번호]`만 채우시면 됩니다."),
    ("본문입니다.\n\n근거 대조 점검을 마쳤고, 메모에 없는 수치·날짜는 없습니다. 보고자 성함만 채워 "
     "넣으시면 됩니다.", "본문입니다.\n\n보고자 성함만 채워 넣으시면 됩니다."),
    ("「베타 기간 한정, 기존 고객 무료」는 18자로 한도 안입니다. 최종 확정된 25개입니다.\n\n"
     "| 각도 | # |", "| 각도 | # |"),
])
def test_work_narration_is_taken_out(before, after):
    notes = internal_words.strip_work_notes(before, request="써 줘")
    out = internal_words.strip_tool_apologies(notes)
    assert out == after


def test_an_answer_s_own_plan_sentence_inside_its_content_stays():
    text = "## 일정\n\n이제 두 문서를 작성합니다. 이 문장은 본문 뒤라 남습니다."
    assert internal_words.strip_work_notes(text, request="써 줘") == text


# ── values the person's numbers give, assumptions the answer states ─────────────

TRACE = skill_scripts.entry("grounding-check")


def test_an_assumed_range_and_the_end_of_a_given_hour_are_not_unsourced():
    request = ("느린 기차 여행 플래너가 되어 줘. 출발: 암스테르담 / 도착: 로마, 9월 하순, 10일. "
               "오전 7시 이전 출발 금지. 정보가 모자라면 합리적으로 가정한 뒤 맨 위에 적어.")
    answer = ("9월 하순(대략 9월 22일~10월 1일) 출발로 잡았고, 하루 이동 6시간 이하를 지켰습니다.")
    assert TRACE.check(answer, request, [], {}) == []
    agenda_request = (
        "다음 주 목요일(10/15) 14시 교육혁신팀 주간회의야. 1시간이고, 다룰 것: LMS 1차 시안 "
        "검토, 워크숍(10/22) 준비 점검.")
    assert not [f for f in TRACE.check("회의는 14시부터 15시까지입니다.", agenda_request, [], {})
                if f["kind"] == "time"]
    change_line = ("- change(billing): 클라우드형 요금 청구서 메일 발송일 매월 1일 → 매월 3일 "
                   "(배포 11월)")
    assert TRACE.check("11월 1일 청구서는 이미 발송됐습니다.", change_line, [], {}) == []


def test_a_mark_after_a_time_takes_the_hour_word_with_it():
    # 「오전 8(확인 필요)시」
    line = "오전 8시 이후 출발"
    assert line[:value_guard.token_end(line, line.index("8") + 1)] == "오전 8시"


def test_a_linking_ending_before_a_title_is_no_name():
    # 「규정에 적혀 있지 [이름] 총무 담당 부서」
    found = TRACE.check("규정에 적혀 있지 않으니 총무 담당 부서에 확인해 주세요.",
                        "출장비 규정을 알려 줘", [], {})
    assert not [f for f in found if f["kind"] == "person"]


def test_a_discount_and_vat_chain_from_the_quotes_is_supported():
    # correct totals marked 「(확인 필요)」
    material = ("[견적 3] 누리컴\n- 단가 1,480,000원(부가세 별도) × 30대, 30대 이상 구매 시 "
                "총액 5% 할인\n- 데이터 삭제 확인서는 별도 대당 5,000원(부가세 별도)\n"
                "[견적 1] 가온정보통신\n- 단가 1,320,000원(부가세 별도) × 30대")
    answer = ("30대 공급가액 합계는 42,180,000원(부가세 별도), 부가세 포함 총액은 "
              "46,398,000원입니다. 확인서를 포함하면 총액은 46,563,000원입니다. "
              "가온정보통신은 43,560,000원으로 약 3,003,000원 저렴합니다.")
    blanked = answer
    for value in ("42,180,000", "46,398,000", "46,563,000", "3,003,000"):
        blanked = blanked.replace(value, "[수치]")
    out = value_guard.settle(answer, blanked, value_guard.Evidence.of([material], answer=answer))
    assert out.text == answer and not out.marked


@TIERS
def test_a_strong_writer_s_sentence_is_marked_where_a_small_one_s_is_dropped(writer):
    material = "[메모] 2026-11-02(월)부터 새 시스템."
    answer = "11월 2일부터 새 시스템을 씁니다. 등록금 납부 3월 2일~3월 6일."
    options = {"strong": True} if model_tier.strong(writer) else {}
    found = TRACE.check(answer, material, [], options)
    fixed = TRACE.fix(answer, found)
    if model_tier.strong(writer):
        assert "등록금 납부" in fixed
    else:
        assert "등록금 납부" not in fixed


# ── example clocks, dates and units ─────────────────────────────────────────────────────────

def test_an_example_s_clock_and_next_month_s_day_are_not_unsourced():
    # 「예컨대 "저녁 7시(확인 필요)에 …"」, 「실제 월(예: 「11월 15일(확인 필요)」)」
    walking_advice = ('예컨대 "저녁 7시에 집 앞 편의점까지 걷기", "아침에 이불 정리하기" 수준이면 '
                      "충분합니다.")
    assert TRACE.check(walking_advice, "우울할 때 할 수 있는 걸 알려 줘", [],
                       {"advice": True}) == []
    deadline_notice = "프로젝트 마감일이 이번달 말에서 다음달 15일로 변경되였습니다."
    found = TRACE.check("마감일은 11월 15일입니다.", deadline_notice, [], {"today": "2026-10-13"})
    assert not [f for f in found if f["kind"] == "date"]


def test_years_since_a_given_year_and_a_conditional_penalty_are_not_marked():
    # 「2018년 출시 이후 8년(확인 필요)」
    material = "- 서비스: 개인용 메모 앱 「한빛메모」(무료, 2018년 출시), 현재 월 이용자 약 3,200명"
    answer = "2018년 출시 이후 8년 동안 「한빛메모」를 사용해 주신 모든 분께 감사드립니다."
    assert TRACE.check(answer, material, [], {"today": "2026-10-13"}) == []
    # 「…사고(인사사고, 뺑소니, 12대 중과실)라면 지금 바로 변호사를 선임하세요 (확인 필요).」
    accident_advice = (
        "형사처벌 우려가 있는 사고(인사사고, 뺑소니, 12대 중과실)라면 지금 바로 변호사를 "
        "선임하세요.")
    assert TRACE.check(accident_advice, "교통사고 났어. 조언만 해 줘", [], {"advice": True}) == []


@TIERS
def test_a_question_about_blanks_that_all_went_back_is_taken_out(writer):
    # 「확인이 필요한 날짜(자료에 없어 로 두었습니다. …): - 공지일() 기준 …」
    before = "공지일은 2026. 10. 14.입니다. 종료일은 2027. 3. 31.입니다."
    after = ("공지일은 [날짜]입니다. 종료일은 2027. 3. 31.입니다.\n\n"
             "확인이 필요한 날짜(자료에 없어 [날짜]로 두었습니다. 언제로 할지 정해 주세요):\n"
             "- 공지일([날짜]) 기준 종료일까지 약 [수치]개월")
    ev = value_guard.Evidence.of(["공지일 2026. 10. 14., 종료 2027. 3. 31."], answer=before)
    out = value_guard.settle(before, after, ev, writer=model_tier.strong(writer)).text
    assert "로 두었습니다" not in out and "공지일()" not in out
    assert "공지일은 2026. 10. 14.입니다." in out


# ── marks sit after the whole value, never in a doubled bracket ─────────────────

@pytest.mark.parametrize("line, value, whole", [
    ("머리가 2.5cm 앞으로", "2.5", "2.5cm"),     # a trainer plan 「2.5(확인 필요)cm」
    ("5kg → 6~7kg 덤벨", "6~7", "6~7kg"),       # a trainer plan 「6~7(확인 필요)kg」
    ("오전 8시 이후 출발", "8", "8시"),           # a train trip 「오전 8(확인 필요)시」
    ("금요일 18시까지", "18", "18시"),            # a proofread notice 「18(확인 필요)시」
    ("머리글 아래 3행에", "3", "3행"),             # a spreadsheet formula 「3(확인 필요)행」
    ("2m 거리에서", "2", "2m"),
    ("5GB 용량", "5", "5GB"),
])
def test_a_mark_goes_after_the_unit(line, value, whole):
    start = line.index(value)
    assert line[start:value_guard.token_end(line, start + len(value))] == whole


def test_a_mark_closing_a_bracket_goes_inside_it():
    # 「(이번 주 금요일 10월 16일(확인 필요))」
    before = "회신은 (이번 주 금요일 10월 16일)까지 부탁드립니다."
    after = before.replace("10월 16일", "[일자]")
    out = value_guard.settle(before, after, value_guard.Evidence.of(["교정해 줘"])).text
    assert "(확인 필요))" not in out and "10월 16일, 확인 필요)" in out


def test_a_year_running_into_buteo_is_no_value():
    # 「2007부」 read as a count of 부: would become 「[수치]터」
    line = "SUMIFS는 엑셀 2007부터 쓸 수 있습니다."
    assert TRACE.fix(line, TRACE.check(line, "엑셀 2016에서 쓸 수식 알려 줘", [], {})) == line


def test_a_date_formula_s_rolled_over_day_is_its_own():
    # 「DATE(2026,13,1)=2027-01-01」 marked
    line = "=DATE(2026,13,1)은 2027-01-01이 되어 그 전날까지 셉니다."
    assert TRACE.check(line, "12월 합계 수식 알려 줘", [], {}) == []


def test_a_day_written_another_way_is_the_material_s():
    # the material writes 「워크숍(10/22)」, the answer 「10월 22일」
    material = "다음 주 목요일(10/15) 14시 교육혁신팀 주간회의야. 워크숍(10/22) 준비 점검."
    assert TRACE.check("워크숍은 10월 22일(목)입니다.", material, [], {}) == []
    before = "워크숍은 10월 22일입니다."
    out = value_guard.settle(before, "워크숍은 [일자]입니다.", value_guard.Evidence.of([material]))
    assert out.text == before


def test_advice_to_the_writer_on_their_own_text_is_no_demand_to_drop():
    # a proofreading suggestion went whole as 「자료에 없는 준비·제출 요구」
    material = ("교정자가 되어 줘.\n\n안녕하세요. 프로젝트 마감일이 이번달 말에서 다음달 15일로 "
                "변경되였습니다. 각 팀은 변경된 일정에 맞춰 업무 계획을 수정하시고, 금요일 까지 "
                "저에게 회신 부탁드립니다.")
    sentence = ("마지막으로 회신을 받는 경로(메일 답장인지 메신저인지)와 수정한 업무 계획서를 함께 "
                "제출하라고 밝히면 좋습니다.")
    assert TRACE.check(sentence, material, [], {"requirements": True}) == []


@pytest.mark.parametrize("quote", ["사업은 일정대로 정상 추진 중입니다", "추가 비용은 없습니다"])
def test_a_small_writer_s_invented_assertion_is_still_marked(quote):
    # 「정상 추진 중」, 「추가 비용 없음」 in a 개조식 report: marked where they stand
    # (no figure or name for code to confirm absent)
    answer = f"## 현황\n- 1차 시안을 받았습니다.\n- {quote}."
    model = _Writer([{"quote": quote, "why": "자료에 없음"}], answer)
    out = asyncio.run(post_checks.run(
        answer, keys=["internal-comms"], request="팀장 보고 써 줘", material="1차 시안 수령",
        attached="", computed=[], today="2026-10-13", complete=model, model=SMALL,
        judge="flash", api_key="k"))
    assert f"{quote}(확인 필요)." in out.text and "1차 시안을 받았습니다." in out.text


PRESS_BRIEF_REQUEST = (
    "아래 발표문을 부서장 보고용으로 정리해 줘. 핵심은 5줄 이내로 요약하고, 우리 대학(비수도권 "
    "사립대, 재학생 약 8,000명, 올해 RISE 사업 참여 중)에 미치는 영향 3가지와 우리가 지금 준비할 "
    "일을 정리해 줘.\n\n---\n[교육부 보도 참고자료(가상)] 2027년 대학 재정지원사업 개편 방향\n\n"
    "교육부는 2027년부터 대학 재정지원사업을 '성과 중심·지역 연계형'으로 개편한다고 밝혔다. "
    "재정 진단 결과 하위 대학은 지원 대상에서 제외되며, 진단 지표는 2026년 하반기에 "
    "공개된다.\n")


def test_a_pasted_press_release_makes_no_medical_question():
    # 「재정 진단」 in the pasted release forced a medical search and reading
    from app.services import exact_facts

    ask = sessions._ask_of(PRESS_BRIEF_REQUEST)
    assert exact_facts.advice_domain(ask) is None and exact_facts.search_reason(ask) is None
    # pasted results under 「봐 줘」 are still read whole
    lab = ("이 검사 결과 괜찮은 건지 봐 줘.\n\n---\n공복혈당 130 mg/dL, 당화혈색소 6.8%, "
           "총콜레스테롤 240, 중성지방 180, 혈압 145/92, 체중 82kg, 복용 중인 약 없음\n")
    assert sessions._ask_of(lab) == lab


def test_a_second_call_written_after_a_comma_is_taken_too():
    # two JSON calls in a row, the second left visible after 「, 」
    text = ('찾아보겠습니다.\n{"name": "web_search", "arguments": {"query": "한빛소프트 매출"}}, '
            '{"name": "web_search", "arguments": {"query": "한빛소프트 직원 수"}}\n\n본문입니다.')
    out, calls = internal_words.written_tool_calls(text, {"web_search"})
    assert "{" not in out and "본문입니다." in out
    assert [args["query"] for _, args in calls] == ["한빛소프트 매출", "한빛소프트 직원 수"]
    assert "{" not in internal_words.strip_tool_calls(text)
