#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27.0",
#   "youtube-transcript-api>=0.6.2",
#   "yt-dlp>=2024.1.0",
# ]
# ///
"""YouTube transcript MCP — caption-first, whisper-fallback.

Single tool `transcript(url, language=None)`:
  1. youtube-transcript-api 로 자막 시도 (영상에 자막 있으면 OR/GPU 미사용 — 즉시 반환).
  2. 자막 없거나 비공개면 yt-dlp 로 audio (m4a/opus) 추출.
  3. WHISPER_URL 우선 — 컴포즈 안의 /whisper 서비스 (faster-whisper + GPU).
  4. 실패하거나 미설정이면 LITELLM_URL 의 OpenAI-compat /v1/audio/transcriptions
     로 폴백 (OR 경유 whisper-1, OR 키 필요).

환경변수:
  WHISPER_URL          예: http://whisper:9000 (없으면 LiteLLM 직행)
  LITELLM_URL          기본 http://litellm:8000
  LITELLM_MASTER_KEY   LiteLLM 인증
  WHISPER_OR_MODEL     OR 폴백 모델 (기본 'whisper-1')
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
LOG = logging.getLogger("youtube-mcp")

WHISPER_URL       = (os.getenv("WHISPER_URL") or "").rstrip("/")
LITELLM_URL       = (os.getenv("LITELLM_URL") or "http://litellm:8000").rstrip("/")
LITELLM_KEY       = os.getenv("LITELLM_MASTER_KEY", "")
WHISPER_OR_MODEL  = os.getenv("WHISPER_OR_MODEL", "whisper-1")

mcp = FastMCP("youtube")


_VIDEO_ID_RX = re.compile(
    r"(?:v=|youtu\.be/|youtube\.com/(?:embed|shorts|live)/)([\w-]{11})"
)


def _video_id(url: str) -> str | None:
    m = _VIDEO_ID_RX.search(url)
    return m.group(1) if m else (url if re.fullmatch(r"[\w-]{11}", url) else None)


def _try_captions(vid: str, language: str | None) -> str | None:
    """YouTube 공식 자막 시도. 우선순위: 명시된 언어 → ko → en → 첫 가능 항목.
    None 반환 시 caller 가 whisper 폴백."""
    try:
        listing = YouTubeTranscriptApi.list_transcripts(vid)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception as e:
        LOG.info("transcript listing failed for %s: %s", vid, e)
        return None

    candidates = []
    if language:
        candidates.append([language])
    candidates.extend([["ko"], ["en"]])
    for langs in candidates:
        try:
            t = listing.find_transcript(langs)
            return " ".join(s["text"] for s in t.fetch())
        except NoTranscriptFound:
            continue
    # 마지막 시도: 가능한 아무거나 (자동 생성된 자막 포함)
    try:
        t = next(iter(listing))
        return " ".join(s["text"] for s in t.fetch())
    except Exception:
        return None


def _download_audio(vid: str, dest_dir: str) -> str:
    """yt-dlp 로 audio-only 받음. 반환: 다운로드된 파일 경로."""
    from yt_dlp import YoutubeDL
    out_tmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
        return ydl.prepare_filename(info)


async def _post_audio(url: str, audio_path: str, *,
                      headers: dict[str, str] | None = None,
                      extra_form: dict[str, str] | None = None) -> str | None:
    """multipart upload to OpenAI-compat /v1/audio/transcriptions."""
    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "application/octet-stream")}
        data = {"response_format": "json"}
        if extra_form:
            data.update(extra_form)
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{url}/v1/audio/transcriptions",
                             headers=headers or {}, files=files, data=data)
    if r.status_code >= 400:
        LOG.warning("whisper %s → HTTP %d: %s", url, r.status_code, r.text[:200])
        return None
    try:
        return (r.json() or {}).get("text") or None
    except ValueError:
        return r.text or None


async def _whisper(audio_path: str) -> str:
    """로컬 whisper 우선, 실패 시 OR 폴백. 둘 다 실패하면 예외."""
    if WHISPER_URL:
        text = await _post_audio(WHISPER_URL, audio_path)
        if text:
            return text
        LOG.info("local WHISPER_URL=%s failed/empty, falling back to LiteLLM", WHISPER_URL)
    if not LITELLM_KEY:
        raise RuntimeError(
            "Whisper backend unavailable: WHISPER_URL not set/failed and "
            "LITELLM_MASTER_KEY missing for OR fallback."
        )
    text = await _post_audio(
        LITELLM_URL, audio_path,
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        extra_form={"model": WHISPER_OR_MODEL},
    )
    if not text:
        raise RuntimeError("OR whisper returned empty response.")
    return text


@mcp.tool()
async def transcript(url: str, language: str | None = None) -> str:
    """YouTube 영상의 텍스트를 반환합니다.

    자막이 있으면 그걸 그대로, 없으면 audio 를 받아 whisper 로 전사합니다 (로컬 GPU
    컨테이너 우선, OR 폴백). 짧은 영상은 수 초, 긴 영상은 분 단위 소요.

    Args:
        url: YouTube 영상 URL 또는 11자리 video ID.
        language: 자막 우선순위 (예: "ko", "en"). 미지정 시 ko → en → 자동.
    """
    vid = _video_id(url)
    if not vid:
        return f"Error: cannot extract video ID from {url!r}"

    # 1. 자막 우선
    txt = await asyncio.to_thread(_try_captions, vid, language)
    if txt:
        return txt

    # 2. 자막 없음 → audio 다운로드 + whisper
    with tempfile.TemporaryDirectory() as tmp:
        try:
            audio = await asyncio.to_thread(_download_audio, vid, tmp)
        except Exception as e:
            return f"Error: audio download failed: {e}"
        try:
            return await _whisper(audio)
        except Exception as e:
            return f"Error: transcription failed: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
