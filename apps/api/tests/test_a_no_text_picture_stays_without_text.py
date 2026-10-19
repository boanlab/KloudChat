"""The 「글자 없음」 option holds when the prompt planner fails, and the planner is a chat model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import ImageRequest
from app.services import imagegen

IMAGE = {
    "id": "bytedance-seed/seedream-5-0-flash", "kinds": ["image"], "dataBoundary": "external",
    "creditCost": 431,
}
CHEAP_CHAT = {"id": "vendor/tiny", "kinds": ["chat"], "dataBoundary": "external", "creditCost": 1}
DEFAULT_CHAT = {"id": "vendor/main", "kinds": ["chat"], "dataBoundary": "external", "creditCost": 5}
MODELS = [IMAGE, CHEAP_CHAT, DEFAULT_CHAT]


def test_the_no_text_rule_is_in_the_prompt_without_the_planner() -> None:
    composed = imagegen.compose_prompt(
        "가을 도서관 풍경 SNS 썸네일", aspect="1:1", style="자동", labels="none"
    )
    assert "No text anywhere" in composed
    korean = imagegen.compose_prompt("정수장 구조도", aspect="16:9", style="자동", labels="ko")
    assert "exact Korean strings" in korean
    assert "No text" not in imagegen.compose_prompt("풍경", aspect="1:1", style="자동")


@pytest.mark.asyncio
async def test_a_failed_planner_still_sends_the_no_text_rule_and_the_planner_is_the_chat_default(
    monkeypatch,
) -> None:
    session = ChatSession(
        id="s-img", user_id="u-img", kind=SessionKind.image, model=IMAGE["id"]
    )
    user = User(id="u-img", email="p@example.test", password_hash="h", name="P")
    planners: list[str] = []
    prompts: list[str] = []

    async def owned(db, user, session_id):
        return session

    async def catalogue(force: bool = False):
        return {
            "models": MODELS,
            "defaultChatModel": DEFAULT_CHAT["id"],
            "defaultModelByKind": {"image": IMAGE["id"]},
        }

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def policy():
        return Governance()

    async def no_history(db, session_id):
        return []

    async def failed_plan(request, **kwargs):
        planners.append(kwargs["model"])
        return request.strip(), {"inputTokens": 0, "outputTokens": 0}

    async def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        raise RuntimeError("stop after the call is addressed")

    monkeypatch.setattr(sessions, "_owned", owned)
    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions, "_history", no_history)
    monkeypatch.setattr(sessions.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "design_for", nothing)
    monkeypatch.setattr(sessions, "_audit_media_boundary", nothing)
    monkeypatch.setattr(sessions, "has_headroom", lambda user, model: True)
    monkeypatch.setattr(sessions.imagegen, "plan", failed_plan)
    monkeypatch.setattr(sessions.imagegen, "generate", generate)
    db = SimpleNamespace(is_modified=lambda user: False)
    payload = ImageRequest(
        prompt="가을 도서관 풍경 SNS 썸네일. 창밖의 단풍.", aspect="1:1", labels="none"
    )

    with pytest.raises(RuntimeError):
        await sessions.generate_images("s-img", payload, user, db, None)
    assert planners == [DEFAULT_CHAT["id"]]
    assert "No text anywhere" in prompts[0]
