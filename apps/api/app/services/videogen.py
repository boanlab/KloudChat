"""Video generation: submit, poll, fetch.

The only modality that does not finish inside its request. OpenRouter takes the
prompt on `/api/v1/videos`, returns an id, and the clip appears minutes later,
so each call here is one step of a job.

Three LiteLLM pass-throughs:

    POST /orvideo/submit/<alias>/<res>/<a|na>/<dur>   billed, one fixed price
    GET  /orvideo/job/<id>                            free, polled
    GET  /orvideo/job/<id>/content?index=0            free, the clip itself

**Two different credentials.** Submitting uses the caller's virtual key, so the
charge lands on them. Polling and downloading use the instance master key,
because LiteLLM classifies job routes as management endpoints and answers a
virtual key with 401. Attribution is unaffected: only the submit route is
priced.

**The alias selects a price, not a model** — the model is the one in the body.
Pass-throughs cannot price dynamically, so the config enumerates every
(model × resolution × audio × duration) combination with its own
`cost_per_request`. The wrong path bills the wrong amount for a correct clip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: Submitting returns a ticket; nothing here waits for a clip.
_TIMEOUT = 60.0
#: Downloading one does.
_FETCH_TIMEOUT = 300.0

#: Catalogue id → the alias the billing paths are keyed by. An id with no entry
#: has no priced path and is refused rather than billed at a guess.
ALIASES = {
    "google/veo-3.1-lite": "veo-lite",
    "google/veo-3.1-fast": "veo-fast",
    "google/veo-3.1": "veo",
    "openai/sora-2-pro": "sora-2",
}

#: $/second, from each model's `pricing_skus`. Quoted before the run, and has
#: to agree with the pass-through's fixed figure.
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
    """The message is written for the person who asked."""


@dataclass(slots=True)
class Submitted:
    provider_job_id: str
    #: What the pass-through will bill, in dollars, so the caller can record it.
    cost_usd: float


@dataclass(slots=True)
class Progress:
    status: str
    progress: int
    url: str | None = None
    error: str | None = None
    #: What the upstream says it charged. Authoritative once the clip is done;
    #: the pass-through's fixed per-path price is only an estimate.
    cost_usd: float | None = None


def price_usd(model: str, *, resolution: str, seconds: int, audio: bool) -> float | None:
    """`None` when the combination has no priced path — refuse rather than guess."""
    alias = ALIASES.get(model)
    if alias is None:
        return None
    rate = _RATES.get((alias, resolution, audio))
    return None if rate is None else round(rate * seconds, 4)


def submit_path(model: str, *, resolution: str, seconds: int, audio: bool) -> str | None:
    """The billing path, or None when the combination has no priced route.

    The path is assembled from the matching `_RATES` key rather than from the
    arguments: this string becomes a URL path, and interpolating a caller's
    `resolution` would be a traversal into whatever the gateway serves.
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

    # Accepted field names: `duration`, `resolution`, `generate_audio`,
    # `aspect_ratio`. `duration_seconds`, `aspectRatio` and `seed` are silently
    # ignored, producing a default-length clip at twice the quoted price.
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
                    # Attributes the pass-through's per-request charge to this
                    # account rather than the instance.
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
    # OpenRouter returns `unsigned_urls`, a list — not a singular `url`.
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
    """Asks the proxy how the clip is coming along.

    Instance key, not the caller's: LiteLLM treats this as a management
    endpoint and refuses a virtual key.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/orvideo/job/{provider_job_id}",
                headers={"Authorization": f"Bearer {master_key}"},
            )
    except httpx.HTTPError as exc:
        # A failed poll is not a failed job; the next tick asks again.
        log.info("video poll failed: %s", exc)
        return Progress(status="running", progress=0)

    if response.status_code in (401, 403):
        # Wrong credentials, not a network blip. Reported as failure — read as
        # progress, the job would sit at 1% forever.
        log.error("video poll rejected (%s): %s", response.status_code, response.text[:200])
        return Progress(status="failed", progress=0, error="영상 상태를 확인할 권한이 없습니다.")
    if response.status_code >= 400:
        log.warning("video poll %s: %s", response.status_code, response.text[:200])
        return Progress(status="running", progress=0)
    return _read_progress(response.json())


async def fetch(
    *, base_url: str, master_key: str, provider_job_id: str, index: int = 0
) -> bytes:
    """Downloads the finished clip through the proxy.

    `unsigned_urls` points at OpenRouter directly and needs the upstream key,
    which this service does not hold. The same asset is behind the pass-through.
    """
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
