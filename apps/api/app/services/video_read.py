"""Reading a video's keyframes with the strict-local vision model (`video_jobs`).

Only a model the catalogue lists as strict-local with vision is ever sent a frame — the one
the OCR settings choose (`ocr.config`), checked uncached and per deployment before a job's
first frame. Nothing about a video leaves the deployment whatever the chat model is.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.core import logs
from app.services import models as model_service
from app.services import ocr, settings_store, thinking
from app.services.video_doc import SAME_PICTURE as SAME_HASH
from app.services.video_doc import Shown, dhash
from app.services.video_doc import distance as dhash_distance

log = logging.getLogger(__name__)

_TIMEOUT = 180.0
_MAX_TOKENS = 900
#: characters of on-screen text kept per frame
TEXT_CHARS = 1500
ABOUT_CHARS = 80
KINDS = ("slide", "code", "diagram", "chart", "screen", "document", "person", "other")

PROMPT = (
    "You are reading one frame of a recorded video (a lecture, a talk or a screen "
    "recording) so that questions about it can be answered later. Reply with one JSON "
    "object and nothing else:\n"
    '{"kind": one of "slide", "code", "diagram", "chart", "screen", "document", "person", '
    '"other",\n'
    ' "text": every piece of readable text in the frame, verbatim and in reading order — '
    "titles, bullets, labels, axis names, numbers, code (keep each code line, separated by "
    '" / "); "" when there is none,\n'
    ' "about": one line in Korean (at most 60 characters) saying what the frame shows, e.g. '
    '"막대그래프: 연도별 사용자 수", "VS Code에서 main.py 편집", "발표자 화면"}\n'
    "Rules: copy text exactly as written, in its own language; never guess text you cannot "
    "read (leave it out); do not describe people's looks — say only 발표자 or 사람; "
    "do not add anything that is not visible."
)


class VisionUnavailable(RuntimeError):
    """No strict-local vision model can read frames now."""


@dataclass(slots=True)
class Reading:
    shown: Shown
    input_tokens: int = 0
    output_tokens: int = 0


async def model() -> dict:
    """The catalogue entry of the vision model frames go to; raises `VisionUnavailable`."""
    cfg = await ocr.config(egress=True)
    if not cfg.vision_model:
        raise VisionUnavailable("no strict-local vision model")
    if not await ocr._every_deployment_contained(cfg.vision_model):
        raise VisionUnavailable("vision model has a deployment that is not strict-local")
    catalogue = await model_service.list_models()
    entry = model_service.find(catalogue.get("models") or [], cfg.vision_model)
    return entry or {"id": cfg.vision_model}


def picture_hash(jpeg: bytes) -> int:
    """64-bit difference hash of a frame (near-duplicates are a few bits apart)."""
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        tiny = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        return dhash(list(tiny.tobytes()))


#: the second check compares edge maps: text and slide shapes are crisp edges, a speaker
#: moving mostly soft ones
_EDGE_SIZE = (640, 360)
_EDGE_LEVEL = 80
#: two frames whose difference hashes match are still different pictures when more than this
#: share of the frame's edge pixels moved (an edge in one with none within a pixel in the
#: other). Measured on three lectures (61 slide changes) and a talking head: a new slide —
#: one template, other text — moves ≥ 3.2 %; the same slide 0.07 % (p90 of the template
#: deck); a speaker over six seconds 0.34 % (p90), 0.77 % (second largest of 99).
SAME_PIXELS = 0.015


def small(jpeg: bytes):
    """The frame's edge map (`PIL.Image`, 0/255), for `changed`."""
    from PIL import Image, ImageFilter

    with Image.open(io.BytesIO(jpeg)) as image:
        grey = image.convert("L").resize(_EDGE_SIZE, Image.Resampling.BILINEAR)
    return grey.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > _EDGE_LEVEL else 0)


def changed(a, b) -> float:
    """Share of the frame whose edges moved: edge pixels in either map with no edge within a
    pixel in the other."""
    from PIL import ImageChops, ImageFilter

    near_a, near_b = a.filter(ImageFilter.MaxFilter(3)), b.filter(ImageFilter.MaxFilter(3))
    moved = (ImageChops.subtract(a, near_b).histogram()[255]
             + ImageChops.subtract(b, near_a).histogram()[255])
    return moved / (_EDGE_SIZE[0] * _EDGE_SIZE[1])


def same_picture(hash_a: int, small_a, hash_b: int, small_b) -> bool:
    """The same picture: hashes close (cheap) and, to be sure, few edges moved — so template
    slides with different text stay apart while a speaker's gestures do not count."""
    if dhash_distance(hash_a, hash_b) > SAME_HASH:
        return False
    return changed(small_a, small_b) <= SAME_PIXELS


def thumbnail(jpeg: bytes, width: int = 320) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        image = image.convert("RGB")
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))),
                                 Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=78, optimize=True)
        return out.getvalue()


_OBJECT = re.compile(r"\{.*\}", re.S)


def parse(answer: str, t: float) -> Shown:
    """The model's JSON as `Shown`; a non-JSON answer is kept as the frame's text."""
    raw = (answer or "").strip()
    found = _OBJECT.search(raw)
    data: dict = {}
    if found is not None:
        try:
            loaded = json.loads(found.group(0))
            data = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            data = {}
    if not data:
        return Shown(t=t, kind="other", text=" ".join(raw.split())[:TEXT_CHARS])
    kind = str(data.get("kind") or "other").strip().lower()
    return Shown(
        t=t,
        kind=kind if kind in KINDS else "other",
        text=" ".join(str(data.get("text") or "").split())[:TEXT_CHARS],
        about=" ".join(str(data.get("about") or "").split())[:ABOUT_CHARS],
    )


async def read(entry: dict, picture: bytes, t: float, api_key: str) -> Reading:
    """One frame read by the strict-local vision model."""
    base, master = await settings_store.litellm_config()
    if not base:
        raise VisionUnavailable("model gateway not configured")
    uri = f"data:image/jpeg;base64,{base64.b64encode(picture).decode()}"
    body = {
        "model": entry["id"],
        "temperature": 0,
        "max_tokens": _MAX_TOKENS,
        # strict-local has no outside twin; guard if one appears
        "disable_fallbacks": True,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}],
        **thinking.template_switch(entry["id"]),
    }
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key or master}",
                     # a private video: no LiteLLM log copy
                     "x-litellm-enable-message-redaction": "true"},
            timeout=_TIMEOUT,
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"vision model failed: {logs.safe(exc)}") from exc
    choices = data.get("choices") or []
    answer = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    usage = data.get("usage") or {}
    return Reading(
        shown=parse(answer, t),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
    )


__all__ = ["PROMPT", "Reading", "SAME_PIXELS", "VisionUnavailable", "changed", "model", "parse",
           "picture_hash", "read", "same_picture", "small", "thumbnail"]
