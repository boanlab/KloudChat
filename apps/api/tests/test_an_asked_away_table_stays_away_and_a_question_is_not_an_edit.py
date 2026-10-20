"""A numbered table asked away stays away through the same edit's rewrite, an approval the
material only asks for is not stated as a condition, a slide drawn as a diagram keeps a
description of the figure (not the ask) and whole marker labels, and a question on a document's
screen is told how to ask it, not called a new-document request."""

from __future__ import annotations

import time

import pytest
from test_an_edit_reply_says_each_ask_done_or_not import CHARTED, WEEKLY

from app.pipeline import revise_stages
from app.pipeline.stage import Context, Stop
from app.services import deck, flow_layout, revise, stated_policy

MEMO = ("요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부), "
        "물류팀 출고 SLA 협의 자리 마련.")
ISSUES = [
    WEEKLY[0],
    {"id": "s1", "heading": "2. 이슈와 의사결정 요청",
     "content": "대성유통 단가 인하 폭에 대한 결재가 필요합니다(표 2).\n\n"
                "**표 2. 인하 안**\n\n| 안 | 인하율 |\n| --- | --- |\n"
                "| 현 단가 유지 | 0% |\n| 최대 허용 인하 적용 | 4% |\n\n"
                "물류팀과 출고 SLA 협의 자리를 요청합니다."},
    WEEKLY[3],
]
#: the rewrite brings a new table (named 표 2 again) into the part 표 2 just left
REWRITTEN = ("**표 2. 이슈와 의사결정 요청**\n\n| 구분 | 결정 내용 |\n| --- | --- |\n"
             "| 단가 인하 폭 | 최대 4% 인하 허용 여부 결재 |\n"
             "| 물류 출고 SLA | 협의 자리 마련 |\n\n"
             "대성유통 단가 인하 폭은 최대 4%까지 허용할지 결재를 요청합니다(표 2). "
             "해당 결재가 없으면 2차 미팅에서 단가를 확정할 수 없습니다.\n\n"
             "물류팀과 출고 SLA 협의 자리를 요청합니다.")


def _bundle(typed: str):
    return revise_stages.bundle_for(
        request=f"주간 업무 보고서를 만들어줘.\n{MEMO}", instruction=typed, model={"id": "m"},
        planner=None, api_key="k", is_deck=False, title="주간 업무 보고", data={},
        parts=[dict(p) for p in ISSUES], material=[], plan=None, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


@pytest.mark.asyncio
async def test_a_table_asked_away_is_not_brought_back_by_the_rewrite_of_its_part(monkeypatch):
    async def planner(**_kwargs):
        return revise.Plan(scope="parts", targets=[1], note="한도를 결재 요청으로")

    notes: list[str] = []

    async def rewrite_section(**kwargs):
        notes.append(kwargs["note"])
        return REWRITTEN, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.revise, "plan", planner)
    monkeypatch.setattr(revise_stages._report(), "rewrite_section", rewrite_section)
    bundle = _bundle("2절의 표 2는 빼고, 4%가 한도처럼 보이는 표현은 결재 요청으로 고쳐줘.")
    _ = [e async for e in revise_stages.Route().run(bundle, Context())]
    doc = bundle.document
    assert doc.removed == ["2절의 표 2"] and doc.emptied == {"s1": ["표 2"]}
    _ = [e async for e in revise_stages.Widen().run(bundle, Context())]
    _ = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    assert "표 2 taken out of this section: add no table or figure" in notes[0]
    second = doc.sections[1]["content"]
    # no table there again, no reference to it, the rest of the rewrite kept
    assert "|" not in second and "표 2" not in second
    assert "최대 4%까지 허용할지 결재를 요청합니다." in second
    # the condition the memo never states is marked, not stated as settled
    assert "단가를 확정할 수 없습니다 (확인 필요)." in second
    # 표 3 of the plan closes up to 표 2
    assert "**표 2. 다음 주 계획**" in doc.sections[2]["content"]


def test_only_a_table_the_part_did_not_keep_goes_and_an_asked_one_stays():
    kept = ("**표 1. 단가 안**\n\n| 안 | 금액 |\n| --- | --- |\n| A | 3,040만원 |\n"
            "| B | 3,072만원 |")
    edited = kept.replace("3,072만원", "3,070만원")
    added = ("\n\n**표 2. 새 표**\n\n| 구분 | 값 |\n| --- | --- |\n| C | 1 |\n\n"
             "표 2는 새로 정리한 표입니다.")
    out = revise.drop_added(kept, edited + added, ["표 2"])
    # the kept table, even edited, stays; the added one and the sentence about it go
    assert out == edited
    assert revise.drop_added(kept, edited + added, ["표 2"], "2절을 표로 정리해 줘") == (
        edited + added)
    # a figure taken out: tables are not touched
    assert revise.drop_added(kept, edited + added, ["그림 1"]) == edited + added


def test_an_approval_set_as_a_condition_the_material_never_states_is_traced():
    said = stated_policy.untraced(
        "해당 결재가 없으면 2차 미팅에서 단가를 확정할 수 없습니다.", MEMO)
    assert said == ["결재가 없으면"]
    assert stated_policy.untraced("승인이 나지 않으면 계약을 진행하지 않습니다.", MEMO) == [
        "승인이 나지 않으면"]
    # the material saying it: no mark
    assert stated_policy.untraced("결재가 없으면 발송할 수 없습니다.",
                                  "결재가 없으면 견적서를 발송할 수 없음") == []


SLIDES = [
    {"id": "a", "layout": "title", "title": "Kubernetes 구조와 동작 원리"},
    {"id": "b", "layout": "bullets", "title": "Kubernetes 기본 개념과 용어",
     "bullets": ["kubectl create pod: 명령 입력", "API Server: 모든 요청 중앙 관문",
                 "etcd: 클러스터 상태 데이터 저장소", "Desired State vs Actual State"]},
]


@pytest.mark.asyncio
async def test_a_slide_drawn_as_a_diagram_describes_the_figure_not_the_ask(monkeypatch):
    async def make(planned, *, model, api_key, slide, accent=""):
        source = ("flowchart LR\n  cmd[kubectl create pod] --> api(API Server)\n"
                  "  api --> etcd[(etcd)]\n  etcd --> d[Desired State]")
        return ({"figure": planned.figure, "description": planned.description,
                 "source": source, "caption": planned.caption, "key": "k" * 16},
                {"inputTokens": 1, "outputTokens": 1}, b"\x89PNG")

    async def render_png(*_args, **_kwargs):
        return None

    monkeypatch.setattr(deck.diagrams, "make", make)
    monkeypatch.setattr(deck.diagram_render, "render_png", render_png)
    written, _ = await deck.figure_slide(
        request="Kubernetes 강의", slides=SLIDES, target_id="b",
        instruction="2장을 구조도로 바꿔줘.", model="m", api_key="k")
    description = written["diagram"]["description"]
    assert "구조도로 바꿔" not in description
    assert description.startswith("Kubernetes 기본 개념과 용어: kubectl create pod: 명령 입력")


def test_a_back_edge_marker_shows_its_target_in_whole_words_and_fits_them():
    assert flow_layout.marker_text("", "Desired State") == "→ Desired State"
    assert flow_layout.marker_text("", "Controller Manager") == "→ Controller Manager"
    assert flow_layout.marker_text("비교", "Controller Manager Loop") == "비교 → Controller…"
    text = flow_layout.marker_text("", "클러스터 상태 데이터")
    assert text == "→ 클러스터 상태 데이터"
    # the box takes the words on one line at 8 pt
    assert flow_layout.marker_width(text) >= 4.8 * 2 + 8.0 * 9 + 14


def _deck_bundle(typed: str):
    return revise_stages.bundle_for(
        request="임원회의 보고 슬라이드", instruction=typed, model={"id": "m"}, planner=None,
        api_key="k", is_deck=True, title="보고", data={},
        parts=[dict(s) for s in CHARTED], material=[], plan=None, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


@pytest.mark.asyncio
async def test_a_question_on_the_deck_screen_is_told_how_to_ask_not_called_a_new_document(
        monkeypatch):
    async def planner(**_kwargs):
        return revise.Plan()  # the planner reads no change in a question

    monkeypatch.setattr(revise_stages.revise, "plan", planner)
    bundle = _deck_bundle("지금 슬라이드가 몇 장이야?")
    events = []
    with pytest.raises(Stop):
        async for event in revise_stages.Route().run(bundle, Context()):
            events.append(event)
    text = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    assert "새로 쓰려면" not in text and text.startswith("질문으로 보여 바꾼 것이 없습니다.")
    assert "「3장을 표로 바꿔줘」처럼" in text and "챗에 첨부해 물어보세요" in text
    assert bundle.document.sections == CHARTED
    # a request that reads as none is still told the old way
    bundle = _deck_bundle("다른 회사 소개 자료")
    events = []
    with pytest.raises(Stop):
        async for event in revise_stages.Route().run(bundle, Context()):
            events.append(event)
    assert "새로 쓰려면" in "".join(e.get("text", "") for e in events if e["type"] == "delta")


def test_a_question_is_told_from_an_ask():
    assert revise.asks_question("지금 슬라이드가 몇 장이야?")
    assert revise.asks_question("이 보고서 결론이 뭐야")
    assert revise.asks_question("발표자는 누구인가요")
    assert not revise.asks_question("3장을 표로 바꿔 줄 수 있나요?")
    assert not revise.asks_question("결론을 더 짧게 해줘")
    assert not revise.asks_question("이 표를 어떻게 바꿔 주실 수 있나요")
    assert not revise.asks_question("다른 회사 소개 자료")
    started = time.monotonic()
    revise.asks_question("몇" + " " * 50000 + "x")
    assert time.monotonic() - started < 0.5
