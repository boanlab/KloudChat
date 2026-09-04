"""`outline.flat_layouts`: the layout-variety rule applied to a plan."""

from __future__ import annotations

import pytest

from app.services import deck, outline
from app.services import design_templates as dt


def plan(*layouts: str) -> list[dict[str, str]]:
    return [{"title": f"{i}", "layout": layout} for i, layout in enumerate(layouts)]


_DECK = deck._BODY_LAYOUTS


def test_a_plan_that_uses_one_layout_all_the_way_down_is_flat():
    missing = outline.flat_layouts(plan("title", *["bullets"] * 6), _DECK)
    assert set(missing) == set(_DECK) - {"bullets"}


def test_a_run_of_three_is_flat_even_when_every_layout_appears():
    """A run of three identical layouts is flat even when every layout is used."""
    dominant = plan("title", "bullets", "bullets", "bullets", *_DECK)
    assert set(outline.flat_layouts(dominant, _DECK)) == set(_DECK) - {"bullets"}


def test_alternating_layouts_are_varied_even_when_one_is_common():
    varied = plan("title", "bullets", "quote", "bullets", "two-column", "bullets")
    assert outline.flat_layouts(varied, _DECK) == []


def test_a_varied_plan_passes():
    assert outline.flat_layouts(plan("title", "bullets", "two-column", "quote"), _DECK) == []


def test_a_short_plan_is_left_alone():
    """A plan with two body slides is never flat."""
    assert outline.flat_layouts(plan("title", "bullets", "bullets"), _DECK) == []


def test_a_template_with_one_body_layout_is_never_flat():
    """A template with one body layout is never flat."""
    template = dt.get("doc-minutes")
    assert template is not None
    assert outline.flat_layouts(
        plan("cover", *["section"] * 8), template.layouts[1:]
    ) == []


@pytest.mark.parametrize(
    "template",
    [t for t in dt.all_templates() if t.kind in dt.HTML_KINDS and len(t.layouts) > 2],
    ids=lambda t: t.id,
)
def test_every_multi_layout_template_can_fail_the_check(template):
    """Every multi-layout template has a plan that is flat."""
    body = template.layouts[1]
    flat = plan(template.layouts[0], *[body] * 6)
    assert outline.flat_layouts(flat, template.layouts[1:])
