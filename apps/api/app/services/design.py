"""Design-system tokens (renderers), prompt blocks (model), craft rules; no DB access."""

from __future__ import annotations

import re

from app.models.chat import SessionKind

#: deck styles; reports use the first three
VISUAL_STYLES = (
    "editorial",
    "poster",
    "minimal",
    "dark",
    "split",
    "warm",
    "mono",
    "pastel",
    "forest",
    "slate",
    "paper",
)

#: no-design defaults; match deck._ACCENT and exporters
DEFAULT_TOKENS: dict[str, str] = {
    "accent": "#5b5bd6",
    "ink": "#1a1a1a",
    "muted": "#666666",
    "font": "gothic",
    "visualStyle": "editorial",
    #: every deck slide but the cover
    "footer": "",
    "logo": "",
}

#: inline data URI (no server); no SVG (PIL can't decode)
_MAX_LOGO_BYTES = 256 * 1024
_LOGO = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)

#: fonts.py keys shipped in the image
FONTS = ("gothic", "serif")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

#: free-text prose for every project turn
MAX_BODY = 400
MAX_IMAGE_STYLE = 200

#: brand-agnostic craft rules; kinds = target surfaces
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
    """Complete token set; invalid fields fall back individually."""
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
    """Token set for a renderer; None means defaults."""
    return normalise_tokens(getattr(design, "tokens", None) if design else None)


def craft_keys(raw) -> list[str]:
    """Known craft keys, CRAFT order, deduplicated."""
    asked = {str(key).strip() for key in (raw or [])}
    return [key for key in CRAFT if key in asked]


def craft_block(keys, kind: SessionKind) -> str:
    """Craft rules for this surface, or ""."""
    parts = [CRAFT[key]["text"] for key in craft_keys(keys) if kind in CRAFT[key]["kinds"]]
    return "\n".join(parts)


def prompt_block(design, kind: SessionKind) -> str:
    """Trusted context block for one turn, or ""."""
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
    """Accent and house style for an image prompt, or empty."""
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
    """Visual style from request style words; "editorial" default."""
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
    if any(word in text for word in ("파스텔", "연한 색", "부드러운 색감", "라벤더")):
        return "pastel"
    if any(word in text for word in ("숲", "초록 배경", "자연 느낌", "에코", "친환경 느낌")):
        return "forest"
    if any(word in text for word in ("강철", "회색 톤", "차가운 색", "슬레이트", "메탈")):
        return "slate"
    if any(word in text for word in ("종이 서식", "논문 서식", "고전적", "테두리 선", "학술지")):
        return "paper"
    return "editorial"


#: venue → look; first venue named wins
_VENUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("minimal", ("학술", "학회", "세미나", "심사", "논문", "디펜스", "연구실", "컨퍼런스", "저널")),
    (
        "dark",
        ("데모", "시연", "제품 소개", "출시", "해커톤", "개발자", "기술 발표", "런칭", "쇼케이스"),
    ),
    (
        "split",
        (
            "경영진",
            "임원",
            "이사회",
            "사업",
            "제안",
            "현황 보고",
            "킥오프",
            "투자",
            "기관",
            "성과 보고",
        ),
    ),
    (
        "warm",
        ("교육", "강의", "수업", "워크숍", "복지", "문화", "학부모", "동아리", "오리엔테이션"),
    ),
    ("poster", ("홍보", "설명회", "발표회", "전시", "모집", "캠페인", "박람회", "행사")),
    ("mono", ("디자인", "건축", "사진전", "갤러리")),
)


#: report looks renderers support; other venue styles map to nearest
_REPORT_LOOKS = {"editorial": "editorial", "poster": "poster", "minimal": "minimal",
                 "paper": "minimal", "mono": "minimal", "slate": "editorial", "dark": "poster",
                 "split": "editorial", "warm": "poster", "pastel": "poster", "forest": "editorial"}


#: serif subjects/looks (humanities, academic, law, quiet); else gothic
_SERIF_WORDS = (
    "논문", "학술", "학회", "세미나", "연구", "인문", "역사", "문학", "철학", "법", "판례",
    "에세이", "수필", "칼럼", "백서", "연차 보고", "annual report", "회고", "기념",
)
_SERIF_LOOKS = {"minimal", "paper", "warm"}


def font_for(request: str, style: str = "") -> str:
    """"serif" or "gothic" for a subject/look."""
    text = (request or "").lower()
    if style in _SERIF_LOOKS or any(word in text for word in _SERIF_WORDS):
        return "serif"
    return "gothic"


def report_look_for(request: str, accent: str) -> dict[str, str]:
    """No-design report tokens: requested/venue look plus accent."""
    style = visual_style_for(request)
    if style == "editorial":
        style = venue_style_for(request) or "editorial"
    look = _REPORT_LOOKS.get(style, "editorial")
    font = font_for(request, look)
    return normalise_tokens({"visualStyle": look, "accent": accent, "font": font})


def venue_style_for(request: str) -> str:
    """Venue look from the request, or ""; style words outrank it."""

    text = (request or "").lower()
    for style, words in _VENUES:
        if any(word in text for word in words):
            return style
    return ""


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
    "venue_style_for",
]
