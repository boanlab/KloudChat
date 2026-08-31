"""덱이 발표 자료의 모양을 갖는다.

Seven layouts covered what a deck says and not how a Korean deck looks saying
it. Three shapes were doing the most work in every real 사업 발표 opened beside
this one, and every one of them arrived here as a flat list:

* the row label — 미션 · 배경 · 추진전략 down the left, the content beside it.
  A bullet has nowhere to put the name of what it is, so the names became the
  first words of the sentences and stopped being names.
* the mark — P · H · A · S · E, a letter set large with its meaning under it.
  Written as bullets it is five lines starting with a capital letter.
* the timeline — 연혁, a date beside what happened. As bullets the dates are
  prose and the order is a coincidence.

They are one data shape wearing three designs, so there is one cleaner and one
reader rather than three of each — three would be three places to disagree
about what a half-empty pair means.
"""

from __future__ import annotations

import io

import pytest
from pptx import Presentation

from app.services import deck, deck_export

_SLIDES = {
    "bands": [["미션", "차세대 AX 인재를 양성한다"], ["배경", "인재 격차가 벌어지고 있다"]],
    "tiles": [["P", "Physical AI"], ["H", "Human-centered AI"]],
    "timeline": [["2024.05", "1기 개설"], ["2025.03", "2기 개설"]],
}


@pytest.mark.parametrize("layout", sorted(_SLIDES))
def test_each_shape_reaches_both_files(layout: str) -> None:
    slide = {"layout": layout, "title": "장", layout: _SLIDES[layout]}
    assert deck_export.to_pdf("덱", [slide])[:4] == b"%PDF"

    written = Presentation(io.BytesIO(deck_export.to_pptx("덱", [slide])))
    words = " ".join(
        shape.text_frame.text
        for shape in written.slides[0].shapes
        if shape.has_text_frame
    )
    for left, right in _SLIDES[layout]:
        assert left in words
        assert right in words


@pytest.mark.parametrize("layout", sorted(_SLIDES))
def test_a_half_empty_pair_is_dropped(layout: str) -> None:
    """A name with nothing beside it is a heading for a band that is not there,
    and a band with no name is a paragraph on the wrong slide."""
    pairs = deck._clean_pairs(
        [["이름", "내용"], ["이름만"], ["", "내용만"], ["둘째", ""]], layout
    )
    assert pairs == [["이름", "내용"]]


@pytest.mark.parametrize("layout", sorted(_SLIDES))
def test_an_empty_shape_becomes_bullets_rather_than_a_blank_slide(layout: str) -> None:
    """A coloured rectangle with nothing in it is worse than a list."""
    assert deck._clean_pairs([], layout) == []
    assert deck._clean_pairs("not a list", layout) == []


def test_the_halves_are_bounded_differently_because_they_hold_different_things() -> None:
    """The left half is a name and the right half is a sentence. One bound for
    both would either cut the sentence or let the name become one."""
    long_pair = [["가" * 200, "나" * 200]]
    left, right = deck._clean_pairs(long_pair, "bands")[0]
    assert len(left) == 10
    assert len(right) == 90
    # A tile's mark is a letter, not a word.
    assert len(deck._clean_pairs(long_pair, "tiles")[0][0]) == 4


@pytest.mark.parametrize("layout", sorted(_SLIDES))
def test_the_writer_is_told_what_each_half_holds(layout: str) -> None:
    """A model told "왼쪽과 오른쪽을 채워라" fills both with sentences, and the
    whole point of the shape is that the left half is a name."""
    prompt = deck._PROMPTS[layout]
    assert layout in prompt
    assert "{heading}" in prompt and "{outline}" in prompt


@pytest.mark.parametrize("layout", sorted(_SLIDES))
def test_they_are_shapes_an_argument_takes(layout: str) -> None:
    """Unlike the cover and the divider — so the variety check may ask for them,
    and none of them needs a figure to be honest."""
    assert layout in deck._BODY_LAYOUTS
    assert layout not in deck._NUMERIC_LAYOUTS


def test_an_artifact_written_before_these_existed_still_draws() -> None:
    """Both writers are handed decks made before the shape had a name."""
    stale = {"layout": "bands", "title": "장", "bullets": ["옛 문서"]}
    assert deck_export.to_pdf("덱", [stale])[:4] == b"%PDF"
    assert deck_export.to_pptx("덱", [stale])[:2] == b"PK"


def test_a_filled_shape_is_content() -> None:
    """새 모양을 더하고 이 목록을 안 고치면, 다 쓴 장이 못 쓴 장이 된다.

    `_CONTENT_FIELDS` decides whether a slide got written. The three paired
    layouts were added and the list was not, so a finished 참여 혜택 slide —
    four filled bands on it — was read as a failure and "이 장을 쓰지
    못했습니다." was written across it. The same list once dropped finished
    table slides out of decks.

    Derived rather than typed out twice, which is the only way a fourth shape
    cannot repeat this.
    """
    for layout in sorted(_SLIDES):
        assert layout in deck._CONTENT_FIELDS
        assert deck.has_content({"layout": layout, layout: _SLIDES[layout]})


def test_the_marker_is_not_written_over_a_slide_that_has_something_on_it() -> None:
    """A slide whose prose failed but whose shape filled is a slide with a
    shape on it."""
    slide = {"layout": "bands", "title": "혜택", "bands": _SLIDES["bands"]}
    assert deck.has_content(slide)
