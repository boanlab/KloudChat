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


__all__ = ["embedded", "korean"]
