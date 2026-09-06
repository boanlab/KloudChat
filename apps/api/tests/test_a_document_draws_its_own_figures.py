"""A deck or report draws structure, flow, comparison and concept figures for itself.

The planner reads the drafted parts and names the places where a figure says more than the
words; `diagram.draw` writes the mermaid in the house style; the browser rasterises it. No
image model is involved, so nothing is proposed on a card or charged beyond the writer.
"""

from __future__ import annotations

import json

import pytest

from app.services import deck, diagram, diagrams
from app.services.report_export import diagram_key

_PLAN = json.dumps(
    [
        {
            "part": 2,
            "figure": "flow",
            "description": "요청이 검색기 → 계획기 → 스타일 조정기를 거쳐 초안이 된다",
            "caption": "생성 흐름",
        },
        {"part": 2, "figure": "method", "description": "같은 장에 두 번째 도식", "caption": "중복"},
        {"part": 4, "figure": "compare", "description": "기존 파이프라인과 제안 파이프라인의 대비"},
        {"part": 1, "figure": "flow", "description": "표지에는 도식이 어울리지 않는다"},
        {"part": 3, "figure": "poster", "description": "모르는 종류의 그림은 버린다"},
        {"part": 5, "figure": "concept", "description": "짧다"},
    ],
    ensure_ascii=False,
)


def test_the_plan_keeps_known_kinds_on_eligible_parts_once_each():
    rows = diagrams.parse(_PLAN, count=6, limit=3, eligible=[1, 2, 3, 4])
    assert [(row.index, row.figure) for row in rows] == [(1, "flow"), (3, "compare")]
    assert rows[0].caption == "생성 흐름"
    assert rows[1].caption == ""


def test_the_plan_is_capped_and_survives_a_chatty_answer():
    chatty = "도식은 다음과 같습니다.\n" + _PLAN + "\n이상입니다."
    assert len(diagrams.parse(chatty, count=6, limit=1, eligible=range(6))) == 1
    assert diagrams.parse("없음", count=6, limit=3, eligible=range(6)) == []
    assert diagrams.parse('{"part": 2}', count=6, limit=3, eligible=range(6)) == []


@pytest.mark.asyncio
async def test_nothing_eligible_means_no_call_and_no_plan():
    async def never(*args):
        raise AssertionError("the planner was called")

    rows, usage = await diagrams.plan(
        parts=[("표지", ""), ("목차", "")],
        eligible=[],
        request="발표",
        model="m",
        api_key="k",
        complete=never,
        slide=True,
    )
    assert rows == [] and usage == {"inputTokens": 0, "outputTokens": 0}


@pytest.mark.asyncio
async def test_the_planner_reads_only_the_eligible_parts_and_a_failure_is_an_empty_plan():
    seen: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        seen.append(messages[-1]["content"])
        return _PLAN, {"inputTokens": 10, "outputTokens": 5}

    rows, usage = await diagrams.plan(
        parts=[("표지", ""), ("생성 흐름", "검색기, 계획기, 스타일 조정기"), ("결과", "표")],
        eligible=[1],
        request="발표",
        model="m",
        api_key="k",
        complete=complete,
        slide=True,
    )
    assert [row.index for row in rows] == [1]
    assert usage == {"inputTokens": 10, "outputTokens": 5}
    assert "[2] 생성 흐름" in seen[0] and "[1] 표지" not in seen[0] and "[3] 결과" not in seen[0]
    assert "생성 흐름 (표 있음)" not in seen[0] and "(표 있음)」이라고 적힌" in seen[0]
    assert "발표 슬라이드" in seen[0] and "12자" in seen[0]

    async def broken(*args):
        raise RuntimeError("upstream")

    rows, usage = await diagrams.plan(
        parts=[("a", "b")],
        eligible=[0],
        request="",
        model="m",
        api_key="k",
        complete=broken,
        slide=False,
    )
    assert rows == [] and usage["inputTokens"] == 0


@pytest.mark.asyncio
async def test_a_made_figure_carries_its_source_caption_and_key(monkeypatch):
    calls: list[dict] = []

    async def draw(**kwargs):
        calls.append(kwargs)
        return (
            "flowchart LR\n  a(검색기) --> b(계획기)",
            "생성 순서",
            {"inputTokens": 3, "outputTokens": 4},
        )

    monkeypatch.setattr(diagram, "draw", draw)
    planned = diagrams.Planned(
        index=1, figure="flow", description="검색기에서 계획기로", caption=""
    )
    made, usage = await diagrams.make(planned, model="m", api_key="k", slide=True)
    assert made["source"].startswith("flowchart LR")
    assert made["caption"] == "생성 순서"
    assert made["key"] == diagram_key(made["source"])
    assert made["figure"] == "flow"
    assert usage == {"inputTokens": 3, "outputTokens": 4}
    assert calls[0]["slide"] is True and calls[0]["figure"] == "flow"
    # The planner's own caption wins when it gave one.
    named = diagrams.Planned(
        index=1, figure="flow", description="검색기에서 계획기로", caption="생성 흐름"
    )
    made, _ = await diagrams.make(named, model="m", api_key="k", slide=False)
    assert made["caption"] == "생성 흐름"


def test_a_report_carries_the_figure_as_a_fence_with_its_caption():
    text = diagrams.fence({"source": "flowchart LR\n  a --> b", "caption": "생성 흐름"})
    assert text == "```mermaid\nflowchart LR\n  a --> b\n```\n\n*그림: 생성 흐름*"
    assert diagrams.fence({"source": "flowchart LR\n  a --> b", "caption": ""}).endswith("```")


def test_a_part_that_already_has_a_table_is_marked_for_the_planner():
    table = "| 기준 | 기존 | 제안 |\n| --- | --- | --- |\n| 지식 | 내재 | 외부 |"
    assert diagrams._has_table(table)
    assert not diagrams._has_table("단계 1 → 단계 2\n- 항목")


def test_the_house_rules_know_comparison_and_slide_sizes():
    assert "compare" in diagram.FIGURES
    paper = diagram._messages("기존과 제안", "compare", "ko")[0]["content"]
    assert "비교도" in paper and "subgraph 둘" in paper and "direction TB" in paper
    assert "6개를 넘으면 `flowchart TB`" in paper
    assert "슬라이드용" not in paper
    slide = diagram._messages("기존과 제안", "compare", "ko", slide=True)[0]["content"]
    assert "슬라이드용" in slide and "7개 이하" in slide


def test_a_slide_with_its_own_figure_narrows_its_words_like_a_large_picture():
    plain = deck._body_width({"layout": "bullets"})
    drawn = deck._body_width({"layout": "bullets", "diagram": {"source": "flowchart LR"}})
    large = deck._body_width(
        {"layout": "bullets", "image": {"src": "data:image/png;base64,AA", "size": "large"}}
    )
    assert drawn == large < plain
    # A title slide keeps its width whatever it carries.
    assert deck._body_width({"layout": "title", "diagram": {"source": "x"}}) == plain


@pytest.mark.asyncio
async def test_a_deck_plans_figures_from_its_draft_and_puts_them_beside_the_words(monkeypatch):
    """`_write_slides` end to end with a fake model: the planner runs once after the draft, a
    chosen slide gets `diagram`, a slide with an approved picture keeps the picture."""
    prompts: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        text = messages[-1]["content"]
        prompts.append(text)
        if "도식의 종류" in text:
            return (
                json.dumps(
                    [
                        {
                            "part": 2,
                            "figure": "flow",
                            "description": "검색기에서 계획기로 이어지는 흐름",
                            "caption": "생성 흐름",
                        },
                        {
                            "part": 3,
                            "figure": "method",
                            "description": "그림이 있는 장에는 도식을 두지 않는다",
                        },
                    ],
                    ensure_ascii=False,
                ),
                {"inputTokens": 1, "outputTokens": 1},
            )
        if max_tokens > 2000:
            raise ValueError("no draft this time")
        if "생성 흐름" in text.split("\n")[0]:
            body = {
                "cards": [
                    ["검색기", "질문과 가까운 문서 조각을 찾는다"],
                    ["계획기", "찾은 조각으로 답의 뼈대를 세운다"],
                    ["스타일 조정기", "독자에 맞춰 문장을 고른다"],
                    ["검토기", "근거 없는 문장을 되돌린다"],
                    ["출력", "최종 답"],
                ],
                "notes": "",
            }
            return json.dumps(body, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        return json.dumps({"bullets": ["첫째", "둘째"], "notes": ""}, ensure_ascii=False), {
            "inputTokens": 1,
            "outputTokens": 1,
        }

    async def make(planned, *, model, api_key, slide):
        assert slide is True
        return {
            "figure": planned.figure,
            "description": planned.description,
            "source": "flowchart LR\n  a(검색기) --> b(계획기)",
            "caption": planned.caption,
            "key": "abcdef0123456789",
        }, {"inputTokens": 2, "outputTokens": 2}

    async def draw(figure, image_model, api_key):
        return {"src": "data:image/png;base64,AAAA", "caption": "그림"}

    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(deck.diagrams, "make", make)
    monkeypatch.setattr(deck, "_draw", draw)

    plan = [
        {"title": "표지", "layout": "title"},
        {"title": "생성 흐름", "layout": "cards"},
        {"title": "결과 사진", "layout": "bullets"},
        {"title": "마무리", "layout": "closing"},
    ]
    events = []
    async for event in deck._write_slides(
        plan=plan,
        title="발표",
        subtitle="",
        accent="#5b5bd6",
        request="논문 발표 슬라이드",
        model="m",
        api_key="k",
        trusted_context=None,
        untrusted_context=None,
        usage={"inputTokens": 0, "outputTokens": 0},
        figures_plan=[{"section": 2, "prompt": "a photo", "caption": "결과"}],
        image_model={"id": "img"},
    ):
        events.append(event)

    final = next(e for e in events if e["type"] == "deck")["slides"]
    assert "diagram" not in final[0]
    assert final[1]["diagram"]["source"].startswith("flowchart LR")
    assert final[1]["diagram"]["caption"] == "생성 흐름"
    assert final[1]["diagram"]["key"] == "abcdef0123456789"
    # The cards slide gave its room to the figure: a few short lines beside it, no shape.
    assert final[1]["layout"] == "bullets" and "cards" not in final[1]
    assert len(final[1]["bullets"]) == 4
    assert final[1]["bullets"][0].startswith("검색기: ")
    assert all(len(line) <= 48 for line in final[1]["bullets"])
    # The picture slide was never offered to the planner and keeps its picture.
    assert final[2]["image"]["src"].startswith("data:image/png")
    assert "diagram" not in final[2]
    planner = next(p for p in prompts if "도식의 종류" in p)
    assert (
        "[2] 생성 흐름" in planner and "[3] 결과 사진" not in planner and "[1] 표지" not in planner
    )
    steps = [(e["id"], e["status"]) for e in events if e["type"] == "step" and e["id"] == "dia1"]
    assert steps == [("dia1", "running"), ("dia1", "done")]


class _Db:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def get(self, model, item_id):
        return self.row if item_id == self.row.id else None

    def add(self, row):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        pass


class _User:
    id = "owner"


def _deck(slide: dict):
    from app.models.workspace import Artifact, ArtifactKind

    return Artifact(
        id="d1", user_id="owner", kind=ArtifactKind.deck, title="덱", data={"slides": [slide]}
    )


_SOURCE = "flowchart LR\n  a(검색기) --> b(계획기)"
_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC"
    "hwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_the_browser_s_raster_becomes_the_slide_picture_for_the_exporters():
    from app.routers import workspace
    from app.schemas.workspace import SlideDiagramPicture

    slide = {
        "id": "s1",
        "title": "생성 흐름",
        "layout": "bullets",
        "diagram": {"source": _SOURCE, "caption": "생성 흐름", "key": diagram_key(_SOURCE)},
    }
    deck_row = _deck(slide)
    db = _Db(deck_row)
    payload = SlideDiagramPicture(slide_id="s1", key=diagram_key(_SOURCE), src=_PNG)

    await workspace.store_slide_diagram("d1", payload, _User(), db)

    stored = deck_row.data["slides"][0]
    assert stored["image"] == {
        "src": _PNG,
        "caption": "생성 흐름",
        "fit": "contain",
        "position": "right",
        "size": "large",
        "diagram": True,
    }
    assert stored["diagram"]["source"] == _SOURCE
    assert db.commits == 1
    # The same raster again changes nothing.
    await workspace.store_slide_diagram("d1", payload, _User(), db)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_stale_key_a_missing_figure_and_a_hand_placed_picture_are_left_alone():
    from app.routers import workspace
    from app.schemas.workspace import SlideDiagramPicture

    good = diagram_key(_SOURCE)
    figure = {"source": _SOURCE, "caption": "생성 흐름", "key": good}

    with pytest.raises(workspace.HTTPException) as stale:
        await workspace.store_slide_diagram(
            "d1",
            SlideDiagramPicture(slide_id="s1", key="0000000000000000", src=_PNG),
            _User(),
            _Db(_deck({"id": "s1", "layout": "bullets", "diagram": figure})),
        )
    assert stale.value.status_code == 409

    with pytest.raises(workspace.HTTPException) as none:
        await workspace.store_slide_diagram(
            "d1",
            SlideDiagramPicture(slide_id="s1", key=good, src=_PNG),
            _User(),
            _Db(_deck({"id": "s1", "layout": "bullets"})),
        )
    assert none.value.status_code == 400

    placed = {"src": "data:image/png;base64,AAAA", "caption": "사진"}
    row = _deck({"id": "s1", "layout": "bullets", "diagram": figure, "image": placed})
    db = _Db(row)
    await workspace.store_slide_diagram(
        "d1", SlideDiagramPicture(slide_id="s1", key=good, src=_PNG), _User(), db
    )
    assert row.data["slides"][0]["image"] == placed and db.commits == 0


@pytest.mark.asyncio
async def test_a_picture_placed_by_hand_replaces_the_deck_s_own_figure(monkeypatch):
    from app.routers import workspace
    from app.schemas.workspace import SlideImage

    async def picture_bytes(db, user, artifact_id):
        return "image/png", b"\x89PNG"

    monkeypatch.setattr(workspace, "_picture_bytes", picture_bytes)
    row = _deck(
        {
            "id": "s1",
            "layout": "bullets",
            "diagram": {"source": _SOURCE, "caption": "생성 흐름", "key": diagram_key(_SOURCE)},
            "image": {"src": _PNG, "diagram": True},
        }
    )
    await workspace.add_slide_image(
        "d1", SlideImage(slide_id="s1", artifact_id="p1", caption="모델 그림"), _User(), _Db(row)
    )
    stored = row.data["slides"][0]
    assert "diagram" not in stored
    assert stored["image"]["caption"] == "모델 그림" and "diagram" not in stored["image"]
