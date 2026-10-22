"""Korean fonts for reportlab: embedded Nanum TTF, else CID fallback (blank without CMaps)."""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)

#: fonts-nanum files, by preference
_CANDIDATES = {
    "gothic": (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ),
    # bold faces; regular as fallback
    "gothic-bold": (
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    ),
    "serif-bold": (
        "/usr/share/fonts/truetype/nanum/NanumMyeongjoBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ),
}

#: reportlab CID fonts, never embedded
_CID_FALLBACK = {"gothic": "HYSMyeongJo-Medium", "serif": "HYSMyeongJo-Medium"}

_resolved: dict[str, str] = {}


def korean(style: str = "gothic", *, bold: bool = False) -> str:
    """Cached registered font name for setFont (bold falls back to regular)."""
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


#: accented Latin the Nanum faces lack, by preference: DejaVu Sans (fonts-dejavu-core: ř, ő,
#: ễ), else reportlab's own Bitstream Vera (í, ü, ş)
_LATIN = {
    False: ("Latin-wide", ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "Vera.ttf")),
    True: ("Latin-wide-bold",
           ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "VeraBd.ttf")),
}


def latin(*, bold: bool = False) -> str | None:
    """Registered name of the accented-Latin face, or None."""
    name, files = _LATIN[bold]
    if name in _resolved:
        return _resolved[name] or None
    _resolved[name] = ""
    for file in files:
        if file.startswith("/") and not Path(file).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, file))
            _resolved[name] = name
            break
        except Exception:  # noqa: BLE001 — a missing face leaves the letters to stand-ins
            log.warning("could not register %s", file)
    return _resolved[name] or None


def _register(style: str) -> str | None:
    """Register the first installed candidate for style, or None."""

    for path in _CANDIDATES.get(style, ()):
        file = Path(path)
        if not file.is_file():
            continue
        name = f"Nanum-{style}"
        try:
            pdfmetrics.registerFont(TTFont(name, str(file)))
            # map <b>/<i> variants to regular to avoid missing-family errors
            pdfmetrics.registerFontFamily(
                name, normal=name, bold=name, italic=name, boldItalic=name
            )
        except Exception:  # noqa: BLE001 — a bad font file must not fail the export
            log.warning("could not register %s, falling back to the CID font", path)
            continue
        return name
    return None


def embedded(style: str = "gothic") -> bool:
    """Whether korean(style) is an embedded TTF (health check)."""
    return korean(style).startswith("Nanum-")


#: stand-ins for glyphs Korean faces may lack (− drew □)
_STAND_INS = {
    "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2015": "—",
    "\u2009": " ", "\u202f": " ", "\u2007": " ", "\u200a": " ", "\u2044": "/", "\u2215": "/",
    "\u2264": "<=", "\u2265": ">=", "\u2248": "≒", "\u2260": "!=", "\u2032": "'", "\u2033": '"',
}


def drawable(text: str, *names: str) -> str:
    """text with undrawable characters replaced by stand-ins."""
    if not text or not any(ch in _STAND_INS for ch in text):
        return text
    maps = []
    for name in names:
        face = getattr(pdfmetrics.getFont(name), "face", None)
        maps.append(getattr(face, "charToGlyph", None))
    if any(m is None for m in maps):
        # CID fonts: no glyph table, stand-ins always safe
        return "".join(_STAND_INS.get(ch, ch) for ch in text)
    return "".join(_STAND_INS[ch] if ch in _STAND_INS and any(ord(ch) not in m for m in maps)
                   else ch for ch in text)


def drawable_tree(value, *names: str):
    """Every string in value made drawable."""
    if isinstance(value, str):
        return drawable(value, *names)
    if isinstance(value, list):
        return [drawable_tree(v, *names) for v in value]
    if isinstance(value, dict):
        return {k: drawable_tree(v, *names) for k, v in value.items()}
    return value


__all__ = ["drawable", "drawable_tree", "embedded", "korean", "latin"]
