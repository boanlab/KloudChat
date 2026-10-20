"""Default models are the admin's choice per surface; price never picks one."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.routers import sessions
from app.services import models as model_service


def _row(model_id: str, kinds: list[str], *, cost: int = 0, strict: bool = False,
         modality: str = "chat") -> dict:
    return {"id": model_id, "kinds": kinds, "creditCost": cost, "inputCreditCost": 0,
            "strictLocal": strict, "modality": modality,
            "dataBoundary": "self_hosted" if strict else "hybrid"}


DOCS = ["chat", "report", "slides"]
# A self-hosted install: every model costs 0, and the coding model sorts first.
CODER = _row("local/qwen3-coder-next", DOCS)
FLASH = _row("local/qwen3.8-flash", DOCS)
BIG = _row("local/qwen3.8-27b", DOCS)
STRICT_CODER = _row("strict-local/qwen3-coder-next", ["chat"], strict=True)
STRICT_FLASH = _row("strict-local/qwen3.8-flash", ["chat"], strict=True)
PRICEY_IMAGE = _row("vendor/pricey-image", ["image"], cost=900, modality="image")
CHEAP_IMAGE = _row("vendor/cheap-image", ["image"], cost=1, modality="image")
MODELS = [CODER, BIG, FLASH, STRICT_CODER, STRICT_FLASH, PRICEY_IMAGE, CHEAP_IMAGE]
CATALOGUE = {
    "models": MODELS,
    "defaultChatModel": FLASH["id"],
    "defaultModelByKind": {"report": BIG["id"], "slides": BIG["id"]},
    "defaultAvModelByMode": {},
}


def _user(allowed: list[str] | None = None) -> User:
    return User(id="u-def", email="d@example.test", password_hash="h", name="D",
                allowed_models=allowed or [])


def _usable(user: User, kind: str) -> list[dict]:
    return sessions._allowed_models(user, MODELS, kind=kind)


def test_a_conversation_with_no_model_runs_on_the_admins_chat_default_not_the_coder() -> None:
    """An agent saved with no model (the form's default) reaches the turn with nothing picked."""
    picked = model_service.pick_default(_usable(_user(), "chat"), CATALOGUE, "chat")
    assert picked is FLASH


def test_report_and_slides_take_their_own_default_then_the_chat_default() -> None:
    assert model_service.pick_default(_usable(_user(), "report"), CATALOGUE, "report") is BIG
    no_report = {**CATALOGUE, "defaultModelByKind": {}}
    assert model_service.pick_default(_usable(_user(), "slides"), no_report, "slides") is FLASH


def test_a_chat_default_the_account_may_not_use_gives_way_in_catalogue_order() -> None:
    """Past the admin's defaults the first allowed model answers, never the cheapest."""
    pricey = _row("vendor/pricey", DOCS, cost=50)
    cheap = _row("vendor/cheap", DOCS, cost=1)
    narrow = _user([pricey["id"], cheap["id"]])
    usable = sessions._allowed_models(narrow, [pricey, cheap, *MODELS], kind="chat")
    assert model_service.pick_default(usable, CATALOGUE, "chat") is pricey


def test_a_picture_without_a_configured_default_is_not_drawn_on_the_cheapest() -> None:
    assert model_service.pick_default(_usable(_user(), "image"), CATALOGUE, "image") is None
    chosen = {**CATALOGUE, "defaultModelByKind": {"image": PRICEY_IMAGE["id"]}}
    assert model_service.pick_default(_usable(_user(), "image"), chosen, "image") is PRICEY_IMAGE
    # A default outside the allowlist: the first allowed one, as for chat.
    narrow = _user([CHEAP_IMAGE["id"]])
    assert model_service.pick_default(_usable(narrow, "image"), chosen, "image") is CHEAP_IMAGE


@pytest.mark.asyncio
async def test_media_calls_follow_the_defaults_and_a_strict_turn_takes_the_defaults_twin() -> None:
    user = _user()
    outside = sessions._MediaBoundary(False, "x", "", [])
    defaults = model_service.configured_defaults(CATALOGUE, "chat")
    assert sessions._media_model(
        user, MODELS, kind="chat", boundary=outside, defaults=defaults
    ) is FLASH
    inside = sessions._MediaBoundary(True, "x", "internal_data", [])
    assert sessions._media_model(
        user, MODELS, kind="chat", boundary=inside, defaults=defaults
    ) is STRICT_FLASH
    # No picture default: nothing drawn rather than the cheapest model.
    assert sessions._media_model(
        user, MODELS, kind="image", boundary=outside,
        defaults=model_service.configured_defaults(CATALOGUE, "image"),
    ) is None


def test_configured_defaults_name_only_the_admins_choices() -> None:
    assert model_service.configured_defaults(CATALOGUE, "chat") == (FLASH["id"],)
    assert model_service.configured_defaults(CATALOGUE, "report") == (BIG["id"], FLASH["id"])
    assert model_service.configured_defaults(CATALOGUE, "image") == ()


@pytest.mark.asyncio
async def test_audio_without_a_default_says_so_instead_of_choosing(monkeypatch) -> None:
    from app.schemas.chat import AudioRequest

    async def catalogue(force: bool = False):
        return {**CATALOGUE, "models": [*MODELS, _row("openai/gpt-audio-mini", ["av"],
                                                      cost=5, modality="audio")]}

    async def owned(db, user, session_id):
        return object()

    monkeypatch.setattr(model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions, "_owned", owned)
    with pytest.raises(HTTPException) as refused:
        await sessions.generate_audio(
            "s1", AudioRequest(prompt="안내 방송", audio_kind="narration"), _user(), None
        )
    assert refused.value.status_code == 409
    assert refused.value.detail == "default_audio_model_unset"


@pytest.mark.asyncio
async def test_an_instance_serving_no_audio_or_video_may_leave_them_unset(monkeypatch) -> None:
    from app.routers.admin import _check_model_roles
    from app.schemas.admin import SystemSettingsIn
    from app.services import settings_store

    async def all_values(force: bool = False):
        return {}

    async def catalogue(force: bool = False):
        return CATALOGUE

    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    await _check_model_roles(SystemSettingsIn(model_audio="", model_video=""))
