"""OpenAI-compatible Whisper aggregator in front of one or more whisper backends.

Each backend is a `scripts/install-whisper.sh` instance (faster-whisper + GPU
on a host systemd unit) exposing OpenAI-style `/v1/audio/transcriptions`. The
shim multiplexes the same protocol across the backends — no translation, just
routing — so LibreChat (or anything else that speaks `WHISPER_URL`) gets a
single endpoint while audio jobs spread across the GPU fleet.

Routing
───────
For each request, among backends that pass /health:

  1. **Inflight first.** Each backend serves a request on its GPU sequentially
     (whisper's model state is per-process; concurrent requests queue inside
     the worker). Lowest in-flight count wins so a busy node doesn't keep
     accumulating jobs while an idle node sits empty.
  2. **Ollama VRAM tie-break.** Same unified-memory dynamic as
     comfyui-shim — on GB10 each node shares VRAM between ollama and the
     whisper worker, so prefer the host whose ollama is holding less weight.
  3. **Stable fallback.** If every backend's health probe fails we still
     forward to the first one so the caller gets a meaningful 5xx instead of
     a silent drop; the youtube MCP treats that as "local whisper failed →
     OR fallback".

No variant catalogue (every backend serves the same WHISPER_MODEL), no
prompt_id stickiness (each request is one HTTP round-trip and self-contained).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

LOG = logging.getLogger("whisper-shim")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())


def _parse_backends() -> list[str]:
    """WHISPER_URLS (preferred, comma-separated) → WHISPER_URL (legacy single)
    → default in-network compose-host fallback."""
    raw = os.getenv("WHISPER_URLS") or os.getenv("WHISPER_URL") or "http://host.docker.internal:9000"
    urls = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    if not urls:
        raise RuntimeError("WHISPER_URLS / WHISPER_URL is empty")
    return urls


BACKENDS: list[str] = _parse_backends()
HEALTH_PROBE_TIMEOUT_SEC = float(os.getenv("HEALTH_PROBE_TIMEOUT_SEC", "2.0"))
HEALTH_CACHE_TTL_SEC    = float(os.getenv("HEALTH_CACHE_TTL_SEC", "10"))
TRANSCRIBE_TIMEOUT_SEC  = float(os.getenv("TRANSCRIBE_TIMEOUT_SEC", "900"))
OLLAMA_VRAM_LOADED_THRESHOLD_BYTES = int(
    os.getenv("OLLAMA_VRAM_LOADED_THRESHOLD_BYTES", str(30 * 1024**3))
)

# In-process inflight counter — accurate per shim replica. Multiple replicas
# wouldn't share this view, but we only run one shim container per stack so
# this is the authoritative load signal at the routing layer.
_INFLIGHT: dict[str, int] = defaultdict(int)
_INFLIGHT_LOCK = asyncio.Lock()

# Cached /health probe result so we don't hit every backend on every request
# (each call is a quick GET but they add up under bursty MCP usage). TTL is
# short enough that a node that goes down is dropped within ~10s.
_HEALTH: dict[str, bool] = {}
_HEALTH_AT: float = 0.0
_HEALTH_LOCK = asyncio.Lock()

LOG.info("Whisper backends: %s", ", ".join(BACKENDS))

app = FastAPI(title="KloudChat Whisper Shim", version="0.1.0")


# ──────────────────────────────────────────────────────────────────────
# Backend selection
# ──────────────────────────────────────────────────────────────────────

async def _probe_health(client: httpx.AsyncClient, backend: str) -> bool:
    try:
        r = await client.get(f"{backend}/health", timeout=HEALTH_PROBE_TIMEOUT_SEC)
        return r.status_code < 400
    except httpx.HTTPError:
        return False


async def _refresh_health(client: httpx.AsyncClient) -> None:
    """Refresh the cached health map if stale. Holds a lock so concurrent
    requests share one probe round."""
    global _HEALTH_AT
    async with _HEALTH_LOCK:
        fresh = _HEALTH and (time.monotonic() < _HEALTH_AT + HEALTH_CACHE_TTL_SEC)
        if fresh:
            return
        results = await asyncio.gather(*(_probe_health(client, b) for b in BACKENDS))
        _HEALTH.clear()
        for b, ok in zip(BACKENDS, results):
            _HEALTH[b] = ok
        _HEALTH_AT = time.monotonic()
        LOG.info("Whisper health: %s",
                 ", ".join(f"{b}={'up' if ok else 'down'}" for b, ok in _HEALTH.items()))


async def _ollama_vram_used(client: httpx.AsyncClient, backend: str) -> int | None:
    """Sum of VRAM occupied by ollama-loaded models on the same host as the
    given whisper backend. None when the ollama probe is unreachable.

    Pairs whisper :9000 with ollama :11434 on the same host — same unified-
    memory dynamic comfyui-shim leverages on GB10."""
    try:
        host = backend.split("://", 1)[1].split(":", 1)[0]
        url = f"http://{host}:11434/api/ps"
        r = await client.get(url, timeout=HEALTH_PROBE_TIMEOUT_SEC)
        r.raise_for_status()
        return sum(m.get("size_vram", 0) for m in r.json().get("models", []) if isinstance(m, dict))
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


async def _pick_backend(client: httpx.AsyncClient) -> str:
    """Pick the least-loaded healthy backend.

    Score tuple: (loaded_tier, inflight, vram_used). loaded_tier is 1 when
    paired ollama is holding >threshold VRAM — pushes loaded hosts to the
    bottom unless every candidate is loaded. Within a tier, prefer fewest
    inflight whisper requests (smallest queue), then least ollama VRAM
    occupied (most room for whisper's model)."""
    if len(BACKENDS) == 1:
        return BACKENDS[0]

    await _refresh_health(client)
    healthy = [b for b in BACKENDS if _HEALTH.get(b)]
    if not healthy:
        LOG.warning("All whisper backends unhealthy; forwarding to %s anyway", BACKENDS[0])
        return BACKENDS[0]
    if len(healthy) == 1:
        return healthy[0]

    vrams = await asyncio.gather(*(_ollama_vram_used(client, b) for b in healthy))

    def _score(item):
        backend, vram = item
        v_eff = vram if vram is not None else 0
        loaded = 1 if v_eff > OLLAMA_VRAM_LOADED_THRESHOLD_BYTES else 0
        return (loaded, _INFLIGHT[backend], v_eff)

    pairs = list(zip(healthy, vrams))
    pairs.sort(key=lambda p: _score(p))
    chosen = pairs[0][0]

    def _fmt_vram(v):
        return "?" if v is None else f"{v / 2**30:.1f}GiB"
    LOG.info(
        "Routing to %s — candidates: %s",
        chosen,
        ", ".join(
            f"{b}[inflight={_INFLIGHT[b]}, vram={_fmt_vram(v)}]"
            for b, v in pairs
        ),
    )
    return chosen


async def _inc_inflight(backend: str) -> None:
    async with _INFLIGHT_LOCK:
        _INFLIGHT[backend] += 1


async def _dec_inflight(backend: str) -> None:
    async with _INFLIGHT_LOCK:
        _INFLIGHT[backend] = max(0, _INFLIGHT[backend] - 1)


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        await _refresh_health(client)
    return {
        "status": "ok" if any(_HEALTH.values()) else "degraded",
        "backends": [{"url": b, "up": _HEALTH.get(b, False), "inflight": _INFLIGHT[b]} for b in BACKENDS],
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(request: Request) -> Response:
    """Multipart passthrough — forward the raw request body and content-type
    to the chosen backend, then mirror its response.

    Reading the body into memory once is fine: whisper inputs are short audio
    files (the youtube MCP downloads bestaudio[ext=m4a]), and faster-whisper
    needs the whole file on disk anyway. Streaming through wouldn't save
    memory and would complicate the inflight accounting.
    """
    body = await request.body()
    content_type = request.headers.get("content-type", "application/octet-stream")

    async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT_SEC) as client:
        backend = await _pick_backend(client)
        await _inc_inflight(backend)
        try:
            r = await client.post(
                f"{backend}/v1/audio/transcriptions",
                content=body,
                headers={"content-type": content_type},
            )
        except httpx.HTTPError as e:
            LOG.warning("whisper backend %s request failed: %s", backend, e)
            raise HTTPException(502, f"whisper backend {backend} unreachable: {e}")
        finally:
            await _dec_inflight(backend)

    # Mirror status + headers (Content-Type especially — backend may return
    # text/plain when caller asked for response_format=text). Strip hop-by-hop
    # headers httpx already collapsed; Content-Length is regenerated by
    # FastAPI from the body.
    excluded = {"content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    return Response(content=r.content, status_code=r.status_code, headers=headers,
                    media_type=r.headers.get("content-type"))
