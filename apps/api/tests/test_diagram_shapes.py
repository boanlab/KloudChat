"""A deck's own figure is drawn with native shapes and glued connectors."""

from pptx import Presentation
from pptx.util import Inches

from app.services import diagram_shapes as ds

FLOW = """flowchart LR
  subgraph p["24시간 총량"]
    a[아세트아미노펜 용량] --> b[타이레놀 + 종합감기약] --> c[성분 중복 확인]
  end
  s((시작)) -.->|입력| a
  c:::hot"""


def test_a_flowchart_is_read_with_groups_labels_and_emphasis():
    g = ds.parse(FLOW)
    assert g.direction == "LR" and list(g.groups) == ["p"]
    assert g.nodes["s"].shape == "circle" and g.nodes["c"].hot
    assert [(e.a, e.b, e.label, e.dashed) for e in g.edges] == [
        ("a", "b", "", False), ("b", "c", "", False), ("s", "a", "입력", True),
    ]
    assert ds.parse("sequenceDiagram\n A->>B: hi") is None


def test_shapes_fit_the_box_without_overlap():
    g = ds.parse(FLOW)
    placed = ds.layout(g, 816, 300)
    boxes = [(p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2) for p in placed.values()]
    assert all(0 <= x0 and x1 <= 816 and 0 <= y0 and y1 <= 300 for x0, y0, x1, y1 in boxes)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_the_slide_gets_native_shapes_and_routed_lines():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    ds.draw_pptx(slide, ds.parse(FLOW), left=72, top=100, width=816, height=300, accent="#2563EB")
    # Each edge is an open polyline with an arrowhead, routed around the boxes.
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    lines = [s for s in slide.shapes if s._element.find(f".//{A}custGeom") is not None]
    assert len(lines) == 3
    assert all(s._element.find(f".//{A}tailEnd") is not None for s in lines)
    labels = [s.text_frame.text for s in slide.shapes if s.has_text_frame]
    assert "성분 중복 확인" in labels and "입력" in labels
    run = next(s for s in slide.shapes if s.has_text_frame and s.text_frame.text == "시작")
    assert run.text_frame.word_wrap is True
    assert run.text_frame.paragraphs[0].runs[0].font.name == "Pretendard"
    # Flat: no theme style reference (and its shadow) on any drawn shape.
    assert not any(s._element.findall("{http://schemas.openxmlformats.org/presentationml/2006/main}style")
                   for s in slide.shapes)


def test_labels_get_material_icons_embedded_as_svg():
    assert ds.icon_for("서버") == "dns" and ds.icon_for("데이터 암호화") == "lock"
    assert ds.icon_for("1920년대 신문") is None
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    graph = ds.parse("flowchart LR\n  a[사용자 요청] --> b[API 서버] --> c[(데이터베이스)]")
    ds.draw_pptx(slide, graph, left=72, top=100, width=816, height=300, accent="#2563EB")
    svgs = [p for p in prs.part.package.iter_parts() if str(p.partname).endswith(".svg")]
    assert len(svgs) >= 2 and b'fill="#' in svgs[0].blob


def test_a_figure_deck_is_one_white_slide_sized_to_the_figure():
    import io

    from app.services import diagram_render

    blob = diagram_render.figure_deck(FLOW)
    deck = Presentation(io.BytesIO(blob))
    assert len(deck.slides) == 1
    width_pt = deck.slide_width / 12700
    assert 420 <= width_pt <= 940
    assert diagram_render.figure_deck("sequenceDiagram\n A->>B: hi") is None


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_render_falls_back_when_the_printer_is_not_configured(monkeypatch):
    from app.services import diagram_render

    monkeypatch.setattr(diagram_render.settings, "print_base_url", "")
    assert await diagram_render.render_png(FLOW) is None



def test_a_route_goes_around_a_box_in_its_way():
    # a → b with c squarely between them on the straight line.
    a, b, c = (0, 100, 100, 160), (400, 100, 500, 160), (180, 90, 320, 170)
    path = ds._route(a, "right", b, "left", obstacles=[c], frames=[], used={},
                     bounds=(-40, 0, 540, 300))
    assert path[0] == (100, 130) and path[-1] == (400, 130)
    for (x0, y0), (x1, y1) in zip(path, path[1:], strict=False):
        assert x0 == x1 or y0 == y1  # orthogonal
        lo_x, hi_x, lo_y, hi_y = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        crosses = lo_x < c[2] and hi_x > c[0] and lo_y < c[3] and hi_y > c[1]
        assert not crosses


def test_labels_break_at_br_and_comparisons_have_no_arrows_or_cross_row_links():
    graph = ds.parse(
        "flowchart LR\n"
        "  subgraph a[\"기존\"]\n    a1[행동 통제 <br/> 목표 : 안정성]\n    a2[거버넌스]\n  end\n"
        "  subgraph b[\"제안\"]\n    b1[행동 통제 <br/> 목표 : 안전성]\n    b2[거버넌스]\n  end\n"
        "  a1 -.- b1\n  a2 -.->|대비| b1\n"
    )
    assert graph.nodes["a1"].label == "행동 통제\n목표 : 안정성"
    assert [(e.a, e.b, e.arrow) for e in graph.edges] == [("a1", "b1", False)]


@pytest.mark.asyncio
async def test_a_plan_with_too_few_figures_is_asked_once_more():
    from app.services import diagrams

    calls = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return "[]", {"inputTokens": 1, "outputTokens": 1}
        return ('[{"part": 2, "figure": "flow", "description": "요청 → 게이트웨이 → 서버", '
                '"caption": "요청 흐름"}]'), {"inputTokens": 1, "outputTokens": 1}

    parts = [("개요", "배경입니다."), ("구조", "요청은 게이트웨이를 거쳐 서버로 갑니다."),
             ("결론", "끝.")]
    planned, usage = await diagrams.plan(parts=parts, eligible=[0, 1, 2], request="보고서 써 줘",
                                         model="m", api_key="k", complete=complete, slide=False,
                                         at_least=1)
    assert [p.index for p in planned] == [1] and "적어도 1개" in calls[1]
    assert usage == {"inputTokens": 2, "outputTokens": 2}
