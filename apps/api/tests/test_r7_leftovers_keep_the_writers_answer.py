"""Round 7 leftovers: what the code can decide is done by code, and nothing else is taken from
the writer — given-value arithmetic when the calculation pass times out (A10-T2), agenda
numbers after slides are added (A09-T2), whole chart categories and unit-aware note numbers
(A09-T1), one blank rule for SNS and ad copy (A13-T2), derived prices accepted (A11-T2), no
reference line drawn on a category (A06-T2), and repair prompts in the writer's tier."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.pipeline import report_stages
from app.services import calc, deck, freshness, page, prompt_tier, report
from app.services.tools import builtin
from app.services.tools.base import ToolContext

STRONG = "deepseek/deepseek-v4-pro-0813"
SMALL = "local/qwen3.8-27b"
SKILLS = Path(__file__).resolve().parents[1] / "app" / "skills"

# ── 1. calc timeout: the given figures' own arithmetic ─────────────────

#: r7 tasks/R3-A10-T2/prompt.md
A10_T2 = "\n".join([
    "국내 초·중·고 대상 「AI 학습 튜터(에듀테크)」 시장 규모를 추정해 줘. 신사업 TF 1차 검토용이야.",  # noqa: E501
    "- TAM·SAM·SOM 구분해서, 하향식과 상향식 둘 다로",
    "- 학생 수 등 기초 통계는 공개 출처(교육부·통계청 등)를 찾아서 쓰고 출처 달기",
    "- 우리 가정(한빛소프트 사내 가정, 가상): 월 구독료 9,900원, 초기 3년 목표 점유율은 유료 이용 학생 기준 2%",  # noqa: E501
    "- 고객군(학부모 직접 구매, 학교·교육청 구매, 학원)별로 나눠서 보여 주고, 각 숫자가 어떤 가정에서 나왔는지 계산식으로",  # noqa: E501
])


def test_the_given_figures_are_worked_out_by_code() -> None:
    assert calc.given_values(A10_T2) == ["월 구독료 9,900원 × 12개월 = 연 구독료 118,800원"]
    # r7 A04-T1 / A04-T2 lines: written products, the monthly one over a year too
    assert calc.given_values("- 화상회의 라이선스 1인당 월 8,000원 × 40명") == [
        "월 8,000원 × 40명 = 320,000원", "월 320,000원 × 12개월 = 연 3,840,000원"]
    assert calc.given_values("- 단가 1,320,000원(부가세 별도) × 30대") == [
        "1,320,000원(부가세 별도) × 30대 = 39,600,000원"]
    # a finished equation, a bare count, an operand that is no number: nothing guessed
    for line in ("9,900원 × 12 = 118,800원", "2024 + 1년",
                 "연료비(왕복 거리(km) × 1km당 200원)", "목표 점유율 2%"):
        assert calc.given_values(line) == []


async def test_a_calc_timeout_hands_the_writer_the_given_arithmetic(monkeypatch) -> None:
    from app.pipeline.stage import Context

    async def timed_out(*_args, **_kwargs):
        return "", {"inputTokens": 0, "outputTokens": 0}, "timeout"

    monkeypatch.setattr(report_stages.calc, "computed_values", timed_out)
    bundle = report_stages.bundle_for(request=A10_T2, model="m", api_key="k")
    events = [e async for e in report_stages.Calc().run(bundle, Context())]
    assert events[-1]["label"] == "코드 계산 시간 초과 — 모델이 계산"
    texts = [p.text for p in bundle.material.trusted]
    assert "연 구독료 118,800원" in bundle.computed.values
    assert bundle.computed.values in texts
    # what code cannot give (student counts, conversion rates) is still the writer's
    assert report_stages.CALC_MISSING_NOTE in texts
    assert texts.index(bundle.computed.values) < texts.index(report_stages.CALC_MISSING_NOTE)


# ── 2. deck ─────────────────────────────────────────────────────────────


def _slide(title: str, layout: str = "bullets") -> dict:
    return {"title": title, "layout": layout, "bullets": ["x"]}


def test_agenda_numbers_follow_the_slides_added_between_them() -> None:
    # r7 out/R3-A09-T2/kc_GLP.md: a 12-slide outline, five Gantt slides added by code
    agenda = {"title": "목차", "layout": "agenda", "bullets": [
        "개요·예제 프로세스 설정 8분 (1~3번)", "다섯 알고리즘 간트 차트 도출 25분 (4~8번)",
        "비교 표와 퀀텀 이야기 9분 (9~10번)", "확인 퀴즈 5분 (11번)", "정리 3분 (12번)"]}
    derive = [_slide(f"{n} Gantt 도출") for n in ("FCFS", "SJF", "SRTF", "RR", "우선순위")]
    before = [_slide("CPU 스케줄링", "title"), agenda, _slide("예제 프로세스 설정"), *derive,
              _slide("비교"), _slide("퀀텀이 성능을 가른다"), _slide("확인 퀴즈"), _slide("정리")]
    slides = list(before)
    for at in (8, 7, 6, 5, 4):
        slides.insert(at, _slide(f"간트 차트 {at}", "table"))
    assert len(before) == 12 and len(slides) == 17
    deck.refresh_agenda(slides, before)
    assert agenda["bullets"] == [
        "개요·예제 프로세스 설정 8분 (1~3번)", "다섯 알고리즘 간트 차트 도출 25분 (4~13번)",
        "비교 표와 퀀텀 이야기 9분 (14~15번)", "확인 퀴즈 5분 (16번)", "정리 3분 (17번)"]


def test_agenda_slide_ranges_follow_the_slides_and_times_are_kept() -> None:
    # r7 out/R3-A09-T2/kc_QMX.md: 「(슬라이드 5~7)」 after a Gantt slide behind FCFS and SJF
    agenda = {"title": "발표 순서", "layout": "agenda", "bullets": [
        "도입: 개념과 예제 (슬라이드 1~4) — 7분", "비선점형: FCFS·SJF (슬라이드 5~7) — 10분",
        "퀴즈와 정리 (슬라이드 8~9) — 8분"]}
    before = [_slide("표지", "title"), agenda, _slide("용어"), _slide("예제"),
              _slide("비선점형", "section"), _slide("FCFS"), _slide("SJF"), _slide("퀴즈"),
              _slide("정리")]
    slides = list(before)
    slides.insert(7, _slide("SJF 간트 차트", "table"))
    slides.insert(6, _slide("FCFS 간트 차트", "table"))
    deck.refresh_agenda(slides, before)
    assert agenda["bullets"] == [
        "도입: 개념과 예제 (슬라이드 1~4) — 7분", "비선점형: FCFS·SJF (슬라이드 5~9) — 10분",
        "퀴즈와 정리 (슬라이드 10~11) — 8분"]


def test_chart_categories_keep_their_words() -> None:
    # r7 A09-T1: 「2단계구조제거시」 and 「교차인코더」
    chart = deck._clean_chart({"kind": "bar", "categories": [
        "LiteRerank(제안)", "조기 종료 제거 시", "2단계 구조 제거 시",
        "교차 인코더(bge-reranker-large)"],
        "series": [{"name": "지연(ms)", "values": [38, 71, 154, 412]}]})
    assert chart["categories"] == ["LiteRerank(제안)", "조기 종료 제거 시", "2단계 구조 제거 시",
                                   "교차 인코더(bge-reranker-large)"]
    # past two lines' room: the aside goes first, then whole trailing words — never a cut word
    assert deck._short_category("교차 인코더 기반 재순위화 (bge-reranker-large, 560M 파라미터)") \
        == "교차 인코더 기반 재순위화"
    long = deck._short_category("아주 긴 범주 이름이 여러 단어로 이어져서 두 줄을 넘는 경우의 처리")
    assert len(long) <= deck._CATEGORY_CHARS and long.split(" ")[-1] == "넘는"


def test_the_pdf_wraps_a_category_at_its_spaces() -> None:
    from app.services import deck_export

    font = "Helvetica"
    lines = deck_export._category_lines("2단계 구조 제거 시", font, 12, 40)
    assert " ".join(lines) == "2단계 구조 제거 시" and len(lines) == 2
    assert all(w in "2단계 구조 제거 시".split() or " " in w for w in lines)
    assert deck_export._category_lines("FCFS", font, 12, 200) == ["FCFS"]


#: r7 tasks/R3-A09-T1/prompt.md (the results table and its notes)
A09_T1 = """실험 결과 (생성 모델: 3B sLM, 검색 상위 100개 후보, A100 1장)
| 재순위화 방식 | KorQuAD-RAG EM | 사내 QA 정확도 | 재순위화 지연(ms/질의) | 파라미터 |
| 없음(BM25만) | 61.2 | 58.4 | 0 | - |
| 교차 인코더(bge-reranker-large) | 72.8 | 69.1 | 412 | 560M |
| MiniLM 교차 인코더 | 69.5 | 65.7 | 96 | 33M |
| LiteRerank(제안) | 71.9 | 68.3 | 38 | 22M |
- 조기 종료 제거 시: EM 72.1, 지연 71ms
- 2단계 구조 제거(100개 전부 정밀 평가) 시: EM 72.0, 지연 154ms
- 한계: 영어 데이터셋 미평가, 긴 문서(2,000토큰 이상)에서 EM 3.1포인트 하락"""


def test_note_numbers_are_compared_with_their_units() -> None:
    facts = deck._facts_set(A09_T1)
    # r7 out/R3-A09-T1/kc_DSP.md: 「96%의 정확도」 passed on MiniLM's 96 ms
    invented = "오늘 기억하실 것은 96%의 정확도를 지키면서 지연을 38밀리초로 줄였다는 점입니다."
    assert deck.notes_grounded(invented, facts, A09_T1) == ""
    # 「25만」 in 「BM25만」 is no figure
    assert deck.notes_grounded("하루 질의가 25만 건이라면 문제입니다.", facts, A09_T1) == ""
    right = ("긴 문서에서는 EM이 3.1%p 떨어집니다. 사내 QA 정확도는 68.3%입니다. "
             "재순위화 지연은 96ms입니다. EM은 72.1입니다.")
    assert deck.notes_grounded(right, facts, A09_T1) == right
    # the same amount in another scale is the given one
    assert deck.notes_grounded("예산은 5만 원이고 인원은 40명입니다.", set(),
                               "예산 50,000원, 40명") == "예산은 5만 원이고 인원은 40명입니다."
    assert deck.notes_grounded("인원은 40건입니다.", set(), "예산 50,000원, 40명") == ""


# ── 3. skills ───────────────────────────────────────────────────────────


def test_sns_and_ad_copy_share_one_blank_rule() -> None:
    social = (SKILLS / "social-content" / "SKILL.md").read_text(encoding="utf-8")
    ads = (SKILLS / "ad-creative" / "SKILL.md").read_text(encoding="utf-8")
    # r7 A13-T2: 「[체험 신청 경로: 프로필 링크 등 담당자 확인 후 기재]」 in nearly every post
    assert "[운영 방식: …]" not in social
    assert "stays true without it" in social and "stays true without it" in ads
    assert "프로필 링크에서 신청하세요" in social
    assert "only for what the person alone can fill in" in social
    assert "SNS 콘텐츠" in ads and "광고 소재" in social


def _script(name: str):
    path = SKILLS / "source-check" / "scripts" / f"{name}.py"
    key = f"_source_check_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


#: r7 tasks/R3-A11-T2/prompt.md (our product's material)
A11_T2 = """- 가격: 클라우드형 1인 월 6,000원(부가세 별도), 설치형은 100명 기준 연 1,800만원
- 고객 지원: 전담 매니저, 평일 9~18시 전화 지원
- 약점: 모바일 앱 평점 3.6, 외부 서비스 연동(구글·슬랙 등)이 적음, 화상회의 최대 50명"""


def test_a_price_worked_out_from_the_material_is_not_unsourced() -> None:
    claims, post = _script("claims"), _script("post")
    table = ("| 항목 | 한빛워크 | 네이버웍스 |\n|---|---|---|\n"
             "| 설치형 1인 월 환산 | 15,000원/인/월(부가세 별도) | 확인 필요 |\n"
             "| 클라우드형 연 100명 | 720만원 | 확인 필요 |\n"
             "| 지어낸 할인가 | 13,700원/인/월 | 확인 필요 |")
    flagged = [f["text"] for f in claims.check(table, A11_T2) if f["kind"] == "unsourced_cell"]
    assert flagged == ["13,700원/인/월"]
    # r7 out/R3-A11-T2/diff_DSP.md: 「…1인당 월 환산 시 15,000원(부가세 별도)입니다.
    # 「출처 미확인」」
    line = "설치형은 1인당 월 환산 시 15,000원(부가세 별도)입니다."
    assert post.post(line, {"material": A11_T2})["text"] == line
    assert "출처 미확인" in post.post("설치형은 1인당 월 13,700원입니다.",
                                       {"material": A11_T2})["text"]


async def test_a_reference_value_is_not_drawn_on_a_category() -> None:
    # r7 A06-T2: marks [{"x": "해외 단기연수", "label": "적정 75%"}] drew a vertical line there
    ctx = ToolContext(user_id="u", session_id="s")
    result = await builtin.create_chart({
        "title": "사업별 9월말 집행률", "yLabel": "%",
        "series": [{"name": "집행률", "points": [{"x": "교육혁신", "y": 29.0},
                                                 {"x": "해외 단기연수", "y": 95.0}]}],
        "marks": [{"x": "해외 단기연수", "label": "적정 75%"}, {"x": "교육혁신", "label": "개편"}],
    }, ctx)
    marks = ctx.pending_artifacts[-1]["data"]["marks"]
    assert marks == [{"x": "교육혁신", "label": "개편"}]
    assert "기준선(적정 75%)은 그리지 못해" in result.content
    chart_skill = (SKILLS / "chart-make" / "SKILL.md").read_text(encoding="utf-8")
    tabulation = (SKILLS / "data-tabulation" / "SKILL.md").read_text(encoding="utf-8")
    assert "no horizontal reference line" in chart_skill
    assert "draws no reference line" in tabulation


# ── 4. repair and page prompts in the writer's tier ─────────────────────


def _system(messages: list[dict]) -> str:
    return messages[0]["content"]


@pytest.mark.parametrize(("model", "light"), [(STRONG, True), (SMALL, False)])
async def test_page_prompts_follow_the_writers_tier(monkeypatch, model, light) -> None:
    from app.services import design_templates

    seen: list[list[dict]] = []

    async def complete(_model, messages, _key, _max):
        seen.append(messages)
        return "<p>본문</p>", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(page, "_complete", complete)
    template = next(t for t in design_templates.all_templates() if t.kind == "deck")
    blocks = [{"title": "표지", "layout": template.layouts[0], "html": "<p>x</p>"},
              {"title": "본론", "layout": template.layouts[0], "html": ""}]
    await page.rewrite_block(request="발표 자료 만들어 줘", blocks=blocks, index=1,
                             template=template, model=model, api_key="k")
    assert (freshness.FRESHNESS_INSTRUCTION not in _system(seen[-1])) is light


def test_the_page_outline_rule_against_invention_is_one_line_for_a_strong_writer() -> None:
    prompt = page._OUTLINE_PROMPT
    light = prompt_tier.for_writer(prompt, light=True)
    assert "요청에 없는 소재를 지어내지 마라" not in light
    assert light.count(prompt_tier.ANTI_FABRICATION) == 1
    assert "layout 은 다음 중에서만 골라라" in light
    assert prompt_tier.for_writer(prompt, light=False) == prompt


@pytest.mark.parametrize(("model", "light"), [(STRONG, True), (SMALL, False)])
async def test_report_repair_calls_follow_the_writers_tier(monkeypatch, model, light) -> None:
    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def unavailable():
        return False

    asked: dict[str, list[dict]] = {}

    async def complete(_model, messages, _key, max_tokens=0, **_kw):
        prompt = messages[-1]["content"]
        if "still\nhave blanks" in prompt:
            asked["fill"] = messages
            return "측정값은 3.2 V입니다.", {"inputTokens": 1, "outputTokens": 1}
        if 'writing only the "측정 결과" section' in prompt:
            return ("| 항목 | 값 |\n|---|---|\n| 전압 | [측정값] |\n| 전류 | [측정값] |\n"
                    "| 저항 | [측정값] |"), {"inputTokens": 1, "outputTokens": 1}
        return "", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report.research, "available", unavailable)
    monkeypatch.setattr(report, "_complete", complete)
    approved = {"title": "실험 보고서", "sections": ["측정 결과"]}
    _ = [e async for e in report.write(
        request="실험 보고서 써 줘", model=model, api_key="k", approved_plan=approved,
        untrusted_context=["전압 3.2 V, 전류 0.5 A, 저항 6.4 Ω"], web_search=False)]
    assert "fill" in asked
    assert (freshness.FRESHNESS_INSTRUCTION not in _system(asked["fill"])) is light
