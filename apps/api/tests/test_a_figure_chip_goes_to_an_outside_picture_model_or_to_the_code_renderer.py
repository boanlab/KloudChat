"""구조도·흐름도·개념도: an outside picture model draws it; inside the institution, code does."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import ImageRequest

OUTSIDE = {
    "id": "google/gemini-nano-banana-2.1",
    "kinds": ["image"],
    "dataBoundary": "external",
    "creditCost": 3870,
}
INSIDE = {
    "id": "strict-local/flux2-klein-4b",
    "kinds": ["image"],
    "dataBoundary": "self_hosted",
    "strictLocal": True,
}
CHAT = {"id": "vendor/main", "kinds": ["chat"], "dataBoundary": "external", "creditCost": 5}


async def _route(monkeypatch, models, asked, *, style="구조도", raw=False, planned=True):
    session = ChatSession(id="s", user_id="u", kind=SessionKind.image, model=asked)
    user = User(id="u", email="p@example.test", password_hash="h", name="P")
    went: list[str] = []

    async def owned(db, user, session_id):
        return session

    async def catalogue(force: bool = False):
        return {
            "models": models,
            "defaultChatModel": CHAT["id"],
            "defaultModelByKind": {"image": asked},
        }

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def policy():
        return Governance()

    async def no_history(db, session_id):
        return []

    async def paper(*args, **kwargs):
        went.append("paper")
        return ["drawn"] if planned else None

    async def by_code(*args, **kwargs):
        went.append("code")
        return ["code"]

    async def plan(request, **kwargs):
        return request, {}

    async def stop(**kwargs):
        went.append("plain")
        raise RuntimeError("stop")

    monkeypatch.setattr(sessions, "_owned", owned)
    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions, "_history", no_history)
    monkeypatch.setattr(sessions.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "_audit_media_boundary", nothing)
    monkeypatch.setattr(sessions, "has_headroom", lambda user, model: True)
    monkeypatch.setattr(sessions, "_draw_paper_figure", paper)
    monkeypatch.setattr(sessions, "_figure_by_code", by_code)
    monkeypatch.setattr(sessions.imagegen, "plan", plan)
    monkeypatch.setattr(sessions.imagegen, "generate", stop)
    db = SimpleNamespace(is_modified=lambda user: False)
    payload = ImageRequest(prompt="수집, 학습, 평가의 구조도", style=style, raw=raw, model=asked)
    try:
        await sessions.generate_images("s", payload, user, db, None)
    except RuntimeError:
        pass
    return went


@pytest.mark.asyncio
async def test_a_figure_chip_on_an_outside_picture_model_is_drawn_by_that_model(
    monkeypatch,
) -> None:
    for style in ("구조도", "흐름도", "개념도"):
        assert await _route(monkeypatch, [OUTSIDE, CHAT], OUTSIDE["id"], style=style) == ["paper"]


@pytest.mark.asyncio
async def test_a_figure_chip_on_a_picture_model_inside_the_institution_is_drawn_by_code(
    monkeypatch,
) -> None:
    assert await _route(monkeypatch, [INSIDE, CHAT], INSIDE["id"]) == ["code"]


@pytest.mark.asyncio
async def test_a_figure_with_no_layout_falls_back_to_code(monkeypatch) -> None:
    assert await _route(monkeypatch, [OUTSIDE, CHAT], OUTSIDE["id"], planned=False) == [
        "paper",
        "code",
    ]


@pytest.mark.asyncio
async def test_other_styles_and_an_edited_prompt_keep_the_plain_picture_path(monkeypatch) -> None:
    assert await _route(monkeypatch, [OUTSIDE, CHAT], OUTSIDE["id"], style="일러스트") == ["plain"]
    assert await _route(monkeypatch, [OUTSIDE, CHAT], OUTSIDE["id"], raw=True) == ["plain"]
