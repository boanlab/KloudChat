"""A figure on its own takes a layout 1.3-3.5 times wider than tall (the document column's
tall box still picks the largest text); its zones, stores, frozen parts, decisions and loss
links are drawn in the paper palette."""

from __future__ import annotations

import pytest
from pptx import Presentation
from pptx.util import Pt

from app.services import diagram_shapes as ds
from app.services import elk_layout

_METHOD = """flowchart LR
    subgraph enc["인코더"]
        img[입력 영상] --> cnn(CNN 인코더):::frozen
    end
    subgraph dec["디코더"]
        q[질의 벡터] --> tr(트랜스포머)
    end
    subgraph loss["손실"]
        hung(헝가리안 매칭):::hot
    end
    cnn --> tr
    tr --> db[(저장소)]
    tr --> ok{통과?}
    tr -.-> hung
"""


def _fake_layouts(graph, wide: bool, sizes: list[tuple[float, float]]) -> None:
    requests = elk_layout._requests(graph, wide)
    assert len(requests) == len(sizes)
    elk_layout._CACHE[elk_layout._key(requests)] = [{"width": w, "height": h} for w, h in sizes]


@pytest.fixture(autouse=True)
def _clean():
    yield
    elk_layout._CACHE.clear()


def test_a_wrapped_candidate_is_asked_for_only_when_the_figure_is_on_its_own():
    graph = ds.parse(_METHOD)
    assert len(elk_layout._requests(graph, True)) == len(elk_layout._requests(graph)) + 2


def test_a_figure_on_its_own_takes_the_landscape_layout_the_column_takes_the_tall_one():
    graph = ds.parse(_METHOD)
    narrow = [(1009.0, 184.0), (263.0, 729.0), (747.0, 190.0), (222.0, 654.0)]
    _fake_layouts(graph, False, narrow)
    tall, _scale = elk_layout._chosen(graph, elk_layout.FIGURE)
    assert tall["height"] > tall["width"]  # the old choice for the document column
    _fake_layouts(graph, True, [*narrow, (709.0, 296.0), (507.0, 306.0)])
    laid, scale = elk_layout._chosen(graph, elk_layout.PAPER, True)
    ratio = laid["width"] / laid["height"]
    assert elk_layout.RATIO[0] <= ratio <= elk_layout.FLAT_RATIO
    assert (laid["width"], laid["height"]) == (747.0, 190.0)  # flat, not the wrapped 709x296
    width, height = elk_layout.frame_size(graph, wide=True)
    assert elk_layout.RATIO[0] <= width / height <= elk_layout.FLAT_RATIO


def test_without_any_landscape_layout_of_elks_the_folded_rows_draw_the_figure():
    graph = ds.parse(_METHOD)
    _fake_layouts(graph, True, [(1400.0, 150.0), (200.0, 900.0), (1300.0, 160.0),
                                (190.0, 800.0), (1200.0, 170.0), (1100.0, 180.0)])
    assert elk_layout._chosen(graph, elk_layout.PAPER, True) is None
    from app.services import flow_layout

    width, height = flow_layout.wide_frame(graph)
    assert elk_layout.RATIO[0] <= width / height <= elk_layout.RATIO[1]


def test_a_line_into_a_diamond_ends_on_its_outline_not_on_the_box_corner():
    box = (100.0, 0.0, 200.0, 60.0)  # diamond: vertices at the side midpoints
    path = ds._clip_diamond([(0.0, 15.0), (100.0, 15.0)], box, end=True)
    tip_x, tip_y = path[-1]
    assert tip_y == 15.0 and 100.0 < tip_x < 150.0
    # on the outline: |x-cx|/hw + |y-cy|/hh == 1
    assert abs(abs(tip_x - 150.0) / 50.0 + abs(tip_y - 30.0) / 30.0 - 1.0) < 1e-6
    out = ds._clip_diamond([(200.0, 30.0), (300.0, 30.0)], box, end=False)
    assert out[0] == (200.0, 30.0)  # a line from a vertex stays put


def _draw(source: str, wide: bool):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Pt(760), Pt(400)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    graph = ds.parse(source)
    ds.draw_pptx(slide, graph, left=20, top=20, width=700, height=340, accent="#2563EB",
                 wide=wide)
    return slide


def _fill(slide, text: str) -> str:
    shape = next(s for s in slide.shapes if s.has_text_frame and s.text_frame.text == text)
    return str(shape.fill.fore_color.rgb)


def test_the_paper_palette_gives_each_zone_a_tint_and_the_one_emphasis_the_warm_colour():
    slide = _draw(_METHOD, wide=True)
    # zones cycle: a frozen part is cool grey, the emphasis warm, the others their zone's tint
    assert _fill(slide, "CNN 인코더") == ds._FROZEN_FILL
    assert _fill(slide, "헝가리안 매칭") == ds._HOT
    assert _fill(slide, "트랜스포머") == ds._ZONES[1][2]
    assert _fill(slide, "입력 영상") == ds._ZONES[0][2]
    # a frozen part carries its ice mark
    assert any("STAR" in str(s.auto_shape_type) for s in slide.shapes if s.shape_type == 1)


def test_a_dashed_line_into_a_loss_is_red_and_other_lines_grey():
    slide = _draw(_METHOD, wide=True)
    colours = {str(s.line.color.rgb) for s in slide.shapes
               if s.shape_type == 5 and s.line.dash_style is not None}
    assert ds._LOSS in colours


def test_a_deck_or_report_figure_keeps_its_accent_look():
    slide = _draw("flowchart LR\n  a[A] --> b(B):::hot", wide=False)
    assert _fill(slide, "A") == "FFFFFF" and _fill(slide, "B") == "2563EB"


def test_an_arc_with_no_free_route_still_rises_clear_of_the_boxes():
    a, b = (0.0, 40.0, 80.0, 80.0), (200.0, 40.0, 280.0, 80.0)
    path = ds._route(a, "top", b, "top", obstacles=[], frames=[(0.0, 30.0, 300.0, 33.0)],
                     used={}, bounds=(0.0, 30.0, 40.0, 32.0))  # a grid with no room
    assert max(y for _x, y in path[1:-1]) <= 40.0 - 14.0 + 1e-6


def test_no_label_or_note_is_drawn_outside_the_figure():
    source = """flowchart LR
  a(Client) -->|Request| b(Gateway) -->|Token| c(Auth Service) -->|Verified| d[(Job Queue)]
  d -->|Task| e(Worker) -->|Write to cache| f[(Cache)]
  e -->|Result| g[(Database)]
  f -.->|read hit| a
"""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Pt(760), Pt(400)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    ds.draw_pptx(slide, ds.parse(source), left=20, top=20, width=700, height=340,
                 accent="#2563EB", wide=True)
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            assert shape.left >= Pt(20) - 2 and shape.left + shape.width <= Pt(720) + 2, \
                shape.text_frame.text


def test_a_flat_layout_with_shorter_links_beats_a_longer_one_and_a_folded_one():
    graph = ds.parse(_METHOD)

    def laid(w, h, length):
        sec = [{"startPoint": {"x": 0, "y": 0}, "endPoint": {"x": length, "y": 0}}]
        return {"width": w, "height": h, "children": [{}] * 2, "edges": [{"sections": sec}]}

    flat_long, flat_short = laid(800.0, 200.0, 900.0), laid(850.0, 200.0, 100.0)
    folded = laid(500.0, 300.0, 50.0)
    elk_layout._CACHE[elk_layout._key(elk_layout._requests(graph, True))] = [
        flat_long, laid(200.0, 700.0, 1.0), flat_short, laid(190.0, 600.0, 1.0), folded, folded]
    got, _scale = elk_layout._chosen(graph, elk_layout.PAPER, True)
    assert got is flat_short
