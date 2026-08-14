"""The Korean font used by every PDF this service produces.

reportlab bundles CID fonts, which is why exporting Korean never needed a font
file. What they do not do is embed anything: the PDF names `Adobe-Korea1` and
leaves it to the reader to supply the CMaps and a substitute face. Acrobat and
Chrome do. A Linux box without `poppler-data` does not, and it draws nothing at
all — not a fallback glyph, not a box, just empty white where the text was.

That failure is invisible from this end. The file is the right size, the page
count is right, and `pdffonts` lists the font — so the export looks fine until
it is opened somewhere that cannot resolve it, which for a deck is a projector
in front of a room.

So the TTF is embedded when one is present, and the CID font stays as the
fallback for an image built without it. `available()` says which happened, so
this cannot degrade silently the way it did before.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)

#: Installed by `fonts-nanum` in the image. Ordered by preference: Gothic for
#: slides, Myeongjo for documents — both are checked so a partial install still
#: yields something embeddable.
_CANDIDATES = {
    "gothic": (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    ),
    "serif": (
        "/usr/share/fonts/truetype/nanum/NanumMyeongjo.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ),
}

#: reportlab's own, embedded in no PDF. See the module docstring.
_CID_FALLBACK = {"gothic": "HYSMyeongJo-Medium", "serif": "HYSMyeongJo-Medium"}

_resolved: dict[str, str] = {}


def korean(style: str = "gothic") -> str:
    """The registered font name to pass to `setFont`, embedding one if possible.

    Registration is global to reportlab and idempotent per name, so the result
    is cached rather than re-registered per export.
    """
    if style in _resolved:
        return _resolved[style]

    for path in _CANDIDATES.get(style, ()):
        file = Path(path)
        if not file.is_file():
            continue
        name = f"Nanum-{style}"
        try:
            pdfmetrics.registerFont(TTFont(name, str(file)))
        except Exception:  # noqa: BLE001 — a bad font file must not fail the export
            log.warning("could not register %s, falling back to the CID font", path)
            continue
        _resolved[style] = name
        return name

    fallback = _CID_FALLBACK[style]
    log.warning(
        "no embeddable Korean font found; PDFs will use %s, which renders only "
        "where the viewer supplies the Adobe-Korea1 CMaps",
        fallback,
    )
    pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    _resolved[style] = fallback
    return fallback


def embedded(style: str = "gothic") -> bool:
    """Whether `korean(style)` returned a font that is embedded in the file.

    The health check reads this: an image built without the font package
    produces PDFs that are blank in some viewers, and that should be visible
    here rather than discovered in a lecture hall.
    """
    return korean(style).startswith("Nanum-")


__all__ = ["embedded", "korean"]
