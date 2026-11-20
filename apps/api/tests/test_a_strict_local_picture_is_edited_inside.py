"""이미지 고치기 inside: a self-hosted model that edits (Qwen-Image-Edit 2511) changes a picture
that has to stay inside — a strict-local conversation, a Super Agent strict one, a picture a
strict-local model drew, internal material under the internal-material rule — picked for the turn
whatever picture model the person draws with. It only edits, so nothing asks it for a new picture;
with no strict-local editor the edit is still refused."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import test_an_image_edit_sends_the_source_and_keeps_it as edit_tests
from fastapi import HTTPException
from test_an_image_edit_sends_the_source_and_keeps_it import (
    CATALOGUE,
    DRAWER,
    EDITOR,
    STRICT_CHAT,
    STRICT_DRAWER,
    _png,
)

from app.models.chat import ChatSession, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import ImageRequest
from app.services import imagegen, models

#: the edit tests' database, files and stubbed gateway
stack = edit_tests.stack

EDIT_ONLY = "strict-local/qwen-image-edit-2511"
STRICT_EDITOR = {
    "id": EDIT_ONLY, "kinds": [], "modality": "image", "dataBoundary": "self_hosted",
    "strictLocal": True, "creditCost": 0, "editsImages": True, "drawsImages": False,
}
OUTSIDE_CHAT = {
    "id": "vendor/chat", "kinds": ["chat"], "modality": "chat", "dataBoundary": "external",
    "creditCost": 10,
}
WITH_EDITOR = {
    "models": [EDITOR, DRAWER, STRICT_DRAWER, STRICT_EDITOR, STRICT_CHAT, OUTSIDE_CHAT],
    "defaultModelByKind": {"image": EDITOR["id"]},
}


def _self_hosted(model_id: str, **info) -> dict:
    return {
        "model_name": model_id,
        "litellm_params": {"model": f"openai/{model_id}", "api_base": "http://imagegen-shim:9100/v1"},
        "model_info": {"mode": "image_generation", "input_cost_per_token": 0,
                       "output_cost_per_token": 0, "kchat_data_boundary": "self_hosted",
                       "kchat_strict_local": True, **info},
    }


# ── capability ─────────────────────────────────────────────────────────────


def test_the_self_hosted_editor_edits_and_draws_nothing_new():
    assert imagegen.edits_images(EDIT_ONLY, {})
    assert not imagegen.draws_images(EDIT_ONLY, {})
    for drawer in ("strict-local/z-image-turbo", "strict-local/qwen-image-2512", EDITOR["id"]):
        assert imagegen.draws_images(drawer, {})
    # the gateway's declaration wins either way
    assert imagegen.draws_images(EDIT_ONLY, {"kchat_draws_images": True})
    assert not imagegen.draws_images("strict-local/other-edit", {"kchat_draws_images": False})


def test_the_catalogue_puts_the_editor_on_no_surface():
    row = models._shape(_self_hosted(EDIT_ONLY))
    assert row is not None and row["strictLocal"] is True
    assert row["editsImages"] is True and row["drawsImages"] is False
    # no picture surface lists it, so no default, picker or strict pick draws with it
    assert "image" not in row["kinds"]
    drawer = models._shape(_self_hosted("strict-local/z-image-turbo"))
    assert drawer is not None and drawer["kinds"] == ["image"]
    assert drawer["drawsImages"] is True and drawer["editsImages"] is False
    chat = models._shape({"model_name": "vendor/chat",
                          "model_info": {"mode": "chat", "output_cost_per_token": 0.00001}})
    assert chat is not None and chat["drawsImages"] is False and chat["kinds"][0] == "chat"


def test_a_new_picture_never_goes_to_the_editor():
    me = User(id="me", email="me@example.test", name="나", password_hash="x")
    inside = sessions._MediaBoundary(True, "고양이", "strict_session", [])
    drawn = sessions._media_model(me, WITH_EDITOR["models"], kind="image", boundary=inside,
                                  asked=EDIT_ONLY)
    assert drawn is not None and drawn["id"] == STRICT_DRAWER["id"]
    usable = sessions._allowed_models(me, WITH_EDITOR["models"], kind="image")
    assert EDIT_ONLY not in [m["id"] for m in usable]
    # an edit finds it by what it does
    editors = sessions._allowed_models(me, WITH_EDITOR["models"], kind="image_edit")
    assert [m["id"] for m in editors] == [EDITOR["id"], EDIT_ONLY]


# ── the call ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_picture_goes_as_a_data_url_in_the_last_user_message(monkeypatch):
    sent: list[dict] = []
    reply = "data:image/png;base64," + base64.b64encode(_png((0, 0, 255))).decode()

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"images": [{"image_url": {"url": reply}}]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        })

    real = httpx.AsyncClient
    monkeypatch.setattr(imagegen.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    source = imagegen.source_image(_png((220, 30, 30)))
    await imagegen.generate(base_url="http://llm", api_key="k", model=EDIT_ONLY,
                            prompt=imagegen.edit_prompt("배경만 파랗게"), source=source)
    body = sent[0]
    assert body["model"] == EDIT_ONLY
    last = body["messages"][-1]
    assert last["role"] == "user"
    picture = [part for part in last["content"] if part["type"] == "image_url"]
    # the shim refuses an http URL: the picture itself goes, as a data URL
    assert len(picture) == 1 and picture[0]["image_url"]["url"].startswith("data:image/png;base64,")
    texts = [part["text"] for part in last["content"] if part["type"] == "text"]
    assert len(texts) == 1 and "배경만 파랗게" in texts[0]
    assert "image_config" not in body


# ── the route ──────────────────────────────────────────────────────────────


async def _edit(stack, session_id: str, **request) -> list:
    async with stack.db() as db:
        me = await db.get(User, "me")
        return await sessions.generate_images(
            session_id, ImageRequest(prompt="배경만 파랗게", **request), me, db, None)


@pytest.fixture
def inside(stack, monkeypatch):
    monkeypatch.setattr(edit_tests, "CATALOGUE", WITH_EDITOR)
    return stack


@pytest.mark.asyncio
async def test_a_strict_local_conversation_edits_on_the_inside_editor(inside):
    # the picked picture model (an outside editor) gives way to the strict-local one
    rows = await _edit(inside, "s-strict", model=EDITOR["id"], source_artifact_id="a-made")
    assert rows[0].data["model"] == EDIT_ONLY
    assert [call["model"] for call in inside.calls] == [EDIT_ONLY]
    assert inside.calls[0]["source"].data == inside.original
    # a drawing-only pick gives way too, kept inside
    rows = await _edit(inside, "s-strict", model=STRICT_DRAWER["id"], source_artifact_id="a-made")
    assert rows[0].data["model"] == EDIT_ONLY


@pytest.mark.asyncio
async def test_a_super_agent_strict_conversation_keeps_the_picture_inside(inside):
    async with inside.db() as db:
        # Super Agent turns leave the conversation's model as it was: an outside one here
        db.add(ChatSession(id="s-super", user_id="me", kind=SessionKind.chat,
                           model=OUTSIDE_CHAT["id"], routing_mode=RoutingMode.super_strict))
        await db.commit()
    rows = await _edit(inside, "s-super", model=EDITOR["id"], source_artifact_id="a-made")
    assert [call["model"] for call in inside.calls] == [EDIT_ONLY]
    assert rows[0].data["model"] == EDIT_ONLY


@pytest.mark.asyncio
async def test_a_super_agent_strict_conversation_without_an_editor_is_refused(stack):
    async with stack.db() as db:
        db.add(ChatSession(id="s-super", user_id="me", kind=SessionKind.chat,
                           model=OUTSIDE_CHAT["id"], routing_mode=RoutingMode.super_strict))
        await db.commit()
    with pytest.raises(HTTPException) as refused:
        await _edit(stack, "s-super", model=EDITOR["id"], source_artifact_id="a-made")
    assert refused.value.status_code == 409
    assert refused.value.detail == "image_edit_strict_local"
    assert stack.calls == []


@pytest.mark.asyncio
async def test_internal_material_and_inside_pictures_edit_inside(inside):
    inside.policy["policy"] = Governance(internal_data_strict_local=True)
    rows = await _edit(inside, "s1", model=EDITOR["id"], source_file_id="f-upload")
    assert rows[0].data["model"] == EDIT_ONLY and rows[0].data["internalSource"] is True
    inside.policy["policy"] = Governance(internal_data_strict_local=False)
    rows = await _edit(inside, "s1", model=EDITOR["id"], source_artifact_id="a-inside")
    assert rows[0].data["model"] == EDIT_ONLY
    assert [call["model"] for call in inside.calls] == [EDIT_ONLY, EDIT_ONLY]


@pytest.mark.asyncio
async def test_an_outside_picture_still_goes_to_the_picked_editor(inside):
    rows = await _edit(inside, "s1", model=EDITOR["id"], source_artifact_id="a-made")
    assert rows[0].data["model"] == EDITOR["id"]
    # outside, a picked model that only draws is still refused, not swapped
    with pytest.raises(HTTPException) as refused:
        await _edit(inside, "s1", model=DRAWER["id"], source_artifact_id="a-made")
    assert refused.value.detail == "image_model_cannot_edit"


def test_the_shared_catalogue_has_no_strict_editor():
    # the 409 cases of the edit tests run on a catalogue where no strict-local model edits
    assert not any(m.get("editsImages") and m.get("strictLocal") for m in CATALOGUE["models"])
