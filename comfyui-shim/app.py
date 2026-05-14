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

  * probes each backend's /queue at request time and picks the one with the
    fewest running+pending jobs (least-loaded), excluding unreachable nodes;
  * remembers the prompt_id → backend mapping so the subsequent /history
    polls and /view fetches land on the same node that ran the workflow —
    ComfyUI's run state is per-node, so naive round-robin would break here.

Templates ship with sensible defaults for sdxl / qwen-image / qwen-image-edit
but real-world tuning (sampler choice, scheduler, resolution) almost always
needs adjustment after the first end-to-end run — keep them in version
control and iterate.
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

# Route OR image models through LiteLLM (not direct) so spend tracking,
# team budgets, and request logging stay in one place. Empty by default —
# add entries (alias → LiteLLM model_name) for chat models that emit images
# in `message.images[]` when the request includes modalities=["image","text"].
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:8000").rstrip("/")
LITELLM_API_KEY = os.getenv("LITELLM_MASTER_KEY") or os.getenv("OPENROUTER_API_KEY", "")
OR_IMAGE_MODELS: dict[str, str] = {}

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
    "sdxl": "sdxl-txt2img.json",
    "sd_xl_base_1.0": "sdxl-txt2img.json",
    "qwen-image": "qwen-image-txt2img.json",
    "qwen-image-2512": "qwen-image-txt2img.json",
    "qwen-image-edit": "qwen-image-edit.json",
    "qwen-image-edit-2509": "qwen-image-edit.json",
    "flux-dev": "flux-dev-txt2img.json",
    "flux-schnell": "flux-schnell-txt2img.json",
}

# Same map, but for img2img requests. SDXL has its own img2img workflow;
# qwen-image-edit naturally accepts an input image and is the only edit path
# we expose by default.
MODEL_ALIASES_IMG2IMG = {
    "sdxl": "sdxl-img2img.json",
    "sd_xl_base_1.0": "sdxl-img2img.json",
    "qwen-image-edit": "qwen-image-edit.json",
    "qwen-image-edit-2509": "qwen-image-edit.json",
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


async def _pick_backend(client: httpx.AsyncClient) -> str:
    """Least-loaded reachable backend. Falls back to the first configured one
    if every probe failed — gives the caller a meaningful error instead of
    silently dropping the request."""
    if len(BACKENDS) == 1:
        return BACKENDS[0]
    depths = await asyncio.gather(*(_queue_depth(client, b) for b in BACKENDS))
    reachable = [(d, b) for d, b in zip(depths, BACKENDS) if d is not None]
    if not reachable:
        LOG.warning("All ComfyUI backends failed /queue probe; defaulting to %s", BACKENDS[0])
        return BACKENDS[0]
    reachable.sort(key=lambda pair: pair[0])
    chosen = reachable[0][1]
    LOG.info("Routing to %s (depths: %s)",
             chosen,
             ", ".join(f"{b}={d}" for d, b in zip(depths, BACKENDS)))
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
        while True:
            if time.monotonic() > deadline:
                raise HTTPException(504, f"ComfyUI run timed out after {POLL_TIMEOUT_SEC}s on {backend}")
            h = await client.get(f"{backend}/history/{prompt_id}", timeout=15)
            h.raise_for_status()
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
        backend = await _pick_backend(client)
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
        backend = await _pick_backend(client)
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
