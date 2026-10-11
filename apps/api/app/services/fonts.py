"""Korean font registration for reportlab PDFs.

A Nanum TTF is embedded when installed; otherwise the non-embedded CID font
`HYSMyeongJo-Medium` is used, which renders blank in viewers without the
Adobe-Korea1 CMaps. `embedded()` reports which happened.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)

#: Installed by `fonts-nanum` in the image, in order of preference.
_CANDIDATES = {
    "gothic": (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ),
    # Bold faces, for titles and table heads; the regular face stands in when absent.
    "gothic-bold": (
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    ),
    "serif-bold": (
        "/usr/share/fonts/truetype/nanum/NanumMyeongjoBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ),
}

#: reportlab's bundled CID fonts; never embedded in the PDF.
_CID_FALLBACK = {"gothic": "HYSMyeongJo-Medium", "serif": "HYSMyeongJo-Medium"}

_resolved: dict[str, str] = {}


def korean(style: str = "gothic", *, bold: bool = False) -> str:
    """Registered font name for `setFont`; registration is global, so the result is cached.

    `bold=True` names the bold face, or the regular one when no bold file is installed.
    """
    if bold:
        face = f"{style}-bold"
        if face not in _resolved:
            _resolved[face] = _register(face) or korean(style)
        return _resolved[face]
    if style in _resolved:
        return _resolved[style]
    registered = _register(style)
    if registered:
        _resolved[style] = registered
        return registered

    fallback = _CID_FALLBACK[style]
    log.warning(
        "no embeddable Korean font found; PDFs will use %s, which renders only "
        "where the viewer supplies the Adobe-Korea1 CMaps",
        fallback,
    )
    pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    _resolved[style] = fallback
    return fallback


def _register(style: str) -> str | None:
    """Registers the first installed candidate for `style`; None when none is."""

    for path in _CANDIDATES.get(style, ()):
        file = Path(path)
        if not file.is_file():
            continue
        name = f"Nanum-{style}"
        try:
            pdfmetrics.registerFont(TTFont(name, str(file)))
            # `<b>`/`<i>` markup looks up family variants; map all to the
            # regular face rather than raise a missing-family error.
            pdfmetrics.registerFontFamily(
                name, normal=name, bold=name, italic=name, boldItalic=name
            )
        except Exception:  # noqa: BLE001 — a bad font file must not fail the export
            log.warning("could not register %s, falling back to the CID font", path)
            continue
        return name
    return None


def embedded(style: str = "gothic") -> bool:
    """Whether `korean(style)` resolved to an embedded TTF; read by the health check."""
    return korean(style).startswith("Nanum-")


#: Look-alikes for characters a Korean face may lack: 「완료 − 도착」 drew 「완료 □ 도착」.
_STAND_INS = {
    "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2015": "—",
    "\u2009": " ", "\u202f": " ", "\u2007": " ", "\u200a": " ", "\u2044": "/", "\u2215": "/",
    "\u2264": "<=", "\u2265": ">=", "\u2248": "≒", "\u2260": "!=", "\u2032": "'", "\u2033": '"',
}


def drawable(text: str, *names: str) -> str:
    """`text` with each character none of the faces `names` can draw replaced by its stand-in
    (a minus sign by a hyphen); a character with no stand-in is left as written."""
    if not text or not any(ch in _STAND_INS for ch in text):
        return text
    maps = []
    for name in names:
        face = getattr(pdfmetrics.getFont(name), "face", None)
        maps.append(getattr(face, "charToGlyph", None))
    if any(m is None for m in maps):
        # A CID font draws by code, with no table to ask: the stand-ins are safe there.
        return "".join(_STAND_INS.get(ch, ch) for ch in text)
    return "".join(_STAND_INS[ch] if ch in _STAND_INS and any(ord(ch) not in m for m in maps)
                   else ch for ch in text)


def drawable_tree(value, *names: str):
    """Every string inside `value` (a slide's dicts and lists) made `drawable`."""
    if isinstance(value, str):
        return drawable(value, *names)
    if isinstance(value, list):
        return [drawable_tree(v, *names) for v in value]
    if isinstance(value, dict):
        return {k: drawable_tree(v, *names) for k, v in value.items()}
    return value


__all__ = ["drawable", "drawable_tree", "embedded", "korean"]
