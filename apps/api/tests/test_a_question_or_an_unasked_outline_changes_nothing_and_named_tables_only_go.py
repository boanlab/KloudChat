"""A question or a message asking no change (「설명만 해줘」, 「don't change anything」) changes
nothing; an outline reading of words with no edit in them is asked back, not rewritten; and an
edit takes out only the tables it names, never a look-alike table the document already had."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_an_edit_reply_says_each_ask_done_or_not import CHARTED

from app.pipeline import revise_stages
from app.pipeline.stage import Context, Stop
from app.services import revise


@pytest.mark.parametrize("words", [
    "이 슬라이드의 핵심 메시지가 뭐야? 설명만 해줘.",
    "What is the main takeaway of slide 2? Just answer, don't change anything.",
    "4장은 바꾸지 말고, 이 수치가 왜 나왔는지 알려줘?",
    "Why does slide 3 say 64%?",
])
def test_no_change_wanted_is_a_question(words):
    assert revise.asks_question(words)


@pytest.mark.parametrize("words", [
    "Can you make slide 3 a table?", "3장을 표로 바꿔줄래?",
    "4장은 그대로 두고 5장을 표로 바꿔줘", "Shorten slide 2.",
])
def test_an_edit_is_no_question(words):
    assert not revise.asks_question(words)


def _deck(typed: str, plan: revise.Plan | None = None):
    return revise_stages.bundle_for(
        request="임원회의 보고 슬라이드", instruction=typed, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[dict(s) for s in CHARTED], material=[], plan=plan, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


async def _stopped_with(bundle) -> tuple[str, list[dict]]:
    events = []
    with pytest.raises(Stop) as stop:
        async for event in revise_stages.Route().run(bundle, Context()):
            events.append(event)
    return str(stop.value), events


@pytest.mark.asyncio
async def test_a_question_rewrites_no_slide_even_when_planned_for_all(monkeypatch):
    whole = revise.Plan(scope="whole", targets=list(range(len(CHARTED))), note="전체")
    bundle = _deck("이 슬라이드의 핵심 메시지가 뭐야? 설명만 해줘.", whole)
    reason, events = await _stopped_with(bundle)
    assert reason == "a question" and bundle.document.sections == CHARTED
    assert not [e for e in events if e["type"] == "slide"]
    bundle = _deck("What is the main takeaway of slide 2? Just answer, don't change anything.",
                   revise.Plan(scope="parts", targets=[1], note="x"))
    _, events = await _stopped_with(bundle)
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert text.startswith("This reads as a question, so nothing was changed.")


@pytest.mark.asyncio
async def test_an_outline_reading_of_words_with_no_edit_is_asked_back(monkeypatch):
    from app.models.workspace import ArtifactKind
    from app.routers import sessions as sessions_router

    class Db:
        async def get(self, _model, _id):
            return SimpleNamespace(title="보고", kind=ArtifactKind.deck,
                                   data={"slides": [{"title": s["title"]} for s in CHARTED]})

    async def planner(**_kwargs):
        return revise.Plan(scope="outline", note="흐름을 다시 잡는다")

    monkeypatch.setattr(revise, "plan", planner)
    ask = "발표 흐름이 좀 어색한 느낌이야"
    plan, _ = await sessions_router._revision_plan(
        Db(), SimpleNamespace(artifact_id="a1"), instruction=ask, model={"id": "m"},
        api_key="k")
    assert plan.scope == "ask" and not plan.restructures and not plan.revises
    bundle = _deck(ask, plan)
    reason, events = await _stopped_with(bundle)
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert reason == "what to change" and bundle.document.sections == CHARTED
    assert text == "바꾼 것이 없습니다. 어디를 어떻게 고칠지 알려 주세요. 예: 「3장을 표로 바꿔줘」"


OPTIONS = ("| 안 | 인하 폭 | 비고 |\n| --- | --- | --- |\n| A안 | 5% | 상대 측 요구 수치 |\n"
           "| B안 | 4% | {note} |")
REPORT = [
    {"id": "s0", "heading": "1. 금주 실적", "content": "**표 1. 실적**\n\n| 항목 | 값 |\n"
     "| --- | --- |\n| 매출 | 41.2억 |\n| 목표 | 40억 |"},
    {"id": "s1", "heading": "2. 금주 진행 현황", "content": "협상이 진행 중입니다(표 2).\n\n"
     "**표 2. 협상 수치**\n\n| 항목 | 값 |\n| --- | --- |\n| 발주 | 3,200만원 |\n"
     "| 요구 | 5% |"},
    {"id": "s2", "heading": "3. 이슈", "content": "검수가 필요합니다(표 3).\n\n"
     "**표 3. 대성유통 단가 인하 안**\n\n" + OPTIONS.format(note="경쟁사 A 단가 기준")},
    {"id": "s3", "heading": "4. 결재 요청", "content": "표 4의 두 안 중 하나를 선택해 "
     "주십시오.\n\n**표 4. 대성유통 단가 인하 결재 안**\n\n"
     + OPTIONS.format(note="최대 4%까지 허용 여부 결재 요청")},
]


@pytest.mark.asyncio
async def test_a_look_alike_table_the_report_already_had_stays_through_the_edit(monkeypatch):
    async def complete(_model, _messages, _key, _limit):
        return ("협상이 진행 중입니다.\n매출은 41.2억입니다.\n결재가 필요합니다.",
                {"inputTokens": 1, "outputTokens": 1})

    async def no_planner(**_kwargs):
        raise AssertionError("each ask names its place")

    monkeypatch.setattr(revise_stages.revise, "plan", no_planner)
    monkeypatch.setattr(revise_stages._report(), "_complete", complete)
    bundle = revise_stages.bundle_for(
        request="주간 업무 보고", instruction="표 2와 그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘.",
        model={"id": "m"}, planner=None, api_key="k", is_deck=False, title="주간 업무 보고",
        data={}, parts=[dict(p) for p in REPORT], material=[], plan=None, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    for stage in (revise_stages.Route(), revise_stages.Widen(), revise_stages.Rewrite()):
        _ = [e async for e in stage.run(bundle, Context())]
    texts = [p["content"] for p in bundle.document.sections]
    assert "| 발주 |" not in texts[1]
    # both option tables stay, numbered on, the sentence pointing at the second follows it
    assert "**표 2. 대성유통 단가 인하 안**" in texts[2]
    assert "**표 3. 대성유통 단가 인하 결재 안**" in texts[3] and "| B안 | 4% |" in texts[3]
    assert texts[3].startswith("표 3의 두 안 중 하나를 선택해 주십시오.")
