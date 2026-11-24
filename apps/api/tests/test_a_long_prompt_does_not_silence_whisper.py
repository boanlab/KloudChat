"""A vocabulary hint must not leave a short clip with no transcript."""

from __future__ import annotations

import pytest

from app.services import transcribe

pytestmark = pytest.mark.asyncio


real_whisper = transcribe._whisper


async def test_only_the_tail_of_a_long_prompt_is_used(monkeypatch):
    # `_whisper` is faked, so the cap is checked where the form is built.
    seen: dict[str, str] = {}

    class _Client:
        def __init__(self, *_a, **_k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, _url, *, files, data):
            seen.update(data)

            class _R:
                status_code = 200
                text = ""

                @staticmethod
                def json():
                    return {"text": "안녕", "language": "ko"}

            return _R()

    monkeypatch.setattr(transcribe.tools_gateway, "client", lambda **_k: _Client())
    await real_whisper("http://whisper.internal", b"RIFF", "speech.wav", None, "가" * 299 + "끝")
    assert len(seen["prompt"]) == transcribe.PROMPT_MAX
    assert seen["prompt"].endswith("끝")


async def test_an_empty_result_with_a_prompt_is_tried_again_without_it(monkeypatch):
    calls: list[str] = []

    async def fake(_url, _data, _filename, _language, prompt="", *, segments=False):
        calls.append(prompt)
        return {"text": "" if prompt else "안녕하세요", "language": "ko"}

    class _Tools:
        stt = "http://whisper.internal"

    async def tools_config():
        return _Tools()

    monkeypatch.setattr(transcribe, "_whisper", fake)
    monkeypatch.setattr(transcribe.settings_store, "tools_config", tools_config)
    assert await transcribe.transcribe(b"RIFF", "speech.wav", None, "이전 답") == "안녕하세요"
    assert calls == ["이전 답", ""]


async def test_real_silence_is_still_reported(monkeypatch):
    async def fake(*_args, **_kwargs):
        return {"text": "", "language": "ko"}

    class _Tools:
        stt = "http://whisper.internal"

    async def tools_config():
        return _Tools()

    monkeypatch.setattr(transcribe, "_whisper", fake)
    monkeypatch.setattr(transcribe.settings_store, "tools_config", tools_config)
    with pytest.raises(transcribe.TranscribeError, match="들린 말이 없습니다"):
        await transcribe.transcribe(b"RIFF", "speech.wav", None, "이전 답")
