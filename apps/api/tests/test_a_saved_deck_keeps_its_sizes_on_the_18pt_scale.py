"""Moving the deck body scale from 22 to 18 pt rewrites saved decks so no slide changes size."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services import deck_type

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0076_deck_body_at_18pt.py"
_SPEC = importlib.util.spec_from_file_location("m0076", _PATH)
m0076 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m0076)


def test_every_body_slide_keeps_the_size_it_was_drawn_at():
    data = {"slides": [
        {"layout": "title", "title": "표지"},
        {"layout": "bullets", "title": "기본"},
        {"layout": "bullets", "title": "한 단계", "textScale": round(18 / 22, 4)},
        {"layout": "table", "title": "작게", "textScale": round(12 / 22, 4)},
        {"layout": "closing", "title": "끝"},
    ]}
    assert m0076._rescale(data, m0076._RATIO)
    slides = data["slides"]
    sizes = [deck_type.TYPE["body"] * s.get("textScale", 1.0) for s in slides[1:4]]
    assert [round(size) for size in sizes] == [22, 18, 12]
    # The 18 pt slide is now the plain size; covers and closings carry no scale.
    assert "textScale" not in slides[2]
    assert "textScale" not in slides[0] and "textScale" not in slides[4]


def test_the_rewrite_undoes():
    data = {"slides": [{"layout": "bullets", "textScale": 0.5455}]}
    m0076._rescale(data, m0076._RATIO)
    m0076._rescale(data, 1 / m0076._RATIO)
    assert abs(data["slides"][0]["textScale"] - 0.5455) < 1e-3
