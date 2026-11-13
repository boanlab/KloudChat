"""Client of the media worker (`apps/media`): stock ffmpeg/ffprobe in its own container.

The worker reads uploads through a read-only mount of the same storage, so a request names a
storage key, never sends the video. Each call is one short step; `video_jobs` sequences them.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(960.0, connect=10.0)


class MediaError(RuntimeError):
    """The worker could not do a step. `final`: the file itself is the problem (unreadable,
    too long), so trying again will not help."""

    def __init__(self, message: str, *, final: bool = False):
        super().__init__(message)
        self.final = final


def configured() -> bool:
    return bool(settings.media_url.strip())


async def _post(path: str, body: dict) -> httpx.Response:
    url = settings.media_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise MediaError(f"media worker unreachable: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("detail") or "")
        except ValueError:
            detail = ""
        final = response.status_code in (400, 404, 413, 422)
        raise MediaError(f"media worker {response.status_code} {detail}".strip(), final=final)
    return response


async def probe(key: str) -> dict:
    return (await _post("/probe", {"key": key})).json()


async def audio(key: str, start: float, duration: float) -> bytes:
    """16 kHz mono FLAC of `[start, start + duration)`."""
    return (await _post("/audio", {"key": key, "start": start, "duration": duration})).content


async def scenes(key: str, *, max_frames: int, min_gap: float = 4.0, max_gap: float = 90.0,
                 threshold: float = 0.12) -> list[dict]:
    """Candidate frame times `{t, score}` where the picture changes (first frame included)."""
    body = {"key": key, "max_frames": max_frames, "min_gap": min_gap, "max_gap": max_gap,
            "threshold": threshold}
    return list((await _post("/scenes", body)).json().get("frames") or [])


async def frame(key: str, t: float, width: int = 1280) -> bytes:
    return (await _post("/frame", {"key": key, "t": t, "width": width})).content


async def healthy() -> bool:
    if not configured():
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(settings.media_url.rstrip("/") + "/health")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


__all__ = ["MediaError", "audio", "configured", "frame", "healthy", "probe", "scenes"]
