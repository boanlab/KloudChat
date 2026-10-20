"""An edit takes away the numbered tables and figures it is asked to, by code, keeps the
numbers and references straight, asks back when 「그림」 on a deck can only mean a chart, and the
reply says each ask as done, not found or not shown, by part number."""

from __future__ import annotations

import pytest

from app.pipeline import revise_stages, save_stages
from app.pipeline.bundle import Document
from app.pipeline.stage import Context, Stop
from app.services import deck, revise

INSTRUCTION = ("2절의 표 2와 그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘. 내부 허용 한도가 4%라는 "
               "표현은 메모에 없으니 결재 요청으로 고쳐줘.")
WEEKLY = [
    {"id": "s0", "heading": "1. 주간 결론 및 주요 결과",
     "content": "대성유통은 5% 인하를 요구하고 있습니다. 내부 허용 범위는 최대 4%까지입니다.\n\n"
                "**표 1. 단가 안**\n\n| 안 | 금액(월) |\n| --- | --- |\n"
                "| A: 5% 인하 | 3,040만원 |\n"
                "| B: 4% 인하 | 3,072만원 |\n\n표 1에서 보듯 두 안의 차이는 월 32만원입니다."},
    {"id": "s1", "heading": "2. 주간 진행 현황",
     "content": "**표 2. 주간 진행 현황**\n\n| 사업 | 상태 |\n| --- | --- |\n"
                "| 대성유통 | 진행 중 |\n| 한솔마트 | 완료 |\n\n"
                "표 2의 네 가지 업무가 이번 주 핵심 진행 사항입니다. 한솔마트 "
                "반품 420만원은 9월 물류센터 이전 시 출고 지연이 원인입니다."},
    {"id": "s2", "heading": "3. 현재 이슈 및 협의 사항",
     "content": "물류팀과 출고 SLA 협의 자리가 필요합니다. CRM 병합 작업은 다음 주 수요일 완료 "
                "예정입니다(그림 1).\n\n```mermaid\nflowchart LR\n"
                "  a(\"결재\") --> b(\"협상\")\n```"
                "\n\n*그림 1. 이슈 대응 및 결정 경로*"},
    {"id": "s3", "heading": "4. 다음 주 계획",
     "content": "**표 3. 다음 주 계획**\n\n| 요일 | 내용 |\n| --- | --- |\n"
                "| 화요일 | 한솔마트 방문 |\n"
                "| 목요일 | 대성유통 2차 미팅 |\n\n표 3이 다음 주 일정을 담고 있습니다."},
]


def _report_bundle(typed: str, plan: revise.Plan | None = None):
    return revise_stages.bundle_for(
        request="이번 주 메모로 주간 업무 보고서를 만들어줘.", instruction=typed,
        model={"id": "m"}, planner=None, api_key="k", is_deck=False, title="주간 업무 보고",
        data={}, parts=[dict(p) for p in WEEKLY], material=[], plan=plan, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


async def _no_planner(**_kwargs):
    raise AssertionError("the planner is not asked what code already took away")


def test_the_asked_tables_and_figures_are_read_from_the_sentence():
    assert revise.numbered_removals(INSTRUCTION) == [("표", 2), ("그림", 1)]
    assert revise.numbered_removals("표 3 삭제해 주세요") == [("표", 3)]
    assert revise.numbered_removals("표 2를 더 자세히 써 줘") == []
    # more than a removal asked of the table: the rewrite reads it before it goes
    assert revise.numbered_removals("표 2는 빼고 그 내용은 글로 풀어 써 줘") == []
    assert revise.numbered_removals("표 2는 빼고, 그 내용은 본문에 넣어 줘") == []
    assert revise.without_numbered_removals(INSTRUCTION) == (
        "맨 앞에 3줄 요약을 넣어줘. 내부 허용 한도가 4%라는 표현은 메모에 없으니 "
        "결재 요청으로 고쳐줘.")
    assert revise.without_numbered_removals("표 2는 빼 줘") == ""


@pytest.mark.asyncio
async def test_the_asked_table_and_figure_go_by_hand_and_the_rest_is_asked_of_its_part(
        monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)
    # the planner read the whole sentence and sent the removal to 2절
    bundle = _report_bundle(INSTRUCTION, revise.Plan(scope="parts", targets=[0, 1], note="빼기"))
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    doc = bundle.document
    assert doc.removed == ["2절의 표 2", "3절의 그림 1"] and doc.missed == []
    second, third = doc.sections[1]["content"], doc.sections[2]["content"]
    assert "|" not in second and "표 2" not in second
    # the sentence about the gone table goes, the others stay
    assert second == "한솔마트 반품 420만원은 9월 물류센터 이전 시 출고 지연이 원인입니다."
    assert "mermaid" not in third and "그림 1" not in third
    assert third.endswith("CRM 병합 작업은 다음 주 수요일 완료 예정입니다.")
    # the rest of the sentence goes to the part it names; 2절 is not rewritten
    plan = bundle.plan.revision
    assert plan.targets == [0] and plan.summary == 3 and 1 not in plan.clauses
    assert plan.typed.startswith("맨 앞에 3줄 요약")
    # numbers close up with the reference: 표 3 is 표 2 now, its particle follows
    revise_stages._tidy_tables(bundle)
    last = doc.sections[3]["content"]
    assert "**표 2. 다음 주 계획**" in last and "표 2가 다음 주 일정을" in last
    assert "표 3" not in last
    assert "**표 1. 단가 안**" in doc.sections[0]["content"]


@pytest.mark.asyncio
async def test_a_removal_only_edit_needs_no_model_and_says_what_was_not_there(monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)
    bundle = _report_bundle("표 2와 표 5는 빼 줘")
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    doc = bundle.document
    assert doc.by_hand and doc.changed == 1
    assert doc.removed == ["2절의 표 2"] and doc.missed == ["표 5"]
    assert save_stages.revision_reply(doc, bundle.plan.revision, is_deck=False) == (
        "2절의 표 2를 뺐습니다. 다만 요청한 표 5는 문서에서 찾지 못했습니다. 다시 요청해 주세요.")
    # nothing there at all: told so, nothing saved as changed
    bundle = _report_bundle("그림 3은 지워 줘")
    events = []
    with pytest.raises(Stop):
        async for event in revise_stages.Route().run(bundle, Context()):
            events.append(event)
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert text == "요청한 그림 3은 문서에서 찾지 못해 바꾼 것이 없습니다."


def test_the_reply_names_each_ask_and_the_parts_it_rewrote():
    doc = Document(removed=["2절의 표 2", "3절의 그림 1"], touched=[0], changed=4)
    plan = revise.Plan(scope="parts", targets=[0], summary=3)
    assert save_stages.revision_reply(doc, plan, is_deck=False) == (
        "2절의 표 2, 3절의 그림 1을 뺐습니다. 맨 앞에 3줄 요약을 넣었습니다. 1절을 고쳤습니다.")
    # a summary that did not come is not said as put in
    doc = Document(touched=[0, 2], changed=2, missed=["3줄 요약"])
    assert save_stages.revision_reply(doc, plan, is_deck=False) == (
        "1·3절을 고쳤습니다. 다만 요청한 3줄 요약은 문서에서 찾지 못했습니다. 다시 요청해 주세요.")
    doc = Document(touched=[0, 3, 6, 7, 8], changed=5)
    assert save_stages.revision_reply(doc, None, is_deck=False) == "1·4·7~9절을 고쳤습니다."
    # a term swapped by hand: the count of places
    assert save_stages.revision_reply(Document(changed=5), None,
                                      is_deck=False) == "5곳을 고쳤습니다."
    # a slide asked as a table but still drawn as tiles is not counted as done
    doc = Document(touched=[2, 4], changed=2, unmet=["3장 표로 바꾸기"])
    assert save_stages.revision_reply(doc, None, is_deck=True) == (
        "5장을 고쳤습니다. 다만 요청한 3장 표로 바꾸기는 반영되지 않았습니다. 다시 요청해 주세요.")


CHARTED = [
    {"id": "s0", "layout": "title", "title": "2026년 3분기 사업 실적 및 4분기 계획 보고"},
    {"id": "s1", "layout": "bullets", "title": "보고 순서", "bullets": ["실적", "계획"]},
    {"id": "s2", "layout": "table", "title": "3분기 핵심 성과 요약",
     "rows": [["항목", "실적"], ["매출", "128억"]]},
    {"id": "s3", "layout": "chart", "title": "매출 및 영업이익 추이",
     "chart": {"kind": "bar", "unit": "억 원", "categories": ["전년 동기", "2026년 3분기"],
               "series": [{"name": "매출", "values": [114, 128]},
                          {"name": "영업이익", "values": [None, 9.6]}]},
     "notes": "차트에서 보듯 매출이 늘었습니다. 전년 동기 114억 원 대비 12% 증가했습니다."},
    {"id": "s4", "layout": "bullets", "title": "지역별 매출 구성",
     "bullets": ["수도권이 매출의 55% 차지"],
     "chart": {"kind": "pie", "unit": "억원", "categories": ["수도권", "영남"],
               "series": [{"name": "매출", "values": [71.0, 33.0]}]},
     "notes": "수도권 비중이 가장 높습니다."},
]


def _deck_bundle(typed: str, plan: revise.Plan | None = None):
    return revise_stages.bundle_for(
        request="임원회의 보고 슬라이드", instruction=typed, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[dict(s) for s in CHARTED], material=[], plan=plan, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


@pytest.mark.asyncio
async def test_a_picture_asked_away_from_a_deck_of_charts_is_asked_back_not_rewritten(
        monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)
    # the planner had sent 「그림 빼줘」 to the two chart slides
    planned = revise.Plan(scope="parts", targets=[3, 4], note="그림 빼기")
    bundle = _deck_bundle("그림 빼줘.", planned)
    events = []
    with pytest.raises(Stop):
        async for event in revise_stages.Route().run(bundle, Context()):
            events.append(event)
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert text == ("슬라이드에 뺄 그림이 없어 바꾼 것이 없습니다. 차트는 4장 「매출 및 영업이익 "
                    "추이」, 5장 「지역별 매출 구성」에 있습니다. 차트를 빼려면 "
                    "「4장 차트 빼줘」처럼 알려 주세요.")
    assert bundle.document.sections == CHARTED
    assert not [e for e in events if e["type"] == "slide"]


@pytest.mark.asyncio
async def test_a_chart_asked_away_goes_by_hand_and_its_notes_stop_pointing_at_it(monkeypatch):
    monkeypatch.setattr(revise_stages.revise, "plan", _no_planner)

    async def no_rewrite(**_kwargs):
        raise AssertionError("a chart taken away is not a rewrite")

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", no_rewrite)
    bundle = _deck_bundle("4장 차트 빼줘", revise.Plan(scope="parts", targets=[3, 4], note="x"))
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    assert bundle.plan.revision.targets == [3]
    bundle.plan.order = [3]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    slide = bundle.document.sections[3]
    assert "chart" not in slide and slide["layout"] == "bullets"
    # the chart's own values, nothing else
    assert slide["bullets"] == ["전년 동기: 매출 114억 원",
                                "2026년 3분기: 매출 128억 원, 영업이익 9.6억 원"]
    assert slide["notes"] == "전년 동기 114억 원 대비 12% 증가했습니다."
    assert bundle.document.sections[4] == CHARTED[4]
    assert bundle.document.unmet == []
    assert save_stages.revision_reply(bundle.document, bundle.plan.revision, is_deck=True) == (
        "4장의 차트를 뺐습니다.")


def test_notes_do_not_say_a_slide_lacks_what_it_shows():
    notes = ("본 슬라이드에서는 차트를 사용하지 않고 텍스트로만 서술합니다. 매출은 128억 원으로 "
             "12% 늘었습니다.")
    assert deck.notes_agree(CHARTED[3], notes) == "매출은 128억 원으로 12% 늘었습니다."
    # no chart on the slide: the sentence is true and stays
    assert deck.notes_agree(CHARTED[1], notes) == notes
