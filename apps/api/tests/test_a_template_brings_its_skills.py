"""서식이 자기 절차를 데려온다.

A 서식 is a shape and a skill is a procedure, and some shapes imply their
procedure — a 공문 서식 without the 공문 문체 rules produces a notice-shaped
essay, and the person who chose the 서식 has no way to know a second control
existed. So `template.toml` names its skills by `catalog_key`, they join the
document's trusted context automatically, and the same `skills_applied` event a
hand-activated skill gets says so on screen.
"""

from __future__ import annotations

import pytest

from app.services import design_templates
from app.services.starter import _SKILLS

_KEYS = {spec["key"] for spec in _SKILLS}


@pytest.mark.parametrize("template", [t.id for t in design_templates.all_templates()])
def test_every_named_skill_exists_in_the_catalogue(template: str) -> None:
    """A key nobody seeded is skipped at runtime — the document still comes out
    — but a 서식 shipped pointing at nothing is a promise this test keeps."""
    shape = design_templates.get(template)
    for key in shape.skills:
        assert key in _KEYS, f"{template} names unknown skill {key!r}"


def test_every_document_template_names_at_least_one() -> None:
    """The wiring exists so no 서식 ships bare again by accident."""
    for shape in design_templates.all_templates():
        if shape.kind in ("document", "deck"):
            assert shape.skills, f"{shape.id} brings no skills"


def test_a_skills_kinds_cover_the_templates_surface() -> None:
    """A deck 서식 naming a report-only skill would be silently skipped for
    every deck it generates."""
    by_key = {spec["key"]: spec for spec in _SKILLS}
    for shape in design_templates.all_templates():
        surface = "slides" if shape.kind == "deck" else "report"
        for key in shape.skills:
            kinds = by_key[key].get("kinds") or []
            assert surface in kinds, f"{shape.id} → {key} lacks kind {surface}"


def test_the_event_marks_where_a_skill_came_from() -> None:
    """The screen draws one list; `fromTemplate` is how it can say 서식이 켠
    스킬 rather than pretending the person activated it."""
    import inspect

    from app.routers import sessions

    source = inspect.getsource(sessions._template_skills)
    assert '"fromTemplate": True' in source
