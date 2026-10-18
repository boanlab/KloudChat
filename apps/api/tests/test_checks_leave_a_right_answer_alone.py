"""Checks that keep the writer's answer: right values, met discounts, narration, starved turns."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.models.chat import SessionKind
from app.services import (
    context,
    freshness,
    internal_words,
    prompt_tier,
    report,
    report_checks,
    skill_scripts,
)

trace = skill_scripts.module("grounding-check", "trace")
TODAY = "2026-10-14"


def _marked(text: str, material: str = "", **options) -> list[str]:
    return [f["text"] for f in trace.check(text, material, [], {"today": TODAY, **options})]


# ── 1. grounding marks on right values ────────────────────────────────────


def test_a_scale_syllable_that_begins_another_word_is_no_amount():
    # 「연도 2026 조건(확인 필요)」, 「타임 퀀텀 2만(확인 필요)큼만」
    assert trace.ko_number("2026") == 2026
    assert not any("조" in t for t in _marked("**⑤ 연도 2026 조건이 자동으로 포함된다**",
                                               "연도는 2026년만."))
    assert not any("만" in t for t in _marked("발표 노트: 각 프로세스에 타임 퀀텀 2만큼만 CPU 할당.",  # noqa: E501
                                               "타임 퀀텀 2"))


def test_a_korean_amount_with_its_bare_tail_is_one_value():
    # the source's 「18만 4,102명」 read as 18만 and 4,102, marked
    from app.services import value_guard

    found = value_guard._NUMBER.search("네이버웍스 18만 4,102명 + 하이웍스")
    assert found and value_guard.ko_number(found.group("num")) == 184_102
    material = "모바일인덱스(2023년 3월) 네이버웍스 MAU 184,102명"
    assert _marked("단독 브랜드는 네이버웍스 MAU 18만 4,102명과의 사용자 격차를 마주해야 한다.",
                   material) == []


def test_today_and_the_days_the_request_names_by_it_are_sources():
    # 「이번 주 금요일(10월 16일, 확인 필요)」, 「오늘(10월 14일)」
    request = "이번 주 금요일까지 저에게 회신 부탁드립니다."
    assert _marked("회신은 이번 주 금요일(10월 16일)까지", request) == []
    assert _marked("오늘(10월 14일) 기준으로 정리했습니다.", "지금 단기로 굴리기 좋은 방법은?") == []  # noqa: E501
    # a day nobody named stays checked
    assert _marked("회신은 10월 23일까지", request)


def test_a_clock_span_s_minutes_and_a_cell_range_s_rows_are_the_line_s_own():
    # 「14:55~15:00(5분(확인 필요), 확인 필요)」, 「아래로 6행(확인 필요)까지」
    assert _marked("| 4 | 워크숍(10/22) 준비 점검 | 미정 | 14:55~15:00(5분) | 논의 |",
                   "다음 주 목요일(10/15) 14시 주간회의야. 1시간이고, 워크숍(10/22) 준비 점검.") == []  # noqa: E501
    assert _marked("`B2`에 한 번 넣고 오른쪽으로 M열까지, 아래로 6행까지 드래그하면 됩니다.",
                   "'요약' 시트에 지역은 A2:A6, 월은 B1:M1(1~12월 숫자)로 놓을 거야.") == []
    # the date the line's own DATE() formula gives, written without its year
    assert _marked("`<=DATE(2026,1,31)`로 쓰면 1월 31일 오후 데이터가 통째로 빠집니다.",
                   "연도는 2026년만.") == []


def test_a_demonstrative_or_a_verb_before_a_title_is_no_name():
    # 「이게 총무·IT 담당자」 → 「[이름] 총무」; 「다 들어간 대표 문구」
    assert _marked("인증 범위가 전 사업장에 걸쳐 있습니다. 이게 총무·IT 담당자에게 맞는 연결고리입니다.",  # noqa: E501
                   "대웅제약") == []
    assert _marked("1. #6: 사실 세 가지가 다 들어간 대표 문구. 배너 메인.", "한빛워크") == []
    assert trace.NOT_A_NAME.match("이게") and trace.VERB_END.search("들어간")
    # a real name before a title is still one
    assert not trace.NOT_A_NAME.match("이수진") and not trace.VERB_END.search("이수진")


def test_a_data_move_is_no_criminal_record():
    # 「데이터 이전과 결재 연동 재구축 비용」 matched 「전과」
    assert not trace._PENALTY.search("유료 전환 시점에 데이터 이전과 결재 연동 재구축 비용이 발생")
    assert trace._PENALTY.search("전과 기록이 남을 수 있습니다")


def test_an_earlier_answer_s_cited_sentences_back_advice_that_repeats_them():
    from app.pipeline import check_stages

    earlier = ("카카오뱅크 정기예금은 연 3.70%입니다 [[4]](https://example.test/rate).\n"
               "지금 굴리기 좋은 방법을 정리해 볼게요.")
    assert check_stages._cited_sentences(earlier) == (
        "카카오뱅크 정기예금은 연 3.70%입니다 [[4]](https://example.test/rate).")


# ── 2. compare-cost: a condition the material meets is applied ────────────

LAPTOP_QUOTES_REQUEST = """교직원 업무용 노트북 30대 교체 건, 견적 세 곳 받은 걸로 비교 검토 보고서 써 줘. 어디로 할지 추천까지.

[견적 1] 가온정보통신
- 단가 1,320,000원(부가세 별도) × 30대
- 무상 보증 3년(방문 수리), 납기 계약 후 3주
- 기존 노트북 30대 수거·데이터 완전 삭제 무료

[견적 2] 다솜시스템
- 단가 1,390,000원(부가세 포함) × 30대
- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)
- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)

[견적 3] 누리컴
- 단가 1,480,000원(부가세 별도) × 30대, 30대 이상 구매 시 총액 5% 할인
- 무상 보증 3년(방문 수리), 납기 5주
- 기존 기기 수거 무료, 데이터 삭제 확인서는 별도 대당 5,000원(부가세 별도)

조건: 예산 4,800만원(부가세 포함), 11월 30일까지 지급 완료돼야 함, 보증 3년 필수, 기존 기기 데이터 삭제 증빙 필요."""  # noqa: E501


def _reading(discount_when: str | None = "30대 이상 구매 시") -> dict:
    discount = {"name": "할인", "unit_price": 1480000, "count": 30, "per": "once",
                "rate": 0.05, "sign": -1, "vat": "excl"}
    if discount_when:
        discount["when"] = discount_when
    return {"period": "3년", "years": 3, "options": [
        {"name": "가온정보통신", "items": [
            {"name": "노트북", "unit_price": 1320000, "count": 30, "per": "once", "vat": "excl"}]},
        {"name": "다솜시스템", "items": [
            {"name": "노트북", "unit_price": 1390000, "count": 30, "per": "once", "vat": "incl"},
            {"name": "수거", "unit_price": 10000, "count": 30, "per": "once", "vat": "incl"}]},
        {"name": "누리컴", "items": [
            {"name": "노트북", "unit_price": 1480000, "count": 30, "per": "once", "vat": "excl"},
            discount,
            {"name": "데이터 삭제 확인서", "unit_price": 5000, "count": 30, "per": "once",
             "vat": "excl"}]}]}


def _costs(reading: dict) -> dict:
    numbers = report_checks._material_numbers(LAPTOP_QUOTES_REQUEST)
    counted = report_checks.counted_as_written(reading, LAPTOP_QUOTES_REQUEST)
    return report_checks.option_costs(counted, numbers, material=LAPTOP_QUOTES_REQUEST)


def test_a_quantity_discount_the_order_meets_is_in_the_total_not_a_scenario():
    # 「30대 이상 구매 시」 went to a scenario, the scenario was dropped and
    # 49,005,000원 put 누리컴 over the 4,800만원 budget (right: 46,563,000원)
    costs = _costs(_reading())
    assert costs["options"]["누리컴"]["total"] == 46_563_000
    assert costs["applied"] == ["30대 이상 구매 시 (누리컴)"]
    assert costs["scenarios"] == []
    assert costs["budget"]["room"]["누리컴"] == 1_437_000
    # a reading that wrote the discount without its condition settles the same total
    assert _costs(_reading(None))["options"]["누리컴"]["total"] == 46_563_000
    assert "충족되어 위 합계에 넣은 것" in report_checks._cost_block(costs)


def test_a_buyer_s_choice_stays_a_scenario():
    # 「연 단위 계약 시 15% 할인」 is the buyer's to choose; code does not apply it
    assert not report_checks.condition_met("연 단위 계약 시", [{"raw": {"count": 40}}], 1)
    assert report_checks.condition_met("30대 이상 구매 시", [{"raw": {"count": 30}}], 3)
    assert not report_checks.condition_met("50대 이상 구매 시", [{"raw": {"count": 30}}], 3)
    assert report_checks.condition_met("3년 이상 계약 시", [], 3)


def _scenario(name: str, totals: dict[str, int]) -> dict:
    return {"name": name, "options": {n: {"total": t} for n, t in totals.items()}}


def test_a_discount_scenario_the_readings_price_differently_is_never_dropped_silently():
    base = {"period": "3년", "dropped": [], "scenarios": [
        _scenario("연 계약 시", {"A안": 100, "B안": 80})],
        "options": {"A안": {"total": 100}, "B안": {"total": 120}}}
    other = {**base, "scenarios": [_scenario("연 계약 시", {"A안": 100, "B안": 90})]}
    # small writer: the option the discount lowers is not settled
    kept, unsettled = report_checks._split_scenarios(base, [other], strong=False)
    assert unsettled == ["B안"]
    # strong writer: both readings handed over, marked
    kept, unsettled = report_checks._split_scenarios(base, [other], strong=True)
    assert unsettled == []
    names = [sc["name"] for sc in kept["scenarios"]]
    assert names == ["연 계약 시 — 읽을 때마다 값이 다름",
                     "연 계약 시 (다른 읽기) — 읽을 때마다 값이 다름"]


def test_over_the_budget_only_without_a_stated_condition_is_open_not_out():
    costs = {"budget": {"amount": 48_000_000, "vat": "incl",
                        "compared": {"누리컴": 49_005_000},
                        "scenarios": {"30대 이상 구매 시": {"누리컴": 46_563_000}}}}
    must = {"name": "예산", "kind": "max", "limit": 48_000_000, "unit": "원"}
    assert report_checks._within_under("누리컴", must, costs) == ("30대 이상 구매 시", 46_563_000)


# ── 3. reasoning-starved stream ───────────────────────────────────────────


def test_a_stream_that_thought_through_its_budget_and_wrote_nothing_is_starved():
    from app.services import agent

    starved = SimpleNamespace(content=[], calls={}, finish_reason="length",
                              reasoning_chars=120_432)
    assert agent._starved(starved)
    assert agent._starved(SimpleNamespace(content=[], calls={}, finish_reason="stop",
                                          reasoning_chars=50_000))
    assert not agent._starved(SimpleNamespace(content=["답"], calls={}, finish_reason="length",
                                              reasoning_chars=120_432))
    assert not agent._starved(SimpleNamespace(content=[], calls={0: {}}, finish_reason="length",
                                              reasoning_chars=0))


# ── 4. the notice genre only when a notice is asked for ───────────────────

AI_GUIDELINE_RESEARCH_REQUEST = ("「국내 대학의 생성형 AI 활용 가이드라인 동향」을 조사해서 보고서로 정리해 줘. 우리 대학(한빛대학교, "  # noqa: E501
                                 "가상) 교무처에서 자체 가이드라인을 만들기 전에 참고하려는 거야.")
SAAS_POLICY_RESEARCH_REQUEST = (
    "「공공기관의 민간 클라우드(SaaS) 이용 확대 정책 동향」을 조사해서 보고서로 정리해 줘. 우리 "  # noqa: E501
    "회사(한빛소프트, 가상 — 협업툴 SaaS 개발사)가 공공 시장 진출을 검토하려고 해.")


@pytest.mark.parametrize("request_text",
                         [AI_GUIDELINE_RESEARCH_REQUEST, SAAS_POLICY_RESEARCH_REQUEST])
def test_a_report_about_guidelines_or_policy_is_not_a_notice(request_text):
    assert "안내·정책 문서" not in report._genre_rule(request_text)


@pytest.mark.parametrize("request_text", [
    "신입생 대상 도서관 이용 안내문 써 줘", "학과 생성형 AI 사용 가이드라인 초안 써 줘",
    "연구실 보안 규정을 작성해 줘", "다음 주 휴강 공지 써 줘"])
def test_a_notice_asked_for_keeps_its_genre(request_text):
    assert report._genre_rule(request_text).startswith("Genre: 안내·정책 문서")


# ── 5. the light prompt keeps the calculation-tool line ───────────────────


def test_the_light_tool_line_says_when_to_compute():
    light = context.build_messages(SessionKind.chat, [{"role": "user", "content": "x"}],
                                   with_tools=True, light=True)[0]["content"]
    assert "calculate or execute_code" in light and "add up to the total" in light
    assert "dates" in prompt_tier.ANTI_FABRICATION


# ── 7·8. work narration ahead of the deliverable in a tool turn ───────────


def test_tool_turn_narration_before_the_deliverable_goes():
    # the figures-retry answer opened with the recount
    recount_answer = ("수치를 다시 확인했습니다. 이번 실행에서는 의견 분류 기준이 달라져 온라인학습 관련이 10건(55.56%), "  # noqa: E501
                      "시설 관련이 1건(5.56%)으로 나왔습니다. 이전 턴의 분류(온라인 12건, 시설 6건)와 차이가 나므로, "  # noqa: E501
                      "의견 분류 기준을 명확히 다시 계산해 확정하겠습니다.\n\n"
                      "의견 분류가 확정되었습니다(온라인학습 12건 66.67%, 시설 6건 33.33%). 이전 턴의 답에 있던 "  # noqa: E501
                      "3.5와 66.7은 이제 실행 출력값으로 확인된 수치(3.56, 66.67)로 교체해 답을 다시 씁니다.\n\n")  # noqa: E501
    body = ("# 2026-1학기 강의 만족도 조사 분석 (응답 120명)\n\n**결론 요약:** 5개 문항 평균이 모두 "  # noqa: E501
            "3.56~3.88점(5점 척도)으로 중간 수준입니다.\n\n| 문항 | 평균 |\n|---|---|\n"
            + "| 교과내용 | 3.88 |\n" * 12)
    assert internal_words.strip_tool_preamble(recount_answer + body) == body
    # the check monologue, spaces lost between hops, above 「---」
    check_monologue = ('점검 결과 "1." 번호가 자료에 없는 값으로 잡혔습니다. 이는 목록 번호라 실질적 문제는 아니지만, '  # noqa: E501
                       '안전하게 번호 없는 불릿으로 바꿔 다시 확인합니다.여전히 같은 "1"이 잡힙니다. "2층 라운지"라고만 '  # noqa: E501
                       '쓰겠습니다.점검 통과했습니다. 완성된 공지문입니다.\n\n---\n\n')
    notice = "**제목** 본사 3~5층 네트워크 장비 교체 공사 안내 (10.23~10.25)\n\n" + "안내 문장입니다. " * 30  # noqa: E501
    assert internal_words.strip_tool_preamble(check_monologue + notice) == notice
    # the search note left in parentheses
    search_note = "(검색은 최신 사실 확인용이었고, 요약은 제시된 자료에 근거합니다.)\n\n---\n\n"
    summary = "**핵심 요약**\n\n" + "□ 2027년부터 대학 재정지원사업이 개편됨\n" * 20
    assert internal_words.strip_tool_preamble(search_note + summary) == summary


def test_a_lead_that_answers_stays():
    # the answer's own first sentence above its table
    answer = ("직급(대리·6급 상당)은 여비 산정에 영향이 없습니다. 별표 1은 「직급에 관계없이 동일하게 "  # noqa: E501
              "적용」하므로, 부산은 광역시라 숙박비 상한이 1박 80,000원입니다.\n\n## 여비 계산 (2박 3일)\n\n"  # noqa: E501
              "| 항목 | 금액 |\n|---|---|\n" + "| 운임 | 119,600원 |\n" * 20)
    assert internal_words.strip_tool_preamble(answer) == answer


# ── 9. a results report without dated steps ──────────────────────────────


def test_the_results_report_does_not_force_a_dated_progress_section():
    from app.services import design_templates

    rule = report._genre_rule("행사 결과보고서 써 줘")
    assert "추진 경과 holds only the steps and dates the material gives" in rule
    template = design_templates.get("doc-result-report")
    assert "never fill it with 접수·심사·확정 dates" in template.instructions


# ── 10. an empty search is tried once more, broader ───────────────────────


@pytest.mark.asyncio
async def test_an_empty_search_is_retried_once_with_a_broader_query(monkeypatch):
    from app.services.tools import builtin

    asked: list[str] = []

    async def config():
        return SimpleNamespace(search="synthetic-search", fetch="synthetic-fetch")

    async def results(_base, query, *_args, **_kwargs):
        asked.append(query)
        if len(asked) == 1:
            return []
        return [{"title": "MMF CMA 금리", "url": "https://example.test/cma",
                 "snippet": "CMA RP형 연 2.6%", "published": ""}]

    async def scrape(_base, _url):
        return ""

    monkeypatch.setattr(builtin.settings_store, "tools_config", config)
    monkeypatch.setattr(builtin, "_searxng", results)
    monkeypatch.setattr(builtin, "_scrape", scrape)
    result = await builtin.web_search({"query": "2026년 10월 초 단기 자금 운용 MMF CMA 금리 비교"})
    assert asked == ["2026년 10월 초 단기 자금 운용 MMF CMA 금리 비교", "자금 운용 MMF CMA 금리"]
    assert not result.empty and "CMA RP형" in result.content


# ── 11. the training-cutoff notice under a grounded answer ────────────────


def test_the_training_cutoff_notice_goes_where_the_answer_needs_none():
    model = {"id": "z-ai/glm-5.3"}
    notice = freshness.accuracy_caveat("질문", model, "z-ai/glm-5.3")
    cited = "전 세계 유통 지폐는 최소 9,000억 장 이상입니다 [10]."
    assert freshness.normalize_answer_notice(cited + "\n\n" + notice, "질문", model,
                                             "z-ai/glm-5.3", grounded=True) == cited
    hedged = "이번 검색에서 현재 금리 수치를 확인하지 못했으므로, 구체적 금리는 직접 확인하세요."
    assert freshness.normalize_answer_notice(hedged + "\n\n" + notice, "질문", model,
                                             "z-ai/glm-5.3") == hedged
    plain = "기준금리는 변동될 수 있습니다."
    assert freshness.normalize_answer_notice(plain + "\n\n" + notice, "질문", model,
                                             "z-ai/glm-5.3") == plain + "\n\n" + notice


# ── 12. an answer cut mid-sentence ────────────────────────────────────────


def test_an_answer_cut_mid_sentence_is_seen():
    # stopped at 「○ 2027년 3월」
    cut = ("□ ② RISE 지자체 재량 확대로 지역 연계 성과계획 제출이 의무화됨\n"
           + "○ 일반재정지원의 성과 배분이 30%→40%로 확대됨\n" * 12
           + "○ 지자체 재량 비율이 30%→50%로 높아져, 지자체가 대학별 과제를 직접 설계·배분함\n"
           "○ 2027년 3월")
    assert internal_words.cut_mid_sentence(cut)
    # the section stopped at 「…온라인 동시 송출」
    assert internal_words.cut_mid_sentence(
        "신청 42명(정원 40명)으로 대기자가 발생합니다. " * 8 + "이수진 차장이 온라인 동시 송출",
        min_chars=150)


@pytest.mark.parametrize("ending", [
    "- B안 계약 시 데이터 반환(내보내기) 조항, 서비스 가동률(SLA) 확인 권장",
    "- 걷기 힘들 정도의 심한 어지럼증",
    "| 합계 | 46,563,000원 |",
    "출처: 교육부 보도자료",
    "감사합니다.",
    "한빛소프트 영업팀 드림",
])
def test_a_whole_ending_is_not_cut(ending):
    body = "\n".join(["- 항목은 이렇게 정리함", "- 다음 항목도 정리함", "- 세 번째 항목임"] * 10)
    assert not internal_words.cut_mid_sentence(body + "\n" + ending)


def test_named_days_follow_the_calendar():
    assert trace._named_days("어제 회의 메모야", dt.date(2026, 10, 8)) == [dt.date(2026, 10, 7)]
    days = trace._named_days("오늘 기준, 이번 주 금요일, 다음 주 월요일, 내일",
                             dt.date(2026, 10, 14))
    assert dt.date(2026, 10, 16) in days and dt.date(2026, 10, 19) in days
    assert dt.date(2026, 10, 15) in days and dt.date(2026, 10, 14) in days
