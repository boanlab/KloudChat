"""A self-hosted speech model is generated through /audio/speech, free, with its own voices."""

from __future__ import annotations

import io
import json
import struct
import wave

import httpx
import pytest

from app.services import audiogen
from app.services import models as model_service
from app.services.credits import charge_for_tokens


def _wav(seconds: float, rate: int = 24_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def _serve(monkeypatch, handler):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(audiogen.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_speech_goes_to_audio_speech_and_comes_back_as_wav(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_wav(2.0), headers={"content-type": "audio/wav"})

    _serve(monkeypatch, handler)
    clip = await audiogen.generate(
        base_url="http://litellm:4000/v1/",
        api_key="sk-user",
        model="strict-local/qwen3-tts",
        prompt="안녕하세요. 연구실 안내를 시작합니다.",
        speech=True,
        voice="sohee",
        seconds=30,
        speech_endpoint=True,
    )
    assert seen["url"] == "http://litellm:4000/v1/audio/speech"
    assert seen["auth"] == "Bearer sk-user"
    assert seen["body"] == {
        "model": "strict-local/qwen3-tts",
        # read as written: no length sentence appended
        "input": "안녕하세요. 연구실 안내를 시작합니다.",
        "voice": "sohee",
        "language": "Korean",
        "response_format": "wav",
        "stream": False,
    }
    assert clip.mime == "audio/wav" and clip.extension == "wav"
    assert audiogen.duration_seconds(clip) == 2
    assert (clip.input_tokens, clip.output_tokens) == (0, 0)


@pytest.mark.asyncio
async def test_duration_follows_the_sample_rate_in_the_header(monkeypatch):
    _serve(monkeypatch, lambda r: httpx.Response(200, content=_wav(3.0, rate=16_000)))
    clip = await audiogen.generate(
        base_url="http://x/v1", api_key="k", model="strict-local/qwen3-tts", prompt="hello",
        speech=True, voice="ryan", speech_endpoint=True,
    )
    assert audiogen.duration_seconds(clip) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, text="boom"), httpx.Response(200, content=b"not audio")],
)
async def test_a_failed_speech_call_is_an_audio_error(monkeypatch, response):
    _serve(monkeypatch, lambda r: response)
    with pytest.raises(audiogen.AudioError):
        await audiogen.generate(
            base_url="http://x/v1", api_key="k", model="strict-local/qwen3-tts", prompt="hi",
            speech=True, voice="sohee", speech_endpoint=True,
        )


def test_language_is_korean_for_hangul_else_auto():
    assert audiogen.language_of("안녕하세요 hello") == "Korean"
    assert audiogen.language_of("Hello there, this is a test.") == "Auto"
    assert audiogen.language_of("2024 12 31") == "Auto"


def test_qwen_voices_and_default():
    assert set(audiogen.QWEN_VOICES) == {
        "vivian", "ryan", "aiden", "dylan", "eric", "ono_anna", "serena", "sohee", "uncle_fu",
    }
    assert audiogen.voices_for("strict-local/qwen3-tts", speech_endpoint=True) == (
        audiogen.QWEN_VOICES
    )
    assert audiogen.default_voice("strict-local/qwen3-tts") == "sohee"
    assert audiogen.default_voice("openai/gpt-audio-mini") == "alloy"
    assert audiogen.voices_for("openai/gpt-audio-mini") == audiogen.VOICES
    assert audiogen.voices_for("google/lyria-3") == ()


def _entry(**info):
    return {
        "model_name": "strict-local/qwen3-tts",
        "litellm_params": {"model": "openai/local/qwen3-tts", "api_base": "http://10.0.0.7:8094/v1"},
        "model_info": {
            "mode": "audio_speech",
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
            "kchat_data_boundary": "self_hosted",
            "kchat_strict_local": True,
            **info,
        },
    }


def test_the_catalogue_lists_it_as_a_free_strict_local_narrator():
    row = model_service._shape(_entry())
    assert row is not None
    assert row["kinds"] == ["av"] and row["modality"] == "audio"
    assert row["speechEndpoint"] is True
    assert row["voices"] == list(audiogen.QWEN_VOICES)
    assert row["strictLocal"] is True
    assert row["creditCost"] == 0 and row["creditPerCall"] == 0
    assert charge_for_tokens(row, 0, 0) == 0
