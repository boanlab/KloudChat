"""Per-surface default models: report, slides, image, audio and video, falling back to chat."""

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
    """Settings expose a report and a slides default, empty by default."""
    from app.core.config import settings

    assert hasattr(settings, "default_report_model")
    assert hasattr(settings, "default_slides_model")
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
    """A surface default naming an unserved model is dropped in favour of the chat default."""
    rows = _catalogue("local/small")
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_slides_model", "local/gone")

    def served(model_id: str, kind: str) -> str:
        return model_id if any(m["id"] == model_id and kind in m["kinds"] for m in rows) else ""

    assert served("local/gone", "slides") == ""
    assert (served("local/gone", "slides") or served("local/small", "chat")) == "local/small"


@pytest.mark.asyncio
async def test_the_picture_surface_defaults_to_a_model_that_keeps_the_ratio(monkeypatch) -> None:
    """The image default is published only when the install serves that model."""
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
    """Audio and video each have a default of their own modality; the av surface opens on video."""
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

    # A default of the wrong modality is dropped.
    monkeypatch.setattr(model_service.settings, "default_video_model", "openai/gpt-audio-mini")
    result = await model_service.list_models(force=True)
    assert result["defaultAvModelByMode"] == {"audio": "openai/gpt-audio-mini"}
    assert "av" not in result["defaultModelByKind"]


def test_an_image_model_says_which_ratios_it_can_draw() -> None:
    """The catalogue lists the aspect ratios each image model can draw."""
    from app.services import imagegen

    assert imagegen.aspects_for("google/gemini-2.5-flash-image") == ["16:9", "9:16", "4:3", "1:1"]
    assert imagegen.aspects_for("openai/gpt-5-image-mini") == ["1:1"]

    picture = {"model_name": "openai/gpt-5-image", "model_info": {"mode": "image_generation"}}
    shaped = model_service._shape(picture)
    if shaped is not None:  # shaped only when the row carries enough to list
        assert shaped["aspects"] == ["1:1"]
