"""Pictures, charts and figures follow the chat turn's egress rules: internal material or a
strict-local conversation stays on strict-local models, personal data is masked,
and an account's allowlist holds; with no model inside the boundary nothing is sent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import ImageRequest

EXTERNAL_IMAGE = {
    "id": "bytedance-seed/seedream-5-0-flash", "kinds": ["image"], "dataBoundary": "external",
    "creditCost": 431,
}
OTHER_IMAGE = {
    "id": "x-ai/grok-imagine-image-2.0", "kinds": ["image"], "dataBoundary": "external",
    "creditCost": 100,
}
STRICT_IMAGE = {
    "id": "strict-local/z-image-turbo", "kinds": ["image"], "dataBoundary": "self_hosted",
    "strictLocal": True, "creditCost": 0,
}
EXTERNAL_CHAT = {
    "id": "vendor/big", "kinds": ["chat"], "dataBoundary": "external", "creditCost": 5,
}
STRICT_CHAT = {
    "id": "strict-local/qwen", "kinds": ["chat"], "dataBoundary": "self_hosted",
    "strictLocal": True, "creditCost": 0,
}
EVERYTHING = [EXTERNAL_IMAGE, OTHER_IMAGE, STRICT_IMAGE, EXTERNAL_CHAT, STRICT_CHAT]


def _user(allowed: list[str] | None = None) -> User:
    return User(
        id="u-pic", email="person@example.test", password_hash="hash", name="Person",
        allowed_models=allowed or [],
    )


def _session(model: str = EXTERNAL_CHAT["id"], project: str | None = None) -> ChatSession:
    return ChatSession(
        id="s-pic", user_id="u-pic", kind=SessionKind.report, model=model, project_id=project
    )


@pytest.fixture
def policy(monkeypatch):
    holder = {"policy": Governance(internal_data_strict_local=True)}

    async def current():
        return holder["policy"]

    async def no_history(db, session_id):
        return []

    monkeypatch.setattr(sessions, "_require_egress_policy", current)
    monkeypatch.setattr(sessions, "_history", no_history)
    return holder


@pytest.mark.asyncio
async def test_internal_material_keeps_pictures_on_strict_local_models(policy) -> None:
    user = _user()
    inside = await sessions._media_boundary(
        None, _session(project="p1"), EVERYTHING, "매출 구조도"
    )
    assert inside.strict and inside.reason == "internal_data"
    picked = sessions._media_model(
        user, EVERYTHING, kind="image", boundary=inside,
        asked=EXTERNAL_IMAGE["id"], default=EXTERNAL_IMAGE["id"],
    )
    assert picked is STRICT_IMAGE
    planner = sessions._media_model(
        user, EVERYTHING, kind="chat", boundary=inside, asked=EXTERNAL_CHAT["id"]
    )
    assert planner is STRICT_CHAT

    # A strict-local conversation counts as inside even without a project.
    own = await sessions._media_boundary(None, _session(STRICT_CHAT["id"]), EVERYTHING, "x")
    assert own.strict and own.reason == "strict_session"

    # Nothing inside the boundary: no model at all, and the refusal says why.
    bare = [EXTERNAL_IMAGE, EXTERNAL_CHAT]
    assert sessions._media_model(user, bare, kind="image", boundary=inside) is None
    refused = sessions._refuse_outside_boundary(inside, "image")
    assert refused.status_code == 409 and refused.detail == "internal_data_requires_strict_local"

    # The organisation leaving internal material free: the asked model stands.
    policy["policy"] = Governance(internal_data_strict_local=False)
    free = await sessions._media_boundary(None, _session(project="p1"), EVERYTHING, "x")
    assert not free.strict
    assert sessions._media_model(
        user, EVERYTHING, kind="image", boundary=free, asked=OTHER_IMAGE["id"]
    ) is OTHER_IMAGE


@pytest.mark.asyncio
async def test_personal_data_in_a_picture_prompt_is_masked(policy) -> None:
    said = "홍길동 010-1234-5678 명함을 그려 줘"
    policy["policy"] = Governance(internal_data_strict_local=True, pii_masking=True)
    masked = await sessions._media_boundary(None, _session(), EVERYTHING, said)
    assert not masked.strict and masked.reason == "mask_external"
    assert "010-1234-5678" not in masked.text
    assert "010-1234-5678" not in masked.carry("연락처 010-1234-5678")

    # Guard on, masking off: no dialog on a picture, so masked as for a report.
    policy["policy"] = Governance(
        internal_data_strict_local=True, external_data_guard=True, pii_masking=False
    )
    guarded = await sessions._media_boundary(None, _session(), EVERYTHING, said)
    assert not guarded.strict and guarded.reason == "mask_external"
    assert "010-1234-5678" not in guarded.text

    plain = await sessions._media_boundary(None, _session(), EVERYTHING, "여우 그림")
    assert not plain.strict and plain.reason == "" and plain.text == "여우 그림"


@pytest.mark.asyncio
async def test_a_picture_without_a_model_takes_the_default_and_the_allowlist_holds(
    policy,
) -> None:
    user = _user()
    outside = await sessions._media_boundary(None, _session(), EVERYTHING, "여우")
    # No model asked: the admin default, not the cheapest.
    assert sessions._media_model(
        user, EVERYTHING, kind="image", boundary=outside, default=EXTERNAL_IMAGE["id"]
    ) is EXTERNAL_IMAGE
    # A default this account may not use: the first one it may.
    narrow = _user([OTHER_IMAGE["id"]])
    assert sessions._media_model(
        narrow, EVERYTHING, kind="image", boundary=outside, default=EXTERNAL_IMAGE["id"]
    ) is OTHER_IMAGE
    # Asking for a model outside the allowlist is refused, not swapped.
    with pytest.raises(HTTPException) as refused:
        sessions._refuse_unallowed(narrow, EVERYTHING, EXTERNAL_IMAGE["id"])
    assert refused.value.status_code == 403 and refused.value.detail == "model_not_allowed"
    sessions._refuse_unallowed(narrow, EVERYTHING, OTHER_IMAGE["id"])
    sessions._refuse_unallowed(user, EVERYTHING, EXTERNAL_IMAGE["id"])


@pytest.mark.asyncio
async def test_a_project_picture_reaches_only_the_strict_local_model(
    policy, monkeypatch
) -> None:
    session = _session(project="p1")
    asked: list[str] = []
    audits: list[str] = []

    async def owned(db, user, session_id):
        return session

    async def catalogue(force: bool = False):
        return {"models": EVERYTHING, "defaultModelByKind": {"image": EXTERNAL_IMAGE["id"]}}

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def design(db, user, session):
        return None

    async def generate(**kwargs):
        asked.append(kwargs["model"])
        raise RuntimeError("stop after the call is addressed")

    async def audit(user, request, action, detail, *, metadata=None):
        audits.append(action)

    monkeypatch.setattr(sessions, "_owned", owned)
    monkeypatch.setattr(sessions.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "design_for", design)
    monkeypatch.setattr(sessions, "has_headroom", lambda user, model: True)
    monkeypatch.setattr(sessions.imagegen, "generate", generate)
    monkeypatch.setattr(sessions, "_audit_policy", audit)
    db = SimpleNamespace(is_modified=lambda user: False)
    payload = ImageRequest(prompt="사내 조직도", model=EXTERNAL_IMAGE["id"], style="없음")

    with pytest.raises(RuntimeError):
        await sessions.generate_images("s-pic", payload, _user(), db, None)
    assert asked == [STRICT_IMAGE["id"]]
    assert audits == ["privacy.internal_strict_local"]

    # No strict-local picture model: refused before any upstream call.
    async def bare(force: bool = False):
        return {"models": [EXTERNAL_IMAGE, EXTERNAL_CHAT], "defaultModelByKind": {}}

    monkeypatch.setattr(sessions.model_service, "list_models", bare)
    with pytest.raises(HTTPException) as refused:
        await sessions.generate_images("s-pic", payload, _user(), db, None)
    assert refused.value.status_code == 409
    assert asked == [STRICT_IMAGE["id"]]
    assert audits[-1] == "privacy.internal_data_requires_strict_local"
