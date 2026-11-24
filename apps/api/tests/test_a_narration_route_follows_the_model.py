"""The audio route: voices belong to the model, and a strict-local conversation stays inside."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import AudioRequest
from app.services import audiogen

QWEN = {
    "id": "strict-local/qwen3-tts", "kinds": ["av"], "modality": "audio",
    "dataBoundary": "self_hosted", "strictLocal": True, "speechEndpoint": True,
    "voices": list(audiogen.QWEN_VOICES), "creditCost": 0, "creditPerCall": 0,
}
GPT = {
    "id": "openai/gpt-audio-mini", "kinds": ["av"], "modality": "audio",
    "dataBoundary": "external", "strictLocal": False, "speechEndpoint": False,
    "voices": list(audiogen.VOICES), "creditCost": 100, "creditPerCall": 0,
}


class _Done(Exception):
    pass


def _run(monkeypatch, *, models, default, routing=None, session_model=""):
    session = ChatSession(
        id="s-au", user_id="u-au", kind=SessionKind.av, model=session_model
    )
    if routing is not None:
        session.routing_mode = routing
    user = User(id="u-au", email="a@example.test", password_hash="h", name="A")
    calls: list[dict] = []

    async def owned(db, user, session_id):
        return session

    async def catalogue(force: bool = False):
        return {"models": models, "defaultAvModelByMode": {"audio": default}}

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def policy():
        return Governance()

    async def no_history(db, session_id):
        return []

    async def generate(**kwargs):
        calls.append(kwargs)
        raise _Done

    monkeypatch.setattr(sessions, "_owned", owned)
    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions, "_history", no_history)
    monkeypatch.setattr(sessions.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "_audit_media_boundary", nothing)
    monkeypatch.setattr(sessions, "has_headroom", lambda user, model: True)
    monkeypatch.setattr(sessions.audiogen, "generate", generate)
    db = SimpleNamespace(is_modified=lambda user: False)

    async def go(**payload):
        request = AudioRequest(prompt="안녕하세요", **payload)
        await sessions.generate_audio("s-au", request, user, db, None)

    return go, calls


@pytest.mark.asyncio
async def test_qwen_uses_the_speech_endpoint_and_defaults_to_sohee(monkeypatch):
    go, calls = _run(monkeypatch, models=[QWEN, GPT], default=QWEN["id"])
    with pytest.raises(_Done):
        await go()
    assert calls[0]["model"] == QWEN["id"]
    assert calls[0]["speech_endpoint"] is True
    assert calls[0]["voice"] == "sohee"


@pytest.mark.asyncio
async def test_gpt_audio_keeps_the_chat_path(monkeypatch):
    go, calls = _run(monkeypatch, models=[QWEN, GPT], default=QWEN["id"])
    with pytest.raises(_Done):
        await go(model=GPT["id"], voice="nova")
    assert calls[0]["speech_endpoint"] is False and calls[0]["voice"] == "nova"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "voice"), [(QWEN, "alloy"), (GPT, "sohee"), (QWEN, "nobody")]
)
async def test_a_voice_the_model_lacks_is_refused(monkeypatch, model, voice):
    go, calls = _run(monkeypatch, models=[QWEN, GPT], default=model["id"])
    with pytest.raises(HTTPException) as err:
        await go(model=model["id"], voice=voice)
    assert err.value.status_code == 422 and not calls


@pytest.mark.asyncio
async def test_a_strict_local_conversation_never_reaches_the_outside_model(monkeypatch):
    go, calls = _run(
        monkeypatch, models=[QWEN, GPT], default=GPT["id"], session_model=QWEN["id"]
    )
    with pytest.raises(_Done):
        await go(model=GPT["id"], voice="nova")
    assert calls[0]["model"] == QWEN["id"] and calls[0]["voice"] == "sohee"


@pytest.mark.asyncio
async def test_a_strict_local_conversation_without_a_local_narrator_is_refused(monkeypatch):
    go, calls = _run(monkeypatch, models=[GPT], default=GPT["id"], session_model=QWEN["id"])
    # the session's own model is not in the catalogue: the boundary falls back to the policy
    monkeypatch.setattr(
        sessions, "_media_boundary",
        lambda db, session, catalogue, text: _strict(text),
    )
    with pytest.raises(HTTPException) as err:
        await go()
    assert err.value.status_code == 409 and not calls


async def _strict(text):
    return sessions._MediaBoundary(True, text, "strict_session", [])
