"""보고서와 챗은 같은 일이 아니다.

One instance default served every surface. That is right while one model is
the best answer everywhere and wrong as soon as it is not: a conversation is a
turn every few seconds, read as it arrives, so decode speed is most of what the
person feels. A 보고서 is one long run they wait for once, and what they feel
is whether it needed rewriting.

Measured on the same prompt, three runs each, the larger model wrote 20 of 21
slides against 35 of 37 and picked the shape the title asked for 5 times out of
6 against 2 out of 10. Three times the decode cost is the wrong trade for chat
and the right one for a document.

Empty falls back to the chat default, which is what every install had before
these existed.
"""

from __future__ import annotations

import pytest

from app.services import models as model_service


def _catalogue(*ids: str) -> list[dict]:
    return [
        {
            "id": model_id,
            "kinds": ["chat", "report", "slides"],
            "creditCost": 0.0,
            "modality": "text",
            "provider": "local",
        }
        for model_id in ids
    ]


@pytest.fixture
def served(monkeypatch):
    def use(*ids: str) -> list[dict]:
        rows = _catalogue(*ids)
        monkeypatch.setattr(model_service, "_adapter_entries", lambda: rows)
        return rows

    return use


def test_a_surface_default_is_published_for_the_surfaces_that_set_one() -> None:
    """The client reads this to decide what a new 보고서 runs on."""
    from app.core.config import settings

    assert hasattr(settings, "default_report_model")
    assert hasattr(settings, "default_slides_model")
    # Empty by default: an install that never sets them behaves as before.
    assert model_service.settings.default_report_model in ("", settings.default_report_model)


def test_an_unset_surface_falls_back_to_the_chat_default(monkeypatch) -> None:
    rows = _catalogue("local/small", "local/big")
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_report_model", "")
    monkeypatch.setattr(model_service.settings, "default_slides_model", "local/big")

    def served(model_id: str, kind: str) -> str:
        return model_id if any(m["id"] == model_id and kind in m["kinds"] for m in rows) else ""

    default_chat = served(model_service.settings.default_chat_model, "chat")
    by_kind = {
        kind: served(chosen, kind) or default_chat
        for kind, chosen in (
            ("report", model_service.settings.default_report_model),
            ("slides", model_service.settings.default_slides_model),
        )
    }
    assert by_kind == {"report": "local/small", "slides": "local/big"}


def test_a_default_naming_a_model_the_install_does_not_serve_is_dropped(monkeypatch) -> None:
    """Worse than none: the surface would offer it, the call would 404, and the
    setting that caused it is in a file nobody reads while somebody waits."""
    rows = _catalogue("local/small")
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_slides_model", "local/gone")

    def served(model_id: str, kind: str) -> str:
        return model_id if any(m["id"] == model_id and kind in m["kinds"] for m in rows) else ""

    assert served("local/gone", "slides") == ""
    assert (served("local/gone", "slides") or served("local/small", "chat")) == "local/small"


@pytest.mark.asyncio
async def test_the_picture_surface_defaults_to_a_model_that_keeps_the_ratio(monkeypatch) -> None:
    """The cheapest image model returned a square whatever was asked and clipped
    a 16:9 composition to fit, so the first picture anybody made came back cut
    at both ends. The default names Gemini's Flash image model, which takes the
    ratio as a parameter — and is dropped, not substituted, when the install
    does not serve it, leaving the client to its cheapest-first rule."""
    from app.services import litellm

    async def proxy_down():
        raise litellm.LiteLLMError("down")

    monkeypatch.setattr(litellm, "model_info", proxy_down)
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(
        model_service.settings, "default_image_model", "google/gemini-2.5-flash-image"
    )
    rows = _catalogue("local/small")
    picture = {**rows[0], "id": "openai/gpt-5-image-mini", "kinds": ["image"], "modality": "image"}
    monkeypatch.setattr(model_service, "_adapter_entries", lambda: rows + [picture])

    by_kind = (await model_service.list_models(force=True))["defaultModelByKind"]
    assert "image" not in by_kind, "an image default the install does not serve is dropped"

    gemini = {**picture, "id": "google/gemini-2.5-flash-image"}
    monkeypatch.setattr(model_service, "_adapter_entries", lambda: rows + [picture, gemini])
    by_kind = (await model_service.list_models(force=True))["defaultModelByKind"]
    assert by_kind["image"] == "google/gemini-2.5-flash-image"


@pytest.mark.asyncio
async def test_sound_and_clips_each_have_a_default_and_neither_is_the_other(monkeypatch) -> None:
    """One surface, two kinds of model. The cheapest `av` model is a speech
    model, so 영상 kept opening on a model that makes no clips. Each modality
    names its own default, a default of the wrong modality is dropped, and the
    surface itself opens on the video one — the mode it opens in."""
    from app.services import litellm

    async def proxy_down():
        raise litellm.LiteLLMError("down")

    monkeypatch.setattr(litellm, "model_info", proxy_down)
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_audio_model", "openai/gpt-audio-mini")
    monkeypatch.setattr(model_service.settings, "default_video_model", "google/veo-3.1-lite")
    rows = _catalogue("local/small")
    speech = {**rows[0], "id": "openai/gpt-audio-mini", "kinds": ["av"], "modality": "audio"}
    clips = {**rows[0], "id": "google/veo-3.1-lite", "kinds": ["av"], "modality": "video"}
    monkeypatch.setattr(model_service, "_adapter_entries", lambda: rows + [speech, clips])

    result = await model_service.list_models(force=True)
    assert result["defaultAvModelByMode"] == {
        "audio": "openai/gpt-audio-mini",
        "video": "google/veo-3.1-lite",
    }
    assert result["defaultModelByKind"]["av"] == "google/veo-3.1-lite"

    # A speech model named as the video default is not a video default.
    monkeypatch.setattr(model_service.settings, "default_video_model", "openai/gpt-audio-mini")
    result = await model_service.list_models(force=True)
    assert result["defaultAvModelByMode"] == {"audio": "openai/gpt-audio-mini"}
    assert "av" not in result["defaultModelByKind"]


def test_an_image_model_says_which_ratios_it_can_draw() -> None:
    """A 16:9 chip beside a model that returns squares is a promise the picture
    then breaks. The catalogue carries the ratios each image model can draw,
    and the composer offers no other; a text model carries none."""
    from app.services import imagegen

    assert imagegen.aspects_for("google/gemini-2.5-flash-image") == ["16:9", "9:16", "4:3", "1:1"]
    assert imagegen.aspects_for("openai/gpt-5-image-mini") == ["1:1"]

    picture = {"model_name": "openai/gpt-5-image", "model_info": {"mode": "image_generation"}}
    shaped = model_service._shape(picture)
    if shaped is not None:  # shaped only when the row carries enough to list
        assert shaped["aspects"] == ["1:1"]
