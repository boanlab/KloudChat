#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   # <2: 2.0 removed `mcp.server.fastmcp`.
#   "mcp>=1.2.0,<2",
#   "httpx>=0.27.0",
#   "youtube-transcript-api>=0.6.2",
#   "yt-dlp>=2024.1.0",
# ]
# ///
"""YouTube transcript MCP server: captions first, Whisper fallback.

One tool, `transcript(url, language=None)`: official captions via
youtube-transcript-api; otherwise audio via yt-dlp, transcribed at WHISPER_URL.
The transcript comes under a header naming the video (title, channel, length,
upload date, link) from yt-dlp, or YouTube's oEmbed when yt-dlp fails.

Environment:
  WHISPER_URL  OpenAI-compatible `/v1/audio/transcriptions` base. Filled in by
               the connector runtime from the admin screen's speech-to-text
               address. Unset: only videos with captions work.
  TOOLS_GATEWAY_KEY  Sent as `X-Tools-Key` to WHISPER_URL. Set by the connector
               runtime only when WHISPER_URL is a configured tool address.
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
TOOLS_GATEWAY_KEY = (os.getenv("TOOLS_GATEWAY_KEY") or "").strip()

mcp = FastMCP("youtube")


_VIDEO_ID_RX = re.compile(
    r"(?:v=|youtu\.be/|youtube\.com/(?:embed|shorts|live)/)([\w-]{11})"
)


def _video_id(url: str) -> str | None:
    m = _VIDEO_ID_RX.search(url)
    return m.group(1) if m else (url if re.fullmatch(r"[\w-]{11}", url) else None)


def _try_captions(vid: str, language: str | None) -> str | None:
    """Official captions: explicit language → ko → en → first available. None: fall back to Whisper."""
    try:
        ytt = YouTubeTranscriptApi()
        listing = ytt.list(vid)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
        return None
    except Exception as e:
        LOG.info("transcript listing failed for %s: %s", vid, e)
        return None

    def _join(fetched) -> str:
        # ≥1.0 yields snippet objects (.text); <1.0 yielded dicts.
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
    # Anything available, auto-generated included.
    try:
        t = next(iter(listing))
        return _join(t.fetch())
    except Exception:
        return None


#: Metadata lookup budget (s); the transcript goes out without the header after it.
_META_TIMEOUT = 20.0


def _ytdlp_meta(vid: str) -> dict:
    """Title, channel, length and upload date via yt-dlp, nothing downloaded."""
    from yt_dlp import YoutubeDL
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}
    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "published": info.get("upload_date"),
    }


async def _oembed_meta(vid: str) -> dict:
    """Title and channel from YouTube's oEmbed endpoint (no length there)."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://www.youtube.com/oembed",
                        params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
    if r.status_code >= 400:
        return {}
    data = r.json() or {}
    return {"title": data.get("title"), "channel": data.get("author_name")}


async def _metadata(vid: str) -> dict:
    """What the note's header names; `{}` when YouTube answers neither way."""
    try:
        meta = await asyncio.wait_for(asyncio.to_thread(_ytdlp_meta, vid), _META_TIMEOUT)
        if meta.get("title"):
            return meta
    except Exception as e:
        LOG.info("yt-dlp metadata failed for %s: %s", vid, e)
    try:
        return await _oembed_meta(vid)
    except Exception as e:
        LOG.info("oEmbed metadata failed for %s: %s", vid, e)
        return {}


def _length(seconds: object) -> str:
    """「6분 12초」, 「1시간 2분 3초」; '' when unknown."""
    try:
        total = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours}시간"] if hours else []
    parts += [f"{minutes}분"] if minutes or hours else []
    parts += [f"{secs}초"] if secs or not parts else []
    return " ".join(parts)


def _header(vid: str, meta: dict) -> str:
    """The video's own facts above its transcript (a note names the video it is about)."""
    published = str(meta.get("published") or "")
    if re.fullmatch(r"\d{8}", published):
        published = f"{published[:4]}-{published[4:6]}-{published[6:]}"
    lines = [
        ("Title", str(meta.get("title") or "").strip()),
        ("Channel", str(meta.get("channel") or "").strip()),
        ("Length", _length(meta.get("duration"))),
        ("Published", published),
        ("URL", f"https://www.youtube.com/watch?v={vid}"),
    ]
    return "\n".join(f"{label}: {value}" for label, value in lines if value)


def _with_header(vid: str, meta: dict, text: str) -> str:
    return f"{_header(vid, meta)}\n\nTranscript:\n{text}"


def _download_audio(vid: str, dest_dir: str) -> str:
    """Audio-only download via yt-dlp; returns the file path."""
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
    """Multipart upload to an OpenAI-compatible /v1/audio/transcriptions."""
    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "application/octet-stream")}
        data = {"response_format": "json"}
        headers = {"X-Tools-Key": TOOLS_GATEWAY_KEY} if TOOLS_GATEWAY_KEY else {}
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(
                f"{url}/v1/audio/transcriptions", files=files, data=data, headers=headers
            )
    if r.status_code >= 400:
        LOG.warning("whisper %s → HTTP %d: %s", url, r.status_code, r.text[:200])
        return None
    try:
        return (r.json() or {}).get("text") or None
    except ValueError:
        return r.text or None


async def _whisper(audio_path: str) -> str:
    """Audio → transcript via WHISPER_URL."""
    if not WHISPER_URL:
        raise RuntimeError("Whisper backend unavailable: WHISPER_URL not set (GPU-only, no OR fallback).")
    text = await _post_audio(WHISPER_URL, audio_path)
    if not text:
        raise RuntimeError("Whisper transcription failed or returned empty.")
    return text


@mcp.tool()
async def transcript(url: str, language: str | None = None) -> str:
    """Returns a YouTube video's title, channel, length, upload date and link, then its full
    transcript text.

    This docstring is the tool description the model reads; the Korean trigger
    phrases are functional.

    한국어 트리거: "유튜브 자막", "유튜브 스크립트", "영상 내용 알려줘",
    "유튜브 요약해줘"(먼저 transcript 수신 → 그 본문을 모델이 요약), "영상 텍스트",
    "YouTube 내용", "이 영상 무슨 내용", URL/링크가 youtube.com / youtu.be 인 경우.

    Captions are returned as they are when they exist; otherwise the audio is
    downloaded and transcribed with Whisper on a local GPU. Without Whisper,
    only videos that have captions work. Short videos take seconds, long ones
    minutes.

    Args:
        url: A YouTube video URL, or an 11-character video ID.
        language: Preferred caption language (e.g. "ko", "en"). Unset, the
            order is ko → en → whatever is available.
    """
    vid = _video_id(url)
    if not vid:
        return f"Error: cannot extract video ID from {url!r}"

    # The video's own facts, looked up while the captions are read.
    meta = asyncio.ensure_future(_metadata(vid))

    # Captions first.
    txt = await asyncio.to_thread(_try_captions, vid, language)
    if txt:
        return _with_header(vid, await meta, txt)

    # No captions: download audio and transcribe.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            audio = await asyncio.to_thread(_download_audio, vid, tmp)
        except Exception as e:
            meta.cancel()
            return f"Error: audio download failed: {e}"
        try:
            return _with_header(vid, await meta, await _whisper(audio))
        except Exception as e:
            meta.cancel()
            return f"Error: transcription failed: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
