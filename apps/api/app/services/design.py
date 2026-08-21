"""The look a project's output wears, and the craft rules that come with it.

Two audiences, and they are kept apart on purpose.

**Renderers read `tokens`.** Four values — accent, ink, muted, font — chosen
because `.pptx`, `.pdf`, `.hwpx` and the browser preview can each express all
four. A fifth that only PowerPoint can draw would make the preview lie, which
is the one property the three deck renderers currently guarantee.

**The model reads `prompt_block`.** Colours are not in it for the text
surfaces: a model writing report prose cannot act on `#5b5bd6`, and a hex code
in a system prompt is tokens spent on nothing. Image is the exception — there
the colour is the instruction, so it goes out with `image_clause` instead.

`CRAFT` is the brand-agnostic half: rules that hold whatever the brand is,
carried only when a design system asks for them. They are deliberately about
what the model actually controls — wording, emphasis, structure — rather than
about pixels it never touches.

Nothing here reads the database. `assemble` passes the row in.
"""

from __future__ import annotations

import re

from app.models.chat import SessionKind

#: What a project with no design system already gets: `deck._ACCENT`, the
#: exporters' ink, and the Gothic face `fonts` prefers for slides. Kept here so
#: an explicit design system and the absent one describe the same thing.
DEFAULT_TOKENS: dict[str, str] = {
    "accent": "#5b5bd6",
    "ink": "#1a1a1a",
    "muted": "#666666",
    "font": "gothic",
}

#: `fonts.py` keys. Gothic for slides, serif for documents — the two faces the
#: image ships, so a third value would name a file that is not there.
FONTS = ("gothic", "serif")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Free-text prose that reaches every turn in the project. Capped: a design
#: system is the rule several projects share, and anything longer than this
#: belongs to one project's own instructions, which have their own field.
MAX_BODY = 400
MAX_IMAGE_STYLE = 200

#: Brand-agnostic craft. `kinds` is which surfaces each rule can act on — a
#: rule about heading depth means nothing to an image prompt, and shipping it
#: there is prompt cost with no effect.
CRAFT: dict[str, dict] = {
    "restraint": {
        "label": "군더더기 덜기",
        "kinds": (SessionKind.chat, SessionKind.report, SessionKind.slides),
        "text": (
            "- 이모지를 쓰지 않는다.\n"
            "- '혁신적', '차별화된', '최적의' 같은 채움말 대신 확인할 수 있는 사실을 쓴다.\n"
            "- 쓸 내용이 없으면 분량을 채우지 말고 그 항목을 줄인다."
        ),
    },
    "typography": {
        "label": "글의 결 맞추기",
        "kinds": (SessionKind.report, SessionKind.slides),
        "text": (
            "- 강조는 한 가지 방법으로만 한다. 굵게·따옴표·밑줄을 겹쳐 쓰지 않는다.\n"
            "- 제목 단계는 두 단계까지만 쓴다.\n"
            "- 한 절은 문단이거나 목록이다. 둘을 번갈아 쓰지 않는다."
        ),
    },
}


def normalise_tokens(raw: dict | None) -> dict[str, str]:
    """A complete, drawable token set from whatever was stored or sent.

    Falls back per field rather than wholesale: a row with a good accent and a
    font name nobody recognises should keep the accent.
    """
    out = dict(DEFAULT_TOKENS)
    for key in ("accent", "ink", "muted"):
        value = str((raw or {}).get(key) or "").strip()
        if _HEX.match(value):
            out[key] = value.lower()
    font = str((raw or {}).get("font") or "").strip().lower()
    if font in FONTS:
        out["font"] = font
    return out


def tokens_of(design) -> dict[str, str]:
    """The token set to hand a renderer. `None` design means the defaults."""
    return normalise_tokens(getattr(design, "tokens", None) if design else None)


def craft_keys(raw) -> list[str]:
    """Known craft keys, in `CRAFT` order, deduplicated."""
    asked = {str(key).strip() for key in (raw or [])}
    return [key for key in CRAFT if key in asked]


def craft_block(keys, kind: SessionKind) -> str:
    """The craft rules that can act on this surface, or `""`."""
    parts = [
        CRAFT[key]["text"] for key in craft_keys(keys) if kind in CRAFT[key]["kinds"]
    ]
    return "\n".join(parts)


def prompt_block(design, kind: SessionKind) -> str:
    """The trusted context block for one turn, or `""` when it would say nothing.

    Empty is a real answer: a design system carrying only tokens has nothing to
    tell a model, and a header with no rules under it is a header that costs
    tokens on every turn.
    """
    if design is None:
        return ""
    body = (design.body or "").strip()
    craft = craft_block(design.craft, kind)
    if not body and not craft:
        return ""
    lines = [f"# 디자인 시스템 — {design.name}"]
    if body:
        lines.append(body)
    if craft:
        lines.append(craft)
    return "\n".join(lines)


def image_clause(design) -> str:
    """The design's contribution to an image prompt, in the prompt's language.

    Colour first because it is the part the picture model acts on most
    reliably, then the house style phrase. Both are optional.
    """
    if design is None:
        return ""
    parts: list[str] = []
    accent = tokens_of(design)["accent"]
    if accent != DEFAULT_TOKENS["accent"] or (design.tokens or {}).get("accent"):
        parts.append(f"primary colour {accent}")
    style = (design.image_style or "").strip().rstrip(".")
    if style:
        parts.append(style)
    return ". ".join(parts)


__all__ = [
    "CRAFT",
    "DEFAULT_TOKENS",
    "FONTS",
    "MAX_BODY",
    "MAX_IMAGE_STYLE",
    "craft_block",
    "craft_keys",
    "image_clause",
    "normalise_tokens",
    "prompt_block",
    "tokens_of",
]
