"""녹음도 읽을 수 있는 자료다.

A meeting does not arrive as somebody re-speaking it into the composer. It
arrives as a recording — the room's file, an hour of it, made by whoever was
there. Refusing that and offering a microphone instead asks the one person who
already sat through the meeting to sit through it again.

The backend that turns speech into text was wired to one button and to nothing
else, so the capability was in the deployment and out of reach of the job it
exists for.
"""

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
    # The file's own name travels: the backend uses the extension to decide how
    # to read the bytes.
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
    """The old message named a limitation. This one names a way out."""

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
    """A deployment configured for both, which is the ARM case."""
    from app.services import transcribe as t

    async def tools_config():
        return _Tools("http://whisper.local")

    monkeypatch.setattr(t.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(t.settings, "stt_or_model", "openrouter/voxtral")
    return t


@pytest.mark.anyio
async def test_a_local_whisper_that_cannot_answer_falls_through(local_and_remote, monkeypatch):
    """vLLM serves Whisper on amd64 and not on aarch64.

    An ARM deployment carrying the same configuration as an x86 one has an
    `stt` address pointing at something that cannot answer. Treating a
    configured address as a working one made that a hard failure: the address
    was set, so the address was used, and every recording came back
    받아쓰지 못했습니다.
    """
    t = local_and_remote

    async def local(*_args, **_kwargs) -> str:
        raise t.TranscribeError("받아쓰지 못했습니다.")

    async def remote(data: bytes, filename: str) -> str:
        return "원격이 받아썼습니다."

    monkeypatch.setattr(t, "_transcribe_locally", local)
    monkeypatch.setattr(t, "_transcribe_via_openrouter", remote)

    assert await t.transcribe(b"\x00" * 64, "회의.wav") == "원격이 받아썼습니다."


@pytest.mark.anyio
async def test_a_local_whisper_that_answers_is_the_one_used(local_and_remote, monkeypatch):
    """Local first, always. The audio stays inside the cluster when it can."""
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
    """An empty `stt_or_model` is the operator saying no, and it still means no.

    Falling through is not a privacy decision made in the code. Setting the
    model is that decision; leaving it empty is the other one.
    """
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
    """전사만 환경변수를 직접 읽고 있었다.

    Every other model call takes its address from `settings_store.litellm_config`
    — the operator's setting first, then one derived from the backend address,
    then the environment. This one read the raw environment variable, which
    nothing sets. So a deployment talking to LiteLLM perfectly well had this one
    path building `/v1/chat/completions` with nothing in front of it and
    reporting the backend unreachable.
    """
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
