"""A picture made inside a report or deck session does not move `session.artifact_id`."""

from __future__ import annotations

import inspect

import pytest

from app.models.chat import SessionKind
from app.routers import sessions as router


@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
def test_a_surface_with_its_own_document_keeps_pointing_at_it(kind) -> None:
    source = inspect.getsource(router._record_media)
    assert "SessionKind.report" in source and "SessionKind.slides" in source
    # The guard must precede the assignment.
    assignment = source.index("session.artifact_id = made[-1].id")
    guard = source.index("if session.kind not in")
    assert guard < assignment
    assert kind.value in ("report", "slides")


@pytest.mark.parametrize("kind", [SessionKind.image, SessionKind.av, SessionKind.chat])
def test_the_other_surfaces_still_follow_what_was_just_made(kind) -> None:
    """Image, av and chat sessions are not guarded."""
    assert kind not in (SessionKind.report, SessionKind.slides)


def test_the_turn_is_still_recorded_either_way() -> None:
    """The media prompt and answer are recorded before the pointer guard."""
    source = inspect.getsource(router._record_media)
    prompt = source.index("chat_service.media_prompt")
    answer = source.index("chat_service.media_answer")
    guard = source.index("if session.kind not in")
    assert prompt < guard and answer < guard
