"""Recordings stay on the local Whisper unless an administrator opts in to an outside model."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import transcribe


def test_the_outside_fallback_is_off_by_default():
    assert Settings().stt_or_model == ""


async def test_a_local_failure_does_not_send_audio_out(monkeypatch):
    class Tools:
        stt = "http://whisper.invalid"

    async def tools_config():
        return Tools()

    async def local_fails(*_args, **_kwargs):
        raise transcribe.TranscribeError("local down")

    async def outside(*_args, **_kwargs):
        raise AssertionError("audio was sent to an outside model")

    monkeypatch.setattr(transcribe.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(transcribe, "_transcribe_locally", local_fails)
    monkeypatch.setattr(transcribe, "_transcribe_via_openrouter", outside)
    monkeypatch.setattr(transcribe.settings, "stt_or_model", "")
    with pytest.raises(transcribe.TranscribeError):
        await transcribe.transcribe(b"abc", "a.webm")
