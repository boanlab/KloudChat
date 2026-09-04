"""Speech and music generation via `chat/completions` with `modalities: ["text", "audio"]`.

Both require `stream: true`. Music (Lyria) arrives as a finished file; speech
(GPT Audio) arrives as raw 24 kHz PCM16 and is wrapped in a WAV header here.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import struct
import uuid
from dataclasses import dataclass

import httpx

from app.services import files as file_service

log = logging.getLogger(__name__)

#: Speech is generated in real time.
_TIMEOUT = 240.0

#: PCM format GPT Audio streams; the WAV header is built from these.
_PCM_RATE = 24_000
_PCM_CHANNELS = 1
_PCM_BITS = 16

VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")


class AudioError(RuntimeError):
    """Generation failed; the message is user-facing."""


@dataclass(slots=True)
class GeneratedAudio:
    data: bytes
    mime: str
    extension: str
    input_tokens: int
    output_tokens: int
    #: Stored as the artifact's transcript; empty when the model sends none.
    transcript: str


def _wav(pcm: bytes) -> bytes:
    """A 44-byte RIFF header around raw PCM samples."""
    byte_rate = _PCM_RATE * _PCM_CHANNELS * _PCM_BITS // 8
    block_align = _PCM_CHANNELS * _PCM_BITS // 8
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack(
            "<IHHIIHH", 16, 1, _PCM_CHANNELS, _PCM_RATE, byte_rate, block_align, _PCM_BITS
        )
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


def compose_prompt(prompt: str, *, speech: bool, seconds: int = 0) -> str:
    """The prompt as sent; neither endpoint takes a length parameter, so it is asked for in words.
    """
    text = prompt.strip()
    if seconds > 0:
        text += f" 약 {seconds}초 분량으로 읽어라." if speech else f" 길이는 약 {seconds}초."
    return text


async def generate(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    speech: bool,
    voice: str = "alloy",
    seconds: int = 0,
) -> GeneratedAudio:
    """One clip, or `AudioError`."""
    payload: dict = {
        "model": model,
        "modalities": ["text", "audio"],
        "stream": True,  # required by the audio endpoints
        "stream_options": {"include_usage": True},  # else the stream carries no usage
        "messages": [
            {"role": "user", "content": compose_prompt(prompt, speech=speech, seconds=seconds)}
        ],
    }
    if speech:
        payload["audio"] = {"voice": voice if voice in VOICES else "alloy", "format": "pcm16"}

    chunks: list[bytes] = []
    transcript: list[str] = []
    usage: dict = {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:200]
                    log.warning("audio generation %s: %s", response.status_code, body)
                    raise AudioError("오디오를 만들지 못했습니다. 잠시 후 다시 시도하세요.")
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices") or []:
                        audio = (choice.get("delta") or {}).get("audio") or {}
                        if audio.get("data"):
                            try:
                                chunks.append(base64.b64decode(audio["data"]))
                            except (binascii.Error, ValueError):
                                continue
                        if audio.get("transcript"):
                            transcript.append(audio["transcript"])
    except httpx.HTTPError as exc:
        log.warning("audio request failed: %s", exc)
        raise AudioError("오디오 생성 서버에 연결하지 못했습니다.") from exc

    body = b"".join(chunks)
    if not body:
        raise AudioError("오디오를 만들지 못했습니다.")

    if speech:
        data, mime, extension = _wav(body), "audio/wav", "wav"
    else:
        # Lyria sends no `format` field; sniff the container.
        if body[:3] == b"ID3" or body[:2] == b"\xff\xfb":
            data, mime, extension = body, "audio/mpeg", "mp3"
        elif body[:4] == b"RIFF":
            data, mime, extension = body, "audio/wav", "wav"
        elif body[:4] == b"OggS":
            data, mime, extension = body, "audio/ogg", "ogg"
        else:
            raise AudioError("오디오 형식을 알아보지 못했습니다.")

    return GeneratedAudio(
        data=data,
        mime=mime,
        extension=extension,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        transcript="".join(transcript).strip(),
    )


def duration_seconds(audio: GeneratedAudio) -> int:
    """Exact for the WAV built here; 0 for other containers."""
    if audio.mime != "audio/wav":
        return 0
    samples = max(0, len(audio.data) - 44)
    return round(samples / (_PCM_RATE * _PCM_CHANNELS * _PCM_BITS // 8))


def store(user_id: str, audio: GeneratedAudio) -> tuple[str, str]:
    file_id = uuid.uuid4().hex
    key = file_service.write_blob(user_id, file_id, f"audio.{audio.extension}", audio.data)
    return file_id, key


__all__ = [
    "VOICES",
    "AudioError",
    "GeneratedAudio",
    "compose_prompt",
    "duration_seconds",
    "generate",
    "store",
]
