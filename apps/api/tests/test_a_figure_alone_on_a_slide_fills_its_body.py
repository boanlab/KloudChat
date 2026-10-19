"""A fixed-shape figure alone on a slide fills the slide's body: the wide body draws the map and
the house lower, a ring without notes above or below keeps no room there, and the caption's line
is kept only when a caption is drawn."""

from __future__ import annotations

from app.services import deck_export, figure_kinds

#: the body a figure alone on a slide is drawn into (`deck_export.to_pptx`)
_BODY = (816.0, 312.0)

_AXES = {"kind": "axes", "x": {"name": "가격", "low": "낮음", "high": "높음"},
         "y": {"name": "기능 범위", "low": "좁음", "high": "넓음"},
         "items": [{"name": "메신저", "x": "low", "y": "low"},
                   {"name": "그룹웨어", "x": "high", "y": "high"},
                   {"name": "우리 제품", "x": "low", "y": "high"}], "hot": "우리 제품"}
_HOUSE = {"kind": "house", "goal": "신뢰할 수 있는 데이터 활용",
          "pillars": ["데이터 품질 관리", "메타데이터 관리", "보안·개인정보 보호"],
          "base": [{"name": "거버넌스 조직", "items": ["데이터 위원회", "데이터 관리자"]},
                   {"name": "정책 · 표준", "note": "모든 영역이 따르는 규칙"}]}
_CYCLE = {"kind": "cycle", "center": "PDCA", "stages": [
    {"name": "계획", "note": "목표와 개선안 수립"}, {"name": "실행", "note": "시범 적용"},
    {"name": "점검", "note": "지표로 측정"}, {"name": "개선", "note": "표준화"}]}


def _scale(slots: dict, box: tuple[float, float]) -> float:
    w, h = figure_kinds.size(figure_kinds.spec(slots), box)
    return min(box[0] / w, box[1] / h)


def test_the_map_and_the_house_are_drawn_lower_on_a_slide():
    for slots in (_AXES, _HOUSE):
        assert _scale(slots, _BODY) >= 0.78, slots["kind"]
        # the document keeps the taller drawing
        assert figure_kinds.size(figure_kinds.spec(slots))[1] > \
            figure_kinds.size(figure_kinds.spec(slots), _BODY)[1]


def test_a_ring_keeps_room_above_and_below_only_for_a_note_there():
    four = figure_kinds.spec(_CYCLE)
    three = figure_kinds.spec({**_CYCLE, "stages": _CYCLE["stages"][:3]})
    # four stages put their notes to the sides; three put the first one on top
    assert figure_kinds.size(four)[1] < figure_kinds.size(three)[1]
    assert _scale(_CYCLE, _BODY) >= 0.78


def test_a_figure_alone_takes_the_caption_line_too(monkeypatch):
    drawn: list[float] = []
    real = figure_kinds.draw

    def spy(slide, slots, **kw):
        drawn.append(kw["height"])
        return real(slide, slots, **kw)

    monkeypatch.setattr(deck_export.figure_kinds, "draw", spy)
    pixel = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
             "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    slide = {"id": "s1", "layout": "bullets", "title": "협업 도구",
             "diagram": {"source": figure_kinds.to_source(figure_kinds.spec(_AXES))},
             "image": {"src": pixel, "caption": "협업 도구 포지셔닝", "diagram": True,
                       "fit": "contain"}}
    deck_export.to_pptx("샘플", [slide])
    # alone on its slide the title names the figure, so no caption line is kept under it
    assert drawn == [_BODY[1]]
