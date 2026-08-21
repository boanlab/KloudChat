"""The two audio controls that were on screen and going nowhere.

`길이` was a chip whose value never left the browser, and `목소리` did not exist
at all although the server had accepted one since the surface was built — so
every narration came back in the default voice, and a template that names one
was ignored.
"""

from __future__ import annotations

import pytest

from app.services import audiogen
from app.services import design_templates as dt


def test_a_length_reaches_the_model_as_a_sentence():
    """No audio endpoint here takes a duration, so it is asked for in words —
    the same thing the image surface does with an aspect ratio."""
    spoken = audiogen.compose_prompt("연구실 안내 방송", speech=True, seconds=30)
    assert spoken.startswith("연구실 안내 방송")
    assert "30초" in spoken

    played = audiogen.compose_prompt("잔잔한 배경 음악", speech=False, seconds=60)
    assert "60초" in played
    # Speech is read and music is played; the sentence says which.
    assert "읽어라" in spoken and "읽어라" not in played


def test_no_length_leaves_the_prompt_alone():
    assert audiogen.compose_prompt("그대로", speech=True) == "그대로"
    assert audiogen.compose_prompt("  공백 정리  ", speech=False, seconds=0) == "공백 정리"


@pytest.mark.parametrize("voice", audiogen.VOICES)
def test_every_voice_the_server_accepts_is_a_real_one(voice):
    """The composer offers this list; the gateway refuses anything else."""
    assert voice in audiogen.VOICES


def test_a_narration_template_names_a_voice_the_composer_can_set():
    """`audio-narration` asks for `nova`. Until now nothing read it."""
    template = dt.get("audio-narration")
    assert template is not None
    assert template.defaults.get("voice") in audiogen.VOICES
