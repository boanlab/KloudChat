from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.governance import Governance
from app.models.user import User, UserRole
from app.routers import models as models_router
from app.schemas.chat import SessionPatch


def _model(
    model_id: str,
    *,
    boundary: str = "external",
    strict: bool = False,
    privacy_only: bool = False,
    input_cost: int = 1,
    output_cost: int = 1,
) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "kinds": ["chat"],
        "dataBoundary": boundary,
        "strictLocal": strict,
        "privacyOnly": privacy_only,
        "inputCreditCost": input_cost,
        "creditCost": output_cost,
        "contextWindow": 32_000,
        "supportsTools": False,
    }


@pytest.mark.asyncio
async def test_refresh_models_keeps_user_scoped_auto_catalogue_contract(monkeypatch) -> None:
    classifier = _model(
        "strict-local/classifier",
        boundary="self_hosted",
        strict=True,
        privacy_only=True,
        input_cost=0,
        output_cost=0,
    )
    economy = _model("external/economy")
    blocked = _model("external/blocked")
    user = User(
        email="admin@example.test",
        password_hash="hash",
        name="Admin",
        role=UserRole.admin,
        allowed_models=[classifier["id"], economy["id"]],
    )
    forced: list[bool] = []
    invalidated: list[bool] = []

    async def catalogue(*, force: bool = False):
        forced.append(force)
        return {
            "models": [classifier, economy, blocked],
            "litellmAvailable": True,
            "defaultChatModel": economy["id"],
        }

    async def policy():
        return Governance(
            adaptive_routing_enabled=True,
            adaptive_classifier_model_id=classifier["id"],
            adaptive_economy_model_ids=[blocked["id"], economy["id"]],
        )

    monkeypatch.setattr(
        models_router.model_service,
        "invalidate_cache",
        lambda: invalidated.append(True),
    )
    monkeypatch.setattr(models_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(models_router.governance, "current", policy)

    result = await models_router.refresh_models(user)

    assert invalidated == [True]
    assert forced == [True]
    assert [model["id"] for model in result["models"]] == [classifier["id"], economy["id"]]
    assert result["autoRouting"] == {
        "enabled": True,
        "available": True,
        "reason": None,
        "classifierModelId": classifier["id"],
        "economyModelIds": [economy["id"]],
        # The upgrade lane's own switch defaults to off.
        "qualityAvailable": False,
        "qualityReason": "disabled",
        "qualityModelIds": [],
    }


def test_session_patch_rejects_explicit_null_routing_mode() -> None:
    assert SessionPatch.model_validate({}).model_dump(exclude_unset=True) == {}

    with pytest.raises(ValidationError, match="routing_mode_must_not_be_null"):
        SessionPatch.model_validate({"routingMode": None})
