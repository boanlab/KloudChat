"""Text from scanned pages: an OCR engine first, a vision model for the pages it is unsure of.

Two readers, both inside the deployment:

- the engine (ocr-shim behind `/tools/ocr`, CPU): every line with a score and a box;
- a vision model through LiteLLM, only a `strict-local` one that declares vision: the
  engine hands it the page picture. A page never goes to any other model.

`mode` decides which are used (`both` by default). With both, the engine reads every
page; a page it scored low, or one with a ruled table, is read again by the vision model,
whose text is kept (it keeps word spacing and writes tables as tables). When the two
readings differ in words or in any number, the page is marked for review rather than
trusting either. Which pages need reading at all is decided here too: a PDF page with its
own text layer is not read again.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import io
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core import logs
from app.core.config import settings as env_settings
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import settings_store, thinking, tools_gateway

log = logging.getLogger(__name__)

MODES = ("both", "engine", "vision")
DEFAULT_MAX_PAGES = 300
#: The most an administrator may allow; the engine refuses past 2000 on its own.
MAX_PAGES_CEILING = 2000

#: A page whose own text layer has fewer visible characters than this is a scan.
TEXT_LAYER_MIN_CHARS = 20
#: Engine score under which the vision model reads the page again (both readers on).
VISION_BELOW = 0.90
#: Engine score under which an engine-only page is marked for review.
REVIEW_BELOW = 0.80
#: Agreement (0..1, spacing and table marks ignored) under which two readings disagree.
AGREE_AT_LEAST = 0.85
#: A picture is a document photo, not a photo with a stray sign, from this much text.
PHOTO_TEXT_MIN_CHARS = 20

#: Pictures the readers accept, by recorded MIME type.
IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp", "image/gif"}
)
#: Largest picture or document sent to a reader.
MAX_BYTES = 60 * 1024 * 1024

_ENGINE_TIMEOUT = httpx.Timeout(150.0, connect=8.0)
_VISION_TIMEOUT = 240.0
_VISION_MAX_TOKENS = 4096

UNREADABLE_MARK = "[판독 불가]"
NO_TEXT_MARK = "[글자 없음]"

VISION_PROMPT = (
    "You transcribe one scanned document page into plain text.\n"
    "Rules:\n"
    "- Output only the text printed on the page, in reading order. A two-column page: the "
    "left column first, then the right.\n"
    "- Keep every character, number, unit, symbol and spacing exactly as printed. Do not "
    "translate, correct, summarise, explain or add anything.\n"
    "- Keep line breaks between headings, paragraphs and list items.\n"
    "- Write a table as a Markdown table: a header row, a separator row, then one row per "
    "table row.\n"
    f"- Where part of the page cannot be read, write {UNREADABLE_MARK} in its place.\n"
    f"- If the page has no text at all, output only {NO_TEXT_MARK}.\n"
    "예: 「제12조(휴학) ① 휴학 기간은 통산 4년을 초과할 수 없다.」 → 그대로 옮겨 적는다."
)


class OcrError(RuntimeError):
    """A reader failed on a page; the job retries the file later."""


@dataclass(frozen=True)
class Config:
    """What is in force: `mode` is narrowed to the readers actually available."""

    enabled: bool
    mode: str | None
    engine_url: str
    vision_model: str
    max_pages: int


def vision_choices(catalogue: dict[str, Any]) -> list[dict[str, Any]]:
    """Models a page may be shown to: strict-local and declaring vision, nothing else."""
    return [
        model
        for model in catalogue.get("models") or []
        if model.get("strictLocal")
        and model.get("supportsVision")
        and "chat" in (model.get("kinds") or [])
    ]


def _int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


async def config(*, egress: bool = False) -> Config:
    """The settings in force. `egress` reads the model catalogue uncached, for the moment a
    page is about to be shown to a model; otherwise a cached read is enough."""
    values = await settings_store.all_values()
    enabled = (values.get(settings_store.OCR_ENABLED) or "1").strip() != "0"
    wanted = (values.get(settings_store.OCR_MODE) or "both").strip()
    if wanted not in MODES:
        wanted = "both"
    engine_url = (await settings_store.tools_config()).ocr
    catalogue = (
        await model_service.list_models_for_egress()
        if egress
        else await model_service.list_models()
    )
    choices = [model["id"] for model in vision_choices(catalogue)]
    chosen = (values.get(settings_store.OCR_VISION_MODEL) or "").strip()
    preferred = env_settings.ocr_vision_model
    fallback = preferred if preferred in choices else (choices[0] if choices else "")
    # A chosen model that stopped qualifying is not swapped for another: nothing is sent.
    vision = chosen if chosen in choices else ("" if chosen else fallback)
    has_engine, has_vision = bool(engine_url), bool(vision)
    mode: str | None
    if wanted == "both":
        mode = (
            "both"
            if has_engine and has_vision
            else "engine"
            if has_engine
            else ("vision" if has_vision else None)
        )
    elif wanted == "engine":
        mode = "engine" if has_engine else None
    else:
        # Without the engine a PDF page cannot be turned into a picture; pictures still can.
        mode = "vision" if has_vision else None
    max_pages = max(
        1,
        min(
            MAX_PAGES_CEILING,
            _int(values.get(settings_store.OCR_MAX_PAGES) or "", DEFAULT_MAX_PAGES),
        ),
    )
    return Config(
        enabled=enabled,
        mode=mode if enabled else None,
        engine_url=engine_url,
        vision_model=vision,
        max_pages=max_pages,
    )


# ── which pages need reading ───────────────────────────────────────────


@dataclass(frozen=True)
class Plan:
    """`pages` (1-based) to read; `layer` holds the text-layer pages kept as they are."""

    kind: str  # "pdf" | "image"
    pages: tuple[int, ...]
    total: int
    layer: dict[int, str] = field(default_factory=dict)
    #: Pages past the administrator's limit, left unread.
    skipped: int = 0


def _visible(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def pdf_layer(data: bytes) -> list[str]:
    """Each page's own text layer ("" for a scan). Raises on a PDF that cannot be opened."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > MAX_PAGES_CEILING:
        # More pages than any reader takes; not planned at all.
        raise ValueError("too many pages")
    pages: list[str] = []
    for page in reader.pages:
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception:  # noqa: BLE001 — one bad page must not lose the rest
            pages.append("")
    return pages


#: Pixels of one picture a reader or model is handed; more is a decompression bomb.
MAX_PIXELS = 40_000_000


def _frames(data: bytes, cap: int) -> int:
    """Frames in a picture (a TIFF may hold many), counted by seeking and never past `cap`;
    0 when it cannot be opened or its first frame claims more than MAX_PIXELS. Nothing is
    decoded: Pillow reads sizes from the header."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
            if width * height > MAX_PIXELS:
                return 0
            frames = 0
            while frames < cap:
                try:
                    picture.seek(frames)
                except EOFError:
                    break
                frames += 1
            return frames
    except Exception:  # noqa: BLE001 — Pillow raises many types for bad input
        return 0


def plan(name: str, mime: str, data: bytes, max_pages: int) -> Plan | None:
    """The pages of this upload that need reading, or None when none do. CPU-bound."""
    if not data or len(data) > MAX_BYTES:
        return None
    mime = (mime or "").lower()
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        try:
            layer = pdf_layer(data)
        except Exception as exc:  # noqa: BLE001 — pypdf raises a dozen different types
            log.info("pdf not planned for ocr: %s", exc)
            return None
        scanned = [i + 1 for i, text in enumerate(layer) if _visible(text) < TEXT_LAYER_MIN_CHARS]
        if not scanned:
            return None
        kept = tuple(scanned[:max_pages])
        return Plan(
            kind="pdf",
            pages=kept,
            total=len(layer),
            layer={i + 1: text for i, text in enumerate(layer) if (i + 1) not in scanned},
            skipped=len(scanned) - len(kept),
        )
    if mime in IMAGE_TYPES:
        # One frame past the limit is counted, so a longer file shows it was cut short.
        frames = _frames(data, max_pages + 1)
        if frames <= 0:
            return None
        kept = tuple(range(1, min(frames, max_pages) + 1))
        return Plan(kind="image", pages=kept, total=frames, skipped=frames - len(kept))
    return None


# ── reading one page ───────────────────────────────────────────────────


@dataclass
class PageRead:
    text: str
    source: str
    confidence: float | None = None
    needs_review: bool = False
    has_table: bool = False
    layout: dict[str, Any] | None = None
    #: The vision model read this page (one ledger unit each).
    vision_used: bool = False


async def engine_read(cfg: Config, data: bytes, page: int, *, want_image: bool) -> dict[str, Any]:
    """The engine's reading of one page (and the page picture when asked)."""
    url = cfg.engine_url.rstrip("/") + "/ocr"
    try:
        async with tools_gateway.client(timeout=_ENGINE_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"page": page, "image": 1 if want_image else 0},
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
    except httpx.HTTPError as exc:
        raise OcrError(f"engine unreachable: {logs.safe(exc)}") from exc
    if response.status_code in (413, 415, 422):
        # The document itself is the problem; retrying will not help.
        raise OcrRefused(_engine_reason(response))
    if response.status_code >= 400:
        raise OcrError(f"engine answered {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise OcrError("engine answered something other than JSON") from exc


async def render(cfg: Config, data: bytes, page: int) -> bytes:
    """The page as a JPEG, through the engine's renderer."""
    url = cfg.engine_url.rstrip("/") + "/render"
    try:
        async with tools_gateway.client(timeout=_ENGINE_TIMEOUT) as client:
            response = await client.post(
                url,
                params={"page": page},
                content=data,
                headers={"Content-Type": "application/octet-stream"},
            )
    except httpx.HTTPError as exc:
        raise OcrError(f"renderer unreachable: {logs.safe(exc)}") from exc
    if response.status_code in (413, 415, 422):
        raise OcrRefused(_engine_reason(response))
    if response.status_code >= 400:
        raise OcrError(f"renderer answered {response.status_code}")
    return response.content


class OcrRefused(OcrError):
    """The reader refused the document itself (too large, not a page); no retry."""


_REASONS = {
    "too_large": "파일이 너무 커서 글자를 읽지 못했습니다.",
    "too_many_pixels": "그림이 너무 커서 글자를 읽지 못했습니다.",
    "too_many_pages": "쪽 수가 너무 많아 글자를 읽지 못했습니다.",
    "unreadable_pdf": "손상된 PDF라 글자를 읽지 못했습니다.",
    "unreadable_image": "그림을 열 수 없어 글자를 읽지 못했습니다.",
    "unsupported": "글자를 읽을 수 없는 형식입니다.",
}


def _engine_reason(response: httpx.Response) -> str:
    try:
        detail = str(response.json().get("detail") or "")
    except ValueError:
        detail = ""
    return _REASONS.get(detail, "글자를 읽지 못했습니다.")


async def vision_read(cfg: Config, picture: bytes, mime: str, api_key: str) -> str:
    """The vision model's transcription of one page picture.

    The model is checked against an uncached catalogue right before the call: strict-local
    and declaring vision, or the page is not sent at all."""
    current = await config(egress=True)
    if not current.vision_model or current.vision_model != cfg.vision_model:
        raise OcrError("vision model not available as strict-local")
    if not await _every_deployment_contained(cfg.vision_model):
        raise OcrError("vision model has a deployment that is not strict-local")
    base, master = await settings_store.litellm_config()
    if not base:
        raise OcrError("model gateway not configured")
    uri = f"data:{mime};base64,{base64.b64encode(picture).decode()}"
    body = {
        "model": cfg.vision_model,
        "temperature": 0,
        "max_tokens": _VISION_MAX_TOKENS,
        # A strict-local route has no outside twin; this keeps it that way if one appears.
        "disable_fallbacks": True,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": uri}},
                ],
            }
        ],
        **thinking.template_switch(cfg.vision_model),
    }
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key or master}",
                # The page is someone's document; LiteLLM keeps no copy of it in its logs.
                "x-litellm-enable-message-redaction": "true",
            },
            timeout=_VISION_TIMEOUT,
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OcrError(f"vision model failed: {logs.safe(exc)}") from exc
    choices = data.get("choices") or []
    if not choices:
        raise OcrError("vision model answered nothing")
    text = str((choices[0].get("message") or {}).get("content") or "").strip()
    return _unfence(text)


async def _every_deployment_contained(alias: str) -> bool:
    """Whether every deployment LiteLLM holds under `alias` is strict-local. The catalogue
    names an alias by its first row; a second, outside deployment under the same name could
    be picked by load balancing, so each row is checked here, uncached."""
    try:
        rows = [row for row in await litellm_service.model_info() if row.get("model_name") == alias]
    except litellm_service.LiteLLMError:
        return False
    return bool(rows) and all(model_service.contained(row.get("model_info") or {}) for row in rows)


_FENCE = re.compile(r"^```[a-zA-Z]*\n(.*)\n```$", re.S)


def _unfence(text: str) -> str:
    """A transcription some models wrap in a code fence, without it."""
    match = _FENCE.match(text.strip())
    return match.group(1).strip() if match else text


# ── comparing two readings ─────────────────────────────────────────────

_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}.*$", re.M)
_NOISE = re.compile(r"[\s|]+")
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


def normalised(text: str) -> str:
    """Characters only: no spacing, no table pipes or separator rows, no marks."""
    text = _TABLE_RULE.sub("", text)
    text = text.replace(UNREADABLE_MARK, "").replace(NO_TEXT_MARK, "")
    return _NOISE.sub("", text)


def numbers(text: str) -> Counter:
    """Every number in the text, thousands separators dropped, read with the spacing taken
    out: the engine often drops the spaces of 「2025. 3. 1.」, which must not count as a
    different date."""
    return Counter(match.replace(",", "") for match in _NUMBER.findall(normalised(text)))


def agreement(a: str, b: str) -> float:
    """How alike two readings are, 0..1, spacing and table marks ignored."""
    left, right = normalised(a), normalised(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def disagree(engine_text: str, vision_text: str) -> bool:
    """Whether the two readings differ enough that a person should look: in words, or in any
    number at all (a wrong figure matters more than a wrong particle)."""
    if agreement(engine_text, vision_text) < AGREE_AT_LEAST:
        return True
    return numbers(engine_text) != numbers(vision_text)


def _vision_text(raw: str) -> str:
    return "" if raw.strip() == NO_TEXT_MARK else raw.strip()


async def read_page(
    cfg: Config, data: bytes, kind: str, mime: str, page: int, api_key: str
) -> PageRead:
    """One page through the readers `cfg.mode` names."""
    if cfg.mode == "vision":
        if kind == "pdf":
            if not cfg.engine_url:
                raise OcrRefused("PDF 쪽을 그림으로 바꿀 OCR 서비스가 없어 글자를 읽지 못했습니다.")
            picture, picture_mime = await render(cfg, data, page), "image/jpeg"
        else:
            picture, picture_mime = await _picture_for_model(data, mime, page)
        text = _vision_text(await vision_read(cfg, picture, picture_mime, api_key))
        return PageRead(
            text=text,
            source="vision",
            needs_review=UNREADABLE_MARK in text,
            has_table="|" in text and bool(_TABLE_RULE.search(text)),
            vision_used=True,
        )

    want_image = cfg.mode == "both"
    found = await engine_read(cfg, data, page, want_image=want_image)
    engine_text = str(found.get("text") or "")
    confidence = found.get("confidence")
    tables = found.get("tables") or []
    layout = {
        "dpi": found.get("dpi"),
        "width": found.get("width"),
        "height": found.get("height"),
        "lines": found.get("lines") or [],
        "tables": tables,
    }
    unsure = confidence is None or float(confidence) < VISION_BELOW or bool(tables)
    if cfg.mode == "both" and unsure and found.get("image"):
        picture = base64.b64decode(found["image"])
        vision_text = _vision_text(
            await vision_read(cfg, picture, str(found.get("image_mime") or "image/jpeg"), api_key)
        )
        layout["engine_text"] = engine_text
        layout["vision_model"] = cfg.vision_model
        review = UNREADABLE_MARK in vision_text or (
            bool(engine_text.strip() or vision_text.strip()) and disagree(engine_text, vision_text)
        )
        return PageRead(
            text=vision_text,
            source="vision",
            confidence=confidence,
            needs_review=review,
            has_table=bool(tables),
            layout=layout,
            vision_used=True,
        )
    review = confidence is not None and float(confidence) < REVIEW_BELOW
    return PageRead(
        text=engine_text.strip(),
        source="engine",
        confidence=confidence,
        needs_review=review,
        has_table=bool(tables),
        layout=layout,
    )


async def _picture_for_model(data: bytes, mime: str, page: int) -> tuple[bytes, str]:
    """The chosen frame as a JPEG of at most 2000 px a side. Every picture is decoded here
    under the pixel cap and re-encoded, never handed on as uploaded."""
    return await asyncio.to_thread(_frame_jpeg, data, page), "image/jpeg"


def _frame_jpeg(data: bytes, page: int) -> bytes:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.seek(page - 1)
            width, height = picture.size
            if width * height > MAX_PIXELS:
                raise OcrRefused("그림이 너무 커서 글자를 읽지 못했습니다.")
            frame = picture.convert("RGB")
    except OcrRefused:
        raise
    except Exception as exc:  # noqa: BLE001 — Pillow raises many types for bad input
        raise OcrRefused("그림을 열 수 없어 글자를 읽지 못했습니다.") from exc
    longest = max(frame.size)
    if longest > 2000:
        ratio = 2000 / longest
        frame = frame.resize((int(frame.size[0] * ratio), int(frame.size[1] * ratio)))
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


# ── putting the pages together ─────────────────────────────────────────


def joined(pages: list[tuple[int, str]], kind: str) -> str:
    """The file's text: PDF pages carry the `[페이지 n]` marker citations read; a single
    picture is just its text."""
    if kind == "image" and len(pages) == 1:
        return pages[0][1].strip()
    return "\n\n".join(f"[페이지 {page}]\n{text.strip()}" for page, text in pages if text.strip())


async def api_key_for(user) -> str:
    """The owner's model key, so the page reads are theirs in the ledger; the master key
    when they have none yet."""
    return litellm_service.user_key(user) or (await settings_store.litellm_config())[1]


__all__ = [
    "AGREE_AT_LEAST",
    "Config",
    "IMAGE_TYPES",
    "MODES",
    "NO_TEXT_MARK",
    "OcrError",
    "OcrRefused",
    "PageRead",
    "Plan",
    "UNREADABLE_MARK",
    "VISION_BELOW",
    "agreement",
    "config",
    "disagree",
    "joined",
    "normalised",
    "numbers",
    "plan",
    "read_page",
    "vision_choices",
]
