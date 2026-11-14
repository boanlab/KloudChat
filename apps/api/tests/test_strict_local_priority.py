"""strict-local safe models are a priority list the admin orders on the governance screen."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.models.chat import ChatSession
from app.models.governance import Governance
from app.models.user import User, UserRole
from app.routers import usage as usage_router
from app.routers.sessions import _PrivacyResolution, _resolve_privacy, _safe_models
from app.schemas.admin import GovernanceIn
from app.services import governance


def _strict(model_id: str, **extra) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "kinds": ["chat"],
        "creditCost": 0,
        **extra,
    }


def _external(model_id: str) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "dataBoundary": "external",
        "strictLocal": False,
        "kinds": ["chat"],
        "creditCost": 1,
    }


A, B, C = _strict("strict-local/a"), _strict("strict-local/b"), _strict("strict-local/c")
EXTERNAL = _external("external/model")
CATALOGUE = [A, B, C, EXTERNAL]


def _request() -> Request:
    return Request(
        {"type": "http", "method": "PUT", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
    )


def _admin() -> User:
    return User(email="admin@example.test", password_hash="x", name="Admin", role=UserRole.admin)


class _Db:
    def __init__(self, policy: Governance):
        self.policy = policy
        self.added: list[object] = []

    async def get(self, model, key):
        assert model is Governance and key == "default"
        return self.policy

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


@pytest.fixture
def catalogue(monkeypatch):
    async def listed():
        return {"models": CATALOGUE, "litellmAvailable": True, "defaultChatModel": ""}

    async def sweep(_db):
        return 0

    monkeypatch.setattr(usage_router.model_service, "list_models", listed)
    monkeypatch.setattr(usage_router.model_service, "list_models_for_egress", listed)
    monkeypatch.setattr(usage_router.governance, "sweep_expired", sweep)
    monkeypatch.setattr(usage_router.governance, "invalidate", lambda: None)


@pytest.mark.asyncio
async def test_the_admin_order_is_stored_as_sent(catalogue) -> None:
    policy = Governance(privacy_safe_model_ids=[])
    await usage_router.put_governance(
        GovernanceIn(privacy_safe_model_ids=[C["id"], A["id"], C["id"]]),
        _request(),
        _admin(),
        _Db(policy),
    )
    # Not re-sorted to the catalogue (a, b, c); a repeated id is kept once.
    assert policy.privacy_safe_model_ids == [C["id"], A["id"]]

    read = await usage_router.get_governance(_admin(), _Db(policy))
    assert read["privacySafeModelIds"] == [C["id"], A["id"]]


@pytest.mark.asyncio
async def test_only_strict_local_chat_models_can_be_listed(catalogue) -> None:
    policy = Governance(privacy_safe_model_ids=[A["id"]])
    with pytest.raises(HTTPException) as refused:
        await usage_router.put_governance(
            GovernanceIn(privacy_safe_model_ids=[B["id"], EXTERNAL["id"]]),
            _request(),
            _admin(),
            _Db(policy),
        )
    assert refused.value.detail == "privacy_safe_models_must_be_strict_local"
    assert policy.privacy_safe_model_ids == [A["id"]]


@pytest.mark.asyncio
async def test_a_model_no_longer_strict_local_drops_out_of_the_read_order(catalogue) -> None:
    policy = Governance(privacy_safe_model_ids=[B["id"], "strict-local/gone", A["id"]])
    read = await usage_router.get_governance(_admin(), _Db(policy))
    assert read["privacySafeModelIds"] == [B["id"], A["id"]]


def test_safe_models_follow_the_stored_order_and_the_account_allowance() -> None:
    policy = Governance(privacy_safe_model_ids=[C["id"], A["id"], B["id"]])
    assert [m["id"] for m in _safe_models(policy, CATALOGUE, set())] == [C["id"], A["id"], B["id"]]
    # An account limited to some models gets the first of those it may use.
    allowed = {A["id"], B["id"], EXTERNAL["id"]}
    assert [m["id"] for m in _safe_models(policy, CATALOGUE, allowed)] == [A["id"], B["id"]]


async def _decide(policy: Governance, *, chosen: str | None = None, internal: bool = False):
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    sources = {"current_input": "send person@example.com"}
    first = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=CATALOGUE,
        requested=[EXTERNAL],
        sources=sources,
        explicit_action=None,
        decision_token=None,
        internal_data=internal,
    )
    if internal:
        return first, None
    assert isinstance(first, JSONResponse)
    card = json.loads(first.body)
    routed = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=CATALOGUE,
        requested=[EXTERNAL],
        sources=sources,
        explicit_action="route_strict_local",
        decision_token=card["decisionToken"],
        chosen_model_id=chosen,
    )
    return routed, card


@pytest.mark.asyncio
async def test_the_privacy_card_offers_every_checked_model_in_order_and_takes_the_pick(
    monkeypatch,
) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    policy = Governance(external_data_guard=True, privacy_safe_model_ids=[C["id"], A["id"]])

    routed, card = await _decide(policy)
    assert [m["id"] for m in card["safeModels"]] == [C["id"], A["id"]]
    assert isinstance(routed, _PrivacyResolution) and routed.models == [C]

    picked, _ = await _decide(policy, chosen=A["id"])
    assert isinstance(picked, _PrivacyResolution) and picked.models == [A]

    # A model the card did not offer falls back to the first of the list.
    stray, _ = await _decide(policy, chosen=B["id"])
    assert isinstance(stray, _PrivacyResolution) and stray.models == [C]


@pytest.mark.asyncio
async def test_internal_material_without_a_twin_goes_to_the_first_listed_model(monkeypatch) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    policy = Governance(
        external_data_guard=True,
        internal_data_strict_local=True,
        privacy_safe_model_ids=[B["id"], A["id"]],
    )
    routed, _ = await _decide(policy, internal=True)
    assert isinstance(routed, _PrivacyResolution)
    assert routed.models == [B]


@pytest.mark.asyncio
async def test_internal_material_prefers_the_chosen_models_own_strict_local_twin(
    monkeypatch,
) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    local = {**_external("local/a"), "dataBoundary": "self_hosted"}
    policy = Governance(
        external_data_guard=True,
        internal_data_strict_local=True,
        privacy_safe_model_ids=[B["id"], A["id"]],
    )
    user = User(email="person@example.test", password_hash="hash", name="Person")
    routed = await _resolve_privacy(
        user=user,
        session=ChatSession(user_id=user.id),
        policy=policy,
        catalogue=[*CATALOGUE, local],
        requested=[local],
        sources={"current_input": "summarise the attached file"},
        explicit_action=None,
        decision_token=None,
        internal_data=True,
    )
    assert isinstance(routed, _PrivacyResolution)
    # local/a's twin strict-local/a, though strict-local/b is first in the list.
    assert routed.models == [A]
