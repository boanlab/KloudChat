"""What a picture or a clip leaves behind in the conversation that made it.

Every image and av session on the test account was an untitled row with no
messages and `artifactId: null` — anonymous in 대화 기록, blank when opened, and
pointing at nothing although the gallery could point back. Chat, report and
slides never had the problem because they run a turn, and a turn writes a
title and hangs the finished document on the session.

These cover the two pieces that are testable without spending 12,000 credits on
a clip: the naming rule the media routes now share with the chat path, and the
subtitle the list rows get in place of a last message.
"""

from __future__ import annotations

from app.schemas.chat import made_from_artifacts
from app.services import chat as chat_service


def test_the_name_is_the_prompt_and_the_rule_is_the_chat_path_s():
    """No model call: on these surfaces the prompt *is* the sentence somebody
    wrote, so there is nothing for a title model to summarise."""
    assert chat_service.provisional_title("학과 홍보 영상 4초") == "학과 홍보 영상 4초"
    assert len(chat_service.provisional_title("가" * 200)) == chat_service.TITLE_CHARS


def test_a_prompt_written_across_lines_still_fits_on_one():
    """A sidebar row is one line. A title carrying its own newlines renders as
    a run-on sentence, the same reason `snippet` collapses whitespace."""
    assert chat_service.provisional_title("  포스터\n  가로로  ") == "포스터 가로로"


def test_nothing_to_name_stays_nameless():
    assert chat_service.provisional_title("") == ""
    assert chat_service.provisional_title("   \n ") == ""


def _image(aspect: str = "16:9", actual: str = "16:9") -> tuple[str, dict]:
    return ("image", {"kind": "image", "aspect": aspect, "actualAspect": actual})


def test_a_batch_of_pictures_is_counted_and_measured():
    made = made_from_artifacts([_image(), _image(), _image()])
    assert made is not None
    assert (made.kind, made.count, made.aspect) == ("image", 3, "16:9")
    # Pictures have no length, so none is claimed.
    assert made.seconds == 0


def test_a_ratio_the_pictures_do_not_share_is_not_claimed():
    """Two batches at two ratios have no single ratio. Naming the newest one
    would be a statement about the other two."""
    made = made_from_artifacts([_image("1:1", "1:1"), _image("16:9", "16:9")])
    assert made is not None
    assert made.count == 2
    assert made.aspect == ""


def test_what_came_back_is_preferred_to_what_was_asked_for():
    """`aspect` is a phrase in the prompt; `actualAspect` was measured off the
    picture. Where both are known the measured one is the true thing to print."""
    made = made_from_artifacts([_image(aspect="16:9", actual="3:2")])
    assert made is not None and made.aspect == "3:2"


def test_an_unmeasured_picture_falls_back_to_what_was_asked_for():
    made = made_from_artifacts([_image(aspect="4:3", actual="")])
    assert made is not None and made.aspect == "4:3"


def test_speech_and_music_are_told_apart():
    """Both are `audio` artifacts, but "음악 1곡" and "내레이션 1개" are not the
    same row, and the surface already knows which was asked for."""
    spoken = made_from_artifacts([("audio", {"audioKind": "narration", "durationSec": 12})])
    played = made_from_artifacts([("audio", {"audioKind": "music", "durationSec": 30})])
    assert spoken is not None and played is not None
    assert (spoken.kind, spoken.seconds) == ("narration", 12)
    assert (played.kind, played.seconds) == ("music", 30)
    # A clip has no ratio to show and speech has no picture at all.
    assert spoken.aspect == "" and played.aspect == ""


def test_an_mp3_whose_length_was_never_measured_says_nothing_about_it():
    """`audiogen.duration_seconds` returns 0 for anything but the WAV it built
    here. Zero is absent, not a zero-second clip."""
    made = made_from_artifacts([("audio", {"audioKind": "narration", "durationSec": 0})])
    assert made is not None and made.seconds == 0


def test_a_clip_carries_both_its_length_and_its_shape():
    made = made_from_artifacts([("video", {"aspect": "16:9", "durationSec": 4})])
    assert made is not None
    assert (made.kind, made.count, made.seconds, made.aspect) == ("video", 1, 4, "16:9")


def test_the_newest_artifact_decides_what_the_row_is_about():
    """Rows arrive newest first. A session that made three pictures and then a
    clip is the clip one to whoever is looking for it."""
    made = made_from_artifacts(
        [("video", {"aspect": "9:16", "durationSec": 8}), _image(), _image(), _image()]
    )
    assert made is not None
    assert (made.kind, made.count) == ("video", 1)


def test_a_session_that_produced_nothing_gets_no_subtitle():
    """Eleven picture and thirteen clip sessions have no artifact at all —
    abandoned, or a generation that failed. An invented line under those would
    be describing something that does not exist."""
    assert made_from_artifacts([]) is None


def test_a_document_session_is_not_summarised_this_way():
    """Reports and decks have transcripts, and the last thing said about a
    document beats a count of it. Only the media nouns are recognised."""
    assert made_from_artifacts([("report", {"wordCount": 900})]) is None
    assert made_from_artifacts([("html", {"language": "html"})]) is None
