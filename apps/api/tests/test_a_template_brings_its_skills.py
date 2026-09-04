"""Design templates name catalogue skills that join the document's context automatically."""

from __future__ import annotations

import pytest

from app.services import design_templates
from app.services.starter import _SKILLS

_KEYS = {spec["key"] for spec in _SKILLS}


@pytest.mark.parametrize("template", [t.id for t in design_templates.all_templates()])
def test_every_named_skill_exists_in_the_catalogue(template: str) -> None:
    """Every skill key a template names exists in the seeded catalogue."""
    shape = design_templates.get(template)
    for key in shape.skills:
        assert key in _KEYS, f"{template} names unknown skill {key!r}"


def test_every_document_template_names_at_least_one() -> None:
    """Every document and deck template names at least one skill."""
    for shape in design_templates.all_templates():
        if shape.kind in ("document", "deck"):
            assert shape.skills, f"{shape.id} brings no skills"


def test_a_skills_kinds_cover_the_templates_surface() -> None:
    """Each named skill supports the template's surface (report or slides)."""
    by_key = {spec["key"]: spec for spec in _SKILLS}
    for shape in design_templates.all_templates():
        surface = "slides" if shape.kind == "deck" else "report"
        for key in shape.skills:
            kinds = by_key[key].get("kinds") or []
            assert surface in kinds, f"{shape.id} → {key} lacks kind {surface}"


def test_the_event_marks_where_a_skill_came_from() -> None:
    """The skills_applied event flags template-activated skills with fromTemplate."""
    import inspect

    from app.routers import sessions

    source = inspect.getsource(sessions._template_skills)
    assert '"fromTemplate": True' in source
