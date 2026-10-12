"""A deck agent's check skills run on the deck as they do on a report, and no slide reaches the
person empty or holding the writer's internal text (round-3 A09: a CPU scheduling lecture whose
averages were worked in the model's head and disagreed between the body and the comparison
table, a slide body 「> 이 장을 쓰지 못했습니다.」, notes reading
`[ { "slide_id": "slide-cover`)."""

from __future__ import annotations

import json

import pytest

from app.pipeline.bundle import Bundle, Document, Request
from app.pipeline.check_stages import DeckGrounding
from app.pipeline.stage import Context
from app.services import (
    calc,
    calc_check,
    deck,
    process_settings,
    scheduling,
    skill_packages,
    skill_specs,
    skill_turn,
)

#: tasks/A09/T2/prompt.md as sent
LECTURE = (
    "운영체제(학부 3학년) 50분 강의용 슬라이드 만들어 줘. 주제는 CPU 스케줄링 — FCFS, SJF(비선점), "
    "SRTF, 라운드 로빈(타임 퀀텀 2), 우선순위 스케줄링. 12장 안팎.\n"
    "아래 예제 프로세스 하나로 모든 알고리즘의 간트 차트와 평균 대기시간을 계산해서 비교해 주고, "
    "마지막에 비교 표와 확인 퀴즈 2문제를 넣어 줘.\n\n"
    "예제 (도착 시간, 실행 시간, 우선순위 — 숫자가 작을수록 높음)\n"
    "P1: 0, 7, 3\nP2: 2, 4, 1\nP3: 4, 1, 4\nP4: 5, 4, 2"
)

#: tasks/A09/T1/prompt.md, the results table (no calculation asked)
TALK = (
    "다음 주 학회(한국정보과학회 추계) 구두 발표용 슬라이드 만들어 줘. 발표 15분, 10장 안팎. 아래 "
    "초록과 실험 결과로.\n\n"
    "실험 결과 (생성 모델: 3B sLM, 검색 상위 100개 후보, A100 1장)\n"
    "| 재순위화 방식 | KorQuAD-RAG EM | 사내 QA 정확도 | 재순위화 지연(ms/질의) | 파라미터 |\n"
    "| 없음(BM25만) | 61.2 | 58.4 | 0 | - |\n"
    "| 교차 인코더(bge-reranker-large) | 72.8 | 69.1 | 412 | 560M |\n"
    "| MiniLM 교차 인코더 | 69.5 | 65.7 | 96 | 33M |\n"
    "| LiteRerank(제안) | 71.9 | 68.3 | 38 | 22M |\n"
    "- 조기 종료 제거 시: EM 72.1, 지연 71ms\n"
    "- 2단계 구조 제거(100개 전부 정밀 평가) 시: EM 72.0, 지연 154ms"
)


def _averages(block: str) -> dict[str, str]:
    return {s.row: s.text for s in calc_check.stated_values(block) if s.label == "평균 대기시간"}


def test_the_lecture_s_schedules_are_run_by_code_with_both_priority_readings():
    block = scheduling.computed_block(LECTURE)
    assert _averages(block) == {
        "FCFS": "4.75", "SJF": "4", "SRTF": "3", "RR(q=2)": "5", "라운드 로빈(q=2)": "5",
        "우선순위(비선점)": "5.5", "우선순위(선점)": "5"}
    # the Gantt chart and each waiting time's working, for the slides to copy
    assert "FCFS | 간트 차트 = P1 0-7, P2 7-11, P3 11-12, P4 12-16" in block
    assert ("SRTF | 간트 차트 = P1 0-2, P2 2-4, P3 4-5, P2 5-7, P4 7-11, P1 11-16") in block
    assert "RR(q=2) 대기 풀이: P1 16−0−7=9, P2 9−2−4=3, P3 7−4−1=2, P4 15−5−4=6" in block
    # a plain table with no algorithm named is no exercise
    assert scheduling.computed_block("P1: 0, 7, 3\nP2: 2, 4, 1") == ""


@pytest.mark.asyncio
async def test_a_scheduling_exercise_needs_no_program_and_no_sandbox(monkeypatch):
    class _Off:
        exec = ""

    async def tools_config():
        return _Off()

    async def complete(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("no model writes this program")

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    assert calc.needed(LECTURE, [])
    block, usage, status = await calc.computed_values(
        LECTURE, [], complete=complete, model="m", api_key="k")
    assert status == "done" and usage == {"inputTokens": 0, "outputTokens": 0}
    assert "SRTF | 평균 대기시간 = 3" in block


def test_the_27b_deck_s_worked_averages_are_set_to_the_computed_ones():
    """The 27B deck: 7.75 in the FCFS body and 6.0 in the table, 8.25 and 10.0 for RR."""
    slides = [
        {"layout": "bands", "title": "FCFS: 선입 선처리", "items": [
            ["정의", "도착 순서대로 CPU를 분배하며 선점이 불가하다."],
            ["기대효과", "평균 대기시간 7.75s, 구현이 가장 단순하다."]]},
        {"layout": "bands", "title": "라운드 로빈: 타임 퀀텀 2", "items": [
            ["대기시간", "평균 대기시간은 8.25로 계산된다."]]},
        {"layout": "table", "title": "알고리즘 간 평균 대기시간 비교", "rows": [
            ["알고리즘", "평균 대기시간"], ["FCFS", "6.0"], ["SJF", "4.0"], ["SRTF", "1.25"],
            ["RR (q=2)", "10.0"]]},
    ]
    touched, corrected = calc_check.check_slides(slides, scheduling.computed_block(LECTURE))
    assert touched == [0, 1, 2] and len(corrected) == 5
    assert slides[0]["items"][1][1] == "평균 대기시간 4.75s, 구현이 가장 단순하다."
    assert slides[1]["items"][0][1] == "평균 대기시간은 5로 계산된다."
    assert [r[1] for r in slides[2]["rows"][1:]] == ["4.75", "4.0", "3", "5"]


def test_the_check_skills_run_on_slides_and_bring_the_code_pass():
    for key in ("calc-check", "grounding-check"):
        assert "slides" in skill_packages.package_for(key).kinds
    spec = skill_specs.of_package(skill_packages.package_for("calc-check"))
    assert spec.process == {"code_calc": True}
    own = skill_turn.merge([spec]).process_with(None)
    assert process_settings.resolve(template_id=None, format_id=None, own=own).code_calc
    # the agent's own setting stays above the skill's
    own = skill_turn.merge([spec]).process_with({"code_calc": False})
    assert not process_settings.resolve(template_id=None, format_id=None, own=own).code_calc


@pytest.mark.asyncio
async def test_a_calculation_skill_computes_the_table_s_values_unasked(monkeypatch):
    class _On:
        exec = "http://sandbox"

    async def tools_config():
        return _On()

    prompts: list[str] = []

    async def complete(model, messages, api_key, max_tokens=0):
        prompts.append(messages[0]["content"])
        return "```python\nprint('x')\n```", {"inputTokens": 1, "outputTokens": 1}

    async def execute_code(args):
        from app.services.tools.base import ToolResult

        return ToolResult(content="stdout:\nLiteRerank | 지연 감소율 = 90.78 %\n"
                                  "LiteRerank | EM 차이 = -0.9\nMiniLM | 지연 감소율 = 76.7 %")

    from app.services.tools import builtin

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin, "execute_code", execute_code)
    assert not calc.needed(TALK, []) and calc.needed(TALK, [], always=True)
    block, _, status = await calc.computed_values(
        TALK, [], complete=complete, model="m", api_key="k", always=True)
    assert status == "done" and "지연 감소율 = 90.78" in block
    assert prompts and all("The request names no calculation" in p for p in prompts)


def _bundle(text: str, turn: dict, slides: list[dict]) -> Bundle:
    request = Request(text=text, model="m", api_key="k", turn=turn)
    return Bundle(request=request, document=Document(sections=slides))


@pytest.mark.asyncio
async def test_the_grounding_skill_traces_a_deck_built_on_the_request_s_own_figures():
    slides = [{"layout": "bullets", "title": "FCFS", "bullets": [
        "간트: P1 0-7, P2 7-11, P3 11-12, P4 12-16", "평균 대기시간 4.75", "문맥 교환 1.5ms"]}]
    turn = {"traced": False, "check_keys": [], "writer": {"untrusted_context": []}}
    assert DeckGrounding().skip(_bundle(LECTURE, turn, slides))
    turn["check_keys"] = ["grounding-check"]
    bundle = _bundle(LECTURE, turn, json.loads(json.dumps(slides)))
    bundle.computed.values = scheduling.computed_block(LECTURE)
    assert not DeckGrounding().skip(bundle)
    events = [event async for event in DeckGrounding().run(bundle, Context())]
    bullets = bundle.document.sections[0]["bullets"]
    # the code's values stand; the invented one is marked
    assert bullets[:2] == slides[0]["bullets"][:2]
    assert bullets[2] != slides[0]["bullets"][2] and "확인 필요" in bullets[2]
    assert any(e["type"] == "step" for e in events)
    # a request with nothing to trace against is left alone
    assert DeckGrounding().skip(_bundle("AI 트렌드 발표 10장", turn, slides))


def test_internal_text_never_stays_on_a_slide():
    slides = [
        # 27B T1: the stand-in above bullets a later pass filled in
        {"layout": "bullets", "title": "성능 및 지연 개선", "body": deck.UNWRITTEN,
         "bullets": ["EM 71.9로 성능 유지", "38ms로 지연 대폭 절감"],
         "notes": "제안 모델 LiteRerank는 EM 71.9로 기존 방식과 동등한 성능을 냈습니다."},
        # flash T2: the next slides' JSON as the notes
        {"layout": "bullets", "title": "알고리즘별 평균 대기시간 비교",
         "bullets": ["숫자가 낮을수록 대기시간이 적음"], "notes": '[ { "slide_id": "slide-cover'},
        # flash T1: notes that broke off mid-sentence
        {"layout": "bullets", "title": "LiteRerank: 2단계 경량 구조",
         "bullets": ["총 22M 파라미터의 경량 재순위화기"],
         "notes": "한빛대학교 컴퓨터공학과 이서준입니다. 동시에 지연은 412ms에서 38ms로"},
        # a lecture's own JSON example stays
        {"layout": "bullets", "title": "JSON 응답 예", "bullets": ['{"name": "kim", "age": 3}'],
         "notes": "키와 값이 짝을 이룹니다."},
    ]
    assert deck.strip_internal_text(slides) == [0, 1, 2]
    assert "body" not in slides[0] and len(slides[0]["bullets"]) == 2
    assert slides[1]["notes"] == ""
    assert slides[2]["notes"] == "한빛대학교 컴퓨터공학과 이서준입니다."
    assert slides[3]["bullets"] == ['{"name": "kim", "age": 3}']
    assert not any(deck.unshippable(s) for s in slides)
    # a slide with nothing but the stand-in keeps it, and is not shipped as is
    alone = [{"layout": "bullets", "title": "결론", "body": deck.UNWRITTEN}]
    deck.strip_internal_text(alone)
    assert deck.unshippable(alone[0]) == "unwritten"
    assert deck.unshippable({"layout": "bullets", "title": "결론", "notes": "말."}) == "empty"


async def _write(monkeypatch, draft: dict, rewrite: dict | None) -> list[dict]:
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        text = json.dumps(messages, ensure_ascii=False)
        if "has not been written yet" in text:
            asked.append(text)
            if rewrite is not None:
                return json.dumps(rewrite, ensure_ascii=False), {"inputTokens": 1,
                                                                 "outputTokens": 1}
        return json.dumps(draft, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    async def no_figures(**kwargs):
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "plan", no_figures)
    plan = [{"title": s["title"], "layout": s["layout"]} for s in draft["slides"]]
    events = [
        event
        async for event in deck._write_slides(
            plan=plan, title="재순위화 경량화", subtitle="", accent="", request=TALK,
            model="m", api_key="k", trusted_context=[], untrusted_context=[],
            usage={"inputTokens": 0, "outputTokens": 0},
        )
    ]
    assert asked, "the empty slide is rewritten once"
    return events


_DRAFT = {"slides": [
    {"title": "재순위화 경량화", "layout": "title"},
    {"title": "재순위화 방식 비교", "layout": "bullets",
     "bullets": ["LiteRerank 지연 38ms", "교차 인코더 지연 412ms"],
     "notes": "지연이 412ms에서 38ms로 줄었습니다. 정확도는 거의 같습니다."},
    # nothing on it, notes included: the writer gave up on it
    {"title": "구조별 성능 기여 분석", "layout": "bullets", "bullets": [], "notes": ""},
]}


@pytest.mark.asyncio
async def test_an_empty_slide_is_rewritten_once(monkeypatch):
    rewrite = {"bullets": ["조기 종료 제거 시 EM 72.1, 지연 71ms",
                           "2단계 구조 제거 시 EM 72.0, 지연 154ms"],
               "notes": "두 구조 모두 지연을 줄이는 데 기여합니다. 정확도 차이는 작습니다."}
    events = await _write(monkeypatch, _DRAFT, rewrite)
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    assert [s["title"] for s in slides] == [s["title"] for s in _DRAFT["slides"]]
    assert slides[2]["bullets"] == rewrite["bullets"] and "body" not in slides[2]
    assert not any(e.get("id") == "dropped" for e in events)


@pytest.mark.asyncio
async def test_a_slide_still_empty_after_its_rewrite_is_dropped_and_said(monkeypatch):
    events = await _write(monkeypatch, _DRAFT, None)
    slides = next(e["slides"] for e in events if e["type"] == "deck")
    assert [s["title"] for s in slides] == ["재순위화 경량화", "재순위화 방식 비교"]
    assert not any(deck.UNWRITTEN in json.dumps(s, ensure_ascii=False) for s in slides)
    dropped = next(e for e in events if e.get("id") == "dropped")
    assert dropped["status"] == "error" and "「구조별 성능 기여 분석」" in dropped["detail"]


def test_a_pattern_slide_s_items_and_columns_reach_the_markdown():
    """Flash's 「결론 및 시사점」 (takeaways) and 「구조별 성능 기여 분석」 (compare-2) read empty
    in the Markdown export: their screens are `items` and `columns`."""
    md = deck.to_markdown("발표", [
        {"layout": "takeaways", "title": "결론 및 시사점",
         "items": [["22M로 38ms", "EM 71.9 유지"], ["2단계 구조가 핵심", "제거 시 154ms"]]},
        {"layout": "compare-2", "title": "구조별 성능 기여 분석",
         "columns": [{"title": "조기 종료 제거", "items": ["EM 72.1", "지연 71ms"]},
                     {"title": "2단계 구조 제거", "items": ["EM 72.0", "지연 154ms"]}]},
    ])
    assert "- 22M로 38ms — EM 71.9 유지" in md
    assert "- **조기 종료 제거** EM 72.1 · 지연 71ms" in md
