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


@pytest.fixture
def local_and_remote(monkeypatch):
    """A deployment with both a local Whisper address and a remote STT model."""
    from app.services import transcribe as t

    async def tools_config():
        return _Tools("http://whisper.local")

    monkeypatch.setattr(t.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(t.settings, "stt_or_model", "openrouter/voxtral")
    return t


@pytest.mark.anyio
async def test_a_local_whisper_that_cannot_answer_falls_through(local_and_remote, monkeypatch):
    """A configured local Whisper that fails falls through to the remote model."""
    t = local_and_remote

    async def local(*_args, **_kwargs) -> str:
        raise t.TranscribeError("받아쓰지 못했습니다.")

    async def remote(data: bytes, filename: str, language: str | None = None) -> str:
        return "원격이 받아썼습니다."

    monkeypatch.setattr(t, "_transcribe_locally", local)
    monkeypatch.setattr(t, "_transcribe_via_openrouter", remote)

    assert await t.transcribe(b"\x00" * 64, "회의.wav") == "원격이 받아썼습니다."


@pytest.mark.anyio
async def test_a_local_whisper_that_answers_is_the_one_used(local_and_remote, monkeypatch):
    """A local Whisper that answers is used; the audio never leaves the cluster."""
    t = local_and_remote

    async def local(*_args, **_kwargs) -> str:
        return "로컬이 받아썼습니다."

    async def remote(data: bytes, filename: str) -> str:  # pragma: no cover
        raise AssertionError("로컬이 답했으면 밖으로 내보내지 않는다")

    monkeypatch.setattr(t, "_transcribe_locally", local)
    monkeypatch.setattr(t, "_transcribe_via_openrouter", remote)

    assert await t.transcribe(b"\x00" * 64, "회의.wav") == "로컬이 받아썼습니다."


@pytest.mark.anyio
async def test_without_the_operator_s_leave_the_audio_stays_put(monkeypatch):
    """An empty `stt_or_model` means no fall-through: audio never leaves without leave."""
    from app.services import transcribe as t

    async def tools_config():
        return _Tools("http://whisper.local")

    async def local(*_args, **_kwargs) -> str:
        raise t.TranscribeError("받아쓰지 못했습니다.")

    async def remote(data: bytes, filename: str) -> str:  # pragma: no cover
        raise AssertionError("허락 없이 음성이 나가서는 안 된다")

    monkeypatch.setattr(t.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(t.settings, "stt_or_model", "")
    monkeypatch.setattr(t, "_transcribe_locally", local)
    monkeypatch.setattr(t, "_transcribe_via_openrouter", remote)

    with pytest.raises(t.TranscribeError, match="받아쓰지 못했습니다"):
        await t.transcribe(b"\x00" * 64, "회의.wav")


@pytest.mark.anyio
async def test_the_remote_path_resolves_its_address_like_everything_else(monkeypatch):
    """The remote path takes its address and key from `settings_store.litellm_config`."""
    from app.services import transcribe as t

    seen: dict[str, object] = {}

    async def litellm_config() -> tuple[str, str]:
        return "https://llm.example/litellm", "sk-master"

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "옮겨 적었습니다."}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json=None, headers=None):
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization")
            return _Response()

    monkeypatch.setattr(t.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(t.settings, "stt_or_model", "openrouter/voxtral")
    monkeypatch.setattr(t.httpx, "AsyncClient", lambda **_kw: _Client())

    assert await t._transcribe_via_openrouter(b"\x00" * 16, "회의.wav") == "옮겨 적었습니다."
    assert seen["url"] == "https://llm.example/litellm/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-master"


@pytest.mark.anyio
async def test_with_no_address_at_all_it_says_which_setting_is_missing(monkeypatch):
    from app.services import transcribe as t

    async def litellm_config() -> tuple[str, str]:
        return "", ""

    monkeypatch.setattr(t.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(t.settings, "stt_or_model", "openrouter/voxtral")
    with pytest.raises(t.TranscribeError, match="모델 주소"):
        await t._transcribe_via_openrouter(b"\x00" * 16, "회의.wav")


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
    assert await t._transcribe_locally("http://w", b"\x00", "ko.wav") == "안녕하세요"
    assert calls == [None, "ko"]
    assert t._last_seconds == 1
