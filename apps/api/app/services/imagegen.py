"""Image generation, through the same proxy as everything else.

OpenRouter serves picture models on `chat/completions` with
`modalities: ["image", "text"]`, so this is an ordinary completion whose answer
is a PNG — same client, billed from reported usage.

**Aspect ratio and style have no parameters.** Both are folded into the prompt
in words and honoured approximately — so what came back is measured rather than
assumed, and the requested aspect is kept beside it. A 16:9 label over a square
picture is the kind of small lie that makes a person stop trusting the panel.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import re
import uuid
from dataclasses import dataclass
from math import gcd
from typing import Any

import httpx
from PIL import Image

from app.services import files as file_service

log = logging.getLogger(__name__)

#: Per image; the surface generates them one at a time.
_TIMEOUT = 180.0

_STYLE_PHRASE = {
    "미니멀": "clean minimal flat illustration, generous whitespace",
    "사진": "photorealistic, natural lighting, shallow depth of field",
    "일러스트": "hand-drawn illustration, expressive linework",
    "3D 렌더": "3D render, soft studio lighting, subtle material detail",
    "수채화": "watercolour painting, visible paper texture, soft edges",
}

_DATA_URL = re.compile(r"^data:(image/[a-z+]+);base64,(.+)$", re.S)


class ImageError(RuntimeError):
    """Generation failed. The message is written for the person who asked."""


@dataclass(slots=True)
class GeneratedImage:
    data: bytes
    mime: str
    input_tokens: int
    output_tokens: int
    #: Measured off the bytes, `0` when they could not be read.
    width: int = 0
    height: int = 0

    @property
    def aspect(self) -> str:
        """The ratio actually produced, as `"16:9"`, or `""` when unmeasured."""
        if not self.width or not self.height:
            return ""
        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"


def _measure(data: bytes) -> tuple[int, int]:
    """`(width, height)`, or `(0, 0)` for bytes Pillow cannot open.

    Never raises: a picture that arrived is worth keeping even if it cannot be
    measured, so this degrades to "unknown" rather than failing the generation.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception as exc:  # noqa: BLE001 — any decode failure means unknown
        log.info("could not measure generated image: %s", exc)
        return 0, 0


def compose_prompt(prompt: str, *, aspect: str, style: str, design: str = "") -> str:
    """The request as the model will read it.

    Separate from the caller so the stored prompt stays what the person typed.

    `design` is the project's design system as a phrase — see
    `services.design.image_clause`. It follows the style chip rather than
    leading it: the chip is this picture's instruction and the design system is
    the standing one, and the later phrase is the one a picture model tends to
    honour when the two disagree.
    """
    parts = [prompt.strip()]
    phrase = _STYLE_PHRASE.get(style)
    if phrase:
        parts.append(phrase)
    if design.strip():
        parts.append(design.strip())
    if aspect and aspect != "1:1":
        parts.append(f"aspect ratio {aspect}")
    return ". ".join(p for p in parts if p)


def _extract(message: dict[str, Any]) -> tuple[bytes, str]:
    images = message.get("images") or []
    if not images:
        # Prose instead of an image: a refusal, or a prompt read as a question.
        text = (message.get("content") or "").strip()
        raise ImageError(text[:200] or "이미지를 만들지 못했습니다.")

    url = (images[0].get("image_url") or {}).get("url") or ""
    match = _DATA_URL.match(url)
    if not match:
        raise ImageError("이미지 응답 형식을 해석하지 못했습니다.")
    try:
        return base64.b64decode(match.group(2)), match.group(1)
    except (binascii.Error, ValueError) as exc:
        raise ImageError("이미지 데이터가 손상되었습니다.") from exc


async def generate(
    *, base_url: str, api_key: str, model: str, prompt: str
) -> GeneratedImage:
    """One picture, or `ImageError`."""
    payload = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        log.warning("image request failed: %s", exc)
        raise ImageError("이미지 생성 서버에 연결하지 못했습니다.") from exc

    if response.status_code >= 400:
        detail = response.text[:200]
        log.warning("image generation %s: %s", response.status_code, detail)
        raise ImageError("이미지를 만들지 못했습니다. 잠시 후 다시 시도하세요.")

    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise ImageError("이미지를 만들지 못했습니다.")
    data, mime = _extract(choices[0].get("message") or {})
    usage = body.get("usage") or {}
    width, height = _measure(data)
    return GeneratedImage(
        data=data,
        mime=mime,
        width=width,
        height=height,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        # Billed as completion tokens. `image_tokens` is the same number broken
        # out, so counting both doubles the charge.
        output_tokens=int(usage.get("completion_tokens") or 0),
    )


def store(user_id: str, image: GeneratedImage) -> tuple[str, str]:
    """Writes the blob and returns `(file_id, storage_key)`."""
    file_id = uuid.uuid4().hex
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
        image.mime, "png"
    )
    key = file_service.write_blob(user_id, file_id, f"image.{extension}", image.data)
    return file_id, key


__all__ = ["GeneratedImage", "ImageError", "compose_prompt", "generate", "store"]

