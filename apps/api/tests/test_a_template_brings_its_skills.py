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
    """Every document and deck template names at least one skill, or opts out with `skills = []`."""
    for shape in design_templates.all_templates():
        if shape.kind in ("document", "deck"):
            assert shape.skills or shape.no_skills, f"{shape.id} brings no skills"


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


def test_a_document_template_names_no_skill_that_calls_a_tool() -> None:
    """A document or deck is written without tools; a script skill there is the pipeline's check."""
    by_key = {spec["key"]: spec for spec in _SKILLS}
    for shape in design_templates.all_templates():
        for key in shape.skills:
            assert not by_key[key].get("required_tools"), f"{shape.id} → {key} calls a tool"


async def test_a_tool_skill_named_by_a_template_is_left_out_of_a_report() -> None:
    from types import SimpleNamespace

    from app.models.chat import SessionKind
    from app.routers import sessions

    both = ["chat", "report"]
    rows = [SimpleNamespace(id="s1", name="계산·단위 검증", catalog_key="calc-check", kinds=both,
                            required_tools=["run_skill_script"], body="run_skill_script 를 부른다",
                            description="", estimated_tokens=10),
            SimpleNamespace(id="s2", name="근거", catalog_key="evidence", kinds=both,
                            required_tools=None, body="근거를 단다", description="",
                            estimated_tokens=5)]

    class _Result:
        def all(self):
            return rows

    class _Db:
        async def exec(self, _query):
            return _Result()

    template = SimpleNamespace(skills=("calc-check", "evidence"))
    blocks, event = await sessions._template_skills(_Db(), template, SessionKind.report, None)
    assert blocks == ["# 서식 스킬 — 근거\n근거를 단다"]
    assert [s["catalogKey"] for s in event["skills"]] == ["evidence"]
    chat_blocks, _ = await sessions._template_skills(_Db(), template, SessionKind.chat, None)
    assert len(chat_blocks) == 2
