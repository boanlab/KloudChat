"""Uploaded recordings are transcribed through the same backend as the microphone."""

from __future__ import annotations

import pytest

from app.services import files as file_service


def test_speech_is_told_from_writing() -> None:
    assert file_service.is_speech("audio/wav")
    assert file_service.is_speech("video/mp4")
    assert not file_service.is_speech("text/plain")
    assert not file_service.is_speech("application/pdf")


@pytest.mark.anyio
async def test_a_recording_goes_through_the_same_backend_the_microphone_uses(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def available() -> bool:
        return True

    async def transcribe(data: bytes, filename: str = "speech.webm") -> str:
        seen["bytes"] = len(data)
        seen["name"] = filename
        return "회의를 시작하겠습니다."

    monkeypatch.setattr(file_service.transcribe, "available", available)
    monkeypatch.setattr(file_service.transcribe, "transcribe", transcribe)

    text = await file_service.text_of("주간회의.m4a", "audio/mp4", b"\x00" * 2048)
    assert text == "회의를 시작하겠습니다."
    # The backend picks the decoder from the filename extension.
    assert seen == {"bytes": 2048, "name": "주간회의.m4a"}


@pytest.mark.anyio
async def test_writing_never_reaches_the_transcriber(monkeypatch) -> None:
    """A `.txt` is read, not listened to."""

    async def available() -> bool:  # pragma: no cover - must not be reached
        raise AssertionError("글은 받아쓰기로 보내지 않는다")

    monkeypatch.setattr(file_service.transcribe, "available", available)
    assert await file_service.text_of("메모.txt", "text/plain", "한 줄".encode()) == "한 줄"


@pytest.mark.anyio
async def test_a_recording_too_long_says_so_before_the_call(monkeypatch) -> None:
    async def available() -> bool:
        return True

    async def transcribe(data: bytes, filename: str = "speech.webm") -> str:  # pragma: no cover
        raise AssertionError("한도를 넘긴 파일은 보내지 않는다")

    monkeypatch.setattr(file_service.transcribe, "available", available)
    monkeypatch.setattr(file_service.transcribe, "transcribe", transcribe)

    with pytest.raises(RuntimeError, match="나눠 올려"):
        await file_service.text_of(
            "긴회의.wav", "audio/wav", b"\x00" * (file_service.transcribe.MAX_BYTES + 1)
        )


@pytest.mark.anyio
async def test_with_no_backend_the_message_says_what_to_do(monkeypatch) -> None:
    """With no transcription backend the error points at the administrator."""

    async def available() -> bool:
        return False

    monkeypatch.setattr(file_service.transcribe, "available", available)
    with pytest.raises(RuntimeError) as raised:
        await file_service.text_of("회의.wav", "audio/wav", b"\x00" * 16)
    assert "관리자" in str(raised.value)


class _Tools:
    def __init__(self, stt: str) -> None:
        self.stt = stt


@pytest.mark.anyio
async def test_a_local_whisper_that_cannot_answer_is_reported_not_rerouted(monkeypatch):
    """A Whisper failure surfaces as is; there is no other place to send the audio."""
    from app.services import transcribe as t

    async def tools_config():
        return _Tools("http://whisper.local")

    async def local(*_args, **_kwargs) -> str:
        raise t.TranscribeError("받아쓰지 못했습니다. 잠시 뒤 다시 시도하세요.")

    monkeypatch.setattr(t.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(t, "_transcribe_locally", local)

    with pytest.raises(t.TranscribeError, match="받아쓰지 못했습니다"):
        await t.transcribe(b"\x00" * 64, "회의.wav")


@pytest.mark.anyio
async def test_a_local_whisper_that_answers_is_the_one_used(monkeypatch):
    from app.services import transcribe as t

    async def tools_config():
        return _Tools("http://whisper.local")

    async def local(*_args, **_kwargs) -> str:
        return "로컬이 받아썼습니다."

    monkeypatch.setattr(t.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(t, "_transcribe_locally", local)

    assert await t.transcribe(b"\x00" * 64, "회의.wav") == "로컬이 받아썼습니다."


@pytest.mark.anyio
async def test_whisper_hears_which_language_and_is_retried_only_off_the_pair(monkeypatch):
    """Whisper detects the language; only a guess outside ko/en is retried pinned to Korean."""
    from app.services import transcribe as t

    calls: list[str | None] = []

    async def whisper(_url, _data, _name, language, _prompt=""):
        calls.append(language)
        if language == "ko":
            return {"text": "안녕하세요", "language": "ko", "duration": 1.2}
        if calls.count(None) == 1 and len(calls) == 1 and _name == "en.wav":
            return {"text": "Good morning", "language": "en", "duration": 1.0}
        return {"text": "你好", "language": "zh", "duration": 1.2}

    monkeypatch.setattr(t, "_whisper", whisper)

    assert await t._transcribe_locally("http://w", b"\x00", "en.wav") == "Good morning"
    assert calls == [None]

    calls.clear()
    assert await t._transcribe_timed("http://w", b"\x00", "ko.wav") == ("안녕하세요", 1)
    assert calls == [None, "ko"]


@pytest.mark.asyncio
async def test_two_recordings_at_once_each_keep_their_own_seconds(monkeypatch):
    import asyncio

    from app.services import transcribe as t

    async def whisper(_url, data, _name, language, _prompt="", **_kw):
        await asyncio.sleep(0.01 if data == b"a" else 0)
        return {"text": data.decode(), "language": "ko", "duration": 3.0 if data == b"a" else 9.0}

    monkeypatch.setattr(t, "_whisper", whisper)
    first, second = await asyncio.gather(
        t._transcribe_timed("http://w", b"a", "a.wav"),
        t._transcribe_timed("http://w", b"b", "b.wav"),
    )
    assert first == ("a", 3) and second == ("b", 9)


@pytest.mark.asyncio
async def test_a_silent_recording_and_a_failing_server_are_told_apart(monkeypatch):
    from app.services import transcribe as t

    async def whisper(*_a, **_k):
        return {"text": "", "language": "ko"}

    monkeypatch.setattr(t, "_whisper", whisper)
    with pytest.raises(t.TranscribeError) as silent:
        await t._transcribe_timed("http://w", b"\x00", "s.wav")
    assert silent.value.code == "no_speech"
    assert t.TranscribeError("x").code == "failed"
