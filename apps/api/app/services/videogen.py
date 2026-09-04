"""Video generation through LiteLLM pass-throughs: submit, poll, fetch.

    POST /orvideo/submit/<alias>/<res>/<a|na>/<dur>   billed at a fixed price per path
    GET  /orvideo/job/<id>                            free, polled
    GET  /orvideo/job/<id>/content?index=0            free, the clip itself

Submit uses the caller's virtual key (so the charge is attributed to them); poll and
fetch use the instance master key, since LiteLLM answers a virtual key with 401 there.
The alias in the submit path selects the price only; the model is the one in the body.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 60.0
_FETCH_TIMEOUT = 300.0

#: Catalogue id → billing-path alias. Ids without an entry are refused.
ALIASES = {
    "google/veo-3.1-lite": "veo-lite",
    "google/veo-3.1-fast": "veo-fast",
    "google/veo-3.1": "veo",
    "openai/sora-2-pro": "sora-2",
}

#: $/second per (alias, resolution, audio); must match the pass-through's fixed price.
_RATES = {
    ("veo-lite", "720p", False): 0.03,
    ("veo-lite", "720p", True): 0.05,
    ("veo-lite", "1080p", False): 0.05,
    ("veo-lite", "1080p", True): 0.08,
    ("veo-fast", "720p", False): 0.08,
    ("veo-fast", "720p", True): 0.10,
    ("veo-fast", "1080p", False): 0.10,
    ("veo-fast", "1080p", True): 0.12,
    ("veo", "720p", False): 0.20,
    ("veo", "720p", True): 0.25,
    ("veo", "1080p", False): 0.20,
    ("veo", "1080p", True): 0.40,
    ("sora-2", "720p", True): 0.30,
    ("sora-2", "1080p", True): 0.50,
}


class VideoError(RuntimeError):
    """Video job failure with a user-facing message."""


@dataclass(slots=True)
class Submitted:
    provider_job_id: str
    #: Dollars the pass-through bills for this submit.
    cost_usd: float


@dataclass(slots=True)
class Progress:
    status: str
    progress: int
    url: str | None = None
    error: str | None = None
    #: Upstream-reported charge in dollars; authoritative once the clip is done.
    cost_usd: float | None = None


def price_usd(model: str, *, resolution: str, seconds: int, audio: bool) -> float | None:
    """Quoted price in dollars, or None when the combination has no priced path."""
    alias = ALIASES.get(model)
    if alias is None:
        return None
    rate = _RATES.get((alias, resolution, audio))
    return None if rate is None else round(rate * seconds, 4)


def submit_path(model: str, *, resolution: str, seconds: int, audio: bool) -> str | None:
    """Billing path, or None when the combination has no priced route.

    Built from the matching `_RATES` key, never from caller strings (it becomes a URL path).
    """
    alias = ALIASES.get(model)
    if alias is None:
        return None
    for known_alias, known_resolution, known_audio in _RATES:
        if known_alias == alias and known_resolution == resolution and known_audio == audio:
            return (
                f"/orvideo/submit/{known_alias}/{known_resolution}"
                f"/{'a' if known_audio else 'na'}/{int(seconds)}"
            )
    return None


async def submit(
    *,
    base_url: str,
    api_key: str,
    user_id: str,
    model: str,
    prompt: str,
    resolution: str,
    seconds: int,
    audio: bool,
    aspect: str,
) -> Submitted:
    path = submit_path(model, resolution=resolution, seconds=seconds, audio=audio)
    cost = price_usd(model, resolution=resolution, seconds=seconds, audio=audio)
    if path is None or cost is None:
        raise VideoError("이 조합은 지원하지 않습니다. 길이나 해상도를 바꿔 보세요.")

    # OpenRouter silently ignores unknown field names (`duration_seconds`, `aspectRatio`).
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "resolution": resolution,
        "duration": seconds,
        "generate_audio": audio,
        "aspect_ratio": aspect,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    # Attributes the per-request charge to this account.
                    "x-litellm-end-user-id": user_id,
                },
            )
    except httpx.HTTPError as exc:
        log.warning("video submit failed: %s", exc)
        raise VideoError("영상 생성 서버에 연결하지 못했습니다.") from exc

    if response.status_code >= 400:
        log.warning("video submit %s: %s", response.status_code, response.text[:300])
        raise VideoError("영상 작업을 시작하지 못했습니다.")

    body = response.json()
    job_id = body.get("id") or (body.get("data") or {}).get("id")
    if not job_id:
        raise VideoError("영상 작업 번호를 받지 못했습니다.")
    return Submitted(provider_job_id=str(job_id), cost_usd=cost)


def _read_progress(body: dict[str, Any]) -> Progress:
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    status = str(data.get("status") or "").lower()
    percent = data.get("progress")
    # OpenRouter reports `unsigned_urls` (a list) rather than a singular `url`.
    url = data.get("url") or data.get("video_url")
    if not url:
        unsigned = data.get("unsigned_urls") or data.get("urls")
        if isinstance(unsigned, list) and unsigned:
            url = unsigned[0]
    if not url:
        for key in ("output", "video", "asset"):
            value = data.get(key)
            if isinstance(value, dict) and value.get("url"):
                url = value["url"]
                break
            if isinstance(value, str) and value.startswith("http"):
                url = value
                break
    cost = (data.get("usage") or {}).get("cost") if isinstance(data.get("usage"), dict) else None
    return Progress(
        status=status or "running",
        progress=int(percent) if isinstance(percent, (int, float)) else 0,
        url=url,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        error=(data.get("error") or {}).get("message")
        if isinstance(data.get("error"), dict)
        else data.get("error"),
    )


async def poll(*, base_url: str, master_key: str, provider_job_id: str) -> Progress:
    """Job status from the proxy; needs the master key (LiteLLM rejects virtual keys here)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/orvideo/job/{provider_job_id}",
                headers={"Authorization": f"Bearer {master_key}"},
            )
    except httpx.HTTPError as exc:
        # A failed poll is not a failed job.
        log.info("video poll failed: %s", exc)
        return Progress(status="running", progress=0)

    if response.status_code in (401, 403):
        # Wrong credentials: reported as failure so the job does not poll forever.
        log.error("video poll rejected (%s): %s", response.status_code, response.text[:200])
        return Progress(status="failed", progress=0, error="영상 상태를 확인할 권한이 없습니다.")
    if response.status_code >= 400:
        log.warning("video poll %s: %s", response.status_code, response.text[:200])
        return Progress(status="running", progress=0)
    return _read_progress(response.json())


async def fetch(*, base_url: str, master_key: str, provider_job_id: str, index: int = 0) -> bytes:
    """Downloads the finished clip through the proxy (`unsigned_urls` need the upstream key)."""
    url = f"{base_url.rstrip('/')}/orvideo/job/{provider_job_id}/content?index={index}"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {master_key}"})
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        log.warning("video fetch failed: %s", exc)
        raise VideoError("영상 파일을 내려받지 못했습니다.") from exc


__all__ = ["ALIASES", "Progress", "Submitted", "VideoError", "fetch", "poll", "price_usd", "submit"]
