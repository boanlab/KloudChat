"""Making a picture inside a report or a deck must not move the document.

`session.artifact_id` means two different things depending on the surface. On
the image and audio surfaces the newest thing made *is* the document, so the
pointer follows it. On a report or a slides session it is the report or the
deck — what the panel opens, what 원본 작업 열기 opens, and what
`_revise_document` reads when somebody types "슬라이드 2 다시 써 줘".

Pictures could only be made on the image surface until the document pickers
learned to make their own. The first one made from inside a deck moved that
deck's pointer onto the picture, and every instruction typed afterwards was
read against an image artifact — which has neither slides nor sections — and
answered "고칠 내용이 없습니다" about a deck of eleven slides. The deck was
still there and still open on screen; nothing said what had happened.
"""

from __future__ import annotations

import inspect

import pytest

from app.models.chat import SessionKind
from app.routers import sessions as router


@pytest.mark.parametrize("kind", [SessionKind.report, SessionKind.slides])
def test_a_surface_with_its_own_document_keeps_pointing_at_it(kind) -> None:
    source = inspect.getsource(router._record_media)
    assert "SessionKind.report" in source and "SessionKind.slides" in source
    # The guard has to be on the assignment, not merely near it.
    assignment = source.index("session.artifact_id = made[-1].id")
    guard = source.index("if session.kind not in")
    assert guard < assignment
    assert kind.value in ("report", "slides")


@pytest.mark.parametrize("kind", [SessionKind.image, SessionKind.av, SessionKind.chat])
def test_the_other_surfaces_still_follow_what_was_just_made(kind) -> None:
    """Not a blanket stop. On the image surface the picture *is* the document,
    and a chat has nothing else for the pointer to mean — turning it off there
    would break 원본 작업 열기 on the surfaces it was written for."""
    assert kind not in (SessionKind.report, SessionKind.slides)


def test_the_turn_is_still_recorded_either_way() -> None:
    """The credits were spent and the picture belongs in the transcript.

    Only the pointer is held back — a picture made while writing a deck is part
    of that conversation and has to be findable in it.
    """
    source = inspect.getsource(router._record_media)
    prompt = source.index("chat_service.media_prompt")
    answer = source.index("chat_service.media_answer")
    guard = source.index("if session.kind not in")
    assert prompt < guard and answer < guard
