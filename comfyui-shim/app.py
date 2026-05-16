"""A1111-compatible shim in front of one or more ComfyUI backends.

LibreChat's built-in image-generation tool speaks the A1111 / AUTOMATIC1111
WebUI REST conventions (POST /sdapi/v1/txt2img, /sdapi/v1/img2img, …) and
expects a base64-encoded PNG back. ComfyUI speaks a different protocol: you
POST a workflow graph to /prompt, poll /history/{prompt_id} for status, and
fetch each output image from /view.

This shim translates between the two. For each generation:

  1. Pick a workflow template (workflows/<model>.json) keyed on the model
     name in the A1111 request.
  2. Substitute the prompt / negative / seed / steps / cfg parameters into
     the template's CLIP / KSampler / image-loader nodes.
  3. POST the workflow to ComfyUI's /prompt endpoint.
  4. Poll /history/<prompt_id> until the run finishes.
  5. Fetch each output image via /view, base64 it, return in A1111 shape.

Multi-backend routing
─────────────────────
COMFYUI_URLS may list more than one ComfyUI backend (comma-separated). When
multiple are configured the shim:

  * discovers each backend's loaded checkpoints / unets via /object_info
    (cached, refreshed every MODEL_DISCOVERY_TTL_SEC) and maps them to the
    aliases used in MODEL_ALIASES — heterogeneous GPU clusters can keep
    different model files on different nodes;
  * filters backend candidates by the alias being requested so a node that
    doesn't carry the model is never chosen, then probes /queue on the
    remaining candidates and picks the one with the fewest running+pending
    jobs (least-loaded);
  * remembers the prompt_id → backend mapping so the subsequent /history
    polls and /view fetches land on the same node that ran the workflow —
    ComfyUI's run state is per-node, so naive round-robin would break here.

Templates ship with sensible defaults for qwen-image / qwen-image-edit /
flux-{dev,schnell} but real-world tuning (sampler choice, scheduler, resolution)
almost always needs adjustment after the first end-to-end run — keep them in
version control and iterate.
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
    """COMFYUI_URLS (preferred, comma-separated) → COMFYUI_URL (legacy single)
    → default in-network container."""
    raw = os.getenv("COMFYUI_URLS") or os.getenv("COMFYUI_URL") or "http://comfyui:8188"
    urls = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    if not urls:
        raise RuntimeError("COMFYUI_URLS / COMFYUI_URL is empty")
    return urls


BACKENDS: list[str] = _parse_backends()
WORKFLOWS_DIR = Path(__file__).parent / "workflows"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen-image")
POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "0.5"))
POLL_TIMEOUT_SEC = float(os.getenv("POLL_TIMEOUT_SEC", "1800"))
QUEUE_PROBE_TIMEOUT_SEC = float(os.getenv("QUEUE_PROBE_TIMEOUT_SEC", "2.0"))
MODEL_DISCOVERY_TTL_SEC = float(os.getenv("MODEL_DISCOVERY_TTL_SEC", "300"))
OBJECT_INFO_TIMEOUT_SEC = float(os.getenv("OBJECT_INFO_TIMEOUT_SEC", "10"))

# Route OR image models through LiteLLM (not direct) so spend tracking,
# team budgets, and request logging stay in one place. Empty by default —
# add entries (alias → LiteLLM model_name) for chat models that emit images
# in `message.images[]` when the request includes modalities=["image","text"].
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:8000").rstrip("/")
LITELLM_API_KEY = os.getenv("LITELLM_MASTER_KEY") or os.getenv("OPENROUTER_API_KEY", "")
# alias → LiteLLM model_name. Populated from env: "alias1=model1,alias2=model2".
# Aliases here are what the LLM emits as image-generation tool arg `model=...`.
OR_IMAGE_MODELS: dict[str, str] = {}
for _kv in os.getenv("OR_IMAGE_MODELS", "").split(","):
    if "=" in _kv:
        _a, _m = _kv.split("=", 1)
        _a, _m = _a.strip(), _m.strip()
        if _a and _m:
            OR_IMAGE_MODELS[_a] = _m

LOG.info("ComfyUI backends: %s", ", ".join(BACKENDS))
if OR_IMAGE_MODELS:
    LOG.info("OR image models via %s: %s", LITELLM_URL, ", ".join(OR_IMAGE_MODELS))

# prompt_id → backend URL. ComfyUI keeps run state in-process, so once we
# submit a /prompt to a node every subsequent /history and /view for that
# run MUST hit the same node. Entries are evicted when _comfy_run finishes
# (success, error, or timeout); the size cap below is just a safety net for
# any path that forgets to clean up.
_PROMPT_BACKEND: dict[str, str] = {}
_PROMPT_BACKEND_MAX = 1024


def _remember(prompt_id: str, backend: str) -> None:
    _PROMPT_BACKEND[prompt_id] = backend
    if len(_PROMPT_BACKEND) > _PROMPT_BACKEND_MAX:
        # Drop oldest insertion order — dict preserves it.
        for k in list(_PROMPT_BACKEND.keys())[: len(_PROMPT_BACKEND) - _PROMPT_BACKEND_MAX]:
            _PROMPT_BACKEND.pop(k, None)


def _forget(prompt_id: str) -> None:
    _PROMPT_BACKEND.pop(prompt_id, None)


# Map model aliases the user sends in the A1111 request to workflow templates.
# Aliases match what manage.sh / docs advertise.
MODEL_ALIASES = {
    "qwen-image": "qwen-image-txt2img.json",
    "qwen-image-2512": "qwen-image-txt2img.json",
    "qwen-image-edit": "qwen-image-edit.json",
    "qwen-image-edit-2509": "qwen-image-edit.json",
    "flux-dev": "flux-dev-txt2img.json",
    "flux-schnell": "flux-schnell-txt2img.json",
}

# Same map, but for img2img requests. qwen-image-edit naturally accepts an
# input image and is the only edit path we expose by default.
MODEL_ALIASES_IMG2IMG = {
    "qwen-image-edit": "qwen-image-edit.json",
    "qwen-image-edit-2509": "qwen-image-edit.json",
}

# Canonical alias → (loader-kind, weight filename on disk) used to match
# /object_info contents on each backend. Mirrors __comfyui_node_models in
# scripts/lib.sh — keep both in sync when adding a new image model.
COMFYUI_ALIAS_FILES: dict[str, tuple[str, str]] = {
    "qwen-image":      ("unet", "qwen-image-Q8_0.gguf"),
    "qwen-image-edit": ("unet", "qwen-image-edit-Q8_0.gguf"),
    "flux-schnell":    ("unet", "flux1-schnell.safetensors"),
    "flux-dev":        ("unet", "flux1-dev.safetensors"),
}

# A1111 alias the caller sent → canonical alias used for discovery lookups.
# Lets versioned identifiers (qwen-image-2512, qwen-image-edit-2509) resolve
# to the same on-disk weight as their short alias.
DISCOVERY_ALIAS: dict[str, str] = {
    "qwen-image": "qwen-image",
    "qwen-image-2512": "qwen-image",
    "qwen-image-edit": "qwen-image-edit",
    "qwen-image-edit-2509": "qwen-image-edit",
    "flux-dev": "flux-dev",
    "flux-schnell": "flux-schnell",
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


# VRAM-aware routing: among reachable nodes, prefer the one with the LEAST
# ollama-occupied VRAM (most room for image models). Above this threshold a
# node is considered "loaded" — picked only if every other node is also loaded.
# Default 30 GiB — covers 35B/33B chat models comfortably while pushing 70B/q8_0
# nodes to the back of the queue when an idle alternative exists.
OLLAMA_VRAM_LOADED_THRESHOLD_BYTES = int(
    os.getenv("OLLAMA_VRAM_LOADED_THRESHOLD_BYTES", str(30 * 1024**3))
)


async def _ollama_vram_used(client: httpx.AsyncClient, backend: str) -> int | None:
    """Sum of VRAM occupied by ollama-loaded models on the same host as the
    given ComfyUI backend. None when the ollama probe is unreachable."""
    # Pair ComfyUI 8188 with ollama 11434 on the same host.
    try:
        host = backend.split("://", 1)[1].split(":", 1)[0]
        url = f"http://{host}:11434/api/ps"
        r = await client.get(url, timeout=QUEUE_PROBE_TIMEOUT_SEC)
        r.raise_for_status()
        return sum(m.get("size_vram", 0) for m in r.json().get("models", []) if isinstance(m, dict))
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


# backend URL → set of canonical aliases the node has loaded. Empty when the
# node was unreachable on the last refresh.
_NODE_ALIASES: dict[str, set[str]] = {}
_NODE_ALIASES_AT: float = 0.0
_NODE_ALIASES_LOCK = asyncio.Lock()


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


async def _discover_node_aliases(client: httpx.AsyncClient, backend: str) -> set[str]:
    """Hit /object_info on one backend and return the aliases it can serve.
    Empty set on failure — caller treats that as 'no aliases here right now'
    rather than 'all aliases'."""
    try:
        r = await client.get(f"{backend}/object_info", timeout=OBJECT_INFO_TIMEOUT_SEC)
        r.raise_for_status()
        info = r.json()
    except (httpx.HTTPError, ValueError):
        return set()
    ckpts = _files_from_object_info(info, "CheckpointLoaderSimple", "ckpt_name")
    unets = (_files_from_object_info(info, "UNETLoader", "unet_name")
             | _files_from_object_info(info, "UnetLoaderGGUF", "unet_name"))
    aliases: set[str] = set()
    for alias, (kind, fname) in COMFYUI_ALIAS_FILES.items():
        pool = ckpts if kind == "ckpt" else unets
        if fname in pool:
            aliases.add(alias)
    return aliases


async def _refresh_node_aliases(client: httpx.AsyncClient) -> None:
    """Re-probe every backend's /object_info if the cache is empty or stale.
    Holds an asyncio lock so concurrent requests share a single refresh."""
    global _NODE_ALIASES_AT
    async with _NODE_ALIASES_LOCK:
        fresh = _NODE_ALIASES and (time.monotonic() < _NODE_ALIASES_AT + MODEL_DISCOVERY_TTL_SEC)
        if fresh:
            return
        results = await asyncio.gather(*(_discover_node_aliases(client, b) for b in BACKENDS))
        _NODE_ALIASES.clear()
        for b, aliases in zip(BACKENDS, results):
            _NODE_ALIASES[b] = aliases
        _NODE_ALIASES_AT = time.monotonic()
        for b in BACKENDS:
            LOG.info("ComfyUI %s aliases: %s", b, sorted(_NODE_ALIASES.get(b, set())) or "(none)")


async def _backends_with_alias(client: httpx.AsyncClient, alias: str) -> list[str]:
    """Backends whose last discovery turned up `alias`. Order preserves the
    original BACKENDS order so the fallback in _pick_backend stays deterministic."""
    await _refresh_node_aliases(client)
    return [b for b in BACKENDS if alias in _NODE_ALIASES.get(b, set())]


async def _pick_backend(client: httpx.AsyncClient, *, alias: str | None = None) -> str:
    """Choose a backend: filter by alias availability, then least-loaded.
    Falls back to the first configured backend if every probe failed —
    gives the caller a meaningful error instead of silently dropping the request.

    A 404 surfaces only when `alias` is given but no reachable node has it
    loaded; that's a real configuration miss, not transient load."""
    candidates = BACKENDS
    canonical = DISCOVERY_ALIAS.get(alias) if alias else None
    if canonical and len(BACKENDS) > 1:
        narrowed = await _backends_with_alias(client, canonical)
        if not narrowed:
            raise HTTPException(
                404,
                f"alias {alias!r} not loaded on any reachable ComfyUI node "
                f"(checked: {', '.join(BACKENDS)})",
            )
        candidates = narrowed

    if len(candidates) == 1:
        return candidates[0]
    # Probe VRAM (paired ollama on same host) + queue depth in parallel.
    # Each GB10 node has ollama+ComfyUI sharing unified memory; routing image-gen
    # to a node whose ollama isn't holding a 70B model avoids OOM stalls.
    vrams, depths = await asyncio.gather(
        asyncio.gather(*(_ollama_vram_used(client, b) for b in candidates)),
        asyncio.gather(*(_queue_depth(client, b) for b in candidates)),
    )
    reachable = [
        (d, v, b) for d, v, b in zip(depths, vrams, candidates) if d is not None
    ]
    if not reachable:
        LOG.warning("All candidate ComfyUI backends failed /queue probe; defaulting to %s", candidates[0])
        return candidates[0]
    # Score: (loaded_tier, vram_used, queue_depth). loaded_tier is 1 if VRAM
    # used exceeds threshold (likely no room for ComfyUI), 0 otherwise — pushes
    # loaded nodes to the bottom unless ALL nodes are loaded. Within a tier,
    # prefer least VRAM used, then shortest queue. vram=None (probe failed) is
    # treated as 0 so an unreachable ollama doesn't block image-gen on a
    # healthy ComfyUI.
    def _score(triple):
        d, v, _ = triple
        v_eff = v if v is not None else 0
        loaded = 1 if v_eff > OLLAMA_VRAM_LOADED_THRESHOLD_BYTES else 0
        return (loaded, v_eff, d)
    reachable.sort(key=_score)
    chosen = reachable[0][2]

    def _fmt_vram(v):
        return "?" if v is None else f"{v / 2**30:.1f}GiB"
    LOG.info(
        "Routing to %s (alias=%s) — backends: %s",
        chosen, alias or "any",
        ", ".join(
            f"{b}[vram={_fmt_vram(v)}, q={d}]"
            for d, v, b in zip(depths, vrams, candidates)
        ),
    )
    return chosen


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _load_workflow(filename: str) -> dict[str, Any]:
    path = WORKFLOWS_DIR / filename
    if not path.is_file():
        raise HTTPException(500, f"workflow template missing: {filename}")
    with path.open() as f:
        return json.load(f)


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
    input_image_name: str | None = None,
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
    if input_image_name is not None:
        _set_node_input(wf, "LoadImage", "image", input_image_name)

    # Force SaveImage to run each call.
    unique = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    for node in wf.values():
        if node.get("class_type") == "SaveImage":
            prefix = node.get("inputs", {}).get("filename_prefix", "kloudchat")
            node["inputs"]["filename_prefix"] = f"{prefix}-{unique}"
    return wf


async def _comfy_upload_image(client: httpx.AsyncClient, backend: str, b64_image: str) -> str:
    """Push a base64 image into a specific ComfyUI's input folder. The
    workflow's LoadImage node must reference the returned filename."""
    raw = base64.b64decode(b64_image)
    name = f"shim_input_{int(time.time() * 1000)}.png"
    files = {"image": (name, raw, "image/png"), "type": (None, "input")}
    r = await client.post(f"{backend}/upload/image", files=files, timeout=30)
    r.raise_for_status()
    return r.json().get("name", name)


async def _comfy_run(client: httpx.AsyncClient, backend: str, workflow: dict[str, Any]) -> list[str]:
    """Queue a workflow on `backend`, wait for completion, return base64 PNGs.

    The prompt_id → backend mapping is recorded immediately after the POST
    so that any concurrent inspection (or future progress endpoint) can
    route to the right node. Cleared in finally."""
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
    _remember(prompt_id, backend)

    try:
        deadline = time.monotonic() + POLL_TIMEOUT_SEC
        # ComfyUI 의 web server 는 워크플로 첫 실행 시 모델을 VRAM 로드 하느라
        # 수십 초 응답 못 함 (특히 ollama 큰 모델과 GPU 공유 시). 그 동안의
        # 개별 /history poll 실패는 transient — 외부 deadline 안 넘으면 재시도.
        while True:
            if time.monotonic() > deadline:
                raise HTTPException(504, f"ComfyUI run timed out after {POLL_TIMEOUT_SEC}s on {backend}")
            try:
                h = await client.get(f"{backend}/history/{prompt_id}", timeout=60)
                h.raise_for_status()
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
                # 모델 로드 / 일시적 네트워크 끊김 — 외부 deadline 안 넘으면 계속 폴.
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue
            body = h.json()
            if prompt_id in body and body[prompt_id].get("status", {}).get("completed"):
                run = body[prompt_id]
                break
            await asyncio.sleep(POLL_INTERVAL_SEC)

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
    finally:
        _forget(prompt_id)


def _resolve_model(model: str | None, *, img2img: bool) -> str:
    table = MODEL_ALIASES_IMG2IMG if img2img else MODEL_ALIASES
    key = (model or DEFAULT_MODEL).lower()
    if key not in table:
        known = sorted(set(table) | set(OR_IMAGE_MODELS))
        raise HTTPException(400, f"unknown model alias: {model!r}. Known: {known}")
    return table[key]


async def _openrouter_generate(prompt: str, model_alias: str) -> list[str]:
    """Call an OR image-generating chat model via LiteLLM, return base64 PNGs."""
    if not LITELLM_API_KEY:
        raise HTTPException(500, "LITELLM_MASTER_KEY / OPENROUTER_API_KEY not set in shim env")
    target = OR_IMAGE_MODELS[model_alias]
    payload = {
        "model": target,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": prompt}],
    }
    url = f"{LITELLM_URL}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=POLL_TIMEOUT_SEC) as client:
        r = await client.post(url, json=payload,
                              headers={"Authorization": f"Bearer {LITELLM_API_KEY}"})
    if r.status_code >= 300:
        LOG.warning("OR generate failed: status=%s body=%s", r.status_code, r.text[:500])
        raise HTTPException(502, f"OR backend error: HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    msg = (body.get("choices") or [{}])[0].get("message", {}) or {}
    raw = msg.get("images") or []
    if not raw:
        raise HTTPException(502, f"OR backend returned no images. content={msg.get('content')!r}")
    out: list[str] = []
    for item in raw:
        u = (item.get("image_url") or {}).get("url", "")
        if u.startswith("data:") and "," in u:
            out.append(u.split(",", 1)[1])
        elif u:
            # Fallback: model returned a plain URL — fetch and base64-encode.
            async with httpx.AsyncClient(timeout=60) as fetch_client:
                f = await fetch_client.get(u)
                f.raise_for_status()
                out.append(base64.b64encode(f.content).decode())
    return out


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


class Img2ImgRequest(Txt2ImgRequest):
    init_images: list[str] = Field(default_factory=list)
    denoising_strength: float = 0.75


# ──────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "backends": BACKENDS}


@app.get("/sdapi/v1/sd-models")
async def list_models() -> list[dict[str, str]]:
    """LibreChat fetches this to populate the model dropdown."""
    out = [
        {"title": alias, "model_name": alias, "filename": filename}
        for alias, filename in MODEL_ALIASES.items()
    ]
    for alias, target in OR_IMAGE_MODELS.items():
        out.append({"title": alias, "model_name": alias, "filename": f"openrouter:{target}"})
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


@app.post("/sdapi/v1/txt2img")
async def txt2img(req: Txt2ImgRequest) -> dict[str, Any]:
    model = req.override_settings.get("sd_model_checkpoint") or req.override_settings.get("model")
    key = (model or DEFAULT_MODEL).lower()

    if key in OR_IMAGE_MODELS:
        LOG.info("txt2img: model=%s backend=openrouter(%s)", key, OR_IMAGE_MODELS[key])
        images = await _openrouter_generate(req.prompt, key)
        return {
            "images": images,
            "parameters": req.model_dump(),
            "info": json.dumps({"prompt": req.prompt, "model": key, "backend": "openrouter"}),
        }

    template_name = _resolve_model(model, img2img=False)
    template = _load_workflow(template_name)
    workflow = _populate_workflow(
        template,
        prompt=req.prompt,
        negative=req.negative_prompt,
        seed=req.seed if req.seed >= 0 else None,
        steps=req.steps,
        cfg=req.cfg_scale,
        width=req.width,
        height=req.height,
    )

    async with httpx.AsyncClient() as client:
        backend = await _pick_backend(client, alias=key)
        LOG.info("txt2img: model=%s template=%s backend=%s",
                 model or DEFAULT_MODEL, template_name, backend)
        images = await _comfy_run(client, backend, workflow)

    return {
        "images": images,
        "parameters": req.model_dump(),
        "info": json.dumps({"prompt": req.prompt, "model": model or DEFAULT_MODEL}),
    }


@app.post("/sdapi/v1/img2img")
async def img2img(req: Img2ImgRequest) -> dict[str, Any]:
    if not req.init_images:
        raise HTTPException(400, "img2img requires init_images")

    model = req.override_settings.get("sd_model_checkpoint") or req.override_settings.get("model")
    key = (model or DEFAULT_MODEL).lower()
    if key in OR_IMAGE_MODELS:
        raise HTTPException(400, f"img2img not supported for OR backend model: {key}")
    template_name = _resolve_model(model, img2img=True)

    template = _load_workflow(template_name)

    async with httpx.AsyncClient() as client:
        backend = await _pick_backend(client, alias=key)
        LOG.info("img2img: model=%s template=%s backend=%s",
                 model or DEFAULT_MODEL, template_name, backend)
        # Upload the init image to the same backend that will run the workflow —
        # ComfyUI looks up LoadImage by filename in its local input folder, so
        # mixing nodes here would yield "input image not found".
        uploaded = await _comfy_upload_image(client, backend, req.init_images[0])
        workflow = _populate_workflow(
            template,
            prompt=req.prompt,
            negative=req.negative_prompt,
            seed=req.seed if req.seed >= 0 else None,
            steps=req.steps,
            cfg=req.cfg_scale,
            width=req.width,
            height=req.height,
            input_image_name=uploaded,
        )
        images = await _comfy_run(client, backend, workflow)

    return {
        "images": images,
        "parameters": req.model_dump(),
        "info": json.dumps({"prompt": req.prompt, "model": model or DEFAULT_MODEL}),
    }
