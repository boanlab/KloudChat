"""The plan check both writing tracks run before they spend a call per block.

Pure functions, so these are the tests that can say what the rule *is* without
a model in the room.
"""

from __future__ import annotations

import pytest

from app.services import deck, outline
from app.services import design_templates as dt


def plan(*layouts: str) -> list[dict[str, str]]:
    return [{"title": f"{i}", "layout": layout} for i, layout in enumerate(layouts)]


#: The shapes an argument takes. Not `_LAYOUTS[1:]`: a section divider has no
#: content either, so slicing off the cover alone stopped meaning this.
_DECK = deck._BODY_LAYOUTS


def test_a_plan_that_uses_one_layout_all_the_way_down_is_flat():
    missing = outline.flat_layouts(plan("title", *["bullets"] * 6), _DECK)
    # Every body layout except the one it used. Derived rather than listed, so
    # adding a layout does not silently narrow what this checks.
    assert set(missing) == set(_DECK) - {"bullets"}


def test_a_run_of_three_is_flat_even_when_every_layout_appears():
    """Three of the same in a row is what a reader sees, whatever the totals.

    Nothing is unused here, so the ask cannot be "use the missing one" — it is
    the layouts the plan leans away from.
    """
    # Every layout appears, so the ask cannot be "use the unused one". Built
    # from `_DECK` rather than listed: a layout added later would otherwise be
    # the unused one, and this test would quietly start checking that instead.
    dominant = plan("title", "bullets", "bullets", "bullets", *_DECK)
    assert set(outline.flat_layouts(dominant, _DECK)) == set(_DECK) - {"bullets"}


def test_alternating_layouts_are_varied_even_when_one_is_common():
    varied = plan("title", "bullets", "quote", "bullets", "two-column", "bullets")
    assert outline.flat_layouts(varied, _DECK) == []


def test_a_varied_plan_passes():
    assert outline.flat_layouts(plan("title", "bullets", "two-column", "quote"), _DECK) == []


def test_a_short_plan_is_left_alone():
    """Two body slides cannot show three layouts, and saying so is noise."""
    assert outline.flat_layouts(plan("title", "bullets", "bullets"), _DECK) == []


def test_a_template_with_one_body_layout_is_never_flat():
    """`doc-minutes` styles `cover` and `section`; there is nothing to vary."""
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
    """A rule nothing can trip is a rule that does nothing."""
    body = template.layouts[1]
    flat = plan(template.layouts[0], *[body] * 6)
    assert outline.flat_layouts(flat, template.layouts[1:])
