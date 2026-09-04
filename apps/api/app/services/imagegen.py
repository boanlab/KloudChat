"""Image generation via `chat/completions` with `modalities: ["image", "text"]`.

Style is folded into the prompt; aspect is sent as `image_config` (honoured
by Gemini only) and also said in the prompt. The produced size is measured
and kept beside the requested aspect.
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
from app.services import settings_store, thinking

log = logging.getLogger(__name__)

#: Per image; the surface generates them one at a time.
_TIMEOUT = 180.0

#: Style chip → the `Style:` sentence it becomes in the prompt.
_STYLE_PHRASE = {
    "도식": (
        "Style: clean academic vector diagram, white background, rounded boxes with thin "
        "borders, thin labeled connectors, restrained palette of two or three hues "
        "(navy, teal, amber), sans-serif labels, no decoration, no gradients, no icons that "
        "are not part of the system. Suitable for a paper figure or a technical slide"
    ),
    "인포그래픽": (
        "Style: clean educational infographic, white background, three or four flat hues, "
        "simple geometric icons, clear visual hierarchy with one focal element, generous "
        "spacing, sans-serif labels. Suitable for a lecture opening or a review article"
    ),
    "미니멀": (
        "Style: minimal flat illustration, generous whitespace, one or two hues on a plain "
        "ground, no texture, no outlines heavier than a hairline"
    ),
    "사진": (
        "Style: photorealistic photograph, natural light, shallow depth of field, true "
        "colour, no text, no watermark, no illustration"
    ),
    "일러스트": (
        "Style: editorial illustration, confident hand-drawn linework, flat colour fills in "
        "a limited palette, textured paper feel, no photorealism"
    ),
    "3D 렌더": (
        "Style: clean 3D render, soft studio lighting, subtle material detail, neutral "
        "backdrop, no text"
    ),
    "수채화": (
        "Style: watercolour painting, visible paper texture, soft bleeding edges, light "
        "washes, no hard outlines"
    ),
}

#: Chip names on the wire and on screen, in order. 자동 leaves the choice to
#: the planner; 없음 sends no style; 차트 is drawn by `chart_code`.
STYLE_CHOICES = [
    "자동",
    "도식",
    "인포그래픽",
    "차트",
    "사진",
    "일러스트",
    "미니멀",
    "3D 렌더",
    "수채화",
    "없음",
]

#: Label handling by `labels` value; any other value lets the planner decide.
_LABEL_RULE = {
    "ko": "Labels are allowed and must be the exact Korean strings from the request, written "
    'into the prompt in quotes beside each element — e.g. Intake ("취수") — never translated '
    "or paraphrased; no other text",
    "en": "Labels are allowed and must be in English, short, spelled correctly; no other text",
    "none": "No text anywhere: no labels, no captions, no letters, no numbers",
}

#: Rewrites a request into the structured prompt a picture model follows:
#: subject, composition, relations, labels, closing Style line.
_PLANNER_PROMPT = """너는 그림 모델(Gemini Image, GPT Image)에 줄 프롬프트를 쓰는 기획자다.
아래 요청을 그림 모델이 그대로 따를 수 있는 **영어 프롬프트 하나**로 바꿔라.

프롬프트의 형식 — 이 순서로, 문단을 나눠서:
1. 첫 문장: 무엇을 그리는지와 쓰임 (a system diagram of …, for a lecture slide).
2. 구도: 화면 어디에 무엇이 오는지 — top-left, center, right, bottom — 요소마다 이름과
   생김새(rounded rectangle, cylinder, circle, icon of …). 요소는 요청에 있는 것만.
3. 관계: 화살표·연결의 방향과 그 위에 적을 말 (도식·흐름일 때). 화살표마다 이름을 붙여라.
4. 글자: {label_rule}.
5. 마지막 줄 "Style: …" — 팔레트(색 이름 2~4개), 배경, 선 굵기, 글꼴 계열, 무엇에 쓸
   그림인지. {style_rule}

규칙:
- 요청에 없는 사실(숫자, 이름, 단계)을 지어내지 마라. 사용자가 적은 이름·라벨은 그대로 옮긴다.
- 사진·장면·인물이면 구도·조명·렌즈·시간대를 적고, 화살표나 상자를 넣지 마라.
- 슬라이드나 문서 화면 전체를 그리지 마라 — 그림 하나만.
- 120~220 단어. 프롬프트만 출력하고 설명·머리말·따옴표·코드펜스는 붙이지 마라.
{figure_rule}
형식 보기 — **모양만 따르고 낱말은 옮기지 마라.** 아래 보기의 요소·화살표 이름·용도는
보기의 것이고, 네 답의 요소·이름은 전부 요청에서 가져온다:
---
A process diagram of a small-town drinking-water treatment plant, for a middle-school
science handout.

Left to right across the canvas: River Intake ("취수", rounded rectangle with a wave
icon) -> Coagulation Tank ("응집", rounded rectangle) -> Sedimentation Basin ("침전",
wide low rectangle) -> Sand Filter ("여과", rectangle with a dotted fill) ->
Chlorination ("소독", small cylinder) -> Storage Tower ("배수지", tall cylinder, drawn
larger). Below the basin, a small Sludge Out box ("슬러지 배출").

Labeled arrows: "원수" Intake -> Coagulation; "침전물" Basin -> Sludge Out, dashed;
"정수" Filter -> Chlorination -> Storage.

Labels in Korean, exactly the quoted strings, short. No other text.

Style: clean educational infographic, white background, blue / sand / green palette,
simple geometric icons, sans-serif labels. Suitable for a printed handout.
---
A photograph of a university library reading room at golden hour, for a brochure cover.

Wide shot from the entrance, long oak tables receding to tall windows on the far wall,
warm low sun through the glass, a few students reading, no one facing the camera.
35mm lens, eye level, soft shadows.

No text anywhere.

Style: photorealistic, natural light, true colour, calm and quiet mood.
---

요청: {request}"""

_STYLE_RULE_AUTO = (
    "프리셋이 없다. 요청이 무엇인지 보고 골라라 — 구조·흐름·비교면 clean academic vector "
    "diagram, 설명 그림이면 educational infographic, 장면·인물·제품이면 photograph 나 "
    "illustration."
)

_FIGURE_RULE = (
    "- 이 그림은 문서나 슬라이드 **안에** 들어간다. 제목·캡션·범례·페이지 틀 없이 그림 "
    "하나, 한 가지 주제, 작게 봐도 읽히게. 글자는 넣지 마라.\n"
)

#: For a picture inside a slide or section: one figure, no text, no page frame.
_FIGURE_CLAUSE = (
    "a single illustrative figure to sit inside a document, not a page of its "
    "own: no text, no words, no letters, no title, no caption, no labels or "
    "legends, no slide or page frame, no user-interface chrome, one subject on "
    "an uncluttered ground, legible at a small size"
)

_DATA_URL = re.compile(r"^data:(image/[a-z+]+);base64,(.+)$", re.S)


class ImageError(RuntimeError):
    """Generation failed; the message is user-facing."""


@dataclass(slots=True)
class GeneratedImage:
    data: bytes
    mime: str
    input_tokens: int
    output_tokens: int
    #: Measured from the bytes; `0` when unreadable.
    width: int = 0
    height: int = 0

    @property
    def aspect(self) -> str:
        """The ratio produced, as `"16:9"`, or `""` when unmeasured."""
        if not self.width or not self.height:
            return ""
        divisor = gcd(self.width, self.height)
        return f"{self.width // divisor}:{self.height // divisor}"


def _measure(data: bytes) -> tuple[int, int]:
    """`(width, height)`, or `(0, 0)` for bytes Pillow cannot open. Never raises."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception as exc:  # noqa: BLE001 — any decode failure means unknown
        log.info("could not measure generated image: %s", exc)
        return 0, 0


def honours_aspect(model_id: str) -> bool:
    """Whether the model honours `image_config.aspect_ratio`. Gemini does; the OpenAI image models
    return a square.
    """
    return "gemini" in model_id.lower() or model_id.lower().startswith("google/")


#: Ratios the composer offers; 16:9 is the default.
OFFERED_ASPECTS: tuple[str, ...] = ("16:9", "9:16", "4:3", "1:1")


def aspects_for(model_id: str) -> list[str]:
    """Ratios the composer offers for this model; published with the model catalogue."""
    return list(OFFERED_ASPECTS) if honours_aspect(model_id) else ["1:1"]


def compose_prompt(
    prompt: str,
    *,
    aspect: str,
    style: str,
    template: str = "",
    design: str = "",
    figure: bool = False,
    square_only: bool = False,
) -> str:
    """The prompt as sent: request, figure clause, style, template, design system, aspect note.

    Later phrases win where two disagree. The stored prompt stays what the
    person typed.
    """
    parts = [prompt.strip().rstrip(".").strip()]
    if figure:
        parts.append(_FIGURE_CLAUSE)
    phrase = _STYLE_PHRASE.get(style)
    # A planned prompt already ends on its own Style line.
    if phrase and "\nStyle:" not in prompt:
        parts.append(phrase)
    if template.strip():
        parts.append(template.strip())
    if design.strip():
        parts.append(design.strip())
    if square_only:
        # Said so, the model composes for the square instead of clipping a wide picture.
        parts.append(
            "The canvas is square: compose everything to fit fully inside it with a clear "
            "margin on all sides; nothing may touch or cross the edge"
        )
    elif aspect and aspect != "1:1":
        parts.append(_shape_note(aspect))
    return ". ".join(p for p in parts if p)


async def plan(
    request: str,
    *,
    style: str,
    labels: str,
    figure: bool,
    model: str,
    api_key: str,
) -> tuple[str, dict[str, int]]:
    """`(prompt, usage)`: the request rewritten by a language model into a structured picture
    prompt.

    Falls back to the request itself when the planner fails.
    """
    label_rule = _LABEL_RULE.get(labels) or (
        "Decide from the request: a diagram or infographic carries short labels in the "
        "language the request is written in — the request's own words, quoted beside each "
        'element, e.g. Intake ("취수"), never translated; a photograph, scene or object '
        "carries no text"
    )
    style_rule = (
        f"프리셋이 있다: 이 줄을 그대로 살려서 마지막 줄로 써라 — {_STYLE_PHRASE[style]}"
        if style in _STYLE_PHRASE
        else _STYLE_RULE_AUTO
    )
    prompt = _PLANNER_PROMPT.format(
        label_rule=label_rule,
        style_rule=style_rule,
        figure_rule=_FIGURE_RULE if figure else "",
        request=request.strip()[:2000],
    )
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(90.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 700,
                    "temperature": 0.4,
                    "reasoning": thinking.NO_REASONING,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("image prompt planner failed, sending the request as typed: %s", exc)
        return request.strip(), {"inputTokens": 0, "outputTokens": 0}
    raw = payload.get("usage") or {}
    text = str((payload.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    text = _unfenced(text)
    if len(text) < 40 or "Style:" not in text:
        log.info("image prompt planner answered oddly; sending the request as typed")
        return request.strip(), {
            "inputTokens": int(raw.get("prompt_tokens") or 0),
            "outputTokens": int(raw.get("completion_tokens") or 0),
        }
    return text, {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _unfenced(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
    return text.strip().strip('"').strip()


def _shape_note(aspect: str) -> str:
    """The aspect as ratio plus orientation word; models honour the word more reliably than the
    ratio.
    """
    try:
        wide, tall = (float(part) for part in aspect.split(":", 1))
    except ValueError:
        return f"aspect ratio {aspect}"
    if wide > tall:
        return f"aspect ratio {aspect}, landscape orientation, wider than it is tall"
    if tall > wide:
        return f"aspect ratio {aspect}, portrait orientation, taller than it is wide"
    return f"aspect ratio {aspect}, square"


def _extract(message: dict[str, Any]) -> tuple[bytes, str]:
    images = message.get("images") or []
    if not images:
        # Prose instead of an image: a refusal.
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


#: Ratios `image_config.aspect_ratio` accepts; other models ignore the field.
_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9"}


async def generate(
    *, base_url: str, api_key: str, model: str, prompt: str, aspect: str = ""
) -> GeneratedImage:
    """One picture, or `ImageError`. `aspect` is sent as `image_config` in addition to the prompt.
    """
    payload: dict = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": prompt}],
    }
    if aspect in _RATIOS:
        payload["image_config"] = {"aspect_ratio": aspect}
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
        # `image_tokens` is already included in `completion_tokens`.
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
