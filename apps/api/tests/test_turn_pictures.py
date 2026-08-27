"""A picture attached to a turn, and which turns can actually look at one.

Images used to stop at the door: `extract_text` refuses every `image/*`, so the
file was stored, marked unreadable, and never reached a model. The person was
told honestly — which is better than silence — and then asked to type out what
they had just attached.

The models this instance serves can read one. Measured against the gateway, the
self-hosted `strict-local/qwen3.6-35b` transcribed a screenshot exactly, for no
credits and without the picture leaving the building; two commercial models
accepted the request and answered with **nothing at all**, which is why a model
has to declare this rather than be tried and watched.

Strict-local only, deliberately. The privacy guard reads text; an image is
egress it cannot inspect, and `architecture.md` §7 is explicit that policy
applies before anything reaches a model. Sending pictures to an external model
would put data past that gate with nothing looking at it, so the one route that
cannot leave is the one route that carries them.
"""

from __future__ import annotations

import pytest

from app.services import files as file_service
from app.services import pictures
from app.services.context import with_pictures
from app.services.pictures import MAX_PICTURE_BYTES, can_be_seen
from app.services.workspace_context import ContextFile, _file_report, reads_pictures

# ── what may travel ────────────────────────────────────────────────────


@pytest.mark.parametrize("mime", pictures.EMBEDDABLE)
def test_every_raster_a_document_may_carry_may_also_be_looked_at(mime):
    """One rule, not two. `pictures.EMBEDDABLE` already answers this question
    for documents, and a second list would drift from it."""
    assert can_be_seen(mime, 1_000)


def test_svg_is_a_document_and_does_not_travel_as_a_picture():
    assert not can_be_seen("image/svg+xml", 1_000)


@pytest.mark.parametrize("mime", ["application/pdf", "text/plain", "audio/mpeg", ""])
def test_anything_that_is_not_a_raster_is_not_a_picture(mime):
    assert not can_be_seen(mime, 1_000)


def test_a_picture_too_large_to_send_is_not_sent():
    """Base64 in a prompt is a third larger again, and a context window spent
    on one screenshot is a conversation that stops answering."""
    assert can_be_seen("image/png", MAX_PICTURE_BYTES)
    assert not can_be_seen("image/png", MAX_PICTURE_BYTES + 1)


def test_an_empty_file_is_not_a_picture():
    assert not can_be_seen("image/png", 0)


# ── which turns may look ───────────────────────────────────────────────


def test_only_a_model_that_says_so_and_cannot_leave_reads_pictures():
    strict = {"supportsVision": True, "strictLocal": True}
    assert reads_pictures(strict)

    # Declared but external: the guard cannot inspect an image, so this route
    # would put one past it.
    assert not reads_pictures({"supportsVision": True, "strictLocal": False})
    # Contained but silent about vision: two models answered a picture with an
    # empty message rather than an error, so absence of a claim is a no.
    assert not reads_pictures({"supportsVision": False, "strictLocal": True})
    assert not reads_pictures({"strictLocal": True})
    assert not reads_pictures({})
    assert not reads_pictures(None)


# ── what the model is told ─────────────────────────────────────────────


def test_a_picture_that_was_looked_at_is_reported_as_one():
    report = _file_report((ContextFile("shot.png", "picture", 0, 0),), ())
    assert "shot.png" in report
    assert "그림" in report
    # It must not read as a file that failed, which is what the old state said.
    assert "꺼내지 못함" not in report


def test_a_picture_this_model_cannot_see_says_which_it_is():
    """The difference the person can act on: the file arrived and is fine, and
    a different model would read it."""
    report = _file_report((ContextFile("shot.png", "picture_unseen", 0, 0),), ())
    assert "shot.png" in report
    assert "지어내지" in report or "지어내" in report


# ── the upload, and the wire ───────────────────────────────────────────


def test_a_picture_is_not_a_document_that_failed_to_parse():
    """No text is not an error, and an error puts a warning on the chip.

    Before this the composer drew a triangle beside a screenshot the model was
    about to read out loud.
    """
    assert file_service.extract_text("shot.png", "image/png", b"x" * 1_000) == ""
    with pytest.raises(RuntimeError, match="PNG"):
        file_service.extract_text("d.svg", "image/svg+xml", b"<svg/>")
    with pytest.raises(RuntimeError):
        file_service.extract_text("clip.mp3", "audio/mpeg", b"x" * 10)


URI = "data:image/png;base64,AAAA"


def test_the_picture_rides_on_the_last_thing_the_person_said():
    messages = [
        {"role": "system", "content": "규칙"},
        {"role": "user", "content": "앞선 질문"},
        {"role": "assistant", "content": "앞선 답"},
        {"role": "user", "content": "이 그림 읽어줘"},
    ]
    out = with_pictures(messages, [URI])
    assert [m["role"] for m in out] == [m["role"] for m in messages]
    assert out[1]["content"] == "앞선 질문"
    assert out[3]["content"] == [
        {"type": "text", "text": "이 그림 읽어줘"},
        {"type": "image_url", "image_url": {"url": URI}},
    ]


def test_no_picture_leaves_the_transcript_exactly_as_it_was():
    """Identity, not a copy: every turn without an attachment takes this path."""
    messages = [{"role": "user", "content": "질문"}]
    assert with_pictures(messages, []) is messages


def test_a_transcript_with_nobody_to_attach_to_is_left_alone():
    messages = [{"role": "system", "content": "규칙"}]
    assert with_pictures(messages, [URI]) == messages
