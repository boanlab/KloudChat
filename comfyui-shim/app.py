"""A1111-compatible shim in front of one or more ComfyUI backends.

LibreChat's built-in generate_image tool speaks the A1111 / AUTOMATIC1111
WebUI REST conventions (POST /sdapi/v1/txt2img, …) and
expects a base64-encoded PNG back. ComfyUI speaks a different protocol: you
POST a workflow graph to /prompt, poll /history/{prompt_id} for status, and
fetch each output image from /view.

This shim translates between the two. For each generation:

  1. Resolve the canonical alias (`flux-schnell`, `flux-dev`) from the
     A1111 request, then look up which workflow template the chosen backend
     can run.
  2. Substitute the prompt / negative / seed / steps / cfg parameters into
     the template's CLIP / KSampler / image-loader nodes.
  3. POST the workflow to ComfyUI's /prompt endpoint.
  4. Poll /history/<prompt_id> until the run finishes.
  5. Fetch each output image via /view, base64 it, return in A1111 shape.

Multi-backend routing + per-node variants
─────────────────────────────────────────
COMFYUI_URLS may list more than one ComfyUI backend (comma-separated). When
multiple are configured the shim:

  * discovers each backend's loaded UNet files via /object_info (cached,
    refreshed every MODEL_DISCOVERY_TTL_SEC) and matches them against
    COMFYUI_ALIAS_VARIANTS; the first variant whose file exists on the node
    wins, so heterogeneous clusters can each carry the quant that suits
    their tensor cores without per-node config in the shim;
  * filters candidates by alias availability, then probes /queue plus the
    paired-host vLLM chat (8001) busy count and picks the least-loaded;
  * pins each run to the chosen backend for the whole /prompt → /history →
    /view sequence so polls and fetches land on the node that ran the
    workflow (ComfyUI run state is per-node).
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

LOG = logging.getLogger("comfyui-shim")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())


def _parse_backends() -> list[str]:
    """COMFYUI_URLS — comma-separated ComfyUI backend URLs. 비어 있으면 [] (로컬
    ComfyUI 없는 GPU-less/OR 전용 배포). 외부 OR 이미지/비디오 라우팅 = 정상
    동작, 로컬 모델(flux/ltxv) 요청만 per-request 503 거부."""
    raw = os.getenv("COMFYUI_URLS", "")
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


BACKENDS: list[str] = _parse_backends()
WORKFLOWS_DIR = Path(__file__).parent / "workflows"
# 로컬 ComfyUI 없으면 기본값 = 최저가 외부 OR 이미지 — "모델 미지정" 요청도 동작.
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL") or ("flux-schnell" if BACKENDS else "nano-banana")
POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "0.5"))
POLL_TIMEOUT_SEC = float(os.getenv("POLL_TIMEOUT_SEC", "1800"))
QUEUE_PROBE_TIMEOUT_SEC = float(os.getenv("QUEUE_PROBE_TIMEOUT_SEC", "2.0"))
MODEL_DISCOVERY_TTL_SEC = float(os.getenv("MODEL_DISCOVERY_TTL_SEC", "300"))
OBJECT_INFO_TIMEOUT_SEC = float(os.getenv("OBJECT_INFO_TIMEOUT_SEC", "10"))

LOG.info("ComfyUI backends: %s", ", ".join(BACKENDS) if BACKENDS else "(none — 외부 OR 이미지/비디오 전용)")


# Per-canonical-alias variant catalogue. Each variant is
# (loader-kind, on-disk filename, workflow template) ordered from highest-perf
# (FP8) down to fallback (Q8 GGUF). The first variant whose file is present
# on a given ComfyUI node is the one we route to, so heterogeneous clusters
# can each carry the quant that suits their tensor cores without per-node
# config in the shim. Mirrors __comfyui_node_models in scripts/lib.sh.
COMFYUI_ALIAS_VARIANTS: dict[str, list[tuple[str, str, str]]] = {
    "flux-schnell": [
        ("unet", "flux1-schnell.safetensors", "flux-schnell-txt2img.json"),
        ("unet", "flux1-schnell-Q8_0.gguf",   "flux-schnell-txt2img-gguf.json"),
    ],
    "flux-dev": [
        ("unet", "flux1-dev.safetensors", "flux-dev-txt2img.json"),
        ("unet", "flux1-dev-Q8_0.gguf",   "flux-dev-txt2img-gguf.json"),
    ],
}

ALIAS_TO_CANONICAL: dict[str, str] = {
    "flux-dev": "flux-dev",
    "flux-schnell": "flux-schnell",
}

# 비디오 생성 alias (Video Studio). 이미지(A1111 /txt2img)와 별개 경로 — /video/submit
# 처리, generate_video MCP 가 호출. loader-kind = "checkpoint" 라 디스커버리는
# CheckpointLoaderSimple.ckpt_name 풀에서 매칭(이미지의 "unet" 와 다름). LTXV 체크포인트 =
# DiT+VAE 만, T5 텍스트 인코더는 워크플로가 clip/t5xxl 로 별도 로드.
VIDEO_ALIAS_VARIANTS: dict[str, list[tuple[str, str, str]]] = {
    "ltx-video": [
        ("checkpoint", "ltx-video-2b-v0.9.5.safetensors", "ltx-video-t2v.json"),
    ],
}

# 디스커버리/라우팅 = 이미지+비디오 alias 함께. 각 variant 의 첫 원소(loader-kind)로
# 어느 object_info 풀(unet vs checkpoint)에서 파일 존재 확인할지 결정.
ALL_VARIANTS: dict[str, list[tuple[str, str, str]]] = {
    **COMFYUI_ALIAS_VARIANTS,
    **VIDEO_ALIAS_VARIANTS,
}

# 외부 image-gen alias → LiteLLM model_name. 호출 = LiteLLM 의 chat/completions
# 에 `modalities=["image","text"]` 전송 → 응답의 message.images[0].image_url
# 의 data URL strip → A1111 response shape 으로 wrap.
# 등록 = litellm-config.yaml 의 KLOUDCHAT_AUTOGEN 블록 밖 manual section.
EXTERNAL_IMAGE_ALIASES: dict[str, str] = {
    "nano-banana":    "nano-banana",     # → openrouter/google/gemini-2.5-flash-image
    "nano-banana-2":  "nano-banana-2",   # → openrouter/google/gemini-3.1-flash-image-preview
    "gpt-image-2":    "gpt-image-2",     # → openrouter/openai/gpt-5.4-image-2
}

LITELLM_URL = (os.getenv("LITELLM_URL") or "http://litellm:8000").rstrip("/")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
EXTERNAL_IMAGE_TIMEOUT_SEC = float(os.environ.get("EXTERNAL_IMAGE_TIMEOUT_SEC", "300"))

# ── 외부 비디오 생성 (OpenRouter Video API, LiteLLM 경유) ─────────────────
# OR 의 /videos = 비동기 잡(submit→poll→download)이라 LiteLLM 네이티브 video provider
# 대상 아님 → LiteLLM 의 pass_through_endpoints 로 OR 에 포워딩. shim 은 OR 키
# 직접 미보유, LiteLLM(LITELLM_API_KEY)에만 인증 — OR 키 주입은 LiteLLM 담당.
# 응답 shape = 로컬 LTXV(comfyui) 와 통일. alias → OR model slug. 외부·유료(초당 과금).
# LiteLLM passthrough → openrouter.ai/api/v1. submit(과금 1회) / job(폴·다운로드, 과금 0) 분리.
OR_VIDEO_SUBMIT = f"{LITELLM_URL}/orvideo/submit"
OR_VIDEO_JOB = f"{LITELLM_URL}/orvideo/job"
# 로컬 미디어 명목 단가 — LiteLLM passthrough 로 user 귀속만 기록(자체 GPU, 실비용 0).
# 단가 = OR 동급 50%: 이미지 $0.02/장, 비디오 $0.04/초 (litellm-config 의 cost_per_request).
LOCALBILL = f"{LITELLM_URL}/localbill"
LOCALBILL_VIDEO_MAX_SEC = 12
OR_VIDEO_ALIASES: dict[str, str] = {
    "veo-lite": "google/veo-3.1-lite",   # 기본 — 오디오, 저가
    "veo-fast": "google/veo-3.1-fast",   # 오디오, 고품질
    "veo":      "google/veo-3.1",        # Veo 3.1 풀
    "sora-2":   "openai/sora-2-pro",     # OpenAI Sora 2 Pro
}

app = FastAPI(title="ComfyUI A1111 Shim", version="0.2.0")


# ──────────────────────────────────────────────────────────────────────
# Backend selection
# ──────────────────────────────────────────────────────────────────────

async def _queue_depth(client: httpx.AsyncClient, backend: str) -> int | None:
    """Running + pending jobs on a backend. None when unreachable."""
    try:
        r = await client.get(f"{backend}/queue", timeout=QUEUE_PROBE_TIMEOUT_SEC)
        r.raise_for_status()
        body = r.json()
        return len(body.get("queue_running", [])) + len(body.get("queue_pending", []))
    except (httpx.HTTPError, ValueError, KeyError):
        return None


# Pre-flight VRAM admission control — on unified-memory hosts, vLLM stays
# resident so ComfyUI's working set must fit in the remainder. Refuse to
# forward until /system_stats vram_free ≥ required to avoid OOM during model
# swap (old + new weights co-resident).
MODEL_VRAM_REQUIRED: dict[str, int] = {
    # GGUF dequant = layer-by-layer 라 GPU peak < weight 전체, 게다가
    # GB10 unified memory 는 page cache 가 nvidia-smi used 에 잡혀 vram_free 가
    # 낮게 보임 → 보수적 디폴트(FP16 weight 합산) 그대로면 admission 영구 막힘.
    # weight load peak 직전 측정값 기준.
    "flux-schnell":    int(3 * 1024**3),
    "flux-dev":        int(5 * 1024**3),
    # LTXV 2B weight 는 가볍지만 97프레임 latent + VAE decode peak 큼. GB10
    # unified memory 라 ram_free 폴백으로 admission — 보수적 10G.
    "ltx-video":       int(10 * 1024**3),
}
DEFAULT_VRAM_REQUIRED = int(12 * 1024**3)
CAPACITY_WAIT_TIMEOUT_SEC = float(os.getenv("CAPACITY_WAIT_TIMEOUT_SEC", "300"))
CAPACITY_POLL_INTERVAL_SEC = float(os.getenv("CAPACITY_POLL_INTERVAL_SEC", "3"))


async def _vram_free(client: httpx.AsyncClient, backend: str) -> int | None:
    """이 ComfyUI 가 새 generation 에 쓸 수 있는 메모리 (bytes).

    Discrete GPU = `vram_free + torch_vram_total` — 시스템 free + ComfyUI 자기
    PyTorch allocator reserve (그 reserve 는 같은 프로세스가 재사용
    가능 → admission 판단에 포함).

    Unified memory (GB10 등, `vram_total == ram_total`) = 위 값과 `system.ram_free`
    중 큰 값. unified memory 에선 OS page cache + 같은 노드 vLLM 의 cudaMallocAsync
    reserve 가 `vram_free` underreport → admission 영구 막힘 — page
    cache 는 ComfyUI 가 GPU mem 요청 시 즉시 evict 되므로 사실상 evictable 가용량.
    `ram_free` = 그 진짜 가용량에 더 가까운 신호.

    None = probe 실패 → caller 가 best-effort 통과."""
    try:
        r = await client.get(f"{backend}/system_stats", timeout=QUEUE_PROBE_TIMEOUT_SEC)
        r.raise_for_status()
        body = r.json()
        devices = body.get("devices") or []
        if not devices:
            return None
        sys_free = int(devices[0].get("vram_free", 0))
        own_reserve = int(devices[0].get("torch_vram_total", 0))
        base = sys_free + own_reserve
        # Unified memory 감지 = device.vram_total == system.ram_total.
        system = body.get("system") or {}
        vram_total = int(devices[0].get("vram_total", 0))
        ram_total = int(system.get("ram_total", 0))
        ram_free = int(system.get("ram_free", 0))
        if vram_total and ram_total and vram_total == ram_total and ram_free:
            return max(base, ram_free)
        return base
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


# Load-aware routing: image-gen candidates are always vLLM-paired hosts.
# Paired vLLM 8001 /metrics busy count = GPU-occupation signal — image-gen
# and chat share SM + memory bandwidth on unified memory, so chat-busy →
# avoid scheduling image-gen here.
VLLM_METRICS_PORT = int(os.getenv("VLLM_METRICS_PORT", "8001"))


async def _vllm_busy_count(client: httpx.AsyncClient, backend: str) -> int | None:
    """Sum of paired vLLM's running + waiting requests. None on probe failure."""
    try:
        host = backend.split("://", 1)[1].split(":", 1)[0]
        url = f"http://{host}:{VLLM_METRICS_PORT}/metrics"
        r = await client.get(url, timeout=QUEUE_PROBE_TIMEOUT_SEC)
        r.raise_for_status()
        running = waiting = 0
        for line in r.text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            # Prometheus exposition = `metric_name{labels} value` 또는 `name value`.
            head, _, value = line.rpartition(" ")
            name = head.split("{", 1)[0]
            if name == "vllm:num_requests_running":
                running = int(float(value))
            elif name == "vllm:num_requests_waiting":
                waiting = int(float(value))
        return running + waiting
    except (httpx.HTTPError, ValueError, KeyError):
        return None


# backend URL → {canonical alias: workflow template} the node can serve.
# Workflow comes from the first matching variant in COMFYUI_ALIAS_VARIANTS,
# so heterogeneous nodes can share an alias yet pick the workflow matching
# their on-disk file. Empty dict for unreachable nodes.
_NODE_VARIANTS: dict[str, dict[str, str]] = {}
_NODE_VARIANTS_AT: float = 0.0
_NODE_VARIANTS_LOCK = asyncio.Lock()


def _files_from_object_info(info: dict[str, Any], node_class: str, key: str) -> set[str]:
    """Extract the file-name pool from /object_info. ComfyUI returns
    `<NodeClass>.input.required.<key>` as `[[<list of filenames>], {...}]`;
    we want the inner list."""
    node = info.get(node_class) if isinstance(info, dict) else None
    if not isinstance(node, dict):
        return set()
    slot = (((node.get("input") or {}).get("required") or {}).get(key))
    if isinstance(slot, list) and slot and isinstance(slot[0], list):
        return {x for x in slot[0] if isinstance(x, str)}
    return set()


async def _discover_node_variants(client: httpx.AsyncClient, backend: str) -> dict[str, str]:
    """Hit /object_info on one backend; return {canonical_alias: workflow_template}
    for whichever variant (FP8 → GGUF) the node has on disk. Empty dict on
    failure — caller treats that as 'no aliases here right now' rather than
    'all aliases'."""
    try:
        r = await client.get(f"{backend}/object_info", timeout=OBJECT_INFO_TIMEOUT_SEC)
        r.raise_for_status()
        info = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    # loader-kind 별 파일 풀. 이미지(unet/gguf) = UNETLoader, 비디오(LTXV) =
    # CheckpointLoaderSimple 에서 노출.
    pools: dict[str, set[str]] = {
        "unet": (_files_from_object_info(info, "UNETLoader", "unet_name")
                 | _files_from_object_info(info, "UnetLoaderGGUF", "unet_name")),
        "checkpoint": _files_from_object_info(info, "CheckpointLoaderSimple", "ckpt_name"),
    }
    out: dict[str, str] = {}
    for alias, variants in ALL_VARIANTS.items():
        for kind, fname, workflow in variants:
            if fname in pools.get(kind, set()):
                out[alias] = workflow
                break  # first match wins (preference order)
    return out


async def _refresh_node_variants(client: httpx.AsyncClient) -> None:
    """Re-probe every backend's /object_info if the cache is empty or stale.
    Holds an asyncio lock so concurrent requests share a single refresh."""
    global _NODE_VARIANTS_AT
    async with _NODE_VARIANTS_LOCK:
        fresh = _NODE_VARIANTS and (time.monotonic() < _NODE_VARIANTS_AT + MODEL_DISCOVERY_TTL_SEC)
        if fresh:
            return
        results = await asyncio.gather(*(_discover_node_variants(client, b) for b in BACKENDS))
        _NODE_VARIANTS.clear()
        for b, variants in zip(BACKENDS, results):
            _NODE_VARIANTS[b] = variants
        _NODE_VARIANTS_AT = time.monotonic()
        for b in BACKENDS:
            served = _NODE_VARIANTS.get(b, {})
            summary = ", ".join(f"{a}:{w}" for a, w in sorted(served.items())) or "(none)"
            LOG.info("ComfyUI %s variants: %s", b, summary)


async def _backends_with_alias(client: httpx.AsyncClient, alias: str) -> list[str]:
    """Backends whose last discovery turned up `alias` (any variant). Order
    preserves BACKENDS order so the fallback in _pick_backend stays deterministic."""
    await _refresh_node_variants(client)
    return [b for b in BACKENDS if alias in _NODE_VARIANTS.get(b, {})]


async def _pick_backend(client: httpx.AsyncClient, *, canonical: str | None = None) -> str:
    """Choose a backend: filter by canonical-alias availability, then least-loaded.
    Falls back to the first configured backend if every probe failed —
    gives the caller a meaningful error instead of silently dropping the request.

    A 404 surfaces only when `canonical` is given but no reachable node has any
    variant of it on disk; that's a real configuration miss, not transient load."""
    candidates = BACKENDS
    if canonical:
        # _backends_with_alias 는 _refresh_node_variants 트리거 → _NODE_VARIANTS
        # 캐시 채우는 부수효과. txt2img/img2img 의 template_name lookup 이
        # 같은 캐시를 읽으므로 alias 있는 호출은 반드시 이 path 경유 필수.
        narrowed = await _backends_with_alias(client, canonical)
        if not narrowed:
            raise HTTPException(
                404,
                f"alias {canonical!r} has no variant on any reachable ComfyUI node "
                f"(checked: {', '.join(BACKENDS)})",
            )
        candidates = narrowed

    # Probe queue / vllm busy / vram in a loop until at least one candidate
    # has free vram ≥ model required.
    required = MODEL_VRAM_REQUIRED.get(canonical or "", DEFAULT_VRAM_REQUIRED)
    deadline = time.monotonic() + CAPACITY_WAIT_TIMEOUT_SEC
    wait_logged = False
    while True:
        busies, depths, vrams = await asyncio.gather(
            asyncio.gather(*(_vllm_busy_count(client, b) for b in candidates)),
            asyncio.gather(*(_queue_depth(client, b) for b in candidates)),
            asyncio.gather(*(_vram_free(client, b) for b in candidates)),
        )
        # vram None (probe 실패) = best-effort 통과 — 거부보다 시도가 안전.
        eligible = [
            (d, v, vr, b) for d, v, vr, b in zip(depths, busies, vrams, candidates)
            if d is not None and (vr is None or vr >= required)
        ]
        if eligible:
            def _score(quad):
                d, v, _vr, _b = quad
                return (v if v is not None else 0, d)
            eligible.sort(key=_score)
            chosen = eligible[0][3]
            LOG.info(
                "Routing to %s (alias=%s) — backends: %s",
                chosen, canonical or "any",
                ", ".join(
                    f"{b}[vram={'?' if vr is None else f'{vr/2**30:.1f}G'}, vllm_busy={'?' if v is None else v}, q={d}]"
                    for d, v, vr, b in zip(depths, busies, vrams, candidates)
                ),
            )
            return chosen
        # No eligible — every candidate is either over capacity or unreachable.
        reachable = [b for b, d in zip(candidates, depths) if d is not None]
        if not reachable:
            LOG.warning("All candidate ComfyUI backends failed /queue probe; defaulting to %s", candidates[0])
            return candidates[0]
        if not wait_logged:
            LOG.info(
                "All %d candidate(s) below capacity (alias=%s needs %dG); waiting up to %ds — %s",
                len(candidates), canonical or "any", required // 2**30,
                int(CAPACITY_WAIT_TIMEOUT_SEC),
                ", ".join(f"{b}[vram={'?' if vr is None else f'{vr/2**30:.1f}G'}]" for b, vr in zip(candidates, vrams)),
            )
            wait_logged = True
        if time.monotonic() >= deadline:
            raise HTTPException(
                503,
                f"image-gen capacity wait timeout — {canonical or 'any'} needs "
                f"{required // 2**30}G but no backend has it within {int(CAPACITY_WAIT_TIMEOUT_SEC)}s",
            )
        await asyncio.sleep(CAPACITY_POLL_INTERVAL_SEC)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _load_workflow(filename: str) -> dict[str, Any]:
    path = WORKFLOWS_DIR / filename
    if not path.is_file():
        raise HTTPException(500, f"workflow template missing: {filename}")
    with path.open() as f:
        graph = json.load(f)
    # 템플릿은 문서용 `_comment` 같은 비-노드 최상위 키를 가질 수 있음. ComfyUI
    # /prompt 는 모든 최상위 키를 노드로 취급(문자열이면 검증 실패), shim 의 노드
    # 순회도 dict 가정 → 노드(dict)만 잔류.
    return {k: v for k, v in graph.items() if isinstance(v, dict)}


def _set_node_input(workflow: dict[str, Any], title: str, key: str, value: Any) -> None:
    """Set inputs.<key> on the node whose _meta.title matches `title`.

    Workflow JSONs include a `_meta.title` field per node — we use that as a
    stable handle instead of hard-coding numeric node IDs that shift whenever
    the template is re-exported from the ComfyUI UI.
    """
    for node in workflow.values():
        if node.get("_meta", {}).get("title") == title:
            node.setdefault("inputs", {})[key] = value
            return
    LOG.debug("workflow node titled %r not found — skipping %s=%r", title, key, value)


def _populate_workflow(
    template: dict[str, Any],
    *,
    prompt: str,
    negative: str = "",
    seed: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Apply A1111 request fields onto a workflow template.

    ComfyUI caches node outputs by input hash. If the caller resubmits the
    same workflow inputs (e.g. seed=42, same prompt) we'd get a cache hit on
    every node — including SaveImage — and history returns outputs={}, which
    looks to the shim like "0 images produced". To avoid that we:

      * always inject a seed (random when caller didn't supply one)
      * stamp SaveImage.filename_prefix with a per-call unique suffix, so
        SaveImage always runs and emits an output file
    """
    wf = copy.deepcopy(template)
    _set_node_input(wf, "PositivePrompt", "text", prompt)
    if negative:
        _set_node_input(wf, "NegativePrompt", "text", negative)
    effective_seed = seed if seed is not None else secrets.randbits(63)
    _set_node_input(wf, "KSampler", "seed", effective_seed)
    if steps is not None:
        _set_node_input(wf, "KSampler", "steps", steps)
    if cfg is not None:
        _set_node_input(wf, "KSampler", "cfg", cfg)
    if width is not None:
        _set_node_input(wf, "EmptyLatent", "width", width)
    if height is not None:
        _set_node_input(wf, "EmptyLatent", "height", height)

    # Force SaveImage to run each call.
    unique = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    for node in wf.values():
        if node.get("class_type") == "SaveImage":
            prefix = node.get("inputs", {}).get("filename_prefix", "kloudchat")
            node["inputs"]["filename_prefix"] = f"{prefix}-{unique}"
    return wf


def _populate_video_workflow(
    template: dict[str, Any],
    *,
    prompt: str,
    negative: str = "",
    seed: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    width: int | None = None,
    height: int | None = None,
    length: int | None = None,
    frame_rate: int | None = None,
) -> dict[str, Any]:
    """LTXV 워크플로 템플릿에 t2v 요청 필드 주입.

    이미지(_populate_workflow)와 노드 핸들 상이: 시드/cfg = SamplerCustom("Sampler"),
    steps = LTXVScheduler("Scheduler"), 해상도/길이 = EmptyLTXVLatentVideo("EmptyLTXVLatent").
    캐시 히트로 SaveVideo skip → outputs={} 되는 것 방지 위해 항상 시드 주입 +
    VHS_VideoCombine.filename_prefix 에 per-call 유니크 suffix 로 매번 실행 강제.
    """
    wf = copy.deepcopy(template)
    _set_node_input(wf, "PositivePrompt", "text", prompt)
    if negative:
        _set_node_input(wf, "NegativePrompt", "text", negative)
    effective_seed = seed if seed is not None else secrets.randbits(63)
    _set_node_input(wf, "Sampler", "noise_seed", effective_seed)
    if cfg is not None:
        _set_node_input(wf, "Sampler", "cfg", cfg)
    if steps is not None:
        _set_node_input(wf, "Scheduler", "steps", steps)
    if width is not None:
        _set_node_input(wf, "EmptyLTXVLatent", "width", width)
    if height is not None:
        _set_node_input(wf, "EmptyLTXVLatent", "height", height)
    if length is not None:
        _set_node_input(wf, "EmptyLTXVLatent", "length", length)
    if frame_rate is not None:
        _set_node_input(wf, "LTXVConditioning", "frame_rate", float(frame_rate))
        _set_node_input(wf, "SaveVideo", "frame_rate", int(frame_rate))

    # VHS_VideoCombine / SaveVideo 매 호출 실행되도록 prefix 유니크화.
    unique = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    for node in wf.values():
        if node.get("class_type") in ("VHS_VideoCombine", "SaveVideo"):
            prefix = node.get("inputs", {}).get("filename_prefix", "kloudchat-ltxv")
            node["inputs"]["filename_prefix"] = f"{prefix}-{unique}"
    return wf


async def _comfy_submit_and_wait(client: httpx.AsyncClient, backend: str,
                                 workflow: dict[str, Any]) -> dict[str, Any]:
    """Queue a workflow on `backend`, poll /history until done, return the run
    entry (outputs collection is caller-specific: images vs video)."""
    r = await client.post(f"{backend}/prompt", json={"prompt": workflow}, timeout=30)
    if r.status_code >= 400:
        # Surface ComfyUI's structured error to the caller. The /prompt
        # endpoint returns a JSON body explaining missing checkpoints,
        # unknown node types, malformed inputs, etc. — propagating that
        # verbatim makes debugging the workflow templates much faster than
        # the default httpx 500 reraise would.
        try:
            detail = r.json()
        except Exception:
            detail = {"raw": r.text}
        LOG.warning("ComfyUI /prompt rejected workflow on %s: %s", backend, detail)
        raise HTTPException(status_code=r.status_code, detail=detail)
    prompt_id = r.json()["prompt_id"]

    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    # /history poll failures during cold model load are transient — retry
    # until the outer deadline.
    while True:
        if time.monotonic() > deadline:
            raise HTTPException(504, f"ComfyUI run timed out after {POLL_TIMEOUT_SEC}s on {backend}")
        try:
            h = await client.get(f"{backend}/history/{prompt_id}", timeout=60)
            h.raise_for_status()
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError):
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue
        body = h.json()
        entry = body.get(prompt_id) if isinstance(body, dict) else None
        if entry:
            status = entry.get("status") or {}
            # ComfyUI marks failed runs status_str='error', completed=False —
            # surface the execution_error so we don't poll to deadline.
            if status.get("status_str") == "error":
                msg = "ComfyUI execution error"
                for kind, payload in (status.get("messages") or []):
                    if kind == "execution_error" and isinstance(payload, dict):
                        etype = payload.get("exception_type", "?")
                        emsg = str(payload.get("exception_message", "?")).strip()
                        node = payload.get("node_type") or payload.get("node_id") or "?"
                        msg = f"{etype} at {node}: {emsg}"
                        break
                raise HTTPException(502, f"ComfyUI run failed on {backend}: {msg}")
            if status.get("completed"):
                return entry
        await asyncio.sleep(POLL_INTERVAL_SEC)


async def _comfy_run(client: httpx.AsyncClient, backend: str, workflow: dict[str, Any]) -> list[str]:
    """Queue an image workflow, wait, return base64 PNGs (A1111 image path)."""
    run = await _comfy_submit_and_wait(client, backend, workflow)
    images_b64: list[str] = []
    for node_out in run.get("outputs", {}).values():
        for img in node_out.get("images", []):
            params = {
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            }
            v = await client.get(f"{backend}/view", params=params, timeout=30)
            v.raise_for_status()
            images_b64.append(base64.b64encode(v.content).decode())
    return images_b64


def _resolve_alias(model: str | None) -> tuple[str, str]:
    """Validate the user-supplied alias and return `(alias_key, canonical)`.
    Raises 400 on unknown alias."""
    key = (model or DEFAULT_MODEL).lower()
    canonical = ALIAS_TO_CANONICAL.get(key)
    if not canonical:
        known = sorted(ALIAS_TO_CANONICAL)
        raise HTTPException(400, f"unknown model alias: {model!r}. Known: {known}")
    return key, canonical


# ──────────────────────────────────────────────────────────────────────
# A1111-shaped request bodies (only the fields LibreChat actually sends are
# typed; everything else is accepted and ignored).
# ──────────────────────────────────────────────────────────────────────

class Txt2ImgRequest(BaseModel):
    model_config = {"extra": "allow"}
    prompt: str = ""
    negative_prompt: str = ""
    seed: int = -1
    steps: int = 25
    cfg_scale: float = 7.0
    width: int = 1024
    height: int = 1024
    sampler_name: str | None = None
    override_settings: dict[str, Any] = Field(default_factory=dict)


class Txt2VidRequest(BaseModel):
    """generate_video MCP → /video/submit. A1111 과 무관한 자체 shape.
    model ∈ OR_VIDEO_ALIASES → OpenRouter, 'ltx-video' → 로컬 ComfyUI 분기."""
    model_config = {"extra": "allow"}
    prompt: str = ""
    negative_prompt: str = ""
    model: str = "veo-lite"       # 기본 = OpenRouter(외부). 로컬은 'ltx-video'
    seed: int = -1
    # OpenRouter 용
    duration: int | None = None   # 초
    aspect_ratio: str | None = None
    resolution: str | None = None  # 720p|1080p|4K (모델별 지원셋으로 정규화)
    audio: bool = True             # generate_audio (Veo만; Sora는 항상 포함)
    end_user: str = ""            # 과금 귀속용 caller email (x-litellm-end-user-id)
    # 로컬 LTXV 용
    steps: int | None = None      # None → workflow baked-in default
    cfg: float | None = None
    width: int = 768
    height: int = 512
    length: int = 97              # 프레임 수 (≈ length/frame_rate 초)
    frame_rate: int = 25


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "backends": BACKENDS}


@app.post("/billsink")
async def billsink() -> dict[str, bool]:
    # 로컬 미디어 명목 과금의 LiteLLM passthrough 타깃 — cost_per_request 기록만 트리거.
    # 본문은 사용 안 함(실제 egress 없는 과금 기록용 sink). 200 회신 → LiteLLM 이 비용 집계.
    return {"ok": True}


@app.get("/sdapi/v1/sd-models")
async def list_models() -> list[dict[str, str]]:
    """LibreChat fetches this to populate the model dropdown.
    Lists each user-facing alias once; `filename` reports the highest-priority
    variant in the catalogue (actual served variant is per-node at queue time)."""
    out: list[dict[str, str]] = []
    for alias, canonical in ALIAS_TO_CANONICAL.items():
        variants = COMFYUI_ALIAS_VARIANTS.get(canonical) or []
        filename = variants[0][1] if variants else canonical
        out.append({"title": alias, "model_name": alias, "filename": filename})
    return out


@app.post("/sdapi/v1/options")
async def set_options(_: dict[str, Any]) -> dict[str, str]:
    """A1111 uses this to switch active checkpoint. ComfyUI loads the model
    inside each workflow, so we treat this as a no-op success."""
    return {"status": "ok"}


@app.get("/sdapi/v1/progress")
async def progress() -> dict[str, Any]:
    """A1111-style progress endpoint. We don't track per-run progress yet."""
    return {"progress": 0.0, "eta_relative": 0.0, "state": {}, "current_image": None}


async def _call_external_image(req: "Txt2ImgRequest", alias: str) -> dict[str, Any]:
    """OpenRouter-routed image-gen (Gemini Nano Banana / GPT image) via LiteLLM.

    Uses chat/completions endpoint with `modalities=["image","text"]` (OpenRouter
    의 image-gen 인터페이스 — /v1/images/generations 아님). 응답의
    message.images[0].image_url.url = `data:image/png;base64,...` 형태 →
    prefix 제거, raw base64 만 A1111 response 의 `images` 배열로.

    negative_prompt = instruction-following 모델이 native 미지원 — drop.
    seed / steps / cfg / width / height 도 모델별 옵션 상이 → 우선 무시 (필요
    시 image_config.aspect_ratio 등으로 확장).
    """
    litellm_model = EXTERNAL_IMAGE_ALIASES[alias]
    # caller user_id (LibreChat StableDiffusion.js patch 가 payload.user 로 주입).
    # LiteLLM 이 end_user 컬럼에 기록 → mcp/usage.py 의 /customer/daily/activity?
    # end_user_ids= 가 사용자별 spend 로 포착. 없으면 default_user_id 귀속.
    caller_user = getattr(req, "user", "") or ""
    body: dict[str, Any] = {
        "model": litellm_model,
        "messages": [{"role": "user", "content": req.prompt}],
        # extra_body 로 전송 → LiteLLM 의 drop_unsupported_params 우회 + provider
        # 로 verbatim forward 보장.
        "extra_body": {"modalities": ["image", "text"]},
    }
    if caller_user:
        body["user"] = caller_user
    LOG.info("external image → %s (alias=%s, user=%s) via %s",
             litellm_model, alias, caller_user or "default", LITELLM_URL)
    try:
        async with httpx.AsyncClient(timeout=EXTERNAL_IMAGE_TIMEOUT_SEC) as c:
            r = await c.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LITELLM_API_KEY}" if LITELLM_API_KEY else "",
                },
            )
    except Exception as e:
        raise HTTPException(502, f"external image upstream exception: {e!r}")
    if r.status_code >= 400:
        raise HTTPException(r.status_code,
                            f"external image upstream {r.status_code}: {r.text[:400]}")
    try:
        data = r.json()
        msg = data["choices"][0]["message"]
        images = msg.get("images") or []
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise HTTPException(502, f"external image response unparseable: {e}")
    if not images:
        # content-policy refusal 또는 모델이 text-only 응답한 경우.
        txt = (msg.get("content") or "")[:300] if isinstance(msg, dict) else ""
        raise HTTPException(502,
            f"external image returned no image (model may have refused; text={txt!r})")
    try:
        url = images[0]["image_url"]["url"]
    except (KeyError, TypeError) as e:
        raise HTTPException(502, f"external image url shape unexpected: {e}")
    # data:image/png;base64,XXXX → XXXX. 비 data-URL (https://) 케이스는 일단
    # 그대로 통과 — 일부 provider 가 external URL 회신 가능, LibreChat
    # 의 A1111 파서가 raw base64 만 수용하므로 그 경우 별도 fetch 필요해질 수
    # 있으나 OpenRouter 의 image-gen 은 현재 모두 data URL 회신.
    b64 = url.split(",", 1)[1] if url.startswith("data:") else url
    return {
        "images": [b64],
        "parameters": req.model_dump(),
        "info": json.dumps({"prompt": req.prompt, "model": alias}),
    }


@app.post("/sdapi/v1/txt2img")
async def txt2img(req: Txt2ImgRequest) -> dict[str, Any]:
    model = (req.override_settings.get("sd_model_checkpoint")
             or req.override_settings.get("model") or DEFAULT_MODEL)
    # 외부 모델 = ComfyUI routing/admission/workflow 전부 우회. (DEFAULT_MODEL 도
    # ComfyUI 없으면 외부(nano-banana) → 모델 미지정도 외부로 분기.)
    if model.lower() in EXTERNAL_IMAGE_ALIASES:
        return await _call_external_image(req, model.lower())
    if not BACKENDS:
        raise HTTPException(503, f"로컬 ComfyUI 백엔드 없음 — 외부 이미지 모델({', '.join(EXTERNAL_IMAGE_ALIASES)}) 중 하나를 지정하라")
    _, canonical = _resolve_alias(model)

    async with httpx.AsyncClient() as client:
        backend = await _pick_backend(client, canonical=canonical)
        template_name = _NODE_VARIANTS.get(backend, {}).get(canonical)
        if not template_name:
            raise HTTPException(500, f"no variant resolved for {canonical!r} on {backend}")
        template = _load_workflow(template_name)
        # Flux 계열 = guidance-distilled (schnell=4 steps/cfg=1, dev=20 steps/cfg=1)
        # 이라 LibreChat 의 SD 표준 payload (steps=22, cfg=4.5) 그대로 받으면
        # 5배 느리고 품질 저하. caller 에서 의미있는 override 보낼 경로
        # 없으므로 (LibreChat UI 에 미노출) workflow 의 baked-in default 우선.
        is_flux = (canonical or "").startswith("flux")
        workflow = _populate_workflow(
            template,
            prompt=req.prompt,
            negative=req.negative_prompt,
            seed=req.seed if req.seed >= 0 else None,
            steps=None if is_flux else req.steps,
            cfg=None if is_flux else req.cfg_scale,
            width=req.width,
            height=req.height,
        )
        LOG.info("txt2img: model=%s template=%s backend=%s",
                 model or DEFAULT_MODEL, template_name, backend)
        images = await _comfy_run(client, backend, workflow)
        if images:
            await _bill_local(client, "image", getattr(req, "user", "") or "")

    return {
        "images": images,
        "parameters": req.model_dump(),
        "info": json.dumps({"prompt": req.prompt, "model": model or DEFAULT_MODEL}),
    }


# ── 비동기 잡 핸들 (submit 으로 발급 → fetch 로 회수) ────────────────────
# 문자열 1개로 백엔드+식별자 캡슐화: OR = `or:<alias>:<jobid>`, 로컬 ComfyUI =
# `comfy:<promptid>@<idx>` (idx=BACKENDS 인덱스 — 내부 IP 미노출). 핸들을 job id 로 전달,
# check_video 로 반환.
def _encode_handle(kind: str, ident: str, *, backend: str = "", alias: str = "") -> str:
    if kind == "or":
        return f"or:{alias}:{ident}"
    # backend URL(내부 IP) 미노출 위해 BACKENDS 인덱스로 인코딩 — 핸들은
    # 모델 거쳐 사용자에게 노출 가능 → raw http://10.x... 미삽입.
    try:
        idx = BACKENDS.index(backend)
    except ValueError:
        idx = 0
    return f"comfy:{ident}@{idx}"


def _decode_handle(h: str) -> tuple[str, str, str, str]:
    """→ (kind, ident, backend, alias)."""
    if h.startswith("or:"):
        _, alias, job_id = h.split(":", 2)
        return ("or", job_id, "", alias)
    if h.startswith("comfy:"):
        prompt_id, _, idx = h[len("comfy:"):].partition("@")
        if idx.isdigit() and int(idx) < len(BACKENDS):
            backend = BACKENDS[int(idx)]
        else:
            backend = BACKENDS[0] if BACKENDS else ""
        return ("comfy", prompt_id, backend, "")
    raise HTTPException(400, f"bad job handle: {h!r}")


def _or_headers() -> dict[str, str]:
    # LiteLLM passthrough 인증 — OR 키는 LiteLLM 이 주입.
    return {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}


async def _bill_local(client: httpx.AsyncClient, path: str, end_user: str) -> None:
    """로컬 미디어 명목 단가를 LiteLLM passthrough(/localbill/<path>)로 user 귀속 기록.
    cost_per_request 는 litellm-config 에 박혀 있고 여기선 호출만. best-effort —
    실패해도 이미 만든 생성물엔 영향 없음(과금 누락만)."""
    if not end_user:
        return
    try:
        headers = _or_headers()
        headers["x-litellm-end-user-id"] = end_user
        await client.post(f"{LOCALBILL}/{path}", json={}, headers=headers, timeout=10)
    except Exception as e:  # noqa: BLE001 — 과금 실패가 생성을 막지 않도록.
        LOG.warning("local bill failed (%s): %s", path, e)


# OR 모델별 지원셋 — litellm-config 의 (모델×해상도×오디오×길이) 과금 엔드포인트와 일치 필수.
_OR_DURATIONS: dict[str, tuple[int, ...]] = {
    "veo-lite": (4, 6, 8), "veo-fast": (4, 6, 8), "veo": (4, 6, 8),
    "sora-2": (4, 8, 12, 16, 20),
}
_OR_RESOLUTIONS: dict[str, tuple[str, ...]] = {  # path tag (소문자)
    "veo-lite": ("720p", "1080p"), "veo-fast": ("720p", "1080p", "4k"),
    "veo": ("1080p", "4k"), "sora-2": ("720p", "1080p"),
}
_OR_AUDIO_FIXED = {"sora-2"}  # 오디오 항상 포함(토글 없음)


def _snap_dur(alias: str, d: int | None) -> int:
    """요청 길이를 모델 허용값으로 스냅(OR 거부 방지 + 과금 엔드포인트 일치)."""
    opts = _OR_DURATIONS.get(alias, (4, 6, 8))
    return min(opts, key=lambda x: abs(x - (d or 4)))


def _norm_res(alias: str, res: str | None) -> str:
    """해상도를 모델 지원 path tag(720p|1080p|4k)로 정규화. 미지원/미지정 = 1080p(기본)."""
    opts = _OR_RESOLUTIONS.get(alias, ("1080p",))
    r = (res or "1080p").lower()  # "4K"→"4k", "1080P"→"1080p"
    return r if r in opts else ("1080p" if "1080p" in opts else opts[-1])


async def _or_submit(client: httpx.AsyncClient, req: "Txt2VidRequest", alias: str) -> str:
    """OR 비디오 잡 제출(폴링 안 함). 잡 id 반환. submit 경로 = /orvideo/submit/<alias>/<dur>
    로 라우팅 → LiteLLM 이 모델×길이별 cost_per_request 로 과금(x-litellm-end-user-id 귀속)."""
    or_model = OR_VIDEO_ALIASES[alias]
    dur = _snap_dur(alias, req.duration)
    res = _norm_res(alias, req.resolution)
    audio = True if alias in _OR_AUDIO_FIXED else bool(req.audio)
    atag = "a" if audio else "na"
    body: dict[str, Any] = {"model": or_model, "prompt": req.prompt, "duration": dur,
                            "resolution": ("4K" if res == "4k" else res)}
    if alias not in _OR_AUDIO_FIXED:
        body["generate_audio"] = audio  # Sora 는 오디오 항상 포함 → 미전송
    if req.aspect_ratio:
        body["aspect_ratio"] = req.aspect_ratio
    if req.seed >= 0:
        body["seed"] = req.seed
    headers = _or_headers()
    if req.end_user:
        headers["x-litellm-end-user-id"] = req.end_user  # cost_per_request 를 이 user 에 귀속
    try:
        # 과금 경로 = (모델×해상도×오디오×길이) — litellm-config 엔드포인트와 일치.
        r = await client.post(f"{OR_VIDEO_SUBMIT}/{alias}/{res}/{atag}/{dur}", headers=headers, json=body)
    except Exception as e:
        raise HTTPException(502, f"OR video submit exception: {e!r}")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"OR video submit {r.status_code}: {r.text[:400]}")
    job = r.json()
    job_id = job.get("id")
    if not job_id:
        raise HTTPException(502, f"OR video: no job id in response: {str(job)[:300]}")
    LOG.info("OR video job %s (%s) status=%s", job_id, or_model, job.get("status"))
    return job_id


async def _or_check(client: httpx.AsyncClient, job_id: str, alias: str = "") -> dict[str, Any]:
    """OR 잡 상태 1회 확인. completed 면 다운로드까지 → video 포함 반환.
    반환 status ∈ {pending, in_progress, completed, failed}."""
    pr = await client.get(f"{OR_VIDEO_JOB}/{job_id}", headers=_or_headers())
    if pr.status_code >= 400:
        raise HTTPException(pr.status_code, f"OR video poll {pr.status_code}: {pr.text[:300]}")
    job = pr.json()
    status = job.get("status")
    if status == "failed":
        return {"status": "failed", "error": str(job.get("error") or job)[:300]}
    if status != "completed":
        return {"status": status or "pending"}
    dl = await client.get(f"{OR_VIDEO_JOB}/{job_id}/content", params={"index": 0},
                          headers=_or_headers(), timeout=120)
    dl.raise_for_status()
    cost = (job.get("usage") or {}).get("cost")
    return {
        "status": "completed",
        "video": base64.b64encode(dl.content).decode(),
        "filename": f"{alias or 'video'}-{job_id}.mp4",
        "content_type": "video/mp4",
        "info": json.dumps({"model": OR_VIDEO_ALIASES.get(alias, alias), "cost": cost}),
    }


async def _comfy_submit(client: httpx.AsyncClient, backend: str, workflow: dict[str, Any]) -> str:
    """ComfyUI 워크플로 제출(대기 안 함). prompt_id 반환."""
    r = await client.post(f"{backend}/prompt", json={"prompt": workflow}, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = {"raw": r.text}
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()["prompt_id"]


async def _comfy_check_video(client: httpx.AsyncClient, backend: str, prompt_id: str) -> dict[str, Any]:
    """ComfyUI 잡 1회 확인. 완료면 비디오 파일 다운로드해 반환."""
    try:
        h = await client.get(f"{backend}/history/{prompt_id}", timeout=60)
        h.raise_for_status()
    except httpx.HTTPError:
        return {"status": "pending"}
    body = h.json()
    entry = body.get(prompt_id) if isinstance(body, dict) else None
    if not entry:
        return {"status": "pending"}
    status = entry.get("status") or {}
    if status.get("status_str") == "error":
        return {"status": "failed", "error": "ComfyUI execution error"}
    if not status.get("completed"):
        return {"status": "pending"}
    for node_out in entry.get("outputs", {}).values():
        for item in (node_out.get("gifs", []) + node_out.get("videos", [])):
            params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                      "type": item.get("type", "output")}
            v = await client.get(f"{backend}/view", params=params, timeout=120)
            v.raise_for_status()
            fmt = item.get("format", "")
            return {
                "status": "completed",
                "video": base64.b64encode(v.content).decode(),
                "filename": item["filename"],
                "content_type": fmt if "/" in fmt else "video/mp4",
                "info": json.dumps({"backend": backend}),
            }
    return {"status": "failed", "error": "completed but no video output (VHS_VideoCombine 확인)"}


class VideoFetchRequest(BaseModel):
    handle: str


@app.post("/video/submit")
async def video_submit(req: Txt2VidRequest) -> dict[str, Any]:
    """비동기: 잡만 제출하고 즉시 핸들 반환(렌더 대기 안 함). generate_video MCP 가
    호출 → 이후 /video/fetch 로 회수. 정체 잡에 연결 미점유."""
    alias = (req.model or "veo-lite").lower()
    if alias in OR_VIDEO_ALIASES:
        async with httpx.AsyncClient(timeout=60) as c:
            job_id = await _or_submit(c, req, alias)
        return {"handle": _encode_handle("or", job_id, alias=alias), "status": "pending"}

    canonical = alias
    if canonical not in VIDEO_ALIAS_VARIANTS:
        known = sorted(list(OR_VIDEO_ALIASES) + list(VIDEO_ALIAS_VARIANTS))
        raise HTTPException(400, f"unknown video model: {req.model!r}. Known: {known}")
    if not BACKENDS:
        raise HTTPException(503, f"로컬 ComfyUI 백엔드 없음 — 외부 비디오 모델({', '.join(OR_VIDEO_ALIASES)}) 중 하나를 지정하라")
    async with httpx.AsyncClient() as client:
        backend = await _pick_backend(client, canonical=canonical)
        template_name = _NODE_VARIANTS.get(backend, {}).get(canonical)
        if not template_name:
            raise HTTPException(500, f"no variant resolved for {canonical!r} on {backend}")
        template = _load_workflow(template_name)
        workflow = _populate_video_workflow(
            template, prompt=req.prompt, negative=req.negative_prompt,
            seed=req.seed if req.seed >= 0 else None, steps=req.steps, cfg=req.cfg,
            width=req.width, height=req.height, length=req.length, frame_rate=req.frame_rate,
        )
        prompt_id = await _comfy_submit(client, backend, workflow)
        sec = max(1, min(LOCALBILL_VIDEO_MAX_SEC, round(req.length / max(1, req.frame_rate))))
        await _bill_local(client, f"video/{sec}", req.end_user)
    return {"handle": _encode_handle("comfy", prompt_id, backend=backend), "status": "pending"}


@app.post("/video/fetch")
async def video_fetch(req: VideoFetchRequest) -> dict[str, Any]:
    """비동기: 핸들로 잡 상태 1회 확인. completed 면 mp4(base64) 포함 반환."""
    kind, ident, backend, alias = _decode_handle(req.handle)
    if kind == "or":
        async with httpx.AsyncClient(timeout=130) as c:
            return await _or_check(c, ident, alias)
    async with httpx.AsyncClient() as c:
        return await _comfy_check_video(c, backend, ident)
