"""Deck export to `.pptx` and `.pdf`, one 16:9 slide per page from the stored artifact fields.

Both share the 960×540 pt geometry (13.333×7.5 in). Speaker notes go to the
.pptx notes pane only.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import PIL.Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services import charts, deck, design, fonts, pictures

log = logging.getLogger(__name__)


class _InlineRuns(HTMLParser):
    """Reads the editor's sanitised inline HTML into `(text, style)` runs."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict] = [{}]
        self.runs: list[tuple[str, dict]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        style = dict(self.stack[-1])
        values = dict(attrs)
        if tag in ("b", "strong"):
            style["bold"] = True
        if tag in ("i", "em"):
            style["italic"] = True
        if tag == "u":
            style["underline"] = True
        if tag == "font":
            if (size := values.get("size")) and str(size).isdigit():
                style["size"] = int(size)
            if color := values.get("color"):
                style["color"] = color
        if tag == "span" and (css := values.get("style")):
            rules = {
                key.strip().lower(): value.strip()
                for rule in css.split(";")
                if ":" in rule
                for key, value in [rule.split(":", 1)]
            }
            if re.fullmatch(r"[0-9.]+em", rules.get("font-size", "")):
                style["scale"] = float(rules["font-size"][:-2])
            if rules.get("font-weight") in ("bold", "600", "700", "800", "900"):
                style["bold"] = True
            if rules.get("font-style") == "italic":
                style["italic"] = True
            if "underline" in rules.get("text-decoration", ""):
                style["underline"] = True
            if color := rules.get("color"):
                style["color"] = color
        self.stack.append(style)

    def handle_endtag(self, _tag: str) -> None:
        if len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if data:
            self.runs.append((data, dict(self.stack[-1])))


def _inline_runs(html: str | None, fallback: str) -> list[tuple[str, dict]]:
    if not html:
        return [(fallback, {})]
    parser = _InlineRuns()
    parser.feed(html)
    return parser.runs or [(fallback, {})]


#: 16:9 in points (EMU/12700, reportlab's default unit); drives both exporters.
_W, _H = 960.0, 540.0

#: Default width of a picture sharing the slide with text.
_PICTURE_SPAN = 300.0


def _picture_span(data: dict) -> float:
    """Width of a picture sharing a slide with words, in export points."""
    return {"small": 230.0, "medium": _PICTURE_SPAN, "large": 390.0}.get(
        str((data.get("image") or {}).get("size") or "medium"), _PICTURE_SPAN
    )


_EMU_PER_PT = 12700

#: (latin, East Asian) faces keyed by `design.FONTS`; see `_font`.
_FACES = {
    "gothic": ("Segoe UI", "맑은 고딕"),
    "serif": ("Georgia", "바탕"),
}

_INK = RGBColor(0x1A, 0x1A, 0x1A)
_MUTED = RGBColor(0x66, 0x66, 0x66)

#: Dark-template ground and neutrals; not design tokens.
_DARK_BG = RGBColor(0x0E, 0x11, 0x16)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_DARK_INK = RGBColor(0xF5, 0xF6, 0xF7)
_DARK_MUTED = RGBColor(0x9A, 0xA0, 0xA6)

#: PDF fallbacks when no design system is attached (not exactly `#1a1a1a`).
_PDF_INK = (0.1, 0.1, 0.1)
_PDF_MUTED = (0.4, 0.4, 0.4)


def _rgb(value: str | None) -> RGBColor:
    """`#rrggbb` or `#rgb` → RGBColor; the default accent for anything else."""
    text = (value or "").strip().lstrip("#")
    if len(text) == 3 and re.fullmatch(r"[0-9a-fA-F]{3}", text):
        text = "".join(c * 2 for c in text)
    if len(text) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return RGBColor(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    return RGBColor(0x5B, 0x5B, 0xD6)


def _mix(
    colour: RGBColor, percent: float, *, onto: RGBColor = RGBColor(0xFF, 0xFF, 0xFF)
) -> RGBColor:
    """`percent`% of `colour` over `onto`, matching the preview's CSS `color-mix`."""
    weight = max(0.0, min(1.0, percent / 100))
    return RGBColor(*(round(colour[i] * weight + onto[i] * (1 - weight)) for i in range(3)))


def _block(slide, *, left: float, top: float, width: float, height: float, colour: RGBColor):
    """A filled rectangle with no line and no shadow."""
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(height * _EMU_PER_PT)),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _hex_floats(value: str | None) -> tuple[float, float, float]:
    colour = _rgb(value)
    return colour[0] / 255, colour[1] / 255, colour[2] / 255


@dataclass(frozen=True)
class Look:
    """A visual style, matching the panel's `LOOKS`: ground, neutrals, cover, ornament, card style.
    """

    bg: str
    ink: str
    muted: str
    faint: str
    hair: str
    #: How much accent goes into a tint, onto the ground.
    tint: int
    card: str  # filled | outlined
    radius: float
    badge: str  # square | circle
    cover: str  # gradient | wash | glow | split | paper | brackets
    #: top-band | left-bar | corner-circle | bottom-rule | gutter | frame | bottom-band
    ornament: str
    #: Whether the neutrals above replace the design system's.
    own_neutrals: bool = False


_LOOKS: dict[str, Look] = {
    "editorial": Look(
        bg="#ffffff",
        ink="#1a1a1a",
        muted="#666666",
        faint="#8a8a8a",
        hair="#e6e6e6",
        tint=7,
        card="filled",
        radius=0,
        badge="square",
        cover="gradient",
        ornament="top-band",
        own_neutrals=False,
    ),
    "poster": Look(
        bg="#f7f3ed",
        ink="#1a1a1a",
        muted="#666666",
        faint="#8a8a8a",
        hair="#e2ddd4",
        tint=9,
        card="filled",
        radius=0,
        badge="square",
        cover="gradient",
        ornament="left-bar",
        own_neutrals=False,
    ),
    "minimal": Look(
        bg="#ffffff",
        ink="#1a1a1a",
        muted="#666666",
        faint="#8a8a8a",
        hair="#ececec",
        tint=5,
        card="outlined",
        radius=0,
        badge="square",
        cover="wash",
        ornament="corner-circle",
        own_neutrals=False,
    ),
    "dark": Look(
        bg="#0f172a",
        ink="#f1f5f9",
        muted="#a3b1c6",
        faint="#64748b",
        hair="#273449",
        tint=22,
        card="filled",
        radius=6,
        badge="circle",
        cover="glow",
        ornament="bottom-rule",
        own_neutrals=True,
    ),
    "split": Look(
        bg="#ffffff",
        ink="#111827",
        muted="#5b6472",
        faint="#9aa3b2",
        hair="#e5e7eb",
        tint=6,
        card="outlined",
        radius=0,
        badge="square",
        cover="split",
        ornament="gutter",
        own_neutrals=True,
    ),
    "warm": Look(
        bg="#f6f1e8",
        ink="#3f3328",
        muted="#7a6a5a",
        faint="#a8998a",
        hair="#e2d8c8",
        tint=12,
        card="filled",
        radius=10,
        badge="circle",
        cover="paper",
        ornament="bottom-band",
        own_neutrals=True,
    ),
    "mono": Look(
        bg="#ffffff",
        ink="#111111",
        muted="#555555",
        faint="#8a8a8a",
        hair="#111111",
        tint=0,
        card="outlined",
        radius=0,
        badge="square",
        cover="brackets",
        ornament="frame",
        own_neutrals=True,
    ),
}


def _look_of(visual_style: str) -> Look:
    return _LOOKS.get(visual_style) or _LOOKS["editorial"]


def _shape(slide, kind, *, left: float, top: float, width: float, height: float):
    return slide.shapes.add_shape(
        kind,
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(height * _EMU_PER_PT)),
    )


def _box(
    slide,
    look: Look,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    fill: RGBColor,
    line: RGBColor,
):
    """A card, band or metric box, filled or outlined and rounded as the look says."""
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if look.radius else MSO_SHAPE.RECTANGLE
    shape = _shape(slide, kind, left=left, top=top, width=width, height=height)
    if look.radius:
        # The adjustment is a fraction of the shorter side.
        shape.adjustments[0] = min(0.5, (look.radius * 2.4) / max(1.0, min(width, height)))
    shape.fill.solid()
    if look.card == "outlined":
        shape.fill.fore_color.rgb = _rgb(look.bg)
        shape.line.color.rgb = line
        shape.line.width = Emu(int(0.75 * _EMU_PER_PT))
    else:
        shape.fill.fore_color.rgb = fill
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _badge(slide, look: Look, *, left: float, top: float, side: float, colour: RGBColor):
    """The numbered mark on a step or a tile: a square, or a disc."""
    kind = MSO_SHAPE.OVAL if look.badge == "circle" else MSO_SHAPE.RECTANGLE
    shape = _shape(slide, kind, left=left, top=top, width=side, height=side)
    shape.fill.solid()
    shape.fill.fore_color.rgb = colour
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _mix_floats(
    colour: tuple[float, float, float],
    percent: float,
    *,
    onto: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> tuple[float, float, float]:
    """`_mix` for reportlab, which works in 0–1 floats rather than bytes."""
    weight = max(0.0, min(1.0, percent / 100))
    return tuple(colour[i] * weight + onto[i] * (1 - weight) for i in range(3))  # type: ignore[return-value]


def _table_size(rows: int) -> float:
    """The preview's fitted table cell size in its 225-unit drawing; both exporters scale by the
    same ratio.
    """
    per_row = 122 / (rows + 1.2)
    return max(7.5, min(12.0, per_row / 2.05))


def _font(
    run,
    *,
    size: int,
    bold: bool = False,
    colour: RGBColor = _INK,
    faces: tuple[str, str] = _FACES["gothic"],
) -> None:
    """Sets a run's font; `font.name` writes only `a:latin`, Hangul is drawn from `a:ea`."""
    latin, east_asian = faces
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = colour
    run.font.name = latin
    properties = run.font._rPr
    for tag in ("a:ea", "a:cs"):
        element = properties.makeelement(qn(tag), {"typeface": east_asian})
        properties.append(element)


def _pptx_pairs(
    slide,
    pairs: list[tuple[str, str]],
    *,
    layout: str,
    accent: RGBColor,
    tint: RGBColor,
    muted: RGBColor,
    width: float,
    left: float = 72.0,
    paint,
    look: Look | None = None,
    hair: RGBColor | None = None,
) -> None:
    """Draws a paired layout: bands, tiles, steps, cards, or timeline."""
    top = 150.0
    room = _H - 210
    look = look or _LOOKS["editorial"]
    hair = hair or RGBColor(0xE6, 0xE6, 0xE6)
    if layout == "bands":
        label = 96.0
        height = min(72.0, (room - 10 * (len(pairs) - 1)) / max(len(pairs), 1))
        for index, (name, text) in enumerate(pairs):
            y = top + index * (height + 10)
            _box(
                slide, look, left=left, top=y, width=label, height=height, fill=accent, line=accent
            )
            if look.card == "outlined":
                # The name chip stays filled.
                _block(slide, left=left, top=y, width=label, height=height, colour=accent)
            _box(
                slide,
                look,
                left=left + label + 8,
                top=y,
                width=width - label - 8,
                height=height,
                fill=tint,
                line=hair,
            )
            box = _textbox(slide, left=left, top=y + height / 2 - 12, width=label, height=24)
            box.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = box.paragraphs[0].add_run()
            run.text = name
            paint(run, size=15, bold=True, colour=_WHITE)
            body = _textbox(
                slide,
                left=left + label + 24,
                top=y + 10,
                width=width - label - 40,
                height=height - 20,
            )
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=13)
        return

    if layout == "tiles":
        span = (width - 16 * (len(pairs) - 1)) / max(len(pairs), 1)
        side = min(span, 96.0)
        for index, (mark, name) in enumerate(pairs):
            item_left = left + index * (span + 16)
            _badge(slide, look, left=item_left, top=top + 20, side=side, colour=accent)
            box = _textbox(
                slide, left=item_left, top=top + 20 + side / 2 - 26, width=side, height=52
            )
            box.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = box.paragraphs[0].add_run()
            run.text = mark
            paint(run, size=40, bold=True, colour=_WHITE)
            under = _textbox(
                slide, left=item_left - 8, top=top + 32 + side, width=side + 16, height=44
            )
            under.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = under.paragraphs[0].add_run()
            run.text = name
            paint(run, size=12, colour=muted)
        return

    if layout == "steps":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        side = 44.0
        if len(pairs) > 1:
            # Rule from the first badge's centre to the last's.
            _block(
                slide,
                left=left + side / 2,
                top=top + 20 + side / 2 - 1,
                width=width - span,
                height=2,
                colour=tint,
            )
        for index, (name, text) in enumerate(pairs):
            item_left = left + index * (span + gap)
            _badge(slide, look, left=item_left, top=top + 20, side=side, colour=accent)
            box = _textbox(slide, left=item_left, top=top + 20 + 8, width=side, height=30)
            box.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = box.paragraphs[0].add_run()
            run.text = f"{index + 1:02d}"
            paint(run, size=18, bold=True, colour=_WHITE)
            title = _textbox(slide, left=item_left, top=top + 20 + side + 12, width=span, height=30)
            run = title.paragraphs[0].add_run()
            run.text = name
            paint(run, size=15, bold=True)
            body = _textbox(slide, left=item_left, top=top + 20 + side + 42, width=span, height=120)
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=12, colour=muted)
        return

    if layout == "cards":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        height = min(220.0, room - 20)
        for index, (name, text) in enumerate(pairs):
            item_left = left + index * (span + gap)
            _box(
                slide,
                look,
                left=item_left,
                top=top + 10,
                width=span,
                height=height,
                fill=tint,
                line=hair,
            )
            _block(slide, left=item_left, top=top + 10, width=span, height=5, colour=accent)
            title = _textbox(slide, left=item_left + 14, top=top + 26, width=span - 28, height=34)
            run = title.paragraphs[0].add_run()
            run.text = name
            paint(run, size=16, bold=True, colour=accent)
            body = _textbox(
                slide, left=item_left + 14, top=top + 60, width=span - 28, height=height - 70
            )
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=13)
        return

    # timeline
    axis = 128.0
    step = min(56.0, room / max(len(pairs), 1))
    _block(slide, left=left + axis, top=top, width=1.5, height=step * len(pairs), colour=tint)
    for index, (when, what) in enumerate(pairs):
        y = top + index * step
        date = _textbox(slide, left=left, top=y, width=axis - 12, height=26)
        date.paragraphs[0].alignment = PP_ALIGN.RIGHT
        run = date.paragraphs[0].add_run()
        run.text = when
        paint(run, size=13, bold=True, colour=accent)
        _block(slide, left=left + axis - 3.5, top=y + 6, width=8, height=8, colour=accent)
        body = _textbox(slide, left=left + axis + 16, top=y, width=width - axis - 16, height=step)
        run = body.paragraphs[0].add_run()
        run.text = what
        paint(run, size=13)


def _pptx_chart(
    slide, chart: dict, *, accent, muted, width: float, faces: tuple[str, str], left: float = 72.0
) -> None:
    """A native PowerPoint chart (editable worksheet), styled by `services.charts`."""
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    payload = CategoryChartData()
    payload.categories = chart["categories"]
    for name, values in chart["series"]:
        payload.add_series(name or " ", values)

    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.LINE_MARKERS if chart["kind"] == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED,
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(150 * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int((_H - 220) * _EMU_PER_PT)),
        payload,
    )
    charts.apply(
        frame.chart,
        kind=chart["kind"],
        unit=chart.get("unit") or "",
        accent=accent,
        muted=muted,
        faces=faces,
    )


def _cell_rule(cell, colour: RGBColor, points: float) -> None:
    """A bottom rule on one table cell, written as `a:lnB` XML.

    `a:tcPr` is schema-ordered with line elements first; inserted at the
    front, or PowerPoint offers to repair the file.
    """
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls, qn

    properties = cell._tc.get_or_add_tcPr()
    for existing in properties.findall(qn("a:lnB")):
        properties.remove(existing)
    properties.insert(
        0,
        parse_xml(
            f'<a:lnB {nsdecls("a")} w="{int(points * 12700)}" cap="flat"'
            ' cmpd="sng" algn="ctr">'
            f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill>'
            "</a:lnB>"
        ),
    )


#: Placeholder mark PowerPoint reads for the outline pane and screen readers.
#: Indices are per slide: title 0, body 1.
_PH = (
    '<p:ph xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    ' type="{kind}" idx="{index}"/>'
)


def _placeholder(box, kind: str, index: int) -> None:
    """Marks a drawn textbox as a placeholder of `kind`; only placeholders are outline text."""
    from pptx.oxml import parse_xml

    box._element.nvSpPr.nvPr.append(parse_xml(_PH.format(kind=kind, index=index)))


def _textbox(
    slide,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    placeholder: tuple[str, int] | None = None,
):
    box = slide.shapes.add_textbox(
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(height * _EMU_PER_PT)),
    )
    if placeholder:
        _placeholder(box, *placeholder)
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _outline_placeholder(slide, kind: str, index: int, text: str, colour: RGBColor) -> None:
    """A hidden 1×1 placeholder carrying the outline text; masters re-apply placeholder geometry, so
    the visible box stays a plain textbox.
    """
    frame = _textbox(slide, left=0, top=0, width=1, height=1, placeholder=(kind, index))
    frame.clear()
    for line_index, line in enumerate(text.splitlines() or [text]):
        paragraph = frame.paragraphs[0] if line_index == 0 else frame.add_paragraph()
        run = paragraph.add_run()
        run.text = line
        # LibreOffice ignores `cNvPr hidden` on placeholders; 1pt in the
        # ground colour keeps it invisible there.
        run.font.size = Pt(1)
        run.font.color.rgb = colour
    frame._parent.element.nvSpPr.cNvPr.set("hidden", "1")


def _columns_of(data: dict, bullets: list[str], layout: str) -> list[list[str]]:
    """Columns to lay side by side: explicit `columns` when given, else the bullets halved."""
    given = [list(c) for c in (data.get("columns") or []) if c]
    if layout == "two-column" and len(given) >= 2:
        return given
    return _split_columns(bullets) if layout == "two-column" else [bullets]


def _split_columns(bullets: list[str]) -> list[list[str]]:
    """A list in two columns, top-to-bottom then across; one column below five items."""
    if len(bullets) < 5:
        return [bullets]
    half = (len(bullets) + 1) // 2
    return [bullets[:half], bullets[half:]]


#: Logo height in the foot, and its width cap.
_LOGO_HEIGHT = 18.0
_LOGO_MAX_WIDTH = 120.0


def _logo_of(tokens: dict[str, str] | None) -> tuple[bytes, float, float] | None:
    """`(bytes, width, height)` for the design system's logo drawn to `_LOGO_HEIGHT`, or None."""
    raw = (tokens or {}).get("logo") or ""
    if not raw.startswith("data:image/"):
        return None
    try:
        blob = base64.b64decode(raw.split(",", 1)[1], validate=False)
        with PIL.Image.open(io.BytesIO(blob)) as picture:
            width, height = picture.size
    except Exception:  # noqa: BLE001 — a mark that will not decode is no mark
        log.warning("could not read the design system's logo")
        return None
    if not width or not height:
        return None
    drawn_width = min(_LOGO_MAX_WIDTH, _LOGO_HEIGHT * width / height)
    return blob, drawn_width, _LOGO_HEIGHT * min(1.0, _LOGO_MAX_WIDTH / max(drawn_width, 1e-6))


#: Paired layouts; see `deck._PAIRED`.
_PAIRED = ("bands", "tiles", "timeline", "steps", "cards")

#: Layouts drawn reversed out of the accent.
_COVERS = ("title", "section", "closing")


def _written(slides: list[dict]) -> list[dict]:
    """Slides minus those still marked `deck.UNWRITTEN`; numbering is by position."""
    return [
        slide
        for slide in slides
        if not (str(slide.get("body") or "").strip() == deck.UNWRITTEN and not _filled(slide))
    ]


def _filled(slide: dict) -> bool:
    """Whether anything but the placeholder is on this slide."""
    return any(slide.get(key) for key in ("bullets", "rows", "metrics", "chart", "image", *_PAIRED))


def _pairs_of(slide: dict, layout: str) -> list[tuple[str, str]]:
    """`[(왼쪽, 오른쪽)]` for a paired layout with both halves filled; empty otherwise."""
    if layout not in _PAIRED:
        return []
    out: list[tuple[str, str]] = []
    for item in slide.get(layout) or []:
        pair = item if isinstance(item, (list, tuple)) else ()
        if len(pair) >= 2 and str(pair[0]).strip() and str(pair[1]).strip():
            out.append((str(pair[0]).strip(), str(pair[1]).strip()))
    return out


def _chart_of(slide: dict) -> dict | None:
    """A drawable chart, or None; categories and series are cut to the shortest paired length."""
    chart = slide.get("chart")
    if not isinstance(chart, dict):
        return None
    categories = [str(c) for c in (chart.get("categories") or [])]
    series: list[tuple[str, list[float]]] = []
    for item in chart.get("series") or []:
        if not isinstance(item, dict):
            continue
        values: list[float] = []
        for raw in item.get("values") or []:
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                break
        if values:
            series.append((str(item.get("name") or ""), values))
    if not categories or not series:
        return None
    width = min(len(categories), min(len(values) for _, values in series))
    if width < 2:
        return None
    return {
        "kind": "line" if str(chart.get("kind")) == "line" else "bar",
        "unit": str(chart.get("unit") or ""),
        "categories": categories[:width],
        "series": [(name, values[:width]) for name, values in series],
    }


def _picture_of(slide: dict) -> tuple[bytes, str] | None:
    """`(bytes, caption)` from `image.data` (bytes, via `page_export`) or `image.src` (a `data:`
    URI).
    """
    raw = slide.get("image")
    if not isinstance(raw, dict):
        return None
    caption = str(raw.get("caption") or "")
    data = raw.get("data")
    if isinstance(data, bytes | bytearray) and data:
        return bytes(data), caption
    decoded = pictures.decode(str(raw.get("src") or ""))
    return (decoded[1], caption) if decoded else None


def _fit(data: bytes, *, box: tuple[float, float]) -> tuple[float, float]:
    """The size a picture takes inside `box`, in points, keeping its aspect."""
    max_width, max_height = box
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
    except Exception:  # noqa: BLE001 — a picture we cannot measure gets the box
        return max_width, max_height
    if not width or not height:
        return max_width, max_height
    scale = min(max_width / width, max_height / height)
    return width * scale, height * scale


def _fill(
    data: bytes, *, box: tuple[float, float]
) -> tuple[float, float, float, float, float, float]:
    """`(width, height, left, top, right, bottom)`: the cover-fit size and PowerPoint's 0–1 crop
    fractions.
    """
    box_width, box_height = box
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
    except Exception:  # noqa: BLE001 — the caller already handles bad bytes
        return box_width, box_height, 0.0, 0.0, 0.0, 0.0
    if not width or not height:
        return box_width, box_height, 0.0, 0.0, 0.0, 0.0
    scale = max(box_width / width, box_height / height)
    drawn_width, drawn_height = width * scale, height * scale
    horizontal = max(0.0, (drawn_width - box_width) / drawn_width / 2)
    vertical = max(0.0, (drawn_height - box_height) / drawn_height / 2)
    return drawn_width, drawn_height, horizontal, vertical, horizontal, vertical


def to_pptx(
    title: str,
    slides: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    dark: bool = False,
    template: str = "",
) -> bytes:
    """The deck as a PowerPoint file, drawn on the blank layout.

    `tokens` is the design system copied onto the artifact; `template` is the
    서식's `.pptx`, whose master and theme the file is built on.
    """
    style = design.normalise_tokens(tokens) if tokens else None
    faces = _FACES[style["font"]] if style else _FACES["gothic"]
    ink = _rgb(style["ink"]) if style else _INK
    muted = _rgb(style["muted"]) if style else _MUTED
    if dark:
        # Design-system ink is for paper; on a dark ground the neutrals swap.
        ink, muted = _DARK_INK, _DARK_MUTED

    mark = _logo_of(style)
    footer = (style or {}).get("footer") or ""

    #: Current slide's `textScale`; a list so `paint` can read the rebinding.
    typescale = [1.0]

    def paint(run, *, size: int, bold: bool = False, colour: RGBColor | None = None) -> None:
        _font(
            run,
            size=max(8, round(size * typescale[0])),
            bold=bold,
            colour=colour or ink,
            faces=faces,
        )

    def paint_rich(
        paragraph,
        data: dict,
        key: str,
        text: str,
        *,
        size: int,
        bold: bool = False,
        colour: RGBColor | None = None,
    ) -> None:
        html = (data.get("richText") or {}).get(key)
        scale = {1: 0.65, 2: 0.8, 3: 1.0, 4: 1.2, 5: 1.5, 6: 2.0, 7: 3.0}
        for value, inline in _inline_runs(html, text):
            run = paragraph.add_run()
            run.text = value
            run_colour = colour
            raw_colour = str(inline.get("color") or "")
            if re.fullmatch(r"#[0-9a-fA-F]{6}", raw_colour):
                run_colour = _rgb(raw_colour)
            paint(
                run,
                size=max(
                    8,
                    round(size * float(inline.get("scale") or scale.get(inline.get("size"), 1.0))),
                ),
                bold=bool(inline.get("bold", bold)),
                colour=run_colour,
            )
            run.font.italic = bool(inline.get("italic"))
            run.font.underline = bool(inline.get("underline"))

    # Slides are drawn as free shapes, not placed in the template's placeholders.
    presentation = Presentation(template) if template else Presentation()
    presentation.slide_width = Emu(int(_W * _EMU_PER_PT))
    presentation.slide_height = Emu(int(_H * _EMU_PER_PT))
    blank = presentation.slide_layouts[6]
    visual_style = (style or {}).get("visualStyle") or "editorial"

    for index, data in enumerate(_written(slides)):
        slide = presentation.slides.add_slide(blank)
        typescale[0] = _typescale(data)
        if dark:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = _DARK_BG
        look = _look_of(visual_style)
        if look.own_neutrals and not dark:
            ink, muted = _rgb(look.ink), _rgb(look.muted)
        # The slide's own accent, else the design system's.
        accent = _rgb(data.get("accent") or (style or {}).get("accent"))
        if visual_style == "mono":
            accent = ink
        ground = _rgb(look.bg)
        tint = _mix(accent, look.tint, onto=ground) if look.tint else RGBColor(0xF2, 0xF2, 0xF2)
        hair = _rgb(look.hair)
        layout = data.get("layout") or "bullets"

        cover = layout in _COVERS
        #: Cover text on the accent (white ink) rather than the look's ground.
        on_accent = look.cover in ("gradient", "glow")
        if cover:
            fill = slide.background.fill
            if look.cover == "gradient":
                try:
                    fill.gradient()
                    fill.gradient_angle = 45.0
                    stops = fill.gradient_stops
                    stops[0].color.rgb = accent
                    stops[1].color.rgb = _mix(
                        accent, 48 if visual_style == "poster" else 62, onto=_INK
                    )
                    for extra in list(stops)[2:]:
                        extra.color.rgb = stops[1].color.rgb
                except Exception as exc:  # noqa: BLE001 — a ground, not the deck
                    log.warning("could not fill a cover with a gradient: %s", exc)
                    fill.solid()
                    fill.fore_color.rgb = accent
            elif look.cover == "wash":
                fill.solid()
                fill.fore_color.rgb = _mix(accent, 10)
            else:
                fill.solid()
                fill.fore_color.rgb = ground
                if look.cover == "glow":
                    for ring, pct in ((520, 22), (400, 38), (280, 58)):
                        disc = _shape(
                            slide,
                            MSO_SHAPE.OVAL,
                            left=_W - ring * 0.55,
                            top=_H - ring * 0.45,
                            width=ring,
                            height=ring,
                        )
                        disc.fill.solid()
                        disc.fill.fore_color.rgb = _mix(accent, pct, onto=ground)
                        disc.line.fill.background()
                        disc.shadow.inherit = False
                elif look.cover == "paper":
                    disc = _shape(
                        slide, MSO_SHAPE.OVAL, left=_W - 300, top=44, width=456, height=456
                    )
                    disc.fill.solid()
                    disc.fill.fore_color.rgb = accent
                    disc.line.fill.background()
                    disc.shadow.inherit = False
                elif look.cover == "split":
                    _block(slide, left=0, top=0, width=384, height=_H, colour=accent)
                elif look.cover == "brackets":
                    ink_line = ink
                    _block(slide, left=62, top=67, width=6, height=72, colour=ink_line)
                    _block(slide, left=62, top=67, width=53, height=6, colour=ink_line)
                    _block(slide, left=_W - 68, top=_H - 139, width=6, height=72, colour=ink_line)
                    _block(slide, left=_W - 115, top=_H - 73, width=53, height=6, colour=ink_line)
        else:
            if look.bg.lower() != "#ffffff":
                slide.background.fill.solid()
                slide.background.fill.fore_color.rgb = ground
            if look.ornament == "left-bar":
                _block(slide, left=0, top=0, width=14, height=_H, colour=accent)
            elif look.ornament == "top-band":
                _block(slide, left=0, top=0, width=_W, height=14, colour=accent)
            elif look.ornament == "corner-circle":
                disc = _shape(slide, MSO_SHAPE.OVAL, left=_W - 96, top=-84, width=168, height=168)
                disc.fill.solid()
                disc.fill.fore_color.rgb = _mix(accent, 12, onto=ground)
                disc.line.fill.background()
                disc.shadow.inherit = False
            elif look.ornament == "bottom-rule":
                _block(slide, left=0, top=_H - 10, width=_W * 0.6, height=10, colour=accent)
                _block(
                    slide,
                    left=_W * 0.6,
                    top=_H - 10,
                    width=_W * 0.4,
                    height=10,
                    colour=_mix(accent, 40, onto=ground),
                )
            elif look.ornament == "gutter":
                _block(slide, left=0, top=0, width=7, height=_H, colour=accent)
                counter = _textbox(slide, left=22, top=_H - 118, width=80, height=50)
                run = counter.paragraphs[0].add_run()
                run.text = f"{index + 1:02d}"
                paint(run, size=30, bold=True, colour=accent)
            elif look.ornament == "frame":
                frame_line = _shape(
                    slide, MSO_SHAPE.RECTANGLE, left=24, top=24, width=_W - 48, height=_H - 48
                )
                frame_line.fill.background()
                frame_line.line.color.rgb = ink
                frame_line.line.width = Emu(int(1.0 * _EMU_PER_PT))
                frame_line.shadow.inherit = False
            elif look.ornament == "bottom-band":
                _block(slide, left=0, top=_H - 53, width=_W, height=53, colour=tint)

        heading = str(data.get("title") or "")
        body = str(data.get("body") or "")
        bullets = [str(b) for b in (data.get("bullets") or []) if str(b).strip()]
        rows = [[str(cell) for cell in row] for row in (data.get("rows") or []) if row]
        metrics = [
            [str(pair[0]), str(pair[1])]
            for pair in (data.get("metrics") or [])
            if isinstance(pair, list) and len(pair) >= 2
        ]
        chart = _chart_of(data)
        picture = _picture_of(data)
        picture_span = _picture_span(data)
        pairs = _pairs_of(data, layout)
        picture_left = bool(
            picture
            and (bullets or rows or metrics or chart or body)
            and str((data.get("image") or {}).get("position") or "") == "left"
        )
        # A picture beside text narrows the text column; alone, it is centred.
        text_width = (
            _W
            - 144
            - (
                picture_span + 24
                if picture and (bullets or rows or metrics or chart or body)
                else 0
            )
        )
        text_left = 72 + (picture_span + 24 if picture_left else 0)

        #: Past the accent block on a split cover.
        cover_left = 72 + 336 if look.cover == "split" else 72
        if layout == "closing":
            cover_ink = _WHITE if on_accent else ink
            cover_muted = _mix(_WHITE, 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                counter = _textbox(slide, left=60, top=_H - 130, width=200, height=80)
                run = counter.paragraphs[0].add_run()
                run.text = "END"
                paint(run, size=64, bold=True, colour=_mix(_WHITE, 35, onto=accent))
            if look.cover != "brackets":
                _block(
                    slide,
                    left=cover_left,
                    top=120,
                    width=106,
                    height=7,
                    colour=_WHITE if on_accent else accent,
                )
            frame = _textbox(
                slide, left=cover_left, top=140, width=_W - 144, height=70, placeholder=("title", 0)
            )
            frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            paint_rich(
                frame.paragraphs[0],
                data,
                "title",
                heading or "마무리",
                size=36,
                bold=True,
                colour=cover_ink,
            )
            if bullets:
                listing = _textbox(
                    slide, left=72, top=220, width=_W - 144, height=200, placeholder=("body", 1)
                )
                for position, text in enumerate(bullets[:3]):
                    paragraph = listing.paragraphs[0] if position == 0 else listing.add_paragraph()
                    paragraph.alignment = PP_ALIGN.LEFT
                    paragraph.space_after = Pt(10)
                    marker = paragraph.add_run()
                    marker.text = "— "
                    paint(marker, size=18, bold=True, colour=cover_muted)
                    paint_rich(
                        paragraph, data, f"bullets.{position}", text, size=18, colour=cover_ink
                    )
            if body:
                foot = _textbox(slide, left=cover_left, top=_H - 120, width=_W - 144, height=50)
                foot.paragraphs[0].alignment = PP_ALIGN.LEFT
                paint_rich(
                    foot.paragraphs[0], data, "body", body, size=22, bold=True, colour=cover_ink
                )
        elif cover:
            cover_ink = _WHITE if on_accent else ink
            cover_muted = _mix(_WHITE, 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                counter = _textbox(slide, left=60, top=_H - 130, width=200, height=80)
                run = counter.paragraphs[0].add_run()
                number_text = str(data.get("number") or "").replace(".", "") or "01"
                run.text = "END" if layout == "closing" else number_text
                paint(run, size=64, bold=True, colour=_mix(_WHITE, 35, onto=accent))
            if (
                look.cover != "split"
                and layout == "section"
                and (number := str(data.get("number") or ""))
            ):
                counter = _textbox(slide, left=72, top=150, width=200, height=40)
                run = counter.paragraphs[0].add_run()
                run.text = number
                paint(
                    run,
                    size=22,
                    bold=True,
                    colour=_mix(_WHITE, 70, onto=accent) if on_accent else accent,
                )
            elif look.cover != "brackets":
                _block(
                    slide,
                    left=cover_left + 10,
                    top=186,
                    width=106,
                    height=7,
                    colour=_WHITE if on_accent else accent,
                )
            frame = _textbox(
                slide,
                left=cover_left,
                top=210,
                width=(_W - 72 - cover_left) - (300 if look.cover == "paper" else 0),
                height=180,
            )
            frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            paint_rich(
                frame.paragraphs[0],
                data,
                "title",
                heading or title,
                size=40,
                bold=True,
                colour=cover_ink,
            )
            if body:
                paragraph = frame.add_paragraph()
                paragraph.alignment = PP_ALIGN.LEFT
                paragraph.space_before = Pt(14)
                paint_rich(paragraph, data, "body", body, size=15, colour=cover_muted)
            _outline_placeholder(
                slide,
                "ctrTitle",
                0,
                "\n".join(part for part in (heading or title, body) if part),
                accent if on_accent else ground,
            )
        elif layout == "statement":
            _block(slide, left=(_W - 62) / 2, top=176, width=62, height=5, colour=accent)
            frame = _textbox(
                slide, left=90, top=196, width=_W - 180, height=120, placeholder=("title", 0)
            )
            frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            paint_rich(
                frame.paragraphs[0], data, "title", heading, size=44, bold=True, colour=accent
            )
            if body:
                under = _textbox(
                    slide, left=120, top=320, width=_W - 240, height=80, placeholder=("body", 1)
                )
                under.paragraphs[0].alignment = PP_ALIGN.CENTER
                paint_rich(under.paragraphs[0], data, "body", body, size=18, colour=muted)
        elif layout == "quote":
            frame = _textbox(
                slide,
                left=90,
                top=170,
                width=_W - 180,
                height=200,
                placeholder=("title", 0),
            )
            paragraph = frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.LEFT
            run = paragraph.add_run()
            run.text = f"“{body}”" if body else heading
            paint(run, size=30, bold=True, colour=accent)
            if body and heading:
                caption = frame.add_paragraph()
                caption.space_before = Pt(16)
                run = caption.add_run()
                run.text = heading
                paint(run, size=13, colour=muted)
        else:
            frame = _textbox(
                slide,
                left=text_left,
                top=64,
                width=text_width,
                height=60,
                placeholder=("title", 0),
            )
            frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            paint_rich(frame.paragraphs[0], data, "title", heading, size=24, bold=True)
            # The tab under the title.
            _block(slide, left=text_left, top=126, width=62, height=5, colour=accent)

            if pairs:
                _pptx_pairs(
                    slide,
                    pairs,
                    layout=layout,
                    accent=accent,
                    tint=tint,
                    muted=muted,
                    width=text_width,
                    left=text_left,
                    paint=paint,
                    look=look,
                    hair=hair,
                )
            elif chart:
                _pptx_chart(
                    slide,
                    chart,
                    accent=accent,
                    muted=muted,
                    width=text_width,
                    faces=faces,
                    left=text_left,
                )
            elif metrics and layout == "big-number":
                figure, label = metrics[0]
                box = _textbox(
                    slide,
                    left=text_left,
                    top=160,
                    width=text_width,
                    height=120,
                    placeholder=("body", 1),
                )
                run = box.paragraphs[0].add_run()
                run.text = figure
                paint(run, size=96, bold=True, colour=accent)
                run = box.paragraphs[0].add_run()
                run.text = f"  {label}"
                paint(run, size=20, colour=muted)
                if body:
                    under = _textbox(slide, left=text_left, top=300, width=text_width, height=80)
                    paint_rich(under.paragraphs[0], data, "body", body, size=18)
            elif bullets and layout == "agenda":
                # Two columns above four entries.
                columns = _split_columns(bullets) if len(bullets) > 4 else [bullets]
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                per = max(len(column) for column in columns)
                step = min(56.0, (_H - 210) / max(per, 1))
                number = 0
                for column_index, column in enumerate(columns):
                    left = text_left + column_index * (span + 24)
                    for position, text in enumerate(column):
                        number += 1
                        y = 150 + position * step
                        counter = _textbox(slide, left=left, top=y, width=52, height=step)
                        run = counter.paragraphs[0].add_run()
                        run.text = f"{number:02d}"
                        paint(run, size=22, bold=True, colour=accent)
                        name = _textbox(
                            slide,
                            left=left + 52,
                            top=y + 4,
                            width=span - 52,
                            height=step,
                            placeholder=("body", 1) if number == 1 else None,
                        )
                        run = name.paragraphs[0].add_run()
                        run.text = text
                        paint(run, size=18)
                        _block(
                            slide, left=left, top=y + step - 6, width=span, height=0.75, colour=hair
                        )
            elif metrics:
                span = (text_width - 24 * (len(metrics) - 1)) / len(metrics)
                for position, (figure, label) in enumerate(metrics):
                    left = text_left + position * (span + 24)
                    _box(
                        slide,
                        look,
                        left=left,
                        top=170,
                        width=span,
                        height=124,
                        fill=tint,
                        line=hair,
                    )
                    _block(slide, left=left, top=170, width=span, height=5, colour=accent)
                    box = _textbox(
                        slide,
                        left=left,
                        top=180,
                        width=span,
                        height=110,
                    )
                    run = box.paragraphs[0].add_run()
                    run.text = figure
                    paint(run, size=44, bold=True, colour=accent)
                    under = box.add_paragraph()
                    under.space_before = Pt(6)
                    run = under.add_run()
                    run.text = label
                    paint(run, size=14, colour=muted)
            elif rows:
                shape = slide.shapes.add_table(
                    len(rows),
                    max(len(row) for row in rows),
                    Emu(int(text_left * _EMU_PER_PT)),
                    Emu(int(150 * _EMU_PER_PT)),
                    Emu(int(text_width * _EMU_PER_PT)),
                    Emu(int(min(_H - 220, 34 * len(rows)) * _EMU_PER_PT)),
                )
                table = shape.table
                cell_size = max(8, round(14 * _table_size(len(rows)) / 12))
                # Off, or PowerPoint applies its theme's banded table style.
                table.first_row = False
                table.horz_banding = False
                for r, row in enumerate(rows):
                    for c, text in enumerate(row):
                        if c >= len(table.columns):
                            continue
                        cell = table.cell(r, c)
                        if r == 0:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = accent
                        elif r % 2 == 0:
                            cell.fill.solid()
                            cell.fill.fore_color.rgb = tint
                        else:
                            cell.fill.background()
                        if r == 0:
                            pass
                        elif r < len(rows) - 1:
                            _cell_rule(cell, muted, 0.5)
                        cell.text = ""
                        run = cell.text_frame.paragraphs[0].add_run()
                        run.text = text
                        paint(
                            run,
                            size=cell_size,
                            bold=r == 0,
                            colour=_WHITE if r == 0 else ink,
                        )
            elif bullets:
                # PowerPoint has no column flow, so columns are separate boxes.
                columns = _columns_of(data, bullets, layout)
                span = (text_width - (24 * (len(columns) - 1))) / len(columns)
                bullet_at = 0
                for column_index, column in enumerate(columns):
                    listing = _textbox(
                        slide,
                        left=text_left + column_index * (span + 24),
                        top=150,
                        width=span,
                        height=_H - 210,
                        # Only the first column is the body placeholder.
                        placeholder=("body", 1) if column_index == 0 else None,
                    )
                    for position, text in enumerate(column):
                        paragraph = (
                            listing.paragraphs[0] if position == 0 else listing.add_paragraph()
                        )
                        paragraph.space_after = Pt(12)
                        marker = paragraph.add_run()
                        marker.text = "• "
                        paint(marker, size=16, bold=True, colour=accent)
                        paint_rich(
                            paragraph,
                            data,
                            f"bullets.{bullet_at}",
                            text,
                            size=14 if len(columns) > 1 else 16,
                        )
                        bullet_at += 1
            elif body:
                paragraph_frame = _textbox(
                    slide,
                    left=text_left,
                    top=150,
                    width=text_width,
                    height=_H - 210,
                    placeholder=("body", 1),
                )
                paint_rich(paragraph_frame.paragraphs[0], data, "body", body, size=16, colour=muted)

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body)
            box = (_W - 260, _H - 230) if alone else (picture_span, _H - 230)
            fill = str((data.get("image") or {}).get("fit") or "") == "cover"
            if fill:
                _, _, crop_left, crop_top, crop_right, crop_bottom = _fill(image_bytes, box=box)
                width, height = box
            else:
                width, height = _fit(image_bytes, box=box)
                crop_left = crop_top = crop_right = crop_bottom = 0.0
            left = (72 if picture_left else 72 + text_width + 24) if not alone else (_W - width) / 2
            top = 150 + max(0.0, (_H - 230 - height) / 2)
            try:
                shape = slide.shapes.add_picture(
                    io.BytesIO(image_bytes),
                    Emu(int(left * _EMU_PER_PT)),
                    Emu(int(top * _EMU_PER_PT)),
                    Emu(int(width * _EMU_PER_PT)),
                    Emu(int(height * _EMU_PER_PT)),
                )
                if fill:
                    shape.crop_left = crop_left
                    shape.crop_top = crop_top
                    shape.crop_right = crop_right
                    shape.crop_bottom = crop_bottom
            except Exception as exc:  # noqa: BLE001 — one bad picture, not a failed export
                log.warning("could not place a picture in the pptx: %s", exc)
            else:
                if image_caption:
                    frame = _textbox(
                        slide, left=left, top=top + height + 6, width=max(width, 120), height=24
                    )
                    run = frame.paragraphs[0].add_run()
                    run.text = image_caption
                    paint(run, size=11, colour=muted)

        # Foot: logo, deck title and footer on the left, slide number on the right.
        if not cover:
            _block(slide, left=72, top=_H - 58, width=_W - 144, height=0.75, colour=hair)
            edge = 72.0
            if mark:
                blob, mark_width, mark_height = mark
                try:
                    slide.shapes.add_picture(
                        io.BytesIO(blob),
                        Emu(int(edge * _EMU_PER_PT)),
                        Emu(int((_H - 52) * _EMU_PER_PT)),
                        Emu(int(mark_width * _EMU_PER_PT)),
                        Emu(int(mark_height * _EMU_PER_PT)),
                    )
                except Exception as exc:  # noqa: BLE001 — a mark, not the deck
                    log.warning("could not place the logo on a pptx slide: %s", exc)
                else:
                    edge += mark_width + 10
            name = _textbox(slide, left=edge, top=_H - 52, width=_W - 188 - edge, height=26)
            run = name.paragraphs[0].add_run()
            run.text = title
            paint(run, size=9, colour=muted)
            if footer:
                who = _textbox(slide, left=_W - 226, top=_H - 52, width=110, height=26)
                who.paragraphs[0].alignment = PP_ALIGN.RIGHT
                run = who.paragraphs[0].add_run()
                run.text = footer
                paint(run, size=9, colour=muted)
            chip = _block(slide, left=_W - 106, top=_H - 52, width=34, height=22, colour=accent)
            frame = chip.text_frame
            frame.word_wrap = False
            frame.margin_left = frame.margin_right = 0
            frame.margin_top = frame.margin_bottom = 0
            frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = frame.paragraphs[0].add_run()
            run.text = str(index + 1)
            paint(run, size=10, bold=True, colour=_WHITE)

        notes = str(data.get("notes") or "").strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _pdf_box(
    pdf,
    look: Look,
    *,
    left: float,
    bottom: float,
    width: float,
    height: float,
    fill,
    line,
    bg,
) -> None:
    """The `.pdf` twin of `_box`."""
    if look.card == "outlined":
        pdf.setFillColorRGB(*bg)
        pdf.setStrokeColorRGB(*line)
        pdf.setLineWidth(0.75)
        if look.radius:
            pdf.roundRect(left, bottom, width, height, look.radius * 1.2, stroke=1, fill=1)
        else:
            pdf.rect(left, bottom, width, height, stroke=1, fill=1)
        return
    pdf.setFillColorRGB(*fill)
    if look.radius:
        pdf.roundRect(left, bottom, width, height, look.radius * 1.2, stroke=0, fill=1)
    else:
        pdf.rect(left, bottom, width, height, stroke=0, fill=1)


def _pdf_badge(pdf, look: Look, *, left: float, bottom: float, side: float, colour) -> None:
    pdf.setFillColorRGB(*colour)
    if look.badge == "circle":
        pdf.circle(left + side / 2, bottom + side / 2, side / 2, stroke=0, fill=1)
    else:
        pdf.rect(left, bottom, side, side, stroke=0, fill=1)


def _pdf_pairs(
    pdf,
    pairs: list[tuple[str, str]],
    *,
    layout: str,
    accent,
    tint,
    muted,
    ink,
    top: float,
    width: float,
    font: str,
    scale: float,
    left: float = 72.0,
    look: Look | None = None,
    hair=None,
    bg=None,
) -> None:
    """The `.pdf` twin of `_pptx_pairs`: same geometry, explicit wrapping."""
    room = top - 80
    look = look or _LOOKS["editorial"]
    hair = hair or (0.902, 0.902, 0.902)
    bg = bg or (1.0, 1.0, 1.0)

    def S(n: float) -> float:
        return n * scale

    if layout == "bands":
        label = 96.0
        height = min(72.0, (room - 10 * (len(pairs) - 1)) / max(len(pairs), 1))
        for index, (name, text) in enumerate(pairs):
            bottom = top - height - index * (height + 10)
            pdf.setFillColorRGB(*accent)
            pdf.rect(left, bottom, label, height, stroke=0, fill=1)
            _pdf_box(
                pdf,
                look,
                left=left + label + 8,
                bottom=bottom,
                width=width - label - 8,
                height=height,
                fill=tint,
                line=hair,
                bg=bg,
            )
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(font, S(15))
            pdf.drawCentredString(left + label / 2, bottom + height / 2 - S(5), name)
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(13))
            lines = _wrap(text, font, S(13), width - label - 40)[:3]
            line_top = bottom + height / 2 + S(18) * (len(lines) - 1) / 2 - S(5)
            for offset, line in enumerate(lines):
                pdf.drawString(left + label + 24, line_top - offset * S(18), line)
        return

    if layout == "tiles":
        span = (width - 16 * (len(pairs) - 1)) / max(len(pairs), 1)
        side = min(span, 96.0)
        for index, (mark, name) in enumerate(pairs):
            item_left = left + index * (span + 16)
            _pdf_badge(pdf, look, left=item_left, bottom=top - side - 20, side=side, colour=accent)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(font, S(40))
            pdf.drawCentredString(item_left + side / 2, top - side / 2 - 20 - S(14), mark)
            pdf.setFillColorRGB(*muted)
            pdf.setFont(font, S(12))
            for offset, line in enumerate(_wrap(name, font, S(12), side + 16)[:2]):
                pdf.drawCentredString(item_left + side / 2, top - side - 40 - offset * S(15), line)
        return

    if layout == "steps":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        side = 44.0
        square_top = top - 20
        if len(pairs) > 1:
            pdf.setFillColorRGB(*tint)
            pdf.rect(left + side / 2, square_top - side / 2 - 1, width - span, 2, stroke=0, fill=1)
        for index, (name, text) in enumerate(pairs):
            item_left = left + index * (span + gap)
            _pdf_badge(
                pdf, look, left=item_left, bottom=square_top - side, side=side, colour=accent
            )
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(font, S(18))
            pdf.drawCentredString(
                item_left + side / 2, square_top - side / 2 - S(6), f"{index + 1:02d}"
            )
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(15))
            pdf.drawString(item_left, square_top - side - S(24), name)
            pdf.setFillColorRGB(*muted)
            pdf.setFont(font, S(12))
            for offset, line in enumerate(_wrap(text, font, S(12), span - 4)[:4]):
                pdf.drawString(item_left, square_top - side - S(44) - offset * S(16), line)
        return

    if layout == "cards":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        height = min(220.0, room - 20)
        card_top = top - 10
        for index, (name, text) in enumerate(pairs):
            item_left = left + index * (span + gap)
            _pdf_box(
                pdf,
                look,
                left=item_left,
                bottom=card_top - height,
                width=span,
                height=height,
                fill=tint,
                line=hair,
                bg=bg,
            )
            pdf.setFillColorRGB(*accent)
            pdf.rect(item_left, card_top - 5, span, 5, stroke=0, fill=1)
            pdf.setFont(font, S(16))
            pdf.drawString(item_left + 14, card_top - S(34), name)
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(13))
            lines = _wrap(text, font, S(13), span - 28)[: max(1, int((height - 70) / S(18)))]
            for offset, line in enumerate(lines):
                pdf.drawString(item_left + 14, card_top - S(60) - offset * S(18), line)
        return

    # timeline
    axis = 128.0
    step = min(56.0, room / max(len(pairs), 1))
    pdf.setFillColorRGB(*tint)
    pdf.rect(left + axis, top - step * len(pairs), 1.5, step * len(pairs), stroke=0, fill=1)
    for index, (when, what) in enumerate(pairs):
        line_top = top - 14 - index * step
        pdf.setFillColorRGB(*accent)
        pdf.setFont(font, S(13))
        pdf.drawRightString(left + axis - 12, line_top, when)
        pdf.rect(left + axis - 3.25, line_top - 1, 8, 8, stroke=0, fill=1)
        pdf.setFillColorRGB(*ink)
        pdf.setFont(font, S(13))
        for offset, line in enumerate(_wrap(what, font, S(13), width - axis - 16)[:2]):
            pdf.drawString(left + axis + 16, line_top - offset * S(16), line)


def _pdf_chart(
    pdf,
    chart: dict,
    *,
    accent: tuple[float, float, float],
    muted: tuple[float, float, float],
    top: float,
    width: float,
    font: str,
    left: float = 72.0,
) -> None:
    """The `.pdf` twin of `_pptx_chart`, drawn by hand over a zero floor."""
    bottom = 90.0
    height = top - bottom - 30
    if height < 60 or width < 100:
        return

    values = [value for _, series in chart["series"] for value in series]
    if max(values + [0]) <= 0:
        return
    ceiling = _nice_ceiling(max(values))
    categories = chart["categories"]
    step = width / len(categories)

    pdf.setFont(font, 11)
    for tick in range(5):
        y = bottom + height * tick / 4
        pdf.setStrokeColorRGB(0.9, 0.9, 0.9)
        pdf.setLineWidth(0.5)
        pdf.line(left, y, left + width, y)
        pdf.setFillColorRGB(*muted)
        pdf.drawRightString(left - 6, y - 4, _tick_label(ceiling * tick / 4))
    if unit := chart.get("unit"):
        pdf.setFillColorRGB(*muted)
        pdf.drawString(left - 30, bottom + height + 12, unit)

    for series_index, (_name, series) in enumerate(chart["series"]):
        colour = accent if series_index == 0 else tuple(c + (1 - c) * 0.55 for c in accent)
        if chart["kind"] == "line":
            pdf.setStrokeColorRGB(*colour)
            pdf.setLineWidth(2.5)
            path = pdf.beginPath()
            points = [
                (left + step * (position + 0.5), bottom + height * (value / ceiling))
                for position, value in enumerate(series)
            ]
            for position, (x, y) in enumerate(points):
                path.moveTo(x, y) if position == 0 else path.lineTo(x, y)
            pdf.drawPath(path)
            pdf.setFillColorRGB(*colour)
            for x, y in points:
                pdf.circle(x, y, 3, stroke=0, fill=1)
        else:
            # Series share the category slot side by side.
            count = len(chart["series"])
            span = step * 0.6 / count
            pdf.setFillColorRGB(*colour)
            for position, value in enumerate(series):
                x = left + step * (position + 0.5) - (step * 0.6) / 2 + span * series_index
                pdf.rect(x, bottom, span, height * (value / ceiling), stroke=0, fill=1)

    pdf.setFillColorRGB(*muted)
    pdf.setFont(font, 12)
    for position, label in enumerate(categories):
        pdf.drawCentredString(left + step * (position + 0.5), bottom - 18, label)

    if len(chart["series"]) > 1:
        x = left
        pdf.setFont(font, 12)
        for series_index, (name, _values) in enumerate(chart["series"]):
            colour = accent if series_index == 0 else tuple(c + (1 - c) * 0.55 for c in accent)
            pdf.setFillColorRGB(*colour)
            pdf.circle(x + 4, bottom - 36, 3.5, stroke=0, fill=1)
            pdf.setFillColorRGB(*muted)
            pdf.drawString(x + 13, bottom - 40, name)
            x += 24 + pdfmetrics.stringWidth(name, font, 12)


def _nice_ceiling(highest: float) -> float:
    """Top of scale rounded up to 1, 2, 2.5, 5 or 10 × a power of ten, as PowerPoint does."""
    import math

    if highest <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(highest))
    for multiple in (1, 2, 2.5, 5, 10):
        if highest <= multiple * power:
            return multiple * power
    return highest


def _tick_label(value: float) -> str:
    """A gridline's number: integer at 10 and above, one decimal below."""
    return f"{value:,.0f}" if abs(value) >= 10 else f"{value:,.1f}".rstrip("0").rstrip(".")


def _typescale(slide: dict) -> float:
    """The slide's `textScale` clamped to 0.5–2.0; it arrives on a PATCHable artifact."""
    try:
        value = float(slide.get("textScale") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.5, value))


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy wrap by measured width, breaking between characters (Korean has no spaces)."""
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if pdfmetrics.stringWidth(candidate, font, size) > width and current:
            # Prefer the last space, so Latin text still breaks on words.
            cut = current.rfind(" ")
            if cut > len(current) * 0.6:
                lines.append(current[:cut])
                current = current[cut + 1 :] + char
            else:
                lines.append(current)
                current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def to_pdf(title: str, slides: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
    """The deck as a PDF, one slide per page, without notes."""
    style = design.normalise_tokens(tokens) if tokens else None
    font = fonts.korean(style["font"] if style else "gothic")
    ink = _hex_floats(style["ink"]) if style else _PDF_INK
    muted = _hex_floats(style["muted"]) if style else _PDF_MUTED
    mark = _logo_of(style)
    footer = (style or {}).get("footer") or ""
    visual_style = (style or {}).get("visualStyle") or "editorial"
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(_W, _H))
    pdf.setTitle(title)

    look = _look_of(visual_style)
    if look.own_neutrals:
        ink, muted = _hex_floats(look.ink), _hex_floats(look.muted)
    ground = _hex_floats(look.bg)
    for index, data in enumerate(_written(slides)):
        accent = _hex_floats(data.get("accent") or (style or {}).get("accent"))
        if visual_style == "mono":
            accent = ink
        layout = data.get("layout") or "bullets"
        heading = str(data.get("title") or "")
        body = str(data.get("body") or "")
        bullets = [str(b) for b in (data.get("bullets") or []) if str(b).strip()]
        rows = [[str(cell) for cell in row] for row in (data.get("rows") or []) if row]
        metrics = [
            [str(pair[0]), str(pair[1])]
            for pair in (data.get("metrics") or [])
            if isinstance(pair, list) and len(pair) >= 2
        ]
        chart = _chart_of(data)
        picture = _picture_of(data)
        picture_span = _picture_span(data)
        pairs = _pairs_of(data, layout)
        picture_left = bool(
            picture
            and (bullets or rows or metrics or chart or body)
            and str((data.get("image") or {}).get("position") or "") == "left"
        )
        # Same split as the .pptx.
        text_width = (
            _W
            - 144
            - (
                picture_span + 24
                if picture and (bullets or rows or metrics or chart or body)
                else 0
            )
        )
        text_left = 72 + (picture_span + 24 if picture_left else 0)

        cover = layout in _COVERS
        on_accent = look.cover in ("gradient", "glow")
        cover_left = 72 + 336 if look.cover == "split" else 72
        tint = _mix_floats(accent, look.tint, onto=ground) if look.tint else (0.949, 0.949, 0.949)
        hair = _hex_floats(look.hair)
        if cover:
            if look.cover == "gradient":
                pdf.setFillColorRGB(*accent)
                pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
                pdf.saveState()
                pdf.linearGradient(
                    0,
                    _H,
                    _W,
                    0,
                    [
                        Color(*accent),
                        Color(
                            *_mix_floats(
                                accent,
                                48 if visual_style == "poster" else 62,
                                onto=_PDF_INK,
                            )
                        ),
                    ],
                    extend=True,
                )
                pdf.restoreState()
            elif look.cover == "wash":
                pdf.setFillColorRGB(*_mix_floats(accent, 10))
                pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
            else:
                pdf.setFillColorRGB(*ground)
                pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
                if look.cover == "glow":
                    for ring, pct in ((520, 22), (400, 38), (280, 58)):
                        pdf.setFillColorRGB(*_mix_floats(accent, pct, onto=ground))
                        pdf.circle(_W - ring * 0.05, ring * 0.05, ring / 2, stroke=0, fill=1)
                elif look.cover == "paper":
                    pdf.setFillColorRGB(*accent)
                    pdf.circle(_W - 72, _H - 272, 228, stroke=0, fill=1)
                elif look.cover == "split":
                    pdf.setFillColorRGB(*accent)
                    pdf.rect(0, 0, 384, _H, stroke=0, fill=1)
                elif look.cover == "brackets":
                    pdf.setFillColorRGB(*ink)
                    pdf.rect(62, _H - 139, 6, 72, stroke=0, fill=1)
                    pdf.rect(62, _H - 73, 53, 6, stroke=0, fill=1)
                    pdf.rect(_W - 68, 67, 6, 72, stroke=0, fill=1)
                    pdf.rect(_W - 115, 67, 53, 6, stroke=0, fill=1)
        else:
            pdf.setFillColorRGB(*ground)
            pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
            if look.ornament == "left-bar":
                pdf.setFillColorRGB(*accent)
                pdf.rect(0, 0, 14, _H, stroke=0, fill=1)
            elif look.ornament == "top-band":
                pdf.setFillColorRGB(*accent)
                pdf.rect(0, _H - 14, _W, 14, stroke=0, fill=1)
            elif look.ornament == "corner-circle":
                pdf.setFillColorRGB(*_mix_floats(accent, 12, onto=ground))
                pdf.circle(_W - 12, _H, 84, stroke=0, fill=1)
            elif look.ornament == "bottom-rule":
                pdf.setFillColorRGB(*accent)
                pdf.rect(0, 0, _W * 0.6, 10, stroke=0, fill=1)
                pdf.setFillColorRGB(*_mix_floats(accent, 40, onto=ground))
                pdf.rect(_W * 0.6, 0, _W * 0.4, 10, stroke=0, fill=1)
            elif look.ornament == "gutter":
                pdf.setFillColorRGB(*accent)
                pdf.rect(0, 0, 7, _H, stroke=0, fill=1)
                pdf.setFont(font, 30)
                pdf.drawString(22, 80, f"{index + 1:02d}")
            elif look.ornament == "frame":
                pdf.setStrokeColorRGB(*ink)
                pdf.setLineWidth(1.0)
                pdf.rect(24, 24, _W - 48, _H - 48, stroke=1, fill=0)
            elif look.ornament == "bottom-band":
                pdf.setFillColorRGB(*tint)
                pdf.rect(0, 0, _W, 53, stroke=0, fill=1)

        # `textScale` applies to sizes and line advances alike: this canvas
        # does not reflow.
        ts = _typescale(data)

        def S(n: float, _ts: float = ts) -> float:
            return n * _ts

        if layout == "closing":
            cover_ink = (1.0, 1.0, 1.0) if on_accent else ink
            cover_muted = _mix_floats((1.0, 1.0, 1.0), 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                pdf.setFillColorRGB(*_mix_floats((1.0, 1.0, 1.0), 35, onto=accent))
                pdf.setFont(font, S(64))
                pdf.drawString(60, 60, "END")
            if look.cover != "brackets":
                pdf.setFillColorRGB(*((1, 1, 1) if on_accent else accent))
                pdf.rect(cover_left, _H - 127, 106, 7, stroke=0, fill=1)
            pdf.setFillColorRGB(*cover_ink)
            pdf.setFont(font, S(36))
            y = _H - 176
            for line in _wrap(heading or "마무리", font, S(36), _W - 72 - cover_left)[:2]:
                pdf.drawString(cover_left, y, line)
                y -= S(44)
            y -= 6
            for text in bullets[:3]:
                pdf.setFillColorRGB(*cover_muted)
                pdf.setFont(font, S(18))
                pdf.drawString(cover_left, y, "—")
                pdf.setFillColorRGB(*cover_ink)
                for offset, line in enumerate(_wrap(text, font, S(18), _W - 128 - cover_left)[:2]):
                    pdf.drawString(cover_left + 28, y - offset * S(24), line)
                    y -= S(24)
                y -= 10
            if body:
                pdf.setFillColorRGB(*cover_ink)
                pdf.setFont(font, S(22))
                pdf.drawString(cover_left, 92, _wrap(body, font, S(22), _W - 72 - cover_left)[0])
        elif cover:
            cover_ink = (1.0, 1.0, 1.0) if on_accent else ink
            number = str(data.get("number") or "")
            if look.cover == "split":
                pdf.setFillColorRGB(*_mix_floats((1.0, 1.0, 1.0), 35, onto=accent))
                pdf.setFont(font, S(64))
                pdf.drawString(60, 60, number.replace(".", "") or "01")
            if look.cover != "split" and layout == "section" and number:
                pdf.setFillColorRGB(
                    *(_mix_floats((1.0, 1.0, 1.0), 70, onto=accent) if on_accent else accent)
                )
                pdf.setFont(font, S(22))
                pdf.drawString(cover_left, _H / 2 + 100, number)
            elif look.cover != "brackets":
                pdf.setFillColorRGB(*((1, 1, 1) if on_accent else accent))
                pdf.rect(cover_left + 10, _H / 2 + 74, 106, 7, stroke=0, fill=1)
            pdf.setFillColorRGB(*cover_ink)
            pdf.setFont(font, S(40))
            y = _H / 2 + 20
            title_width = (_W - 72 - cover_left) - (300 if look.cover == "paper" else 0)
            for line in _wrap(heading or title, font, S(40), title_width):
                pdf.drawString(cover_left, y, line)
                y -= S(50)
            if body:
                pdf.setFillColorRGB(
                    *(_mix_floats((1.0, 1.0, 1.0), 80, onto=accent) if on_accent else muted)
                )
                pdf.setFont(font, S(15))
                for line in _wrap(body, font, S(15), _W - 72 - cover_left)[:2]:
                    pdf.drawString(cover_left, y - 6, line)
                    y -= S(22)
        elif layout == "statement":
            pdf.setFillColorRGB(*accent)
            pdf.rect((_W - 62) / 2, _H - 181, 62, 5, stroke=0, fill=1)
            pdf.setFont(font, S(44))
            y = _H / 2 + 10
            for line in _wrap(heading, font, S(44), _W - 180)[:2]:
                pdf.drawCentredString(_W / 2, y, line)
                y -= S(54)
            if body:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(18))
                for line in _wrap(body, font, S(18), _W - 240)[:2]:
                    pdf.drawCentredString(_W / 2, y - 4, line)
                    y -= S(26)
        elif layout == "quote":
            pdf.setFillColorRGB(*accent)
            pdf.setFont(font, S(30))
            y = _H / 2 + 40
            for line in _wrap(f"“{body or heading}”", font, S(30), _W - 200):
                pdf.drawString(90, y, line)
                y -= S(40)
            if body and heading:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(13))
                pdf.drawString(90, y - 8, heading)
        else:
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(24))
            y = _H - 96
            for line in _wrap(heading, font, S(24), text_width):
                pdf.drawString(72, y, line)
                y -= S(32)
            # The tab under the title; `y` has already advanced past the last line.
            pdf.setFillColorRGB(*accent)
            pdf.rect(text_left, y + S(4), 62, 5, stroke=0, fill=1)
            y -= 24

            if pairs:
                _pdf_pairs(
                    pdf,
                    pairs,
                    layout=layout,
                    accent=accent,
                    tint=tint,
                    muted=muted,
                    ink=ink,
                    top=y,
                    width=text_width,
                    font=font,
                    scale=ts,
                    left=text_left,
                    look=look,
                    hair=hair,
                    bg=ground,
                )
            elif chart:
                _pdf_chart(
                    pdf,
                    chart,
                    accent=accent,
                    muted=muted,
                    top=y,
                    width=text_width,
                    font=font,
                    left=text_left,
                )
            elif metrics and layout == "big-number":
                figure, label = metrics[0]
                pdf.setFillColorRGB(*accent)
                pdf.setFont(font, S(96))
                pdf.drawString(text_left, y - S(90), figure)
                figure_width = pdf.stringWidth(figure, font, S(96))
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(20))
                pdf.drawString(text_left + figure_width + 14, y - S(90), label)
                if body:
                    pdf.setFillColorRGB(*ink)
                    pdf.setFont(font, S(18))
                    for offset, line in enumerate(_wrap(body, font, S(18), text_width)[:2]):
                        pdf.drawString(text_left, y - S(130) - offset * S(26), line)
            elif metrics:
                span = (text_width - 24 * (len(metrics) - 1)) / len(metrics)
                for position, (figure, label) in enumerate(metrics):
                    left = text_left + position * (span + 24)
                    _pdf_box(
                        pdf,
                        look,
                        left=left,
                        bottom=y - S(86),
                        width=span,
                        height=S(86) + 18,
                        fill=tint,
                        line=hair,
                        bg=ground,
                    )
                    pdf.setFillColorRGB(*accent)
                    pdf.rect(left, y + 13, span, 5, stroke=0, fill=1)
                    pdf.setFillColorRGB(*accent)
                    pdf.setFont(font, S(44))
                    pdf.drawString(left, y - S(40), figure)
                    pdf.setFillColorRGB(*muted)
                    pdf.setFont(font, S(14))
                    pdf.drawString(left, y - S(70), label)
            elif rows:
                width = text_width / max(len(row) for row in rows)
                cell_size = S(max(9.0, 15 * _table_size(len(rows)) / 12))
                step = cell_size * 2.0
                for row_index, row in enumerate(rows):
                    if row_index == 0:
                        pdf.setFillColorRGB(*accent)
                        pdf.rect(text_left, y - step * 0.3, text_width, step, stroke=0, fill=1)
                    elif row_index % 2 == 0:
                        pdf.setFillColorRGB(*tint)
                        pdf.rect(text_left, y - step * 0.3, text_width, step, stroke=0, fill=1)
                    else:
                        pdf.setStrokeColorRGB(*hair)
                        pdf.setLineWidth(0.75)
                        pdf.line(text_left, y - step * 0.3, text_left + text_width, y - step * 0.3)
                    pdf.setFillColorRGB(*((1.0, 1.0, 1.0) if row_index == 0 else ink))
                    pdf.setFont(font, cell_size)
                    for cell_index, cell in enumerate(row):
                        text = _wrap(cell, font, cell_size, width - 16)
                        pdf.drawString(
                            text_left + 8 + cell_index * width, y, text[0] if text else ""
                        )
                    y -= step
                    # Stop above the foot rule at 58.
                    if y < 84:
                        break
            elif bullets and layout == "agenda":
                columns = _split_columns(bullets) if len(bullets) > 4 else [bullets]
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                per = max(len(column) for column in columns)
                step = min(56.0, (_H - 210) / max(per, 1))
                number = 0
                top = y
                for column_index, column in enumerate(columns):
                    left = text_left + column_index * (span + 24)
                    for position, text in enumerate(column):
                        number += 1
                        line_y = top - 20 - position * step
                        pdf.setFillColorRGB(*accent)
                        pdf.setFont(font, S(22))
                        pdf.drawString(left, line_y, f"{number:02d}")
                        pdf.setFillColorRGB(*ink)
                        pdf.setFont(font, S(18))
                        pdf.drawString(left + 52, line_y, _wrap(text, font, S(18), span - 52)[0])
                        pdf.setFillColorRGB(*hair)
                        pdf.rect(left, line_y - 14, span, 0.75, stroke=0, fill=1)
            elif bullets:
                columns = _columns_of(data, bullets, layout)
                size = S(14 if len(columns) > 1 else 16)
                step = S(20 if len(columns) > 1 else 24)
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                top = y
                for column_index, column in enumerate(columns):
                    left = text_left + column_index * (span + 24)
                    y = top
                    for text in column:
                        wrapped = _wrap(text, font, size, span - 26)
                        pdf.setFillColorRGB(*accent)
                        pdf.setFont(font, size)
                        pdf.drawString(left + 4, y, "•")
                        pdf.setFillColorRGB(*ink)
                        for offset, line in enumerate(wrapped):
                            pdf.drawString(left + 26, y - offset * step, line)
                        y -= step * len(wrapped) + 14
            elif body:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(16))
                for line in _wrap(body, font, S(16), text_width):
                    pdf.drawString(text_left, y, line)
                    y -= S(24)

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body)
            box = (_W - 260, _H - 230) if alone else (picture_span, _H - 230)
            fill = str((data.get("image") or {}).get("fit") or "") == "cover"
            if fill:
                width, height, *_ = _fill(image_bytes, box=box)
                box_width, box_height = box
                box_left = (
                    (72 if picture_left else 72 + text_width + 24)
                    if not alone
                    else (_W - box_width) / 2
                )
                box_bottom = 90
                left = box_left - (width - box_width) / 2
                bottom = box_bottom - (height - box_height) / 2
            else:
                width, height = _fit(image_bytes, box=box)
                box_width, box_height = width, height
                box_left = (
                    (72 if picture_left else 72 + text_width + 24)
                    if not alone
                    else (_W - width) / 2
                )
                box_bottom = 90 + max(0.0, (_H - 230 - height) / 2)
                left, bottom = box_left, box_bottom
            try:
                if fill:
                    pdf.saveState()
                    clip = pdf.beginPath()
                    clip.rect(box_left, box_bottom, box_width, box_height)
                    pdf.clipPath(clip, stroke=0, fill=0)
                pdf.drawImage(
                    ImageReader(io.BytesIO(image_bytes)),
                    left,
                    bottom,
                    width=width,
                    height=height,
                    mask="auto",
                )
                if fill:
                    pdf.restoreState()
            except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                if fill:
                    try:
                        pdf.restoreState()
                    except Exception:  # noqa: BLE001 — recovery only
                        pass
                log.warning("could not draw a picture into the deck pdf: %s", exc)
            else:
                if image_caption:
                    pdf.setFillColorRGB(*muted)
                    pdf.setFont(font, 11)
                    pdf.drawString(
                        box_left,
                        box_bottom - 16,
                        _wrap(image_caption, font, 11, box_width)[0],
                    )

        # Foot: logo, deck title and footer on the left, slide number on the right.
        if not cover:
            pdf.setStrokeColorRGB(*hair)
            pdf.setLineWidth(0.75)
            pdf.line(72, 58, _W - 72, 58)
            left = 72.0
            if mark:
                blob, mark_width, mark_height = mark
                try:
                    pdf.drawImage(
                        ImageReader(io.BytesIO(blob)),
                        left,
                        34,
                        width=mark_width,
                        height=mark_height,
                        mask="auto",
                    )
                except Exception as exc:  # noqa: BLE001 — a mark, not the deck
                    log.warning("could not draw the logo on a pdf slide: %s", exc)
                else:
                    left += mark_width + 10
            pdf.setFillColorRGB(0.55, 0.55, 0.55)
            pdf.setFont(font, 9)
            foot = _wrap(title, font, 9, _W - 260 - (left - 72))[0] if title else ""
            pdf.drawString(left, 40, foot)
            if footer:
                pdf.drawRightString(_W - 116, 40, _wrap(footer, font, 9, 240)[0])
            pdf.setFillColorRGB(*accent)
            pdf.rect(_W - 106, 34, 34, 22, stroke=0, fill=1)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(font, 10)
            pdf.drawCentredString(_W - 89, 41, str(index + 1))
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


__all__ = ["to_pdf", "to_pptx"]
