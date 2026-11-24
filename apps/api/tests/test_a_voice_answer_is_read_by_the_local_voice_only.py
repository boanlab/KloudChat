"""Voice conversation: a sentence is read by a self-hosted speech model only, free, not stored."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.routers import speak
from app.schemas.chat import SpeakRequest
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
PRICED = {**QWEN, "id": "strict-local/priced-tts", "creditPerCall": 3}


def _run(monkeypatch, *, models, default=QWEN["id"], headroom=True):
    speak._recent.clear()
    user = User(id="u-sp", email="s@example.test", password_hash="h", name="S")
    calls: list[dict] = []
    settled: list[dict] = []

    async def catalogue(force: bool = False):
        return {"models": models, "defaultAvModelByMode": {"audio": default}}

    async def nothing(*args, **kwargs):
        return None

    async def credentials(user):
        return "http://llm", "key"

    async def generate(**kwargs):
        calls.append(kwargs)
        return audiogen.GeneratedAudio(b"RIFFdata", "audio/wav", "wav", 0, 0, "")

    async def commit():
        return None

    monkeypatch.setattr(speak.model_service, "list_models", catalogue)
    monkeypatch.setattr(speak.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(speak.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(speak, "has_headroom", lambda user, model: headroom)
    monkeypatch.setattr(speak.audiogen, "generate", generate)
    monkeypatch.setattr(speak, "settle", lambda db, user, credits, **kw: settled.append(
        {"credits": credits, **kw}))
    db = SimpleNamespace(is_modified=lambda user: False, commit=commit)

    async def go(**payload):
        return await speak.speak(SpeakRequest(text="안녕하세요.", **payload), user, db)

    return go, calls, settled


@pytest.mark.asyncio
async def test_a_sentence_is_read_by_the_local_model_in_its_default_voice(monkeypatch):
    go, calls, settled = _run(monkeypatch, models=[QWEN, GPT])
    response = await go()
    assert response.media_type == "audio/wav" and response.body == b"RIFFdata"
    assert response.headers["cache-control"] == "no-store"
    assert calls[0]["model"] == QWEN["id"]
    assert calls[0]["speech_endpoint"] is True and calls[0]["voice"] == "sohee"
    # a free model is recorded nowhere
    assert settled == []


@pytest.mark.asyncio
async def test_an_outside_model_is_never_used_even_when_asked_for(monkeypatch):
    go, calls, _ = _run(monkeypatch, models=[GPT, QWEN], default=GPT["id"])
    await go(model=GPT["id"], voice="nova")
    assert calls[0]["model"] == QWEN["id"] and calls[0]["voice"] == "sohee"


@pytest.mark.asyncio
async def test_without_a_local_speech_model_it_is_unavailable(monkeypatch):
    go, calls, _ = _run(monkeypatch, models=[GPT])
    with pytest.raises(HTTPException) as err:
        await go()
    assert err.value.status_code == 503 and not calls


@pytest.mark.asyncio
async def test_a_voice_the_model_lacks_falls_back_quietly(monkeypatch):
    go, calls, _ = _run(monkeypatch, models=[QWEN])
    await go(voice="nobody")
    assert calls[0]["voice"] == "sohee"
    await go(voice="ryan")
    assert calls[1]["voice"] == "ryan"


@pytest.mark.asyncio
async def test_a_priced_model_is_charged_per_sentence_but_a_warm_up_is_free(monkeypatch):
    go, _, settled = _run(monkeypatch, models=[PRICED], default=PRICED["id"])
    await go()
    assert [s["credits"] for s in settled] == [3] and settled[0]["reason"] == "audio.speak"
    await go(warm=True)
    assert len(settled) == 1


@pytest.mark.asyncio
async def test_no_credits_left_is_refused_before_any_speech(monkeypatch):
    go, calls, _ = _run(monkeypatch, models=[PRICED], default=PRICED["id"], headroom=False)
    with pytest.raises(HTTPException) as err:
        await go()
    assert err.value.status_code == 422 and not calls


@pytest.mark.asyncio
async def test_too_many_sentences_in_a_minute_are_refused(monkeypatch):
    go, _, _ = _run(monkeypatch, models=[QWEN])
    for _ in range(speak._PER_MINUTE):
        await go()
    with pytest.raises(HTTPException) as err:
        await go()
    assert err.value.status_code == 429


@pytest.mark.asyncio
async def test_a_failed_speech_server_is_a_bad_gateway(monkeypatch):
    go, _, _ = _run(monkeypatch, models=[QWEN])

    async def broken(**kwargs):
        raise audiogen.AudioError("no")

    monkeypatch.setattr(speak.audiogen, "generate", broken)
    with pytest.raises(HTTPException) as err:
        await go()
    assert err.value.status_code == 502
