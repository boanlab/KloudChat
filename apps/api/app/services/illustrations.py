"""Pictures a document draws for itself when the person asks for them, planned on its written
parts the way its diagrams are, with a self-hosted image model only. External image models stay
with the outline card, where the person approves each picture and its cost."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services import imagegen, pictures, settings_store
from app.services.diagrams import Completion, Wrap

log = logging.getLogger(__name__)

#: pictures one document draws by itself
MAX_PICTURES = 2

_PROMPT = """Pick the parts of this {what} where a picture shows what the words cannot, and write
the picture for each.

Request:
{request}

Parts:
{parts}

Rules:
- A picture here is an illustration: a metaphor for an idea, how a real object, place or scene
  looks, or the mood of a part that opens a topic. Structures, flows, comparisons and numbers
  are drawn as diagrams, tables and charts; never pick a part for those.
- Skip a part whose point is a table, a list of figures, a procedure or a decision.
- At most {limit}. None is a fine answer; most business documents need one or none.
- `prompt` is one English sentence for the image model: the subject and the setting, as a flat
  editorial illustration. No text, letters, numbers, logos, charts or screens with words.
  Do not draw real, named people.
- `caption` is a short {language} caption (under 30 characters) naming what the picture shows, in
  the part's own terms.

Answer with a JSON array only, for example:
[{{"part": 2,
  "prompt": "Flat editorial illustration of colleagues around one shared table in a bright office",
  "caption": "함께 쓰는 협업 공간"}}]
"""

#: 「그림 없이」, 「이미지는 빼고」: the person declined pictures
_DECLINED = re.compile(
    r"(?:그림|이미지|삽화|일러스트|사진)\s{0,2}(?:은|는|을|를)?\s{0,2}"
    r"(?:없이|빼|넣지\s{0,2}마|필요\s{0,2}없)"
)


#: 「삽화를 곁들여」, 「사진도 넣어」, "with illustrations": the person asked for pictures
_ASKED = re.compile(
    r"삽화|일러스트|illustrat"
    r"|(?:그림|이미지|사진)[^\n.]{0,12}?(?:넣|포함|곁들|추가|붙|담아|들어가|있는|와\s?함께|과\s?함께)"
    r"|(?:add|include|with)\s+(?:some\s+|a\s+few\s+)?(?:pictures|images|photos)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Planned:
    """One picture for part `index` (0-based)."""

    index: int
    prompt: str
    caption: str


def self_hosted(model: dict | None) -> bool:
    """Whether `model` is an image model served inside the data boundary."""
    return bool(model) and model.get("dataBoundary") == "self_hosted"


def declined(request: str) -> bool:
    return bool(_DECLINED.search(request or ""))


def wanted(request: str) -> bool:
    """Whether the request asks for pictures; decorative ones are never added unasked."""
    return bool(_ASKED.search(request or "")) and not declined(request)


def _parse(text: str, allowed: set[int], limit: int) -> list[Planned]:
    block = text[text.find("["): text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        rows = json.loads(block)
    except ValueError:
        return []
    out: list[Planned] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("part", 0)) - 1
        except (TypeError, ValueError):
            continue
        prompt = " ".join(str(row.get("prompt") or "").split())
        if index not in allowed or not prompt or any(p.index == index for p in out):
            continue
        caption = " ".join(str(row.get("caption") or "").split())[:40]
        out.append(Planned(index=index, prompt=prompt[:500], caption=caption))
        if len(out) >= limit:
            break
    return out


async def plan(
    *,
    parts: list[tuple[str, str]],
    eligible: Iterable[int],
    request: str,
    model: str,
    api_key: str,
    complete: Completion,
    slide: bool,
    wrap: Wrap | None = None,
    limit: int = MAX_PICTURES,
    language: str = "ko",
) -> tuple[list[Planned], dict[str, int]]:
    """Pictures for (title, text) parts, eligible indices only; never raises; captions in
    `language` (`ko`, `en`)."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    allowed = sorted(set(eligible))
    if not allowed or limit < 1:
        return [], spent
    listed = "\n\n".join(
        f"[{i + 1}] {parts[i][0]}\n{parts[i][1][:700] or '(내용 없음)'}" for i in allowed
    )
    prompt = _PROMPT.format(
        what="slide deck" if slide else "report",
        request=request[:1200],
        parts=listed[:7000],
        limit=limit,
        language="English" if language == "en" else "Korean",
    )
    try:
        text, usage = await complete(
            model, wrap(prompt) if wrap else [{"role": "user", "content": prompt}], api_key, 900)
    except Exception as exc:  # noqa: BLE001 — a document without pictures is still one
        log.info("picture planning failed: %s", exc)
        return [], spent
    spent = {"inputTokens": int(usage.get("inputTokens") or 0),
             "outputTokens": int(usage.get("outputTokens") or 0)}
    return _parse(text, set(allowed), limit), spent


async def draw(
    planned: Planned, *, image_model: dict | None, api_key: str, aspect: str
) -> dict[str, Any] | None:
    """The picture as a `data:` URI with its caption and size, or None; never raises."""
    if not self_hosted(image_model):
        return None
    base, _ = await settings_store.litellm_config()
    try:
        made = await imagegen.generate(
            base_url=base,
            api_key=api_key,
            model=str(image_model.get("id") or ""),
            prompt=imagegen.compose_prompt(planned.prompt, aspect=aspect, style=""),
            aspect=aspect,
        )
    except Exception as exc:  # noqa: BLE001 — a missing picture is not a failed document
        log.warning("picture %r not drawn: %s", planned.caption, exc)
        return None
    return {
        "src": pictures.encode(made.mime, made.data),
        "caption": planned.caption,
        "width": made.width,
        "height": made.height,
        # popped into the turn's usage
        "_in": made.input_tokens,
        "_out": made.output_tokens,
    }


__all__ = ["MAX_PICTURES", "Planned", "declined", "draw", "plan", "self_hosted", "wanted"]
