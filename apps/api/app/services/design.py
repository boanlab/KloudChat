"""Design system tokens (read by renderers), prompt blocks (read by the model), and craft rules.

Tokens are limited to what `.pptx`, `.pdf`, `.hwpx` and the browser preview
can all draw. Colours reach the model only through `image_clause`. Nothing
here reads the database.
"""

from __future__ import annotations

import re

from app.models.chat import SessionKind

#: Deck styles; a report uses only the first three.
VISUAL_STYLES = ("editorial", "poster", "minimal", "dark", "split", "warm", "mono")

#: What a project with no design system gets; matches `deck._ACCENT` and the
#: exporters' defaults.
DEFAULT_TOKENS: dict[str, str] = {
    "accent": "#5b5bd6",
    "ink": "#1a1a1a",
    "muted": "#666666",
    "font": "gothic",
    "visualStyle": "editorial",
    #: Drawn on every deck slide but the cover.
    "footer": "",
    "logo": "",
}

#: The logo is an inline data URI so an exported deck needs no server. SVG is
#: excluded: the exporters decode with PIL, which cannot read it.
_MAX_LOGO_BYTES = 256 * 1024
_LOGO = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)

#: `fonts.py` keys; the two faces the image ships.
FONTS = ("gothic", "serif")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Free-text prose that reaches every turn in the project.
MAX_BODY = 400
MAX_IMAGE_STYLE = 200

#: Brand-agnostic craft rules; `kinds` is the surfaces each rule is sent to.
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
    """A complete token set; each invalid field falls back to its default independently."""
    out = dict(DEFAULT_TOKENS)
    for key in ("accent", "ink", "muted"):
        value = str((raw or {}).get(key) or "").strip()
        if _HEX.match(value):
            out[key] = value.lower()
    font = str((raw or {}).get("font") or "").strip().lower()
    if font in FONTS:
        out["font"] = font
    visual_style = str((raw or {}).get("visualStyle") or "").strip()
    if visual_style in VISUAL_STYLES:
        out["visualStyle"] = visual_style
    footer = " ".join(str((raw or {}).get("footer") or "").split())[:80]
    if footer:
        out["footer"] = footer
    logo = str((raw or {}).get("logo") or "").strip()
    if _LOGO.match(logo) and len(logo) <= _MAX_LOGO_BYTES:
        out["logo"] = logo
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
    parts = [CRAFT[key]["text"] for key in craft_keys(keys) if kind in CRAFT[key]["kinds"]]
    return "\n".join(parts)


def prompt_block(design, kind: SessionKind) -> str:
    """The trusted context block for one turn, or `""` when there is no body and no craft."""
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
    """Accent colour and house style phrase for an image prompt; empty when neither is set."""
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


def visual_style_for(request: str) -> str:
    """Visual style keyed off style words in the request; `"editorial"` by default."""
    text = (request or "").lower()
    if any(word in text for word in ("매거진", "포스터", "강렬", "임팩트", "피치", "홍보", "색면")):
        return "poster"
    if any(word in text for word in ("미니멀", "절제", "담백", "간결한 디자인", "여백", "학술적")):
        return "minimal"
    if any(word in text for word in ("다크", "어두운 배경", "검은 배경", "네온")):
        return "dark"
    if any(word in text for word in ("분할", "색면 분할", "스플릿")):
        return "split"
    if any(word in text for word in ("따뜻한", "종이 질감", "크림색", "베이지", "아늑")):
        return "warm"
    if any(word in text for word in ("흑백", "모노톤", "블랙앤화이트", "black and white")):
        return "mono"
    return "editorial"


__all__ = [
    "CRAFT",
    "VISUAL_STYLES",
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
    "visual_style_for",
]
