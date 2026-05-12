"""A1111-compatible shim in front of ComfyUI.

LibreChat's built-in stable-diffusion tool speaks the A1111 / AUTOMATIC1111
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

Templates ship with sensible defaults for sdxl / qwen-image / qwen-image-edit
but real-world tuning (sampler choice, scheduler, resolution) almost always
needs adjustment after the first end-to-end run — keep them in version
control and iterate.
"""
from __future__ import annotations

import base64
import copy
import io
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

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://comfyui:8188").rstrip("/")
WORKFLOWS_DIR = Path(__file__).parent / "workflows"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "sdxl")
POLL_INTERVAL_SEC = float(os.getenv("POLL_INTERVAL_SEC", "0.5"))
POLL_TIMEOUT_SEC = float(os.getenv("POLL_TIMEOUT_SEC", "1800"))

# Map model aliases the user sends in the A1111 request to workflow templates.
# Aliases match what manage.sh / docs advertise.
MODEL_ALIASES = {
    "sdxl": "sdxl-txt2img.json",
    "sd_xl_base_1.0": "sdxl-txt2img.json",
    "qwen-image": "qwen-image-txt2img.json",
    "qwen-image-2512": "qwen-image-txt2img.json",
    "qwen-image-edit": "qwen-image-edit.json",
    "qwen-image-edit-2509": "qwen-image-edit.json",
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

app = FastAPI(title="ComfyUI A1111 Shim", version="0.1.0")


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


async def _comfy_upload_image(client: httpx.AsyncClient, b64_image: str) -> str:
    """Push a base64 image into ComfyUI's input folder. Returns the filename
    the workflow's LoadImage node should reference."""
    raw = base64.b64decode(b64_image)
    name = f"shim_input_{int(time.time() * 1000)}.png"
    files = {"image": (name, raw, "image/png"), "type": (None, "input")}
    r = await client.post(f"{COMFYUI_URL}/upload/image", files=files, timeout=30)
    r.raise_for_status()
    return r.json().get("name", name)


async def _comfy_run(client: httpx.AsyncClient, workflow: dict[str, Any]) -> list[str]:
    """Queue a workflow, wait for completion, return the base64 PNGs."""
    r = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}, timeout=30)
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
        LOG.warning("ComfyUI /prompt rejected workflow: %s", detail)
        raise HTTPException(status_code=r.status_code, detail=detail)
    prompt_id = r.json()["prompt_id"]

    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        if time.monotonic() > deadline:
            raise HTTPException(504, f"ComfyUI run timed out after {POLL_TIMEOUT_SEC}s")
        h = await client.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=15)
        h.raise_for_status()
        body = h.json()
        if prompt_id in body and body[prompt_id].get("status", {}).get("completed"):
            run = body[prompt_id]
            break
        time.sleep(POLL_INTERVAL_SEC)

    images_b64: list[str] = []
    for node_out in run.get("outputs", {}).values():
        for img in node_out.get("images", []):
            params = {
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            }
            v = await client.get(f"{COMFYUI_URL}/view", params=params, timeout=30)
            v.raise_for_status()
            images_b64.append(base64.b64encode(v.content).decode())
    return images_b64


def _resolve_model(model: str | None, *, img2img: bool) -> str:
    table = MODEL_ALIASES_IMG2IMG if img2img else MODEL_ALIASES
    key = (model or DEFAULT_MODEL).lower()
    if key not in table:
        raise HTTPException(400, f"unknown model alias: {model!r}. Known: {sorted(table)}")
    return table[key]


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
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sdapi/v1/sd-models")
async def list_models() -> list[dict[str, str]]:
    """LibreChat fetches this to populate the model dropdown."""
    return [
        {"title": alias, "model_name": alias, "filename": filename}
        for alias, filename in MODEL_ALIASES.items()
    ]


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
    template_name = _resolve_model(model, img2img=False)
    LOG.info("txt2img: model=%s template=%s", model or DEFAULT_MODEL, template_name)

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
        images = await _comfy_run(client, workflow)

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
    template_name = _resolve_model(model, img2img=True)
    LOG.info("img2img: model=%s template=%s", model or DEFAULT_MODEL, template_name)

    template = _load_workflow(template_name)

    async with httpx.AsyncClient() as client:
        uploaded = await _comfy_upload_image(client, req.init_images[0])
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
        images = await _comfy_run(client, workflow)

    return {
        "images": images,
        "parameters": req.model_dump(),
        "info": json.dumps({"prompt": req.prompt, "model": model or DEFAULT_MODEL}),
    }
