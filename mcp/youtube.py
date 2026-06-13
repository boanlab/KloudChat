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
"""YouTube transcript MCP — caption-first, whisper(GPU)-fallback.

Single tool `transcript(url, language=None)`:
  1. youtube-transcript-api 로 자막 시도 (영상에 자막 존재 시 GPU 미사용 — 즉시 반환).
  2. 자막 없음/비공개 시 yt-dlp 로 audio (m4a/opus) 추출.
  3. WHISPER_URL(보통 compose 내부 whisper-shim http://whisper-shim:9000)로 전사.
     shim 이 WHISPER_URLS backend 중 inflight/VRAM 기준 라우팅. backend =
     scripts/install-whisper.sh 로 GPU 노드(들)에 설치된 faster-whisper systemd 유닛.
     whisper 는 GPU 전용 — OR STT 폴백 없음. 미설정/미가용 시 자막 있는 영상만 동작.
     (WHISPER_URLS 가 비면 이 도구 자체가 에이전트 미부착 — manage.sh 게이팅.)

환경변수:
  WHISPER_URL          보통 http://whisper-shim:9000 (compose 내부 라우터).
                         단일 노드라 shim 우회 시 http://host.docker.internal:9000 도 가능.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile

import httpx
from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound, TranscriptsDisabled, VideoUnavailable,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
LOG = logging.getLogger("youtube-mcp")

WHISPER_URL = (os.getenv("WHISPER_URL") or "").rstrip("/")

mcp = FastMCP("youtube")


_VIDEO_ID_RX = re.compile(
    r"(?:v=|youtu\.be/|youtube\.com/(?:embed|shorts|live)/)([\w-]{11})"
)


def _video_id(url: str) -> str | None:
    m = _VIDEO_ID_RX.search(url)
    return m.group(1) if m else (url if re.fullmatch(r"[\w-]{11}", url) else None)


def _try_captions(vid: str, language: str | None) -> str | None:
    """YouTube 공식 자막 시도. 우선순위: 명시 언어 → ko → en → 첫 가능 항목.
    None 반환 시 caller 가 whisper 폴백. youtube-transcript-api ≥1.0 신규 API
    (instance method) 사용."""
    try:
        ytt = YouTubeTranscriptApi()
        listing = ytt.list(vid)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception as e:
        LOG.info("transcript listing failed for %s: %s", vid, e)
        return None

    def _join(fetched) -> str:
        # ≥1.0: FetchedTranscriptSnippet 객체 (속성 .text). 구버전 dict 호환 유지.
        out = []
        for s in fetched:
            txt = getattr(s, "text", None) if not isinstance(s, dict) else s.get("text")
            if txt:
                out.append(txt)
        return " ".join(out)

    candidates = []
    if language:
        candidates.append([language])
    candidates.extend([["ko"], ["en"]])
    for langs in candidates:
        try:
            t = listing.find_transcript(langs)
            return _join(t.fetch())
        except NoTranscriptFound:
            continue
    # 마지막 시도: 가능한 아무거나 (자동 생성 자막 포함)
    try:
        t = next(iter(listing))
        return _join(t.fetch())
    except Exception:
        return None


def _download_audio(vid: str, dest_dir: str) -> str:
    """yt-dlp 로 audio-only 수신. 반환: 다운로드 파일 경로."""
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


async def _post_audio(url: str, audio_path: str) -> str | None:
    """multipart upload to OpenAI-compat /v1/audio/transcriptions (whisper-shim)."""
    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "application/octet-stream")}
        data = {"response_format": "json"}
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{url}/v1/audio/transcriptions", files=files, data=data)
    if r.status_code >= 400:
        LOG.warning("whisper %s → HTTP %d: %s", url, r.status_code, r.text[:200])
        return None
    try:
        return (r.json() or {}).get("text") or None
    except ValueError:
        return r.text or None


async def _whisper(audio_path: str) -> str:
    """audio → WHISPER_URL(whisper-shim → GPU backend) 전사. GPU 전용 — OR 폴백 없음."""
    if not WHISPER_URL:
        raise RuntimeError("Whisper backend unavailable: WHISPER_URL not set (GPU-only, no OR fallback).")
    text = await _post_audio(WHISPER_URL, audio_path)
    if not text:
        raise RuntimeError("Whisper transcription failed or returned empty.")
    return text


@mcp.tool()
async def transcript(url: str, language: str | None = None) -> str:
    """YouTube 영상의 자막/스크립트/전사 텍스트 반환.

    한국어 트리거: "유튜브 자막", "유튜브 스크립트", "영상 내용 알려줘",
    "유튜브 요약해줘"(먼저 transcript 수신 → 그 본문을 모델이 요약), "영상 텍스트",
    "YouTube 내용", "이 영상 무슨 내용", URL/링크가 youtube.com / youtu.be 인 경우.
    Returns the full transcript text of a YouTube video.

    자막 존재 시 그대로 반환, 없으면 audio 수신 → whisper(로컬 GPU) 전사.
    whisper 미가용 시 전사 불가(자막 있는 영상만 동작). 짧은 영상은 수 초, 긴 영상은
    분 단위 소요.

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
