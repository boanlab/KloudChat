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
from app.services import settings_store, thinking

log = logging.getLogger(__name__)

#: Per image; the surface generates them one at a time.
_TIMEOUT = 180.0

#: The style chips, and the whole sentence each one becomes.
#:
#: They were five art-style words — "clean minimal flat illustration" for
#: 미니멀, and so on — appended to whatever was asked for. That made a
#: 시스템 구조도 a flat illustration of one, and a 시장 사진 a flat
#: illustration of one: the chip said what a picture *looks like* and never
#: what kind of picture it is. These say both, the way a good figure prompt
#: ends — palette, ground, line, type, and what it is for. 자동 leaves the
#: choice to the planner, which reads it off the request.
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

#: What the chips are called on the wire and on screen, in order. 자동 and
#: 없음 are not phrases: the first hands the choice to the planner, the second
#: withholds it.
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

#: How the words in a picture are handled. `auto` lets the planner decide from
#: the request and the kind of picture; a photograph gets none, a diagram gets
#: labels in the request's language.
LABEL_CHOICES = ("auto", "ko", "en", "none")

_LABEL_RULE = {
    "ko": "Labels are allowed and must be the exact Korean strings from the request, written "
    'into the prompt in quotes beside each element — e.g. Intake ("취수") — never translated '
    "or paraphrased; no other text",
    "en": "Labels are allowed and must be in English, short, spelled correctly; no other text",
    "none": "No text anywhere: no labels, no captions, no letters, no numbers",
}

#: What the planner is taught by. Written in the shape a picture model follows
#: best — one line of subject, the composition placed on the canvas, every
#: element named, relations spelled out, the words settled, and a closing
#: Style line — rather than a heap of adjectives.
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

#: What a picture bound for a slide or a document section has to be told.
#:
#: Asked only for "a picture for 시장 전망", an image model draws the whole
#: slide — a title across the top, a chart, three labelled cards down the side.
#: Dropped into a slide that already has a title and bullets, that is a slide
#: inside a slide, and the first one somebody made looked exactly like that.
#:
#: "No text" is the load-bearing half. Whatever an image model writes it writes
#: badly, and in Korean it writes glyphs that are not words — so a picture with
#: words in it is a picture that has to be thrown away, and the words are the
#: document's job anyway.
_FIGURE_CLAUSE = (
    "a single illustrative figure to sit inside a document, not a page of its "
    "own: no text, no words, no letters, no title, no caption, no labels or "
    "legends, no slide or page frame, no user-interface chrome, one subject on "
    "an uncluttered ground, legible at a small size"
)

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


def honours_aspect(model_id: str) -> bool:
    """Whether the model takes the ratio as a parameter and keeps it.

    Gemini's image models do. The OpenAI ones, reached through chat
    completions, return a square whatever is asked — and told "16:9, wider
    than tall" they compose a wide picture and clip it into the square, which
    is worse than a square picture: the edges of the diagram are gone.
    """
    return "gemini" in model_id.lower() or model_id.lower().startswith("google/")


#: The ratios the composer offers, widest first, 16:9 being the default.
OFFERED_ASPECTS: tuple[str, ...] = ("16:9", "9:16", "4:3", "1:1")


def aspects_for(model_id: str) -> list[str]:
    """The ratios a picture from this model can actually have.

    Published with the model so the composer offers only these — a 16:9 chip
    beside a model that returns squares is a promise the picture then breaks,
    and no sentence of small print under it reads as well as the chip simply
    not being there. Measured against OpenRouter: the OpenAI image models
    return 1024² whatever `image_config` says, directly or through LiteLLM.
    """
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
    """The request as the model will read it.

    Separate from the caller so the stored prompt stays what the person typed.

    Ordered from the particular to the standing: what the person asked for,
    then the style chip they picked for this picture, then the shape the
    design template gives it, then the project's design system, and last the
    aspect note, which is mechanical. Where two disagree the later phrase is
    the one a picture model tends to honour, and the project's look should
    outlast one picture's chip.
    """
    # A planned prompt ends on a full stop, and the joiner below adds one.
    parts = [prompt.strip().rstrip(".").strip()]
    # Directly after what the person asked for, and before the style chip: it
    # says what kind of thing to make, and the chip only says what it looks
    # like. A chip that disagrees is still allowed to — "사진" of one subject is
    # a fine figure — but it cannot turn the figure back into a page.
    if figure:
        parts.append(_FIGURE_CLAUSE)
    phrase = _STYLE_PHRASE.get(style)
    # A planned prompt already ends on its own Style line; a second one
    # would argue with it.
    if phrase and "\nStyle:" not in prompt:
        parts.append(phrase)
    if template.strip():
        parts.append(template.strip())
    if design.strip():
        parts.append(design.strip())
    if square_only:
        # The canvas will be square whatever is asked. Said so, the model
        # composes for it instead of drawing a wide picture and losing its
        # edges to the frame.
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
    """The request, rewritten as the prompt a picture model follows.

    「스타일 쪽이 오히려 생성되는 이미지를 망친다」 — a chip's five words could
    not tell the model what to draw, only what to smear over it. This asks a
    language model to say where everything goes, what each thing is called,
    what the arrows mean and what the words are, then to close on the style
    line. The person's sentence stays the stored prompt; this is what is sent.

    Returns `(prompt, usage)`. Falls back to the request itself when the
    planner fails, so a picture is still made.
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
    """The shape, said as an orientation and not only as a ratio.

    `aspect ratio 4:3` on its own is a line picture models drop often enough
    that this product stores what came back — `actualAspect` — beside what was
    asked for. Dropped, a report figure comes back taller than it is wide and
    eats a whole page; a slide figure comes back square and leaves the layout
    with a hole beside it. Both surfaces ask for a landscape shape (4:3 and
    16:9) and neither could insist on it.

    Orientation is the half a model does honour, because it is a word rather
    than an arithmetic constraint. Said both ways, the ratio has something to
    fall back on.
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


#: The ratios the Gemini image models take as a parameter. Others ignore the
#: field, which is why it is always sent: for them the orientation note in the
#: prompt is still the only lever, and it costs nothing to say both.
_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9"}


async def generate(
    *, base_url: str, api_key: str, model: str, prompt: str, aspect: str = ""
) -> GeneratedImage:
    """One picture, or `ImageError`.

    `aspect` is sent as a parameter as well as said in the prompt. A ratio in
    the prompt is a line picture models drop often enough that this product
    stores what came back beside what was asked for; Gemini honours the
    parameter every time, and the others ignore it.
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
