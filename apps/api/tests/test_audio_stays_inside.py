"""Recordings are only ever sent to the internal Whisper, in every configuration."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services import transcribe

WHISPER = "http://whisper.internal"


class _Tools:
    def __init__(self, stt: str) -> None:
        self.stt = stt


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self) -> dict:
        return self._body


@pytest.fixture
def outbound(monkeypatch):
    """Every HTTP request any client makes, with a model gateway that looks reachable."""
    seen: list[str] = []
    answer = {"status": 200}

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, *_args, **_kwargs):
            seen.append(str(url))
            return _Response(answer["status"], {"text": "안녕하세요", "language": "ko"})

    async def litellm_config() -> tuple[str, str]:
        return "https://llm.example/litellm", "sk-master"

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(transcribe.settings_store, "litellm_config", litellm_config)
    # A leftover setting from the removed outside-model fallback must change nothing.
    monkeypatch.setenv("STT_OR_MODEL", "openrouter/voxtral")
    return seen, answer


def _with_whisper(monkeypatch, url: str) -> None:
    async def tools_config():
        return _Tools(url)

    monkeypatch.setattr(transcribe.settings_store, "tools_config", tools_config)


def test_there_is_no_outside_model_setting(monkeypatch):
    monkeypatch.setenv("STT_OR_MODEL", "openrouter/voxtral")
    assert not hasattr(Settings(), "stt_or_model")
    assert "stt_or_model" not in Settings.model_fields
    assert not hasattr(transcribe, "_transcribe_via_openrouter")


async def test_with_whisper_only_whisper_is_called(monkeypatch, outbound):
    seen, _ = outbound
    _with_whisper(monkeypatch, WHISPER)

    assert await transcribe.available()
    assert await transcribe.transcribe(b"\x00" * 64, "회의.wav") == "안녕하세요"
    assert seen and all(url.startswith(f"{WHISPER}/v1/audio/transcriptions") for url in seen)


async def test_a_whisper_failure_sends_nothing_elsewhere(monkeypatch, outbound):
    seen, answer = outbound
    answer["status"] = 500
    _with_whisper(monkeypatch, WHISPER)

    with pytest.raises(transcribe.TranscribeError):
        await transcribe.transcribe(b"\x00" * 64, "회의.wav")
    assert seen and all(url.startswith(WHISPER) for url in seen)


async def test_without_whisper_nothing_is_sent_and_dictation_is_off(monkeypatch, outbound):
    seen, _ = outbound
    _with_whisper(monkeypatch, "")

    assert not await transcribe.available()
    with pytest.raises(transcribe.TranscribeError, match="Whisper"):
        await transcribe.transcribe(b"\x00" * 64, "회의.wav")
    assert seen == []
