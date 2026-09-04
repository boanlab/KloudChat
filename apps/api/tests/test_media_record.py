"""Media sessions: the provisional title rule and the `made_from_artifacts` list subtitle."""

from __future__ import annotations

from app.schemas.chat import made_from_artifacts
from app.services import chat as chat_service


def test_the_name_is_the_prompt_and_the_rule_is_the_chat_path_s():
    """A media session is titled by its prompt, capped at `TITLE_CHARS`."""
    assert chat_service.provisional_title("학과 홍보 영상 4초") == "학과 홍보 영상 4초"
    assert len(chat_service.provisional_title("가" * 200)) == chat_service.TITLE_CHARS


def test_a_prompt_written_across_lines_still_fits_on_one():
    """Whitespace and newlines in the prompt collapse to single spaces."""
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
    assert made.seconds == 0


def test_a_ratio_the_pictures_do_not_share_is_not_claimed():
    """Pictures at differing ratios report no ratio."""
    made = made_from_artifacts([_image("1:1", "1:1"), _image("16:9", "16:9")])
    assert made is not None
    assert made.count == 2
    assert made.aspect == ""


def test_what_came_back_is_preferred_to_what_was_asked_for():
    """The measured `actualAspect` beats the requested `aspect`."""
    made = made_from_artifacts([_image(aspect="16:9", actual="3:2")])
    assert made is not None and made.aspect == "3:2"


def test_an_unmeasured_picture_falls_back_to_what_was_asked_for():
    made = made_from_artifacts([_image(aspect="4:3", actual="")])
    assert made is not None and made.aspect == "4:3"


def test_speech_and_music_are_told_apart():
    """`audioKind` splits audio artifacts into narration and music; neither has a ratio."""
    spoken = made_from_artifacts([("audio", {"audioKind": "narration", "durationSec": 12})])
    played = made_from_artifacts([("audio", {"audioKind": "music", "durationSec": 30})])
    assert spoken is not None and played is not None
    assert (spoken.kind, spoken.seconds) == ("narration", 12)
    assert (played.kind, played.seconds) == ("music", 30)
    assert spoken.aspect == "" and played.aspect == ""


def test_an_mp3_whose_length_was_never_measured_says_nothing_about_it():
    """A zero `durationSec` means unmeasured, not a zero-second clip."""
    made = made_from_artifacts([("audio", {"audioKind": "narration", "durationSec": 0})])
    assert made is not None and made.seconds == 0


def test_a_clip_carries_both_its_length_and_its_shape():
    made = made_from_artifacts([("video", {"aspect": "16:9", "durationSec": 4})])
    assert made is not None
    assert (made.kind, made.count, made.seconds, made.aspect) == ("video", 1, 4, "16:9")


def test_the_newest_artifact_decides_what_the_row_is_about():
    """Artifacts arrive newest first and the newest decides the subtitle."""
    made = made_from_artifacts(
        [("video", {"aspect": "9:16", "durationSec": 8}), _image(), _image(), _image()]
    )
    assert made is not None
    assert (made.kind, made.count) == ("video", 1)


def test_a_session_that_produced_nothing_gets_no_subtitle():
    """No artifacts, no subtitle."""
    assert made_from_artifacts([]) is None


def test_a_document_session_is_not_summarised_this_way():
    """Only media artifact kinds are summarised; documents use their transcript."""
    assert made_from_artifacts([("report", {"wordCount": 900})]) is None
    assert made_from_artifacts([("html", {"language": "html"})]) is None
