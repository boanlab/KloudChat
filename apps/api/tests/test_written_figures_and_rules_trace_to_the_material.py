"""Figures, limits and approvals in a deck or report trace to the person's material; an edit
is said done only when the new slide shows it."""

from __future__ import annotations

import json

import pytest

from app.pipeline import check_stages, revise_stages
from app.pipeline.bundle import Bundle, Document, Plan, Request
from app.pipeline.check_stages import DeckGrounding, ground_slides, trace_slides
from app.pipeline.report_stages import mark_policy
from app.pipeline.stage import Context
from app.services import check_skills, checks, deck, revise, stated_policy, verify

#: an executive deck's request: its own figures are the material
EXEC = (
    "사업부장이 임원회의에 보고할 슬라이드 8장 안팎으로 만들어줘. 주제: 2026년 3분기 사업 실적과 "
    "4분기 계획. 수치: 매출 128억(전년 동기 114억, +12%), 영업이익 9.6억(이익률 7.5%, 전분기 "
    "6.8%), 지역별 매출 수도권 71억·영남 33억·호남 14억·기타 10억, 신규 거래처 3곳, 출고 지연 "
    "4일(영남 물류센터 이전 탓). 4분기 목표 매출 140억, 출고 지연 0일. 요청 사항: 영남 물류 "
    "안정화 예산 1.5억 승인."
)

#: the writer's slides as they came back: a margin for a quarter no one gave, under a revenue
#: title; a region table whose shares are worked from the given figures
EXEC_SLIDES = [
    {"id": "s3", "layout": "chart", "title": "매출 및 영업이익 추이",
     "chart": {"kind": "bar", "unit": "%",
               "categories": ["2025 3분기", "2026 2분기", "2026 3분기"],
               "series": [{"name": "이익률", "values": [7.8, 6.8, 7.5]}]},
     "notes": "2026년 3분기 영업이익률은 7.5%로 전분기보다 개선되었습니다."},
    {"id": "s4", "layout": "table", "title": "지역별 매출 구성",
     "rows": [["지역", "매출(억 원)", "비중"], ["수도권", "71", "55.5%"], ["영남", "33", "25.8%"],
              ["호남", "14", "10.9%"], ["기타", "10", "7.8%"]], "notes": ""},
]

#: a proposal agent told to put in no figure without a basis, asked with none given
PROPOSAL = "고객 한솔병원, 환자 대기시간 단축용 예약 앱 제안. 세부 수치 없음, 10장."
AGENT = ("# 역할\nB2B 고객 제안서 슬라이드를 만든다. 문제 - 제안 - 효과 - 견적 - 다음 단계 순서. "
         "근거 없는 수치는 넣지 않는다.")
PROPOSAL_SLIDES = [
    {"id": "p1", "layout": "statement", "title": "디지털 전환 필요성",
     "body": "예약 앱 도입은 대기 시간 412→38ms로 단축합니다"},
    {"id": "p2", "layout": "bullets", "title": "현황",
     "bullets": ["예약 취소율 84%", "주차 104대 부족", "전화 상담 20~40% 감소 예상"]},
    {"id": "p3", "layout": "bullets", "title": "견적",
     "bullets": ["구축비 약 1,500만 원 내외", "운영비 3,000만~5,000만 원"]},
]


def _options(material: str, strong: bool = False, estimates: bool = True) -> dict:
    return {**check_skills.options_for(None, surface="deck"), "person": material,
            "requirements": True, **({"strong": True} if strong else {}),
            **({} if estimates else {"estimates": False})}


def _copy(slides: list[dict]) -> list[dict]:
    return json.loads(json.dumps(slides))


def test_a_chart_plots_only_given_values_under_a_title_naming_them():
    slides, found = ground_slides(_copy(EXEC_SLIDES), material=EXEC, computed=[],
                                  options=_options(EXEC))
    chart = slides[0]["chart"]
    # the quarter whose margin no one gave is off the axis; the given two stay
    assert chart["categories"] == ["2026 2분기", "2026 3분기"]
    assert chart["series"][0]["values"] == [6.8, 7.5]
    # a revenue title over a margin series names the margin
    assert slides[0]["title"] == "이익률 추이"
    # shares worked out from the given figures stand, read with their row as in a report
    assert slides[1]["rows"] == EXEC_SLIDES[1]["rows"]
    assert any(f["text"].startswith("7.8") for f in found)


def test_a_strong_writer_s_chart_is_marked_not_cut_or_retitled():
    slides, _ = ground_slides(_copy(EXEC_SLIDES), material=EXEC, computed=[],
                              options=_options(EXEC, strong=True))
    chart = slides[0]["chart"]
    assert chart["categories"][0] == "2025 3분기 (확인 필요)"
    assert chart["series"][0]["values"] == [7.8, 6.8, 7.5]
    assert slides[0]["title"] == "매출 및 영업이익 추이"


def test_a_chart_left_with_one_given_point_becomes_its_values_with_blanks():
    one = [{"id": "c", "layout": "chart", "title": "이익률",
            "chart": {"kind": "bar", "unit": "%", "categories": ["2025 3분기", "2026 3분기"],
                      "series": [{"name": "이익률", "values": [7.1, 7.5]}]}}]
    slides, _ = ground_slides(one, material=EXEC, computed=[], options=_options(EXEC))
    assert "chart" not in slides[0] and slides[0]["layout"] == "bullets"
    assert slides[0]["bullets"] == ["2025 3분기 이익률: [수치]", "2026 3분기 이익률: 7.5%"]


def test_every_invented_figure_on_a_no_figures_proposal_is_marked():
    # the agent forbids figures with no basis: an estimate (「약 1,500만 원」) is one too
    slides, found = ground_slides(_copy(PROPOSAL_SLIDES), material=PROPOSAL, computed=[],
                                  options=_options(PROPOSAL, estimates=False))
    shown = json.dumps(slides, ensure_ascii=False)
    for written in ("412", "84%", "104대", "20~40%", "1,500만", "5,000만"):
        at = shown.find(written)
        assert at < 0 or "확인 필요" in shown[at:at + 40], written
    assert len(found) >= 6


def _bundle(text: str, slides: list[dict], *, traced: bool = False, agent: str = "",
            model: str = "local/qwen3.8-27b") -> Bundle:
    turn = {"traced": traced, "check_keys": [],
            "writer": {"untrusted_context": [], "trusted_context": [agent] if agent else []}}
    return Bundle(request=Request(text=text, model=model, api_key="k", turn=turn),
                  document=Document(sections=slides))


def test_the_deck_check_runs_on_the_request_s_figures_and_on_a_no_invention_instruction():
    assert not DeckGrounding().skip(_bundle(EXEC, _copy(EXEC_SLIDES)))
    assert not DeckGrounding().skip(_bundle(PROPOSAL, _copy(PROPOSAL_SLIDES), agent=AGENT))
    # the same agent without that instruction, and a request with no data of its own
    assert DeckGrounding().skip(_bundle("AI 트렌드 발표 10장", _copy(PROPOSAL_SLIDES),
                                        agent="# 역할\n제안서 슬라이드를 만든다."))
    # a lecture's worked example is not data with measures
    assert DeckGrounding().skip(_bundle(
        "운영체제 50분 강의 12장. 예제 P1: 0, 7, 3\nP2: 2, 4, 1", _copy(PROPOSAL_SLIDES)))


@pytest.mark.asyncio
async def test_slides_with_untraced_values_go_back_to_the_writer_once(monkeypatch):
    asked: list[dict] = []

    async def rewrite_slide(**kwargs):
        asked.append(kwargs)
        target = next(s for s in kwargs["slides"] if s["id"] == kwargs["target_id"])
        if target["id"] == "p2":
            # a better slide: the figures left out
            return {**target, "bullets": ["예약 취소가 잦음", "주차 공간 부족"]}, {
                "inputTokens": 10, "outputTokens": 5}
        # a worse one: kept as it was
        return {**target, "body": "대기 시간 412→38ms, 취소율 90%"}, {
            "inputTokens": 10, "outputTokens": 5}

    monkeypatch.setattr(check_stages.deck_rules, "rewrite_slide", rewrite_slide)
    bundle = _bundle(PROPOSAL, _copy(PROPOSAL_SLIDES), agent=AGENT)
    bundle.usage = {"inputTokens": 0, "outputTokens": 0}
    events = [e async for e in DeckGrounding().run(bundle, Context())]
    slides = bundle.document.sections
    assert {a["target_id"] for a in asked} == {"p1", "p2", "p3"}
    assert all(a["keep_layout"] and "38ms" in a["note"] for a in asked if a["target_id"] == "p1")
    assert slides[1]["bullets"] == ["예약 취소가 잦음", "주차 공간 부족"]
    # the rewrite holding more invented values was not kept; its figures are marked
    assert "90%" not in json.dumps(slides, ensure_ascii=False)
    assert "확인 필요" in slides[0]["body"]
    assert bundle.usage["inputTokens"] == 30
    assert any(e.get("type") == "step" for e in events)


def test_trace_lists_a_chart_title_that_names_another_measure():
    traced = trace_slides(_copy(EXEC_SLIDES), material=EXEC, computed=[],
                          options=_options(EXEC))
    assert any("does not name" in v for v in traced[0])
    assert deck.chart_title_mismatch({"title": "지역별 매출", "chart": {
        "categories": ["수도권", "영남"], "series": [{"name": "매출(억 원)", "values": [71, 33]}]}}
    ) is False


#: a weekly memo that asks for approval and states no limit
MEMO = (
    "아래 이번 주 메모로 팀장님께 올릴 주간 업무 보고서를 만들어줘.\n"
    "- 월: 신규 거래처 '대성유통' 1차 미팅. 월 발주 예상 3,200만원. 단가 협상 잔여(5% 인하 요구).\n"
    "- 목: 대성유통 견적서 발송. 경쟁사 A사가 동일 품목 4% 낮은 단가 제시한 것으로 파악됨.\n"
    "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), 물류팀 출고 SLA 협의 자리 마련."
)

#: the report as the writer wrote it: a limit, a table verdict and a commitment the memo lacks
WEEKLY = [
    {"heading": "주요 이슈 및 결정 필요 사항", "content": (
        "다음 두 가지 사안에 대한 결정을 요합니다. 내부 허용 한도는 최대 4%까지이며, 경쟁사 "
        "A사의 4% 저가 제안이 맞을 경우 협상 여력이 제한적입니다.\n\n"
        "| 안 | 인하율 | 비고 |\n|---|---|---|\n"
        "| 안 1 | 5% (요구값) | 허용 한도 초과 |\n| 안 2 | 4% (허용 최대치) | 허용 범위 내 |\n\n"
        "```callout\n승인 없이는 진행하지 않습니다\n"
        "내부 단가 인하 폭에 대한 결재 완료 전에는 대성유통 최종 견적서를 발송하지 않습니다."
        "\n```\n\n"
        "단가 인하를 최대 4%까지 허용할지 결재를 요청드립니다.")},
]


def test_a_limit_or_approval_the_memo_only_asks_about_is_found():
    found = stated_policy.findings(WEEKLY, MEMO)
    pieces = [piece for _, piece, _ in found]
    assert any(p.startswith("내부 허용 한도는") for p in pieces)
    assert any("허용 한도 초과" in p for p in pieces)
    assert any("허용 범위 내" in p for p in pieces)
    assert "승인 없이는 진행하지 않습니다" in pieces
    assert any(p.startswith("내부 단가 인하 폭에 대한 결재 완료 전에는") for p in pieces)
    # the request itself, and a material that does set the limit, are left alone
    assert not any("결재를 요청드립니다" in p for p in pieces)
    assert not stated_policy.untraced("내부 허용 한도는 최대 4%입니다.",
                                      "본부 전결 규정: 단가 인하 허용 한도 4%")
    # a condition and a technical limit are no settled rule
    assert not stated_policy.untraced(
        "사내 정책상 모바일 앱 설치가 필수적이라면 이 대안은 적용이 어렵습니다.", MEMO)
    assert not stated_policy.untraced("라이선스의 동시 접속 한도를 계약서에 명시해야 합니다.", MEMO)
    said = stated_policy.untraced("안 1은 팀장님 결재권 한도를 넘어섭니다.", MEMO)
    assert said == ["결재권 한도"]


@pytest.mark.asyncio
async def test_a_stated_limit_goes_back_once_and_what_stands_is_marked():
    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        items = json.loads(messages[0]["content"].rsplit("Items:\n", 1)[1])
        # the first fixed, the others answered as written (a messy writer)
        return json.dumps([
            "단가 인하 폭은 결재가 필요합니다." if i == 0 else item["sentence"]
            for i, item in enumerate(items)], ensure_ascii=False), {}

    doc = verify.Doc(json.loads(json.dumps(WEEKLY)), MEMO, MEMO)
    first, _ = await checks.stated_rules(doc)
    assert first and all(f.code == "unsourced_policy" for f in first)
    sections, still, _ = await verify.verify_and_repair(
        doc, [checks.stated_rules], complete=complete, model="m", api_key="k")
    marked = mark_policy(sections, still)[0]["content"]
    assert "내부 허용 한도는" not in marked
    assert "| 허용 한도 초과 (확인 필요) |" in marked
    assert "| 4% (허용 최대치) (확인 필요) | 허용 범위 내 (확인 필요) |" in marked
    assert "승인 없이는 진행하지 않습니다 (확인 필요)" in marked
    assert "발송하지 않습니다 (확인 필요)." in marked
    assert "결재를 요청드립니다." in marked and "요청드립니다 (확인" not in marked


def test_each_slide_reads_its_own_part_of_a_many_slide_instruction():
    typed = "3장을 표로 바꿔줘. 그리고 8장 그림은 빼줘."
    assert revise.slide_clause(typed, 2) == "3장을 표로 바꿔줘."
    assert revise.slide_clause(typed, 7) == "8장 그림은 빼줘."
    assert revise.only_removes_picture("8장 그림은 빼줘.")
    assert not revise.only_removes_picture("8장 그림은 빼고 제목을 줄여줘")
    assert revise.slide_clause("표로 바꿔줘", 4) == "표로 바꿔줘"
    assert deck.requested_layout(revise.slide_clause(typed, 7)) is None


@pytest.mark.asyncio
async def test_a_metrics_slide_asked_into_a_table_is_drawn_as_a_table(monkeypatch):
    async def complete(model, messages, api_key, max_tokens=0, **kwargs):
        return json.dumps({"rows": [["구분", "3분기"], ["매출", "128억"], ["영업이익", "9.6억"]],
                           "notes": "표로 정리했습니다."}, ensure_ascii=False), {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", complete)
    tiles = {"id": "s3", "layout": "metrics", "title": "3분기 핵심 실적 요약",
             "metrics": [["128억", "총 매출"], ["9.6억", "영업이익"]]}
    written, _ = await deck.rewrite_slide(request=EXEC, slides=[tiles], target_id="s3",
                                          model="m", api_key="k", typed="3장을 표로 바꿔줘.")
    assert "metrics" not in written and deck.shown_layout(written) == "table"
    assert deck.unmet_asks(written, "3장을 표로 바꿔줘.") == []
    # the tiles left under a table name are what the renderers draw, and that is said
    stale = {**written, "metrics": tiles["metrics"]}
    assert deck.unmet_asks(stale, "3장을 표로 바꿔줘.") == ["표로 바꾸기"]


@pytest.mark.asyncio
async def test_a_picture_asked_away_goes_and_an_unmet_ask_is_reported(monkeypatch):
    calls: list[dict] = []

    async def rewrite_slide(**kwargs):
        calls.append(kwargs)
        target = next(s for s in kwargs["slides"] if s["id"] == kwargs["target_id"])
        # a writer that changed nothing but the notes
        return {**target, "notes": "다시 썼습니다."}, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    slides = [{"id": f"s{i}", "layout": "bullets", "title": f"{i + 1}장", "bullets": ["가"]}
              for i in range(8)]
    # points that share no columns: not put into a table by code
    slides[2] = {"id": "s2", "layout": "bullets", "title": "3장",
                 "bullets": ["매출은 128억으로 늘었다", "물류 이전이 남았다"]}
    slides[7] = {**slides[7], "image": {"src": "data:image/png;base64,AA", "caption": "다리"}}
    typed = "3장을 표로 바꿔줘. 그리고 8장 그림은 빼줘."
    bundle = revise_stages.bundle_for(
        request=EXEC, instruction=typed, model={"id": "m"}, planner=None, api_key="k",
        is_deck=True, title="보고", data={}, parts=slides, material=[], format_id="",
        plan=None, usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan = Plan(title="보고", revision=revise.Plan(scope="parts", targets=[2, 7],
                                                          note="3장 표로, 8장 그림 제거"))
    bundle.plan.order = [2, 7]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    parts = bundle.document.sections
    # the picture went by hand, with no rewrite and no layout change
    assert [c["target_id"] for c in calls] == ["s2"] and calls[0]["typed"] == "3장을 표로 바꿔줘."
    assert "image" not in parts[7] and parts[7]["layout"] == "bullets"
    # 3장 still draws bullets: said, not passed off, the slide as it was
    assert bundle.document.unmet == ["3장 표로 바꾸기"] and "notes" not in parts[2]
