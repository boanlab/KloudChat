"""A deck written into a 서식 is a deck, not a file.

Choosing a deck 서식 used to send the slides turn down the block writer and
store what came back as an `html` artifact — a finished file in a sandboxed
frame. The person had seen the deck surface draw slides in seven faces, edit
one slide at a time, switch the face and present; what they got under the
서식 was a grey page with a numbered pill, and none of that. The deck writer
writes it now. The 서식 names the face the deck opens on, its genre rules
reach the planner, and its id rides on the artifact so the export builds on
its PowerPoint half — which the export route already knew how to open.
"""

from __future__ import annotations

import inspect

import pytest

from app.routers import sessions
from app.services import design, design_templates


def test_a_slides_turn_with_a_deck_form_goes_to_the_deck_writer() -> None:
    source = inspect.getsource(sessions.send_message)
    # The block writer is left only what neither writer knows.
    assert 'render_template.kind not in ("document", "deck")' in source
    # And the deck writer is handed the 서식 rather than asked to ignore it.
    at = source.find("_run_deck(")
    assert at > 0
    assert "template=render_template" in source[at : at + 200]


_DECKS = [t.id for t in design_templates.all_templates() if t.kind == "deck"]


@pytest.mark.parametrize("template_id", _DECKS)
def test_every_deck_form_opens_on_one_of_the_faces_the_stage_draws(template_id: str) -> None:
    template = design_templates.get(template_id)
    assert template is not None
    assert template.look in design.VISUAL_STYLES, template.look


def test_a_document_form_has_no_face() -> None:
    doc = design_templates.get("doc-report")
    assert doc is not None and doc.look == ""


def test_a_form_that_names_a_face_the_stage_cannot_draw_is_refused() -> None:
    with pytest.raises(ValueError):
        design_templates._look({"look": "neon"}, "deck")
    assert design_templates._look({}, "deck") == "editorial"


def test_the_deck_writer_wears_the_form() -> None:
    """The 서식's face outranks the request's words and rides on the artifact."""
    source = inspect.getsource(sessions._run_deck)
    assert '"visualStyle": template.look' in source
    assert '"templateId": template.id' in source
    # Its genre rules reach the planner the way project instructions do.
    assert "template.instructions" in source
