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
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from reportlab.lib.colors import Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services import (
    charts,
    deck,
    deck_type,
    design,
    diagram_shapes,
    fonts,
    pictures,
    slide_patterns,
)

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

#: Body-slide geometry from the shared type scale (`deck_type`), in points: the panel's
#: 400x225 units times `K`. The title starts at `_TITLE_TOP`; the body box runs from
#: `_BODY_TOP` (lower by one title line for each extra line) to `_BODY_BOTTOM`, above the
#: foot rule.
_K = deck_type.K
_TITLE_TOP = 24 * _K
_BODY_TOP = deck_type.BODY_TOP * _K
_BODY_BOTTOM = deck_type.BODY_BOTTOM * _K
_FOOT_RULE = 32 * _K


def _u(name: str) -> float:
    """A size from the shared type scale, in points."""
    return deck_type.TYPE[name]


#: Default width of a picture sharing the slide with text.
_PICTURE_SPAN = 300.0


def _has_words(data: dict) -> bool:
    """Whether the slide says anything besides its title and picture."""
    return any(
        data.get(key) for key in ("bullets", "body", "rows", "metrics", "chart", *_PAIRED)
    ) or _has_pattern_words(data, slide_patterns.BY_NAME.get(str(data.get("layout") or "")))


def _picture_span(data: dict) -> float:
    """Width of a picture sharing a slide with words, in export points. Unsized pictures
    take the large column: a photo beside three bullets is the slide's point, not an icon."""
    return {"small": 230.0, "medium": _PICTURE_SPAN, "large": 390.0}.get(
        str((data.get("image") or {}).get("size") or "large"), 390.0
    )


_EMU_PER_PT = 12700

#: (latin, East Asian) faces keyed by `design.FONTS`; see `_font`.
class _Drawn(Exception):
    """The figure was drawn as shapes; its picture is not placed."""


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
    """A visual style, matching the panel's `LOOKS`: ground, neutrals, cover, ornament, card
    style, body line height.
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
    #: Body line height; the panel's `leading`.
    leading: float = 1.6


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
        leading=1.55,
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
        leading=1.7,
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
        leading=1.7,
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
    "pastel": Look(
        bg="#f3f0fa",
        ink="#2b2540",
        muted="#6b6480",
        faint="#9a93ad",
        hair="#e3ddf0",
        tint=14,
        card="filled",
        radius=12,
        badge="circle",
        cover="wash",
        ornament="corner-circle",
        own_neutrals=True,
        leading=1.65,
    ),
    "forest": Look(
        bg="#f1f5f0",
        ink="#1f2d22",
        muted="#5c6b5e",
        faint="#8e9a90",
        hair="#d9e2da",
        tint=10,
        card="filled",
        radius=6,
        badge="square",
        cover="gradient",
        ornament="left-bar",
        own_neutrals=True,
    ),
    "slate": Look(
        bg="#eef1f5",
        ink="#1c2431",
        muted="#55617a",
        faint="#8b96ab",
        hair="#d5dbe6",
        tint=8,
        card="outlined",
        radius=2,
        badge="square",
        cover="split",
        ornament="bottom-rule",
        own_neutrals=True,
    ),
    "paper": Look(
        bg="#fbfaf6",
        ink="#2a2622",
        muted="#6b655c",
        faint="#9b948a",
        hair="#e6e1d6",
        tint=6,
        card="outlined",
        radius=0,
        badge="square",
        cover="paper",
        ornament="frame",
        own_neutrals=True,
        leading=1.7,
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
    top: float = _BODY_TOP,
    room: float = _BODY_BOTTOM - _BODY_TOP,
    scale: float = 1.0,
    measure: str | None = None,
    compact: bool = False,
) -> None:
    """Draws a paired layout: bands, tiles, steps, cards, or timeline.

    `compact`: the strip under a figure band — cards at 14/12 pt in the room that is left.

    `scale` is the slide's text scale, for exact line spacing; `measure` the `.pdf` face
    that tells how many lines a card name takes.
    """
    look = look or _LOOKS["editorial"]
    hair = hair or RGBColor(0xE6, 0xE6, 0xE6)
    if layout == "bands":
        label = 96.0
        height = min(72.0, (room - 10 * (len(pairs) - 1)) / max(len(pairs), 1))
        band = max(_u("bandMin"), min(_u("bandMax"), height / 3))
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
            paint(run, size=band, bold=True, colour=_WHITE)
            body = _textbox(
                slide,
                left=left + label + 24,
                top=y + 10,
                width=width - label - 40,
                height=height - 20,
            )
            body.vertical_anchor = MSO_ANCHOR.MIDDLE
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=band)
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
            paint(run, size=_u("tileMark"), bold=True, colour=_WHITE)
            under = _textbox(
                slide, left=item_left - 8, top=top + 32 + side, width=side + 16, height=44
            )
            under.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = under.paragraphs[0].add_run()
            run.text = name
            paint(run, size=_u("tileName"), colour=muted)
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
            paint(run, size=_u("stepBadge"), bold=True, colour=_WHITE)
            title = _textbox(slide, left=item_left, top=top + 20 + side + 12, width=span, height=36)
            run = title.paragraphs[0].add_run()
            run.text = name
            paint(run, size=_u("stepName"), bold=True)
            body = _textbox(slide, left=item_left, top=top + 20 + side + 50, width=span, height=140)
            body.paragraphs[0].line_spacing = Pt(
                _u("stepText") * scale * deck_type.LEADING["stepText"]
            )
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=_u("stepText"), colour=muted)
        return

    if layout == "cards":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        # Compact cards take what their two lines need and leave the foot of the slide clear.
        height = min((42 if compact else 100) * _K, room - (24 if compact else 10))
        name_pt = 14.0 if compact else _u("cardName")
        text_pt = 12.0 if compact else _u("cardText")
        if compact and measure:
            # Under a figure the cards grow to their longest text, so no sentence spills out.
            need = max(
                26
                + name_pt * scale * 1.3
                * max(1, len(_wrap(name, measure, name_pt * scale, span - 28)))
                + 10 + text_pt * scale * deck_type.LEADING["cardText"]
                * len(_wrap(text, measure, text_pt * scale, span - 28)) + 14
                for name, text in pairs
            )
            height = min(max(height, need), room - 24)
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
            name_lines = len(_wrap(name, measure, name_pt * scale, span - 28)) if measure else 1
            name_height = name_pt * scale * 1.3 * max(1, name_lines) + 6
            title = _textbox(
                slide, left=item_left + 14, top=top + 26, width=span - 28, height=name_height
            )
            title.paragraphs[0].line_spacing = Pt(name_pt * scale * 1.3)
            run = title.paragraphs[0].add_run()
            run.text = name
            paint(run, size=name_pt, bold=True, colour=accent)
            body = _textbox(
                slide,
                left=item_left + 14,
                top=top + 26 + name_height + 4,
                width=span - 28,
                height=height - 40 - name_height,
            )
            body.paragraphs[0].line_spacing = Pt(text_pt * scale * deck_type.LEADING["cardText"])
            run = body.paragraphs[0].add_run()
            run.text = text
            paint(run, size=text_pt)
        return

    # timeline
    axis = 128.0
    step = min(56.0, room / max(len(pairs), 1))
    line = max(_u("lineMin"), min(_u("lineMax"), step / 2.3))
    _block(slide, left=left + axis, top=top, width=1.5, height=step * len(pairs), colour=tint)
    for index, (when, what) in enumerate(pairs):
        y = top + index * step
        date = _textbox(slide, left=left, top=y, width=axis - 12, height=30)
        date.paragraphs[0].alignment = PP_ALIGN.RIGHT
        run = date.paragraphs[0].add_run()
        run.text = when
        paint(run, size=line, bold=True, colour=accent)
        _block(slide, left=left + axis - 3.5, top=y + 8, width=8, height=8, colour=accent)
        body = _textbox(slide, left=left + axis + 16, top=y, width=width - axis - 16, height=step)
        body.paragraphs[0].line_spacing = Pt(line * scale * deck_type.LEADING["line"])
        run = body.paragraphs[0].add_run()
        run.text = what
        paint(run, size=line)


def _pptx_chart(
    slide,
    chart: dict,
    *,
    accent,
    muted,
    width: float,
    faces: tuple[str, str],
    left: float = 72.0,
    top: float = _BODY_TOP,
    room: float = _BODY_BOTTOM - _BODY_TOP,
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
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(room * _EMU_PER_PT)),
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
    return any(
        slide.get(key) for key in ("bullets", "rows", "metrics", "chart", "image", *_PAIRED)
    ) or _has_pattern_words(slide, slide_patterns.BY_NAME.get(str(slide.get("layout") or "")))


def _strip_need(
    pairs: list[tuple[str, str]], layout: str, width: float, font: str, bold: str, scale: float
) -> float:
    """The height the compact card strip under a figure needs for its longest card, so the
    figure band above gives it that much and no sentence is cut."""
    if layout != "cards" or not pairs:
        return 0.0
    gap = 18.0
    span = (width - gap * (len(pairs) - 1)) / len(pairs)
    name_pt, text_pt = 14.0 * scale, 12.0 * scale
    lead = text_pt * deck_type.LEADING["cardText"]
    return 10 + max(
        26
        + name_pt * 1.3 * max(1, len(_wrap(name, bold, name_pt, span - 28)[:2]))
        + 10 + lead * len(_wrap(text, font, text_pt, span - 28)) + 14
        for name, text in pairs
    )


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


# ---------------------------------------------------------------------------------------------
# Pattern slides (`slide_patterns`): one drawing per arrangement, laid out once as a scene of
# rectangles, ovals, polygons and fitted text in page points (top-down), then painted into the
# .pptx or onto the .pdf canvas. Both files therefore share the geometry and the wrapping.

_Colour = tuple[float, float, float]

_WHITE_F: _Colour = (1.0, 1.0, 1.0)
_DARK_F: _Colour = (0.1, 0.1, 0.1)

#: SWOT quadrant colours: strengths, weaknesses, opportunities, threats.
_SWOT = (
    ("S", (0.18, 0.62, 0.42)),
    ("W", (0.86, 0.58, 0.16)),
    ("O", (0.23, 0.51, 0.96)),
    ("T", (0.85, 0.30, 0.30)),
)
#: Positive and negative panel colours for `posneg` columns.
_POSITIVE: _Colour = (0.18, 0.62, 0.42)
_NEGATIVE: _Colour = (0.85, 0.33, 0.31)
#: Column titles that mark the negative side of a `posneg` pair.
_NEGATIVE_WORDS = (
    "오해",
    "단점",
    "하지",
    "문제",
    "위험",
    "약점",
    "나쁜",
    "금지",
    "Don",
    "Myth",
    "Con",
)

#: How far a slice or series moves from the accent: (percent of accent, toward dark?).
_SHADES = ((100, False), (55, False), (68, True), (30, False), (45, True), (78, False))


def _shade(accent: _Colour, index: int) -> _Colour:
    """The `index`-th distinct shade of the accent for slices, series and steps."""
    percent, dark = _SHADES[index % len(_SHADES)]
    return _mix_floats(accent, percent, onto=_DARK_F if dark else _WHITE_F)


def _floats(colour) -> _Colour:
    """An RGBColor (0–255) as reportlab floats."""
    return (colour[0] / 255, colour[1] / 255, colour[2] / 255)


def _rgb_of(colour: _Colour) -> RGBColor:
    return RGBColor(*(max(0, min(255, round(c * 255))) for c in colour))


def _fit_text(
    text: str, face: str, size: float, width: float, height: float, leading: float, floor: float
) -> tuple[float, list[str], bool]:
    """`(size, lines, cut)`: the largest size from `size` down to `floor` at which `text`
    wraps into `height`; at the floor the lines that fit, the last one ending in an ellipsis.

    The measured width keeps a margin, since PowerPoint's faces run a little wider than the
    PDF's.
    """
    measure = max(1.0, width * 0.94)
    current = size
    while True:
        lines = _wrap(text, face, current, measure) or [""]
        if len(lines) * current * leading <= height + 0.5 or current <= floor:
            break
        current = max(floor, current - (2 if current > 16 else 1))
    fits = max(1, int((height + 0.5) // (current * leading)))
    if len(lines) <= fits:
        return current, lines, False
    kept = lines[:fits]
    kept[-1] = kept[-1][: max(1, len(kept[-1]) - 1)].rstrip() + "…"
    return current, kept, True


@dataclass
class _Text:
    x: float
    y: float
    w: float
    h: float
    text: str
    lines: list[str]
    size: float
    bold: bool
    colour: _Colour
    align: str
    valign: str
    leading: float


class _Scene:
    """Shapes and fitted text in page points, top-down, for either exporter."""

    def __init__(self, *, font: str, bold: str, scale: float, look: Look, ground: _Colour):
        self.font, self.bold_face, self.scale = font, bold, scale
        self.look, self.ground = look, ground
        self.items: list[tuple] = []

    def rect(self, x, y, w, h, *, fill=None, line=None, width=0.75, radius=0.0) -> None:
        self.items.append(("rect", x, y, w, h, fill, line, width, radius))

    def oval(self, x, y, w, h, *, fill=None, line=None, width=0.75) -> None:
        self.items.append(("oval", x, y, w, h, fill, line, width))

    def poly(self, points, *, fill=None, line=None, width=0.75, closed=True) -> None:
        self.items.append(("poly", [tuple(p) for p in points], fill, line, width, closed))

    def panel(self, x, y, w, h, *, fill, line) -> None:
        """A card in the look's manner: filled, or outlined on the ground."""
        radius = self.look.radius * 1.2 if self.look.radius else 0.0
        if self.look.card == "outlined":
            self.rect(x, y, w, h, fill=self.ground, line=line, radius=radius)
        else:
            self.rect(x, y, w, h, fill=fill, radius=radius)

    def badge(self, x, y, side, *, fill) -> None:
        """The look's mark: a disc or a square."""
        if self.look.badge == "circle":
            self.oval(x, y, side, side, fill=fill)
        else:
            self.rect(x, y, side, side, fill=fill)

    def size(self, size: float) -> float:
        """A type-scale size at the slide's text scale, never under the floor."""
        return max(float(deck_type.FLOOR_PT), size * self.scale)

    def text(
        self,
        text: str,
        x: float,
        y: float,
        w: float,
        h: float,
        size: float,
        *,
        colour: _Colour,
        bold: bool = False,
        align: str = "left",
        valign: str = "top",
        leading: float = 1.3,
        fixed: bool = False,
        floor: float | None = None,
    ) -> float:
        """Text fitted into the box; returns the height it takes. `fixed` sizes skip the
        slide's text scale (marks inside badges)."""
        text = str(text or "").strip()
        if not text or w <= 4 or h <= 4:
            return 0.0
        start = size if fixed else self.size(size)
        least = min(start, float(deck_type.FLOOR_PT) if floor is None else floor)
        face = self.bold_face if bold else self.font
        fitted, lines, cut = _fit_text(text, face, start, w, h, leading, least)
        shown = text
        if cut:
            shown = text[: max(1, sum(len(line) for line in lines) - 1)].rstrip() + "…"
        self.items.append(
            ("text", _Text(x, y, w, h, shown, lines, fitted, bold, colour, align, valign, leading))
        )
        return len(lines) * fitted * leading

    def bullets(
        self,
        items: list[str],
        x: float,
        y: float,
        w: float,
        h: float,
        size: float,
        *,
        colour: _Colour,
        dot: _Colour,
        leading: float = 1.35,
    ) -> None:
        """A list of short items with a dot each, shrunk together until all fit."""
        current = self.size(size)
        least = min(current, float(deck_type.FLOOR_PT))
        while True:
            wrapped = [_wrap(item, self.font, current, (w - 18) * 0.94) or [""] for item in items]
            total = sum(len(lines) for lines in wrapped) * current * leading
            total += max(0, len(items) - 1) * current * 0.55
            if total <= h + 0.5 or current <= least:
                break
            current = max(least, current - (2 if current > 16 else 1))
        cursor = y
        for item, lines in zip(items, wrapped, strict=True):
            line_height = current * leading
            if cursor + line_height > y + h + 0.5:
                break
            need = min(len(lines) * line_height, y + h - cursor)
            self.oval(x + 2, cursor + line_height / 2 - 3, 6, 6, fill=dot)
            self.text(
                item,
                x + 18,
                cursor,
                w - 18,
                need,
                current,
                colour=colour,
                leading=leading,
                fixed=True,
                floor=current,
            )
            cursor += len(lines) * line_height + current * 0.55

    def arrow(self, x1, y, x2, *, colour, head=9.0, width=2.5) -> None:
        """A horizontal arrow from `x1` to `x2` at height `y`."""
        self.poly([(x1, y), (x2 - head, y)], line=colour, width=width, closed=False)
        self.poly(
            [(x2 - head - 1, y - head * 0.7), (x2, y), (x2 - head - 1, y + head * 0.7)], fill=colour
        )


def _pattern_items(data: dict) -> list[tuple[str, str]]:
    """`items` as `(left, right)`; the right may be empty (a checklist entry)."""
    out: list[tuple[str, str]] = []
    for item in data.get("items") or []:
        if isinstance(item, dict):
            item = list(item.values())
        if not isinstance(item, (list, tuple)) or not item:
            continue
        left = str(item[0]).strip()
        right = str(item[1]).strip() if len(item) > 1 else ""
        if left or right:
            out.append((left, right))
    return out


def _pattern_columns(data: dict) -> list[tuple[str, list[str]]]:
    """`columns` as `(title, items)`; the pattern shape is `{"title", "items"}`."""
    out: list[tuple[str, list[str]]] = []
    for column in data.get("columns") or []:
        if not isinstance(column, dict):
            continue
        title = str(column.get("title") or "").strip()
        items = [str(i).strip() for i in (column.get("items") or []) if str(i).strip()]
        if title or items:
            out.append((title, items))
    return out


def _pattern_metrics(data: dict) -> list[tuple[str, str]]:
    return [
        (str(pair[0]), str(pair[1]))
        for pair in (data.get("metrics") or [])
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    ]


def _has_pattern_words(data: dict, pattern) -> bool:
    """Whether a pattern slide carries the field its shape is drawn from."""
    if pattern is None:
        return False
    if pattern.shape == "pairs":
        return bool(_pattern_items(data))
    if pattern.shape == "columns":
        return bool(_pattern_columns(data))
    return False


#: Chart kinds the pattern slides draw beyond `bar` and `line`.
_MORE_CHARTS = ("pie", "donut", "hbar", "stacked")


def _pattern_chart_of(data: dict, pattern) -> dict | None:
    """`_chart_of` with the chart's own kind kept when a pattern draws it, else the pattern's."""
    chart = _chart_of(data)
    if chart is None:
        return None
    raw = str((data.get("chart") or {}).get("kind") or "")
    kind = raw if raw in (*_MORE_CHARTS, "bar", "line") else str(pattern.params.get("kind"))
    chart["kind"] = kind if kind in (*_MORE_CHARTS, "bar", "line") else "bar"
    return chart


def _percent(value: str) -> float:
    """`"72%"` → 72; a bare number above 1 is read as a percentage, below as a fraction."""
    found = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(",", ""))
    if not found:
        return 0.0
    number = float(found.group().replace(",", "."))
    if "%" not in value and 0 < number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def _star(cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    import math

    points = []
    for index in range(10):
        r = radius if index % 2 == 0 else radius * 0.45
        angle = -math.pi / 2 + index * math.pi / 5
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _pattern_scene(
    pattern,
    data: dict,
    *,
    left: float,
    top: float,
    width: float,
    room: float,
    accent: _Colour,
    ink: _Colour,
    muted: _Colour,
    tint: _Colour,
    hair: _Colour,
    ground: _Colour,
    look: Look,
    font: str,
    bold: str,
    scale: float,
) -> _Scene | None:
    """The pattern slide's body laid out in the body box, or None when its field is empty."""
    import math

    sc = _Scene(font=font, bold=bold, scale=scale, look=look, ground=ground)
    render, params = pattern.render, pattern.params
    L, T, W, R = left, top, width, room
    white = _WHITE_F

    if pattern.shape == "text":
        body = str(data.get("body") or "").strip()
        if not body:
            return None
        mode = params.get("mode")
        if mode == "definition":
            term = str(data.get("title") or "").strip()
            sc.rect(L, T + 8, 6, R - 24, fill=accent)
            used = sc.text(
                term, L + 28, T + 8, W - 28, 44, _u("bodyNarrow") + 4, colour=accent, bold=True
            )
            sc.text(
                body,
                L + 28,
                T + 8 + used + 14,
                W - 28,
                R - used - 46,
                _u("quote"),
                colour=ink,
                leading=1.45,
            )
        elif mode == "hypothesis":
            height = min(R - 16, 210.0)
            sc.panel(L, T + 8, W, height, fill=tint, line=hair)
            sc.rect(L + 28, T + 34, 84, 40, fill=accent, radius=6 if look.radius else 0)
            sc.text(
                "가설",
                L + 28,
                T + 34,
                84,
                40,
                18,
                colour=white,
                bold=True,
                align="center",
                valign="middle",
                fixed=True,
            )
            sc.text(
                body,
                L + 136,
                T + 30,
                W - 164,
                height - 44,
                _u("quote"),
                colour=ink,
                bold=True,
                valign="middle",
                leading=1.4,
                floor=14,
            )
        else:  # question
            mark = T + R * 0.12
            sc.rect(L + (W - 62) / 2, mark, 62, 5, fill=accent)
            sc.text(
                body,
                L + 40,
                mark + 22,
                W - 80,
                R * 0.72,
                _u("statement"),
                colour=ink,
                bold=True,
                align="center",
                valign="middle",
                leading=1.35,
                floor=16,
            )
        return sc

    if pattern.shape == "metrics":
        metrics = _pattern_metrics(data)
        if not metrics:
            return None
        mode = params.get("mode")
        if mode == "compare" and len(metrics) >= 2:
            gap = 110.0
            span = (W - gap) / 2
            height = min(R - 16, 220.0)
            for position, (figure, label) in enumerate(metrics[:2]):
                x = L + position * (span + gap)
                colour = muted if position == 0 else accent
                sc.panel(x, T + 8, span, height, fill=tint if position else ground, line=hair)
                sc.rect(x, T + 8, span, 5, fill=colour)
                sc.text(
                    figure,
                    x + 20,
                    T + 30,
                    span - 40,
                    height * 0.5,
                    _u("bigNumber"),
                    colour=colour,
                    bold=True,
                    align="center",
                    valign="middle",
                    leading=1.15,
                    floor=24,
                )
                sc.text(
                    label,
                    x + 20,
                    T + 30 + height * 0.5 + 6,
                    span - 40,
                    height * 0.5 - 50,
                    _u("metricLabel") + 2,
                    colour=muted,
                    align="center",
                )
            sc.arrow(
                L + span + 24,
                T + 8 + height / 2,
                L + span + gap - 24,
                colour=accent,
                head=14,
                width=4,
            )
            return sc
        if mode == "bars":
            count = len(metrics)
            step = min(R / count, 66.0)
            label_w = W * 0.3
            value_size = sc.size(_u("cardName") + 4)
            widest = max(pdfmetrics.stringWidth(f, bold, value_size) for f, _ in metrics)
            value_w = min(max(96.0, widest / 0.92 + 4), W * 0.25)
            track_x = L + label_w + 18
            track_w = W - label_w - 18 - value_w - 14
            for position, (figure, label) in enumerate(metrics):
                y = T + position * step
                bar = 18.0
                mid = y + (step - 8) / 2
                sc.text(
                    label,
                    L,
                    y,
                    label_w,
                    step - 8,
                    _u("cardName"),
                    colour=ink,
                    bold=True,
                    valign="middle",
                )
                sc.rect(
                    track_x, mid - bar / 2, track_w, bar, fill=_mix_floats(accent, 14, onto=ground)
                )
                share = _percent(figure) / 100
                if share > 0:
                    sc.rect(track_x, mid - bar / 2, max(2.0, track_w * share), bar, fill=accent)
                sc.text(
                    figure,
                    track_x + track_w + 14,
                    y,
                    value_w,
                    step - 8,
                    _u("cardName") + 4,
                    colour=accent,
                    bold=True,
                    valign="middle",
                )
            return sc
        # grid
        count = len(metrics)
        cols = count if count <= 3 else 2 if count == 4 else 3
        rows = math.ceil(count / cols)
        gap = 18.0
        span = (W - gap * (cols - 1)) / cols
        height = min((R - 8 - gap * (rows - 1)) / rows, 150.0)
        for position, (figure, label) in enumerate(metrics):
            row, col = divmod(position, cols)
            x = L + col * (span + gap)
            y = T + 8 + row * (height + gap)
            sc.panel(x, y, span, height, fill=tint, line=hair)
            sc.rect(x, y, span, 5, fill=accent)
            used = sc.text(
                figure,
                x + 16,
                y + 16,
                span - 32,
                height * 0.55,
                _u("metric"),
                colour=accent,
                bold=True,
                leading=1.15,
                floor=20,
            )
            sc.text(
                label,
                x + 16,
                y + 16 + used + 4,
                span - 32,
                height - used - 28,
                _u("metricLabel"),
                colour=muted,
            )
        return sc

    if pattern.shape == "columns":
        columns = _pattern_columns(data)
        if not columns:
            return None
        tone = params.get("tone")
        count = len(columns)
        gap = {"vs": 84.0, "beforeafter": 76.0}.get(tone, 20.0) if count == 2 else 20.0
        span = (W - gap * (count - 1)) / count
        height = R - 10
        head = 48.0
        for position, (name, items) in enumerate(columns):
            x = L + position * (span + gap)
            if tone == "posneg":
                # The negative panel is whichever one its title marks so (오해·단점·하지 말 것),
                # else the second.
                negative = any(word in name for word in _NEGATIVE_WORDS) or (
                    position == 1 and not any(word in columns[0][0] for word in _NEGATIVE_WORDS)
                )
                colour = _NEGATIVE if negative else _POSITIVE
            elif tone == "beforeafter":
                colour = muted if position == 0 else accent
            elif tone == "vs":
                colour = accent if position == 0 else _mix_floats(accent, 60, onto=_DARK_F)
            else:
                colour = accent
            radius = look.radius * 1.2 if look.radius else 0.0
            sc.rect(x, T + 6, span, head, fill=colour, radius=radius)
            sc.text(
                name,
                x + 12,
                T + 6,
                span - 24,
                head,
                _u("cardName"),
                colour=white,
                bold=True,
                align="center",
                valign="middle",
                leading=1.2,
            )
            body_fill = _mix_floats(colour, max(look.tint, 7), onto=ground)
            sc.panel(x, T + 6 + head + 6, span, height - head - 6, fill=body_fill, line=hair)
            sc.bullets(
                items,
                x + 18,
                T + 6 + head + 22,
                span - 36,
                height - head - 40,
                _u("cardText") + 2,
                colour=ink,
                dot=colour,
            )
        if count == 2 and tone == "vs":
            side = 56.0
            cx = L + span + gap / 2
            cy = T + 6 + height / 2
            sc.oval(cx - side / 2, cy - side / 2, side, side, fill=accent, line=ground, width=3)
            sc.text(
                "VS",
                cx - side / 2,
                cy - side / 2,
                side,
                side,
                18,
                colour=white,
                bold=True,
                align="center",
                valign="middle",
                fixed=True,
            )
        elif count == 2 and tone == "beforeafter":
            sc.arrow(
                L + span + 16,
                T + 6 + height / 2,
                L + span + gap - 16,
                colour=accent,
                head=14,
                width=4,
            )
        return sc

    # pairs
    pairs = _pattern_items(data)
    if not pairs:
        return None
    count = len(pairs)

    if render == "grid":
        style = params.get("style") or "card"
        cols = max(1, min(int(params.get("cols") or 3), count))
        rows = math.ceil(count / cols)
        gap = 18.0
        span = (W - gap * (cols - 1)) / cols
        cap = {"card": 200.0, "feature": 150.0, "person": 170.0}.get(style, 200.0)
        height = min((R - 8 - gap * (rows - 1)) / rows, cap)
        for position, (name, text) in enumerate(pairs):
            row, col = divmod(position, cols)
            in_row = min(cols, count - row * cols)
            x = L + (cols - in_row) * (span + gap) / 2 + col * (span + gap)
            y = T + 8 + row * (height + gap)
            sc.panel(x, y, span, height, fill=tint, line=hair)
            if style == "person":
                side = min(60.0, height * 0.4)
                sc.oval(x + (span - side) / 2, y + 14, side, side, fill=accent)
                sc.text(
                    name[:1],
                    x + (span - side) / 2,
                    y + 14,
                    side,
                    side,
                    side * 0.45,
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    fixed=True,
                )
                below = y + 14 + side + 10
                used = sc.text(
                    name,
                    x + 10,
                    below,
                    span - 20,
                    30,
                    _u("cardName"),
                    colour=ink,
                    bold=True,
                    align="center",
                )
                sc.text(
                    text,
                    x + 10,
                    below + used + 4,
                    span - 20,
                    y + height - below - used - 10,
                    _u("cardText"),
                    colour=muted,
                    align="center",
                )
            elif style == "feature":
                side = 34.0
                sc.badge(x + 16, y + 16, side, fill=accent)
                sc.text(
                    f"{position + 1:02d}",
                    x + 16,
                    y + 16,
                    side,
                    side,
                    14,
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    fixed=True,
                )
                sc.text(
                    name,
                    x + 16 + side + 12,
                    y + 16,
                    span - 44 - side,
                    side,
                    _u("cardName"),
                    colour=ink,
                    bold=True,
                    valign="middle",
                    leading=1.2,
                )
                sc.text(
                    text,
                    x + 16,
                    y + 16 + side + 12,
                    span - 32,
                    height - side - 40,
                    _u("cardText"),
                    colour=muted,
                    leading=1.45,
                )
            else:
                sc.rect(x, y, span, 5, fill=accent)
                used = sc.text(
                    name,
                    x + 16,
                    y + 20,
                    span - 32,
                    min(64.0, height * 0.4),
                    _u("cardName") + (4 if cols == 2 else 0),
                    colour=accent,
                    bold=True,
                )
                sc.text(
                    text,
                    x + 16,
                    y + 20 + used + 8,
                    span - 32,
                    height - used - 40,
                    _u("cardText") + (2 if cols == 2 else 0),
                    colour=ink,
                    leading=1.5,
                )
        return sc

    if render == "list":
        marker = params.get("marker") or "number"
        stacked = marker in ("qa", "ref")
        step = min(R / count, 92.0 if stacked else 66.0)
        column = 48.0
        for position, (name, text) in enumerate(pairs):
            y = T + position * step
            inner = step - 10
            mark = 28.0
            mark_y = y + 4 if stacked else y + (inner - mark) / 2
            if marker == "number":
                sc.badge(L, mark_y, mark, fill=accent)
                sc.text(
                    str(position + 1),
                    L,
                    mark_y,
                    mark,
                    mark,
                    14,
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    fixed=True,
                )
            elif marker == "check":
                sc.rect(
                    L + 2,
                    mark_y + 2,
                    mark - 4,
                    mark - 4,
                    line=accent,
                    width=1.75,
                    radius=4 if look.radius else 0,
                )
                sc.poly(
                    [(L + 8, mark_y + 14), (L + 12.5, mark_y + 19), (L + 21, mark_y + 9)],
                    line=accent,
                    width=2.5,
                    closed=False,
                )
            elif marker == "qa":
                sc.badge(L, mark_y, mark, fill=accent)
                sc.text(
                    "Q",
                    L,
                    mark_y,
                    mark,
                    mark,
                    14,
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    fixed=True,
                )
            elif marker == "term":
                sc.rect(L + 10, y + 6, 5, inner - 12, fill=accent)
            elif marker == "star":
                sc.poly(_star(L + mark / 2, mark_y + mark / 2, mark / 2), fill=accent)
            elif marker == "target":
                sc.oval(L + 1, mark_y + 1, mark - 2, mark - 2, line=accent, width=2)
                sc.oval(L + 8, mark_y + 8, mark - 16, mark - 16, line=accent, width=2)
                sc.oval(L + mark / 2 - 2.5, mark_y + mark / 2 - 2.5, 5, 5, fill=accent)
            else:  # ref
                sc.text(
                    f"[{position + 1}]",
                    L,
                    mark_y,
                    column - 6,
                    mark,
                    14,
                    colour=accent,
                    bold=True,
                    valign="middle",
                    fixed=True,
                )
            x = L + column
            if stacked:
                used = sc.text(
                    name, x, y + 4, W - column, inner * 0.5, _u("cardName"), colour=ink, bold=True
                )
                answer_x = x
                if marker == "qa" and text:
                    sc.text(
                        "A", x, y + 4 + used + 4, 20, 24, 14, colour=accent, bold=True, fixed=True
                    )
                    answer_x = x + 22
                sc.text(
                    text,
                    answer_x,
                    y + 4 + used + 4,
                    W - (answer_x - L),
                    inner - used - 8,
                    _u("cardText"),
                    colour=muted,
                )
            elif text:
                split = (W - column) * 0.36
                sc.text(
                    name, x, y, split, inner, _u("cardName"), colour=ink, bold=True, valign="middle"
                )
                sc.text(
                    text,
                    x + split + 16,
                    y,
                    W - column - split - 16,
                    inner,
                    _u("cardText"),
                    colour=muted,
                    valign="middle",
                )
            else:
                sc.text(
                    name,
                    x,
                    y,
                    W - column,
                    inner,
                    _u("cardName"),
                    colour=ink,
                    bold=True,
                    valign="middle",
                )
            if position < count - 1:
                sc.rect(L, y + step - 3, W, 0.75, fill=hair)
        return sc

    if render == "flow":
        style = params.get("style") or "arrow"
        if style == "chevron":
            depth, gap, height = 24.0, 4.0, 70.0
            span = (W + (count - 1) * (depth - gap)) / count
            for position, (name, text) in enumerate(pairs):
                x = L + position * (span - depth + gap)
                y = T + 14
                points = [
                    (x, y),
                    (x + span - depth, y),
                    (x + span, y + height / 2),
                    (x + span - depth, y + height),
                    (x, y + height),
                ]
                if position:
                    points.append((x + depth, y + height / 2))
                shade = _mix_floats(accent, 100 - 35 * position / max(1, count - 1), onto=ground)
                sc.poly(points, fill=shade)
                inset = depth + 4 if position else 14
                sc.text(
                    name,
                    x + inset,
                    y,
                    span - depth - inset - 2,
                    height,
                    _u("stepName"),
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    leading=1.2,
                )
                sc.text(
                    text,
                    x + (depth if position else 0),
                    y + height + 16,
                    span - depth,
                    R - height - 34,
                    _u("stepText"),
                    colour=ink,
                    align="center",
                    leading=1.45,
                )
            return sc
        if style == "phase":
            gap = 12.0
            span = (W - gap * (count - 1)) / count
            label_h = 38.0
            band_y = T + 6 + label_h + 8
            for position, (when, text) in enumerate(pairs):
                x = L + position * (span + gap)
                shade = _mix_floats(accent, 100 - 40 * position / max(1, count - 1), onto=ground)
                sc.text(
                    when,
                    x,
                    T + 6,
                    span,
                    label_h,
                    _u("stepName") + 2,
                    colour=accent,
                    bold=True,
                    valign="bottom",
                    leading=1.2,
                )
                sc.rect(x, band_y, span, 12, fill=shade)
                sc.oval(x - 2, band_y - 5, 22, 22, fill=shade, line=ground, width=3)
                card_y = band_y + 30
                sc.panel(x, card_y, span, T + R - card_y - 6, fill=tint, line=hair)
                sc.text(
                    text,
                    x + 14,
                    card_y + 14,
                    span - 28,
                    T + R - card_y - 34,
                    _u("stepText"),
                    colour=ink,
                    leading=1.45,
                )
            return sc
        # arrow
        gap = 46.0
        span = (W - gap * (count - 1)) / count
        height = min(R - 20, 200.0)
        y = T + 10
        for position, (name, text) in enumerate(pairs):
            x = L + position * (span + gap)
            sc.panel(x, y, span, height, fill=tint, line=hair)
            sc.rect(x, y, span, 5, fill=accent)
            used = sc.text(
                name,
                x + 14,
                y + 20,
                span - 28,
                56,
                _u("stepName"),
                colour=accent,
                bold=True,
                align="center",
                leading=1.2,
            )
            sc.text(
                text,
                x + 14,
                y + 20 + used + 10,
                span - 28,
                height - used - 44,
                _u("stepText"),
                colour=ink,
                align="center",
                leading=1.45,
            )
            if position < count - 1:
                sc.arrow(x + span + 8, y + height / 2, x + span + gap - 8, colour=accent)
        return sc

    if render == "vflow":
        axis = min(170.0, W * 0.22)
        step = min(R / count, 70.0)
        size = 18.0
        centre = 12.0
        if count > 1:
            sc.rect(L + axis - 1, T + centre, 2, step * (count - 1), fill=tint)
        for position, (when, what) in enumerate(pairs):
            y = T + position * step
            sc.text(
                when,
                L,
                y,
                axis - 24,
                step - 6,
                size,
                colour=accent,
                bold=True,
                align="right",
                leading=1.3,
            )
            sc.oval(L + axis - 8, y + centre - 8, 16, 16, fill=accent, line=ground, width=3)
            sc.text(what, L + axis + 24, y, W - axis - 24, step - 6, size, colour=ink, leading=1.3)
        return sc

    if render == "stack":
        funnel = params.get("shape") == "funnel"
        figure = min(W * 0.52, 440.0)
        gap = 6.0
        height = min(66.0, (R - 8 - gap * (count - 1)) / count)
        total = count * height + gap * (count - 1)
        y0 = T + 6
        cx = L + figure / 2

        def span_at(offset: float) -> float:
            t = offset / total
            return figure * ((1 - 0.6 * t) if funnel else (0.24 + 0.76 * t))

        text_x = L + figure + 36
        for position, (name, text) in enumerate(pairs):
            y = y0 + position * (height + gap)
            upper = span_at(y - y0)
            lower = span_at(y - y0 + height)
            sc.poly(
                [
                    (cx - upper / 2, y),
                    (cx + upper / 2, y),
                    (cx + lower / 2, y + height),
                    (cx - lower / 2, y + height),
                ],
                fill=_mix_floats(accent, 100 - 45 * position / max(1, count - 1), onto=ground),
            )
            narrow = min(upper, lower)
            sc.text(
                name,
                cx - narrow / 2 + 8,
                y,
                narrow - 16,
                height,
                _u("cardName"),
                colour=white,
                bold=True,
                align="center",
                valign="middle",
                leading=1.15,
            )
            edge = cx + max(upper, lower) / 2
            sc.rect(edge + 6, y + height / 2, text_x - edge - 14, 0.75, fill=hair)
            sc.text(
                text,
                text_x,
                y,
                L + W - text_x,
                height,
                _u("cardText"),
                colour=ink,
                valign="middle",
                leading=1.35,
            )
        return sc

    if render == "cycle":
        node_w = 190.0 if count <= 4 else 160.0
        node_h = 76.0
        cx, cy = L + W / 2, T + R / 2
        ry = max(40.0, R / 2 - node_h / 2 - 4)
        rx = min(W / 2 - node_w / 2 - 4, ry * 2.1)
        sc.oval(
            cx - rx, cy - ry, rx * 2, ry * 2, line=_mix_floats(accent, 40, onto=ground), width=3
        )
        for position in range(count):
            angle = -math.pi / 2 + 2 * math.pi * (position + 0.5) / count
            px, py = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
            dx, dy = -rx * math.sin(angle), ry * math.cos(angle)
            norm = math.hypot(dx, dy) or 1.0
            dx, dy = dx / norm, dy / norm
            sc.poly(
                [
                    (px + dx * 9, py + dy * 9),
                    (px - dx * 6 - dy * 8, py - dy * 6 + dx * 8),
                    (px - dx * 6 + dy * 8, py - dy * 6 - dx * 8),
                ],
                fill=accent,
            )
        for position, (name, text) in enumerate(pairs):
            angle = -math.pi / 2 + 2 * math.pi * position / count
            px, py = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
            x, y = px - node_w / 2, py - node_h / 2
            # Opaque even in an outlined look, so the ring passes behind the node.
            sc.rect(
                x,
                y,
                node_w,
                node_h,
                fill=tint,
                line=hair,
                radius=look.radius * 1.2 if look.radius else 0.0,
            )
            sc.rect(x, y, 5, node_h, fill=accent)
            used = sc.text(
                name,
                x + 14,
                y + 8,
                node_w - 22,
                28,
                _u("cardName") - 2,
                colour=accent,
                bold=True,
                align="center",
                leading=1.2,
            )
            sc.text(
                text,
                x + 14,
                y + 10 + used,
                node_w - 22,
                node_h - used - 16,
                _u("cardText") - 2,
                colour=ink,
                align="center",
                leading=1.3,
            )
        return sc

    if render == "quad":
        swot = params.get("mode") == "swot"
        gap = 14.0
        span = (W - gap) / 2
        height = (R - 6 - gap) / 2
        for position, (name, text) in enumerate(pairs[:4]):
            row, col = divmod(position, 2)
            x = L + col * (span + gap)
            y = T + 6 + row * (height + gap)
            if swot:
                letter, colour = _SWOT[position]
                sc.panel(
                    x,
                    y,
                    span,
                    height,
                    fill=_mix_floats(colour, 11, onto=ground),
                    line=_mix_floats(colour, 45, onto=ground),
                )
                side = 34.0
                sc.badge(x + 14, y + 14, side, fill=colour)
                sc.text(
                    letter,
                    x + 14,
                    y + 14,
                    side,
                    side,
                    18,
                    colour=white,
                    bold=True,
                    align="center",
                    valign="middle",
                    fixed=True,
                )
                sc.text(
                    name,
                    x + 14 + side + 12,
                    y + 14,
                    span - side - 40,
                    side,
                    _u("cardName"),
                    colour=colour,
                    bold=True,
                    valign="middle",
                )
                sc.text(
                    text,
                    x + 16,
                    y + 14 + side + 10,
                    span - 32,
                    height - side - 34,
                    _u("cardText"),
                    colour=ink,
                    leading=1.45,
                )
            else:
                sc.panel(x, y, span, height, fill=tint, line=hair)
                sc.rect(x, y, 5, height, fill=accent)
                used = sc.text(
                    name, x + 22, y + 14, span - 38, 32, _u("cardName"), colour=accent, bold=True
                )
                sc.text(
                    text,
                    x + 22,
                    y + 14 + used + 6,
                    span - 38,
                    height - used - 32,
                    _u("cardText"),
                    colour=ink,
                    leading=1.45,
                )
        return sc

    return None


def _pptx_scene(slide, scene: _Scene, faces: tuple[str, str]) -> None:
    """Paints a scene as shapes and textboxes."""
    from pptx.enum.text import MSO_AUTO_SIZE

    def outline(shape, fill, line, width) -> None:
        if fill is not None:
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb_of(fill)
        else:
            shape.fill.background()
        if line is not None:
            shape.line.color.rgb = _rgb_of(line)
            shape.line.width = Emu(int(width * _EMU_PER_PT))
        else:
            shape.line.fill.background()
        shape.shadow.inherit = False

    for item in scene.items:
        kind = item[0]
        if kind == "rect":
            _, x, y, w, h, fill, line, width, radius = item
            shape = _shape(
                slide,
                MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                left=x,
                top=y,
                width=w,
                height=h,
            )
            if radius:
                shape.adjustments[0] = min(0.5, (radius * 2) / max(1.0, min(w, h)))
            outline(shape, fill, line, width)
        elif kind == "oval":
            _, x, y, w, h, fill, line, width = item
            outline(
                _shape(slide, MSO_SHAPE.OVAL, left=x, top=y, width=w, height=h), fill, line, width
            )
        elif kind == "poly":
            _, points, fill, line, width, closed = item
            vertices = [(int(px * _EMU_PER_PT), int(py * _EMU_PER_PT)) for px, py in points]
            builder = slide.shapes.build_freeform(*vertices[0], scale=1.0)
            builder.add_line_segments(vertices[1:], close=closed)
            outline(builder.convert_to_shape(), fill, line, width)
        else:
            text: _Text = item[1]
            frame = _textbox(slide, left=text.x, top=text.y, width=text.w, height=text.h)
            frame.margin_left = frame.margin_right = 0
            frame.margin_top = frame.margin_bottom = 0
            frame.auto_size = MSO_AUTO_SIZE.NONE
            frame.vertical_anchor = {
                "middle": MSO_ANCHOR.MIDDLE,
                "bottom": MSO_ANCHOR.BOTTOM,
            }.get(text.valign, MSO_ANCHOR.TOP)
            paragraph = frame.paragraphs[0]
            paragraph.alignment = {"center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}.get(
                text.align, PP_ALIGN.LEFT
            )
            paragraph.line_spacing = Pt(text.size * text.leading)
            run = paragraph.add_run()
            run.text = text.text
            _font(
                run,
                size=round(text.size * 2) / 2,
                bold=text.bold,
                colour=_rgb_of(text.colour),
                faces=faces,
            )


def _pdf_scene(pdf, scene: _Scene) -> None:
    """Paints a scene onto the canvas; reportlab's y runs up, so every top is flipped."""
    for item in scene.items:
        kind = item[0]
        if kind in ("rect", "oval"):
            x, y, w, h, fill, line, width = item[1:8]
            if fill is not None:
                pdf.setFillColorRGB(*fill)
            if line is not None:
                pdf.setStrokeColorRGB(*line)
                pdf.setLineWidth(width)
            stroke, filled = int(line is not None), int(fill is not None)
            if kind == "oval":
                pdf.ellipse(x, _H - y - h, x + w, _H - y, stroke=stroke, fill=filled)
            elif item[8]:
                pdf.roundRect(x, _H - y - h, w, h, item[8], stroke=stroke, fill=filled)
            else:
                pdf.rect(x, _H - y - h, w, h, stroke=stroke, fill=filled)
        elif kind == "poly":
            _, points, fill, line, width, closed = item
            path = pdf.beginPath()
            for position, (px, py) in enumerate(points):
                (path.moveTo if position == 0 else path.lineTo)(px, _H - py)
            if closed:
                path.close()
            if fill is not None:
                pdf.setFillColorRGB(*fill)
            if line is not None:
                pdf.setStrokeColorRGB(*line)
                pdf.setLineWidth(width)
                pdf.setLineJoin(1)
                pdf.setLineCap(1)
            pdf.drawPath(path, stroke=int(line is not None), fill=int(fill is not None))
            pdf.setLineJoin(0)
            pdf.setLineCap(0)
        else:
            text: _Text = item[1]
            used = len(text.lines) * text.size * text.leading
            block = text.y
            if text.valign == "middle":
                block += (text.h - used) / 2
            elif text.valign == "bottom":
                block += text.h - used
            pdf.setFillColorRGB(*text.colour)
            pdf.setFont(scene.bold_face if text.bold else scene.font, text.size)
            for position, line in enumerate(text.lines):
                baseline = _H - (
                    block + text.size * text.leading * (position + 0.5) + text.size * 0.35
                )
                if text.align == "center":
                    pdf.drawCentredString(text.x + text.w / 2, baseline, line)
                elif text.align == "right":
                    pdf.drawRightString(text.x + text.w, baseline, line)
                else:
                    pdf.drawString(text.x, baseline, line)


def _pptx_pattern(
    slide,
    pattern,
    data: dict,
    *,
    left: float,
    top: float,
    width: float,
    room: float,
    accent: RGBColor,
    ink: RGBColor,
    muted: RGBColor,
    tint: RGBColor,
    hair: RGBColor,
    ground: RGBColor,
    look: Look,
    faces: tuple[str, str],
    font: str,
    bold: str,
    scale: float,
) -> bool:
    """Draws a pattern slide's body by its arrangement; False when it has nothing to draw."""
    scene = _pattern_scene(
        pattern,
        data,
        left=left,
        top=top,
        width=width,
        room=room,
        accent=_floats(accent),
        ink=_floats(ink),
        muted=_floats(muted),
        tint=_floats(tint),
        hair=_floats(hair),
        ground=_floats(ground),
        look=look,
        font=font,
        bold=bold,
        scale=scale,
    )
    if scene is None:
        return False
    _pptx_scene(slide, scene, faces)
    return True


def _pdf_pattern(
    pdf,
    pattern,
    data: dict,
    *,
    left: float,
    top: float,
    width: float,
    room: float,
    accent: _Colour,
    ink: _Colour,
    muted: _Colour,
    tint: _Colour,
    hair: _Colour,
    ground: _Colour,
    look: Look,
    font: str,
    bold: str,
    scale: float,
) -> bool:
    """The `.pdf` twin of `_pptx_pattern`; `top` is from the top of the page, as there."""
    scene = _pattern_scene(
        pattern,
        data,
        left=left,
        top=top,
        width=width,
        room=room,
        accent=accent,
        ink=ink,
        muted=muted,
        tint=tint,
        hair=hair,
        ground=ground,
        look=look,
        font=font,
        bold=bold,
        scale=scale,
    )
    if scene is None:
        return False
    _pdf_scene(pdf, scene)
    return True


def _pptx_chart_more(
    slide,
    chart: dict,
    *,
    accent: RGBColor,
    muted: RGBColor,
    ink: RGBColor,
    ground: RGBColor,
    width: float,
    faces: tuple[str, str],
    left: float,
    top: float,
    room: float,
) -> None:
    """Pie, donut, horizontal bar and stacked column as native charts."""
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls

    kind = chart["kind"]
    categories = list(chart["categories"])
    series = list(chart["series"])
    round_chart = kind in ("pie", "donut")
    if round_chart:
        series = series[:1]
    if kind == "hbar":
        # PowerPoint draws bar categories bottom-up; reversed, the first reads at the top.
        categories = categories[::-1]
        series = [(name, values[::-1]) for name, values in series]
    payload = CategoryChartData()
    payload.categories = categories
    for name, values in series:
        payload.add_series(name or " ", values)
    frame = slide.shapes.add_chart(
        {
            "pie": XL_CHART_TYPE.PIE,
            "donut": XL_CHART_TYPE.DOUGHNUT,
            "hbar": XL_CHART_TYPE.BAR_CLUSTERED,
            "stacked": XL_CHART_TYPE.COLUMN_STACKED,
        }[kind],
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(room * _EMU_PER_PT)),
        payload,
    )
    graph = frame.chart
    unit = chart.get("unit") or ""
    accent_f = _floats(accent)
    if not round_chart:
        charts.apply(graph, kind="bar", unit=unit, accent=accent, muted=muted, faces=faces)
        plot = graph.plots[0]
        plot.gap_width = 60
        if kind == "stacked":
            plot.overlap = 100
            for position, item in enumerate(plot.series):
                item.format.fill.solid()
                item.format.fill.fore_color.rgb = _rgb_of(_shade(accent_f, position))
        return

    charts._plain_frame(graph)
    graph.has_title = False
    graph.has_legend = True
    graph.legend.position = XL_LEGEND_POSITION.RIGHT
    graph.legend.include_in_layout = False
    charts._text(graph.legend.font, muted, 14, faces)
    plot = graph.plots[0]
    plot.vary_by_categories = True
    for position, point in enumerate(plot.series[0].points):
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = _rgb_of(_shade(accent_f, position))
        point.format.line.color.rgb = ground
        point.format.line.width = Emu(int(1.5 * _EMU_PER_PT))
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.number_format = f'#,##0.##"{unit}"' if unit else "#,##0.##"
    labels.number_format_is_linked = False
    labels.show_value = True
    if kind == "pie":
        labels.position = XL_LABEL_POSITION.OUTSIDE_END
        charts._text(labels.font, ink, 14, faces)
    else:
        charts._text(labels.font, RGBColor(0xFF, 0xFF, 0xFF), 14, faces)
        labels.font.bold = True
    # The round plot sits in a square on the left; the legend keeps the right.
    side = room * 0.86
    plot_area = graph._chartSpace.find(qn("c:chart")).find(qn("c:plotArea"))
    for existing in plot_area.findall(qn("c:layout")):
        plot_area.remove(existing)
    plot_area.insert(
        0,
        parse_xml(
            f"<c:layout {nsdecls('c')}><c:manualLayout>"
            '<c:layoutTarget val="inner"/><c:xMode val="edge"/><c:yMode val="edge"/>'
            f'<c:x val="{0.12:.4f}"/><c:y val="{0.07:.4f}"/>'
            f'<c:w val="{side / width:.4f}"/><c:h val="{side / room:.4f}"/>'
            "</c:manualLayout></c:layout>"
        ),
    )
    if kind == "donut":
        for hole in plot_area.iter(qn("c:holeSize")):
            hole.set("val", "55")
        total = sum(series[0][1])
        cx = left + width * 0.12 + side / 2
        cy = top + room * 0.07 + side / 2
        box = _textbox(slide, left=cx - side * 0.25, top=cy - 30, width=side * 0.5, height=60)
        box.vertical_anchor = MSO_ANCHOR.MIDDLE
        paragraph = box.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER
        run = paragraph.add_run()
        run.text = f"{_tick_label(total)}{unit}"
        _font(run, size=24, bold=True, colour=accent, faces=faces)


def _divisions(ceiling: float) -> int:
    """Gridline count whose steps read as whole numbers: four, else five."""
    return 4 if ceiling < 10 or (ceiling / 4) == int(ceiling / 4) else 5


def _pdf_chart_more(
    pdf,
    chart: dict,
    *,
    accent: _Colour,
    muted: _Colour,
    ink: _Colour,
    ground: _Colour,
    top: float,
    width: float,
    font: str,
    bold: str,
    left: float = 72.0,
) -> None:
    """The `.pdf` twin of `_pptx_chart_more`, drawn by hand; `top` is a canvas y."""
    import math

    kind = chart["kind"]
    unit = chart.get("unit") or ""
    categories = chart["categories"]
    bottom = _H - _BODY_BOTTOM + 6
    height = top - bottom
    if height < 60 or width < 100:
        return

    if kind in ("pie", "donut"):
        values = [max(0.0, v) for v in chart["series"][0][1]]
        total = sum(values)
        if total <= 0:
            return
        radius = height * 0.43
        cx, cy = left + width * 0.12 + radius, bottom + height / 2
        start = 90.0
        middles = []
        pdf.setStrokeColorRGB(*ground)
        pdf.setLineWidth(1.5)
        for position, value in enumerate(values):
            extent = 360.0 * value / total
            pdf.setFillColorRGB(*_shade(accent, position))
            if extent >= 359.99:
                pdf.circle(cx, cy, radius, stroke=0, fill=1)
            elif extent > 0:
                pdf.wedge(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    start - extent,
                    extent,
                    stroke=1,
                    fill=1,
                )
            middles.append(math.radians(start - extent / 2))
            start -= extent
        hole = radius * 0.55 if kind == "donut" else 0.0
        if hole:
            pdf.setFillColorRGB(*ground)
            pdf.circle(cx, cy, hole, stroke=0, fill=1)
            pdf.setFillColorRGB(*accent)
            pdf.setFont(bold, 24)
            pdf.drawCentredString(cx, cy - 8, f"{_tick_label(total)}{unit}")
        pdf.setFont(bold, 14)
        for value, angle in zip(values, middles, strict=True):
            if value <= 0:
                continue
            label = f"{_tick_label(value)}{unit}"
            if hole:
                reach = (radius + hole) / 2
                pdf.setFillColorRGB(1, 1, 1)
            else:
                reach = radius + 18
                pdf.setFillColorRGB(*ink)
            x, y = cx + reach * math.cos(angle), cy + reach * math.sin(angle)
            pdf.drawCentredString(x, y - 5, label)
        # Legend on the right.
        legend_x = cx + radius + 60
        line = 26.0
        y = cy + line * (len(categories) - 1) / 2
        pdf.setFont(font, 14)
        for position, name in enumerate(categories):
            pdf.setFillColorRGB(*_shade(accent, position))
            pdf.rect(legend_x, y - 2, 12, 12, stroke=0, fill=1)
            pdf.setFillColorRGB(*muted)
            pdf.drawString(legend_x + 20, y, name)
            y -= line
        return

    series = chart["series"]
    if kind == "hbar":
        values = [value for _, items in series for value in items]
        if max(values + [0]) <= 0:
            return
        ceiling = _nice_ceiling(max(values))
        label_w = min(width * 0.3, 240.0)
        plot_left = left + label_w + 12
        plot_w = width - label_w - 12 - 50
        plot_top = top - 6
        plot_bottom = bottom + 24
        step = (plot_top - plot_bottom) / len(categories)
        pdf.setFont(font, 11)
        parts = _divisions(ceiling)
        for tick in range(parts + 1):
            x = plot_left + plot_w * tick / parts
            pdf.setStrokeColorRGB(0.9, 0.9, 0.9)
            pdf.setLineWidth(0.5)
            pdf.line(x, plot_bottom, x, plot_top)
            pdf.setFillColorRGB(*muted)
            pdf.drawCentredString(x, plot_bottom - 16, _tick_label(ceiling * tick / parts))
        if unit:
            pdf.drawString(plot_left + plot_w + 10, plot_bottom - 16, unit)
        thick = step * 0.6 / len(series)
        for position, name in enumerate(categories):
            row_top = plot_top - position * step
            pdf.setFillColorRGB(*muted)
            pdf.setFont(font, 12)
            label = _wrap(name, font, 12, label_w)[0]
            pdf.drawRightString(left + label_w, row_top - step / 2 - 4, label)
            for series_index, (_name, items) in enumerate(series):
                value = items[position]
                y = row_top - step * 0.2 - thick * (series_index + 1)
                pdf.setFillColorRGB(*_shade(accent, series_index))
                pdf.rect(plot_left, y, plot_w * max(0.0, value) / ceiling, thick, stroke=0, fill=1)
                if thick < 9:
                    continue
                pdf.setFillColorRGB(*ink)
                pdf.setFont(font, 11)
                pdf.drawString(
                    plot_left + plot_w * max(0.0, value) / ceiling + 4,
                    y + thick / 2 - 4,
                    _tick_label(value),
                )
        return

    # stacked
    sums = [sum(max(0.0, items[i]) for _, items in series) for i in range(len(categories))]
    if max(sums + [0]) <= 0:
        return
    ceiling = _nice_ceiling(max(sums))
    plot_bottom = bottom + (40 if len(series) > 1 else 20)
    plot_height = top - plot_bottom - 30
    step = width / len(categories)
    pdf.setFont(font, 11)
    parts = _divisions(ceiling)
    for tick in range(parts + 1):
        y = plot_bottom + plot_height * tick / parts
        pdf.setStrokeColorRGB(0.9, 0.9, 0.9)
        pdf.setLineWidth(0.5)
        pdf.line(left, y, left + width, y)
        pdf.setFillColorRGB(*muted)
        pdf.drawRightString(left - 6, y - 4, _tick_label(ceiling * tick / parts))
    if unit:
        pdf.setFillColorRGB(*muted)
        pdf.drawString(left - 30, plot_bottom + plot_height + 12, unit)
    bar = step * 0.5
    for position in range(len(categories)):
        x = left + step * (position + 0.5) - bar / 2
        y = plot_bottom
        for series_index, (_name, items) in enumerate(series):
            value = max(0.0, items[position])
            segment = plot_height * value / ceiling
            pdf.setFillColorRGB(*_shade(accent, series_index))
            pdf.rect(x, y, bar, segment, stroke=0, fill=1)
            y += segment
    pdf.setFillColorRGB(*muted)
    pdf.setFont(font, 12)
    for position, label in enumerate(categories):
        pdf.drawCentredString(left + step * (position + 0.5), plot_bottom - 18, label)
    if len(series) > 1:
        x = left
        for series_index, (name, _values) in enumerate(series):
            pdf.setFillColorRGB(*_shade(accent, series_index))
            pdf.circle(x + 4, plot_bottom - 36, 3.5, stroke=0, fill=1)
            pdf.setFillColorRGB(*muted)
            pdf.drawString(x + 13, plot_bottom - 40, name)
            x += 24 + pdfmetrics.stringWidth(name, font, 12)


_WRITTEN_NUMBER = re.compile(r"^\s*(?:\d{1,2}|[IVX]{1,4})\s?[.)．:]\s*")


def _without_written_numbers(slides: list[dict]) -> list[dict]:
    """A divider and the agenda print their own 「01」; a 「01.」 the model wrote into a
    divider's title or an agenda line would print the number twice."""
    def bare(text: object) -> str:
        return _WRITTEN_NUMBER.sub("", str(text or "")).strip() or str(text or "")

    out = []
    for slide in slides:
        if slide.get("layout") == "section" and slide.get("number"):
            slide = {**slide, "title": bare(slide.get("title"))}
        elif slide.get("layout") == "agenda" and slide.get("bullets"):
            slide = {**slide, "bullets": [bare(b) for b in slide["bullets"]]}
        out.append(slide)
    return out


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
    slides = _without_written_numbers(slides)
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
    #: The `.pdf` face, for measuring how many lines a title takes.
    measure = fonts.korean(style["font"] if style else "gothic")
    measure_bold = fonts.korean(style["font"] if style else "gothic", bold=True)

    def paint(
        run,
        *,
        size: float,
        bold: bool = False,
        colour: RGBColor | None = None,
        fixed: bool = False,
    ) -> None:
        # Titles keep their size; everything else follows the slide's scale, never
        # under the floor.
        factor = 1.0 if fixed else typescale[0]
        _font(
            run,
            size=max(deck_type.FLOOR_PT, round(size * factor)),
            bold=bold,
            colour=colour or ink,
            faces=faces,
        )

    def native_figure(
        slide, data: dict, *, left: float, top: float, width: float, height: float,
        accent: RGBColor,
    ) -> bool:
        """The slide's own figure drawn as shapes in the box; False leaves the picture."""
        made = data.get("diagram")
        source = made.get("source") if isinstance(made, dict) else None
        graph = diagram_shapes.parse(str(source or ""))
        if graph is None:
            return False
        try:
            diagram_shapes.draw_pptx(
                slide, graph, left=left, top=top, width=width, height=height,
                accent=accent, font=faces[1],
            )
        except Exception as exc:  # noqa: BLE001 — the picture is the fallback
            log.warning("figure not drawn as shapes, keeping the picture: %s", exc)
            return False
        return True

    def paint_rich(
        paragraph,
        data: dict,
        key: str,
        text: str,
        *,
        size: float,
        bold: bool = False,
        colour: RGBColor | None = None,
        fixed: bool = False,
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
                fixed=fixed,
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
                paint(run, size=_u("gutterNumber"), bold=True, colour=accent)
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
        pattern = slide_patterns.BY_NAME.get(layout)
        if pattern is not None and pattern.shape == "chart":
            chart = _pattern_chart_of(data, pattern)
        pattern_words = _has_pattern_words(data, pattern)
        picture_left = bool(
            picture
            and (bullets or rows or metrics or chart or body or pattern_words)
            and str((data.get("image") or {}).get("position") or "") == "left"
        )
        # A picture beside text narrows the text column; alone, it is centred.
        text_width = (
            _W
            - 144
            - (
                picture_span + 24
                if picture and (bullets or rows or metrics or chart or body or pattern_words)
                else 0
            )
        )
        text_left = 72 + (picture_span + 24 if picture_left else 0)

        #: Past the accent block on a split cover.
        cover_left = 72 + 336 if look.cover == "split" else 72
        cover_size = _u(
            "coverPoster"
            if visual_style == "poster"
            else "coverMono"
            if visual_style == "mono"
            else "cover"
        )
        #: Body box for this slide; a long title lowers its top.
        body_top, room = _BODY_TOP, _BODY_BOTTOM - _BODY_TOP
        if layout == "closing":
            cover_ink = _WHITE if on_accent else ink
            cover_muted = _mix(_WHITE, 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                counter = _textbox(slide, left=60, top=_H - 130, width=200, height=80)
                run = counter.paragraphs[0].add_run()
                run.text = "END"
                paint(run, size=_u("splitNumber"), bold=True, colour=_mix(_WHITE, 35, onto=accent))
            if look.cover != "brackets":
                _block(
                    slide,
                    left=cover_left,
                    top=120,
                    width=106,
                    height=7,
                    colour=_WHITE if on_accent else accent,
                )
            closing_size = _u("closing") * typescale[0]
            closing_lines = len(
                _wrap(heading or "마무리", measure, closing_size, _W - 72 - cover_left)
            )
            frame = _textbox(
                slide,
                left=cover_left,
                top=140,
                width=_W - 72 - cover_left,
                height=closing_size * 1.2 * max(1, closing_lines) + 8,
                placeholder=("title", 0),
            )
            frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            paint_rich(
                frame.paragraphs[0],
                data,
                "title",
                heading or "마무리",
                size=_u("closing"),
                bold=True,
                colour=cover_ink,
            )
            if bullets:
                listing = _textbox(
                    slide,
                    left=cover_left,
                    top=140 + closing_size * 1.2 * max(1, closing_lines) + 20,
                    width=_W - 72 - cover_left,
                    height=200,
                    placeholder=("body", 1),
                )
                for position, text in enumerate(bullets[:3]):
                    paragraph = listing.paragraphs[0] if position == 0 else listing.add_paragraph()
                    paragraph.alignment = PP_ALIGN.LEFT
                    paragraph.space_after = Pt(10)
                    marker = paragraph.add_run()
                    marker.text = "— "
                    paint(marker, size=_u("closingBullets"), bold=True, colour=cover_muted)
                    paint_rich(
                        paragraph,
                        data,
                        f"bullets.{position}",
                        text,
                        size=_u("closingBullets"),
                        colour=cover_ink,
                    )
            if body:
                foot = _textbox(slide, left=cover_left, top=_H - 120, width=_W - 144, height=50)
                foot.paragraphs[0].alignment = PP_ALIGN.LEFT
                paint_rich(
                    foot.paragraphs[0],
                    data,
                    "body",
                    body,
                    size=_u("closingBody"),
                    bold=True,
                    colour=cover_ink,
                )
        elif cover:
            cover_ink = _WHITE if on_accent else ink
            cover_muted = _mix(_WHITE, 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                counter = _textbox(slide, left=60, top=_H - 130, width=200, height=80)
                run = counter.paragraphs[0].add_run()
                number_text = str(data.get("number") or "").replace(".", "") or "01"
                run.text = "END" if layout == "closing" else number_text
                paint(run, size=_u("splitNumber"), bold=True, colour=_mix(_WHITE, 35, onto=accent))
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
                    size=_u("sectionNumber"),
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
                size=cover_size,
                bold=True,
                colour=cover_ink,
            )
            if body:
                paragraph = frame.add_paragraph()
                paragraph.alignment = PP_ALIGN.LEFT
                paragraph.space_before = Pt(14)
                paint_rich(paragraph, data, "body", body, size=_u("coverBody"), colour=cover_muted)
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
                slide, left=90, top=196, width=_W - 180, height=160, placeholder=("title", 0)
            )
            frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            paint_rich(
                frame.paragraphs[0],
                data,
                "title",
                heading,
                size=_u("statement"),
                bold=True,
                colour=accent,
            )
            if body:
                under = _textbox(
                    slide, left=120, top=360, width=_W - 240, height=80, placeholder=("body", 1)
                )
                under.paragraphs[0].alignment = PP_ALIGN.CENTER
                paint_rich(
                    under.paragraphs[0], data, "body", body, size=_u("statementBody"), colour=muted
                )
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
            paint(run, size=_u("quote"), bold=True, colour=accent)
            if body and heading:
                caption = frame.add_paragraph()
                caption.space_before = Pt(16)
                run = caption.add_run()
                run.text = heading
                paint(run, size=_u("quoteBy"), colour=muted)
        else:
            title_size = deck_type.title_pt(heading, text_width / _K)
            title_line = title_size * deck_type.LEADING["title"]
            title_lines = max(1, len(_wrap(heading, measure, title_size, text_width)))
            frame = _textbox(
                slide,
                left=text_left,
                top=_TITLE_TOP,
                width=text_width,
                height=title_line * title_lines + 6,
                placeholder=("title", 0),
            )
            frame.paragraphs[0].alignment = PP_ALIGN.LEFT
            frame.paragraphs[0].line_spacing = Pt(title_line)
            paint_rich(
                frame.paragraphs[0], data, "title", heading, size=title_size, bold=True, fixed=True
            )
            # The tab under the title; the body box starts below it and loses one line for
            # each extra title line.
            tab_top = _TITLE_TOP + title_line * title_lines + 6 * _K
            _block(slide, left=text_left, top=tab_top, width=62, height=5, colour=accent)
            body_top = _BODY_TOP + title_line * (title_lines - 1)
            room = _BODY_BOTTOM - body_top

            # A figure the deck drew for itself is a band above the words, full width;
            # the words beneath are set compact. `picture` is spent here.
            compact = False
            if picture and (data.get("image") or {}).get("diagram") and _has_words(data):
                image_bytes, _caption = picture
                text_width, text_left = _W - 144, 72.0
                need = _strip_need(
                    pairs, layout, text_width, measure, measure_bold, typescale[0]
                )
                band = max(room * 0.3, min(room * 0.56, room - need - 32))
                band_width, band_height = _fit(image_bytes, box=(text_width, band))
                drawn = native_figure(
                    slide, data, left=text_left, top=body_top, width=text_width,
                    height=min(room * 0.5, band), accent=accent,
                )
                if drawn:
                    band_height = min(room * 0.5, band)
                try:
                    if drawn:
                        raise _Drawn
                    slide.shapes.add_picture(
                        io.BytesIO(image_bytes),
                        Emu(int((text_left + (text_width - band_width) / 2) * _EMU_PER_PT)),
                        Emu(int(body_top * _EMU_PER_PT)),
                        Emu(int(band_width * _EMU_PER_PT)),
                        Emu(int(band_height * _EMU_PER_PT)),
                    )
                except _Drawn:
                    pass
                except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                    log.warning("could not place a figure band into the deck: %s", exc)
                body_top += band_height + 8
                room -= band_height + 8
                picture = None
                compact = True

            if (
                pattern is not None
                and pattern.shape != "chart"
                and _pptx_pattern(
                    slide,
                    pattern,
                    data,
                    left=text_left,
                    top=body_top,
                    width=text_width,
                    room=room,
                    accent=accent,
                    ink=ink,
                    muted=muted,
                    tint=tint,
                    hair=hair,
                    ground=ground,
                    look=look,
                    faces=faces,
                    font=measure,
                    bold=measure_bold,
                    scale=typescale[0],
                )
            ):
                pass
            elif pairs:
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
                    top=body_top,
                    room=room,
                    scale=typescale[0],
                    measure=measure,
                    compact=compact,
                )
            elif chart and chart["kind"] in _MORE_CHARTS:
                _pptx_chart_more(
                    slide,
                    chart,
                    accent=accent,
                    muted=muted,
                    ink=ink,
                    ground=ground,
                    width=text_width,
                    faces=faces,
                    left=text_left,
                    top=body_top,
                    room=room,
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
                    top=body_top,
                    room=room,
                )
            elif metrics and layout == "big-number":
                figure, label = metrics[0]
                box = _textbox(
                    slide,
                    left=text_left,
                    top=body_top,
                    width=text_width,
                    height=140,
                    placeholder=("body", 1),
                )
                run = box.paragraphs[0].add_run()
                run.text = figure
                paint(run, size=_u("bigNumber"), bold=True, colour=accent)
                run = box.paragraphs[0].add_run()
                run.text = f"  {label}"
                paint(run, size=_u("bigNumberLabel"), colour=muted)
                if body:
                    under = _textbox(
                        slide, left=text_left, top=body_top + 150, width=text_width, height=80
                    )
                    paint_rich(under.paragraphs[0], data, "body", body, size=_u("bigNumberBody"))
            elif bullets and layout == "agenda":
                # Two columns above four entries.
                columns = _split_columns(bullets) if len(bullets) > 4 else [bullets]
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                per = max(len(column) for column in columns)
                step = min(66.0, room / max(per, 1))
                number = 0
                for column_index, column in enumerate(columns):
                    left = text_left + column_index * (span + 24)
                    for position, text in enumerate(column):
                        number += 1
                        y = body_top + position * step
                        counter = _textbox(slide, left=left, top=y, width=64, height=step)
                        run = counter.paragraphs[0].add_run()
                        run.text = f"{number:02d}"
                        paint(run, size=_u("agendaNumber"), bold=True, colour=accent)
                        name = _textbox(
                            slide,
                            left=left + 64,
                            top=y + 4,
                            width=span - 64,
                            height=step,
                            placeholder=("body", 1) if number == 1 else None,
                        )
                        run = name.paragraphs[0].add_run()
                        run.text = text
                        paint(run, size=_u("agenda"))
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
                        top=body_top + 8,
                        width=span,
                        height=150,
                        fill=tint,
                        line=hair,
                    )
                    _block(slide, left=left, top=body_top + 8, width=span, height=5, colour=accent)
                    box = _textbox(
                        slide,
                        left=left,
                        top=body_top + 20,
                        width=span,
                        height=130,
                    )
                    run = box.paragraphs[0].add_run()
                    run.text = figure
                    paint(run, size=_u("metric"), bold=True, colour=accent)
                    under = box.add_paragraph()
                    under.space_before = Pt(6)
                    run = under.add_run()
                    run.text = label
                    paint(run, size=_u("metricLabel"), colour=muted)
            elif rows:
                row_height = deck_type.table_row_height(len(rows)) * _K
                shape = slide.shapes.add_table(
                    len(rows),
                    max(len(row) for row in rows),
                    Emu(int(text_left * _EMU_PER_PT)),
                    Emu(int(body_top * _EMU_PER_PT)),
                    Emu(int(text_width * _EMU_PER_PT)),
                    Emu(int(row_height * len(rows) * _EMU_PER_PT)),
                )
                table = shape.table
                # Columns by their widest cell, rows by the shared row height; the panel
                # draws the same shares.
                for column_index, share in enumerate(deck_type.column_shares(rows)):
                    table.columns[column_index].width = Emu(int(text_width * share * _EMU_PER_PT))
                for table_row in table.rows:
                    table_row.height = Emu(int(row_height * _EMU_PER_PT))
                cell_size = deck_type.table_size(len(rows)) * _K
                cell_pad = deck_type.table_pad(len(rows)) * _K
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
                        cell.margin_left = cell.margin_right = Emu(int(9 * _K * _EMU_PER_PT))
                        cell.margin_top = cell.margin_bottom = Emu(int(cell_pad * _EMU_PER_PT))
                        cell.text_frame.paragraphs[0].line_spacing = Pt(
                            cell_size * typescale[0] * deck_type.LEADING["table"]
                        )
                        run = cell.text_frame.paragraphs[0].add_run()
                        run.text = text
                        paint(
                            run,
                            size=cell_size,
                            bold=r == 0 or c == 0,
                            colour=_WHITE if r == 0 else ink,
                        )
            elif bullets:
                # PowerPoint has no column flow, so columns are separate boxes.
                columns = _columns_of(data, bullets, layout)
                span = (text_width - (24 * (len(columns) - 1))) / len(columns)
                size = _u("bodyNarrow") if len(columns) > 1 else _u("body")
                drawn = max(deck_type.FLOOR_PT, size * typescale[0])
                line_pt = drawn * look.leading
                gap_pt = drawn * deck_type.BULLET_GAP
                bullet_at = 0
                for column_index, column in enumerate(columns):
                    listing = _textbox(
                        slide,
                        left=text_left + column_index * (span + 24),
                        top=body_top,
                        width=span,
                        height=room,
                        # Only the first column is the body placeholder.
                        placeholder=("body", 1) if column_index == 0 else None,
                    )
                    for position, text in enumerate(column):
                        paragraph = (
                            listing.paragraphs[0] if position == 0 else listing.add_paragraph()
                        )
                        paragraph.line_spacing = Pt(line_pt)
                        paragraph.space_after = Pt(gap_pt)
                        marker = paragraph.add_run()
                        marker.text = "• "
                        paint(marker, size=size, bold=True, colour=accent)
                        paint_rich(paragraph, data, f"bullets.{bullet_at}", text, size=size)
                        bullet_at += 1
            elif body:
                paragraph_frame = _textbox(
                    slide,
                    left=text_left,
                    top=body_top,
                    width=text_width,
                    height=room,
                    placeholder=("body", 1),
                )
                drawn = max(deck_type.FLOOR_PT, _u("paragraph") * typescale[0])
                paragraph_frame.paragraphs[0].line_spacing = Pt(drawn * look.leading)
                paint_rich(
                    paragraph_frame.paragraphs[0],
                    data,
                    "body",
                    body,
                    size=_u("paragraph"),
                    colour=muted,
                )

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body or pattern_words)
            box = (_W - 260, room) if alone else (picture_span, room)
            fill = str((data.get("image") or {}).get("fit") or "") == "cover"
            if fill:
                _, _, crop_left, crop_top, crop_right, crop_bottom = _fill(image_bytes, box=box)
                width, height = box
            else:
                width, height = _fit(image_bytes, box=box)
                crop_left = crop_top = crop_right = crop_bottom = 0.0
            left = (72 if picture_left else 72 + text_width + 24) if not alone else (_W - width) / 2
            top = body_top + max(0.0, (room - height) / 2)
            if (data.get("image") or {}).get("diagram") and native_figure(
                slide, data,
                left=(72 if picture_left else 72 + text_width + 24) if not alone else 72.0,
                top=body_top, width=box[0] if not alone else _W - 144, height=room - 30,
                accent=accent,
            ):
                width, height = (box[0] if not alone else _W - 144), room - 30
                left = (72 if picture_left else 72 + text_width + 24) if not alone else 72.0
                top = body_top
                fill = False
                picture_drawn = True
            else:
                picture_drawn = False
            try:
                if picture_drawn:
                    raise _Drawn
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
            except _Drawn:
                if image_caption:
                    frame = _textbox(
                        slide, left=left, top=top + height + 6, width=max(width, 120), height=24
                    )
                    run = frame.paragraphs[0].add_run()
                    run.text = image_caption
                    paint(run, size=_u("caption"), colour=muted)
            except Exception as exc:  # noqa: BLE001 — one bad picture, not a failed export
                log.warning("could not place a picture in the pptx: %s", exc)
            else:
                if image_caption:
                    frame = _textbox(
                        slide, left=left, top=top + height + 6, width=max(width, 120), height=24
                    )
                    run = frame.paragraphs[0].add_run()
                    run.text = image_caption
                    paint(run, size=_u("caption"), colour=muted)

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
                        Emu(int((_H - 60) * _EMU_PER_PT)),
                        Emu(int(mark_width * _EMU_PER_PT)),
                        Emu(int(mark_height * _EMU_PER_PT)),
                    )
                except Exception as exc:  # noqa: BLE001 — a mark, not the deck
                    log.warning("could not place the logo on a pptx slide: %s", exc)
                else:
                    edge += mark_width + 10
            _block(slide, left=72, top=_H - _FOOT_RULE, width=_W - 144, height=0.75, colour=hair)
            name = _textbox(slide, left=edge, top=_H - 62, width=_W - 320 - edge, height=30)
            run = name.paragraphs[0].add_run()
            run.text = title
            paint(run, size=_u("footer"), colour=muted)
            if footer:
                who = _textbox(slide, left=_W - 330, top=_H - 62, width=210, height=30)
                who.paragraphs[0].alignment = PP_ALIGN.RIGHT
                run = who.paragraphs[0].add_run()
                run.text = footer
                paint(run, size=_u("footer"), colour=muted)
            chip = _block(slide, left=_W - 108, top=_H - 60, width=36, height=36, colour=accent)
            frame = chip.text_frame
            frame.word_wrap = False
            frame.margin_left = frame.margin_right = 0
            frame.margin_top = frame.margin_bottom = 0
            frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = frame.paragraphs[0].add_run()
            run.text = str(index + 1)
            paint(run, size=_u("pageNumber"), bold=True, colour=_WHITE)

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
    room: float | None = None,
    bold: str | None = None,
    compact: bool = False,
) -> None:
    """The `.pdf` twin of `_pptx_pairs`: same geometry, explicit wrapping."""
    bold = bold or font
    room = room if room is not None else top - (_H - _BODY_BOTTOM)
    look = look or _LOOKS["editorial"]
    hair = hair or (0.902, 0.902, 0.902)
    bg = bg or (1.0, 1.0, 1.0)

    def S(n: float) -> float:
        return n * scale

    if layout == "bands":
        label = 96.0
        height = min(72.0, (room - 10 * (len(pairs) - 1)) / max(len(pairs), 1))
        band = max(_u("bandMin"), min(_u("bandMax"), height / 3))
        lead = band * deck_type.LEADING["band"]
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
            pdf.setFont(bold, S(band))
            pdf.drawCentredString(left + label / 2, bottom + height / 2 - S(band) * 0.35, name)
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(band))
            lines = _wrap(text, font, S(band), width - label - 40)[:3]
            line_top = bottom + height / 2 + S(lead) * (len(lines) - 1) / 2 - S(band) * 0.35
            for offset, line in enumerate(lines):
                pdf.drawString(left + label + 24, line_top - offset * S(lead), line)
        return

    if layout == "tiles":
        span = (width - 16 * (len(pairs) - 1)) / max(len(pairs), 1)
        side = min(span, 96.0)
        for index, (mark, name) in enumerate(pairs):
            item_left = left + index * (span + 16)
            _pdf_badge(pdf, look, left=item_left, bottom=top - side - 20, side=side, colour=accent)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(bold, S(_u("tileMark")))
            pdf.drawCentredString(
                item_left + side / 2, top - side / 2 - 20 - S(_u("tileMark")) * 0.35, mark
            )
            pdf.setFillColorRGB(*muted)
            pdf.setFont(font, S(_u("tileName")))
            for offset, line in enumerate(_wrap(name, font, S(_u("tileName")), side + 16)[:2]):
                pdf.drawCentredString(
                    item_left + side / 2,
                    top - side - 20 - S(_u("tileName")) * 1.5 - offset * S(_u("tileName")) * 1.5,
                    line,
                )
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
            pdf.setFont(bold, S(_u("stepBadge")))
            pdf.drawCentredString(
                item_left + side / 2,
                square_top - side / 2 - S(_u("stepBadge")) * 0.35,
                f"{index + 1:02d}",
            )
            pdf.setFillColorRGB(*ink)
            pdf.setFont(bold, S(_u("stepName")))
            name_base = square_top - side - 12 - S(_u("stepName"))
            pdf.drawString(item_left, name_base, name)
            pdf.setFillColorRGB(*muted)
            text_size = S(_u("stepText"))
            text_lead = text_size * deck_type.LEADING["stepText"]
            pdf.setFont(font, text_size)
            for offset, line in enumerate(_wrap(text, font, text_size, span - 4)[:4]):
                pdf.drawString(item_left, name_base - 8 - text_size - offset * text_lead, line)
        return

    if layout == "cards":
        gap = 18.0
        span = (width - gap * (len(pairs) - 1)) / max(len(pairs), 1)
        # Compact cards take what their two lines need and leave the foot of the slide clear.
        height = min((42 if compact else 100) * _K, room - (24 if compact else 10))
        card_top = top - 10
        name_size = S(14.0 if compact else _u("cardName"))
        text_size = S(12.0 if compact else _u("cardText"))
        text_lead = text_size * deck_type.LEADING["cardText"]
        if compact:
            # Under a figure the cards grow to their longest text, so no sentence is cut.
            need = max(
                20
                + name_size * (1 + 1.3 * (len(_wrap(name, bold, name_size, span - 28)[:2]) - 1))
                + 12 + text_size
                + text_lead * (len(_wrap(text, font, text_size, span - 28)) - 1) + 12
                for name, text in pairs
            )
            height = min(max(height, need), room - 24)
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
            pdf.setFont(bold, name_size)
            name_lines = _wrap(name, bold, name_size, span - 28)[:2]
            name_base = card_top - 20 - name_size
            for offset, line in enumerate(name_lines):
                pdf.drawString(item_left + 14, name_base - offset * name_size * 1.3, line)
            name_base -= (len(name_lines) - 1) * name_size * 1.3
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, text_size)
            first = name_base - 12 - text_size
            lines = _wrap(text, font, text_size, span - 28)[
                : max(1, int((first - (card_top - height) - 10) / text_lead) + 1)
            ]
            for offset, line in enumerate(lines):
                pdf.drawString(item_left + 14, first - offset * text_lead, line)
        return

    # timeline
    axis = 128.0
    step = min(56.0, room / max(len(pairs), 1))
    line_size = S(max(_u("lineMin"), min(_u("lineMax"), step / 2.3)))
    line_lead = line_size * deck_type.LEADING["line"]
    pdf.setFillColorRGB(*tint)
    pdf.rect(left + axis, top - step * len(pairs), 1.5, step * len(pairs), stroke=0, fill=1)
    for index, (when, what) in enumerate(pairs):
        line_top = top - 4 - line_size - index * step
        pdf.setFillColorRGB(*accent)
        pdf.setFont(bold, line_size)
        pdf.drawRightString(left + axis - 12, line_top, when)
        pdf.rect(left + axis - 3.25, line_top - 1, 8, 8, stroke=0, fill=1)
        pdf.setFillColorRGB(*ink)
        pdf.setFont(font, line_size)
        for offset, line in enumerate(_wrap(what, font, line_size, width - axis - 16)[:2]):
            pdf.drawString(left + axis + 16, line_top - offset * line_lead, line)


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
    bottom = _H - _BODY_BOTTOM + 6
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
                current = (current[cut + 1 :] + char).lstrip(" ")
            else:
                lines.append(current.rstrip(" "))
                current = char.lstrip(" ")
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def to_pdf(title: str, slides: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
    """The deck as a PDF, one slide per page, without notes."""
    slides = _without_written_numbers(slides)
    style = design.normalise_tokens(tokens) if tokens else None
    font = fonts.korean(style["font"] if style else "gothic")
    bold = fonts.korean(style["font"] if style else "gothic", bold=True)
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
        pattern = slide_patterns.BY_NAME.get(layout)
        if pattern is not None and pattern.shape == "chart":
            chart = _pattern_chart_of(data, pattern)
        pattern_words = _has_pattern_words(data, pattern)
        picture_left = bool(
            picture
            and (bullets or rows or metrics or chart or body or pattern_words)
            and str((data.get("image") or {}).get("position") or "") == "left"
        )
        # Same split as the .pptx.
        text_width = (
            _W
            - 144
            - (
                picture_span + 24
                if picture and (bullets or rows or metrics or chart or body or pattern_words)
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
                pdf.setFont(bold, _u("gutterNumber"))
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
            # A size at the slide's scale, never under the floor.
            return max(float(deck_type.FLOOR_PT), n * _ts)

        cover_size = _u(
            "coverPoster"
            if visual_style == "poster"
            else "coverMono"
            if visual_style == "mono"
            else "cover"
        )
        #: Body box for this slide; a long title lowers its top.
        body_top, room = _BODY_TOP, _BODY_BOTTOM - _BODY_TOP

        if layout == "closing":
            cover_ink = (1.0, 1.0, 1.0) if on_accent else ink
            cover_muted = _mix_floats((1.0, 1.0, 1.0), 80, onto=accent) if on_accent else muted
            if look.cover == "split":
                pdf.setFillColorRGB(*_mix_floats((1.0, 1.0, 1.0), 35, onto=accent))
                pdf.setFont(bold, S(_u("splitNumber")))
                pdf.drawString(60, 60, "END")
            if look.cover != "brackets":
                pdf.setFillColorRGB(*((1, 1, 1) if on_accent else accent))
                pdf.rect(cover_left, _H - 127, 106, 7, stroke=0, fill=1)
            pdf.setFillColorRGB(*cover_ink)
            closing_size = S(_u("closing"))
            pdf.setFont(bold, closing_size)
            y = _H - 140 - closing_size
            for line in _wrap(heading or "마무리", bold, closing_size, _W - 72 - cover_left)[:2]:
                pdf.drawString(cover_left, y, line)
                y -= closing_size * 1.2
            y -= 6
            bullet_size = S(_u("closingBullets"))
            bullet_lead = bullet_size * deck_type.LEADING["body"]
            for text in bullets[:3]:
                pdf.setFillColorRGB(*cover_muted)
                pdf.setFont(font, bullet_size)
                pdf.drawString(cover_left, y, "—")
                pdf.setFillColorRGB(*cover_ink)
                for offset, line in enumerate(
                    _wrap(text, font, bullet_size, _W - 128 - cover_left)[:2]
                ):
                    pdf.drawString(cover_left + 28, y - offset * bullet_lead, line)
                    y -= bullet_lead
                y -= 6
            if body:
                pdf.setFillColorRGB(*cover_ink)
                pdf.setFont(bold, S(_u("closingBody")))
                pdf.drawString(
                    cover_left, 92, _wrap(body, bold, S(_u("closingBody")), _W - 72 - cover_left)[0]
                )
        elif cover:
            cover_ink = (1.0, 1.0, 1.0) if on_accent else ink
            number = str(data.get("number") or "")
            if look.cover == "split":
                pdf.setFillColorRGB(*_mix_floats((1.0, 1.0, 1.0), 35, onto=accent))
                pdf.setFont(bold, S(_u("splitNumber")))
                pdf.drawString(60, 60, number.replace(".", "") or "01")
            if look.cover != "split" and layout == "section" and number:
                pdf.setFillColorRGB(
                    *(_mix_floats((1.0, 1.0, 1.0), 70, onto=accent) if on_accent else accent)
                )
                pdf.setFont(bold, S(_u("sectionNumber")))
                pdf.drawString(cover_left, _H / 2 + 100, number)
            elif look.cover != "brackets":
                pdf.setFillColorRGB(*((1, 1, 1) if on_accent else accent))
                pdf.rect(cover_left + 10, _H / 2 + 74, 106, 7, stroke=0, fill=1)
            pdf.setFillColorRGB(*cover_ink)
            pdf.setFont(bold, S(cover_size))
            y = _H / 2 + 20
            title_width = (_W - 72 - cover_left) - (300 if look.cover == "paper" else 0)
            for line in _wrap(heading or title, bold, S(cover_size), title_width):
                pdf.drawString(cover_left, y, line)
                y -= S(cover_size) * 1.2
            if body:
                pdf.setFillColorRGB(
                    *(_mix_floats((1.0, 1.0, 1.0), 80, onto=accent) if on_accent else muted)
                )
                pdf.setFont(font, S(_u("coverBody")))
                for line in _wrap(body, font, S(_u("coverBody")), _W - 72 - cover_left)[:2]:
                    pdf.drawString(cover_left, y - 6, line)
                    y -= S(_u("coverBody")) * 1.5
        elif layout == "statement":
            pdf.setFillColorRGB(*accent)
            pdf.rect((_W - 62) / 2, _H - 181, 62, 5, stroke=0, fill=1)
            pdf.setFont(bold, S(_u("statement")))
            y = _H / 2 + 10
            for line in _wrap(heading, bold, S(_u("statement")), _W - 180)[:2]:
                pdf.drawCentredString(_W / 2, y, line)
                y -= S(_u("statement")) * 1.25
            if body:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(_u("statementBody")))
                for line in _wrap(body, font, S(_u("statementBody")), _W - 240)[:2]:
                    pdf.drawCentredString(_W / 2, y - 4, line)
                    y -= S(_u("statementBody")) * 1.5
        elif layout == "quote":
            pdf.setFillColorRGB(*accent)
            pdf.setFont(bold, S(_u("quote")))
            y = _H / 2 + 40
            for line in _wrap(f"“{body or heading}”", bold, S(_u("quote")), _W - 200):
                pdf.drawString(90, y, line)
                y -= S(_u("quote")) * 1.4
            if body and heading:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(_u("quoteBy")))
                pdf.drawString(90, y - 8, heading)
        else:
            title_size = deck_type.title_pt(heading, text_width / _K)
            title_line = title_size * deck_type.LEADING["title"]
            title_lines = _wrap(heading, bold, title_size, text_width) or [""]
            pdf.setFillColorRGB(*ink)
            pdf.setFont(bold, title_size)
            y = _H - _TITLE_TOP - title_size * 0.9
            for line in title_lines:
                pdf.drawString(text_left, y, line)
                y -= title_line
            # The tab under the title; the body box starts below it and loses one line for
            # each extra title line.
            pdf.setFillColorRGB(*accent)
            tab_top = _TITLE_TOP + title_line * len(title_lines) + 6 * _K
            pdf.rect(text_left, _H - tab_top - 5, 62, 5, stroke=0, fill=1)
            body_top = _BODY_TOP + title_line * (len(title_lines) - 1)
            room = _BODY_BOTTOM - body_top

            # The figure band, as in the pptx renderer.
            compact = False
            if picture and (data.get("image") or {}).get("diagram") and _has_words(data):
                image_bytes, _caption = picture
                text_width, text_left = _W - 144, 72.0
                need = _strip_need(pairs, layout, text_width, font, bold, ts)
                band = max(room * 0.3, min(room * 0.56, room - need - 32))
                band_width, band_height = _fit(image_bytes, box=(text_width, band))
                try:
                    pdf.drawImage(
                        ImageReader(io.BytesIO(image_bytes)),
                        text_left + (text_width - band_width) / 2,
                        _H - body_top - band_height,
                        width=band_width,
                        height=band_height,
                        mask="auto",
                    )
                except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                    log.warning("could not place a figure band into the deck pdf: %s", exc)
                body_top += band_height + 8
                room -= band_height + 8
                picture = None
                compact = True
            y = _H - body_top

            if (
                pattern is not None
                and pattern.shape != "chart"
                and _pdf_pattern(
                    pdf,
                    pattern,
                    data,
                    left=text_left,
                    top=body_top,
                    width=text_width,
                    room=room,
                    accent=accent,
                    ink=ink,
                    muted=muted,
                    tint=tint,
                    hair=hair,
                    ground=ground,
                    look=look,
                    font=font,
                    bold=bold,
                    scale=ts,
                )
            ):
                pass
            elif pairs:
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
                    room=room,
                    bold=bold,
                    compact=compact,
                )
            elif chart and chart["kind"] in _MORE_CHARTS:
                _pdf_chart_more(
                    pdf,
                    chart,
                    accent=accent,
                    muted=muted,
                    ink=ink,
                    ground=ground,
                    top=y,
                    width=text_width,
                    font=font,
                    bold=bold,
                    left=text_left,
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
                figure_size = S(_u("bigNumber"))
                pdf.setFillColorRGB(*accent)
                pdf.setFont(bold, figure_size)
                pdf.drawString(text_left, y - figure_size, figure)
                figure_width = pdf.stringWidth(figure, font, figure_size)
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(_u("bigNumberLabel")))
                pdf.drawString(text_left + figure_width + 14, y - figure_size, label)
                if body:
                    body_size = S(_u("bigNumberBody"))
                    pdf.setFillColorRGB(*ink)
                    pdf.setFont(font, body_size)
                    for offset, line in enumerate(_wrap(body, font, body_size, text_width)[:2]):
                        pdf.drawString(
                            text_left,
                            y - figure_size - 24 - body_size - offset * body_size * 1.5,
                            line,
                        )
            elif metrics:
                span = (text_width - 24 * (len(metrics) - 1)) / len(metrics)
                figure_size = S(_u("metric"))
                label_size = S(_u("metricLabel"))
                box_height = figure_size * 1.1 + 6 + label_size * 1.5 + 30
                for position, (figure, label) in enumerate(metrics):
                    left = text_left + position * (span + 24)
                    _pdf_box(
                        pdf,
                        look,
                        left=left,
                        bottom=y - 8 - box_height,
                        width=span,
                        height=box_height,
                        fill=tint,
                        line=hair,
                        bg=ground,
                    )
                    pdf.setFillColorRGB(*accent)
                    pdf.rect(left, y - 8 - 5, span, 5, stroke=0, fill=1)
                    pdf.setFillColorRGB(*accent)
                    pdf.setFont(bold, figure_size)
                    pdf.drawString(left + 14, y - 8 - 14 - figure_size, figure)
                    pdf.setFillColorRGB(*muted)
                    pdf.setFont(font, label_size)
                    pdf.drawString(
                        left + 14, y - 8 - 14 - figure_size * 1.1 - 6 - label_size, label
                    )
            elif rows:
                # Columns by their widest cell and rows by the shared row height, as in the
                # panel; a cell that wraps makes its row taller, up to two lines.
                widths = [text_width * share for share in deck_type.column_shares(rows)]
                cell_size = S(deck_type.table_size(len(rows)) * _K)
                cell_pad = S(deck_type.table_pad(len(rows)) * _K)
                line_height = cell_size * deck_type.LEADING["table"]
                pad_x = 9 * _K
                row_top = y
                for row_index, row in enumerate(rows):
                    wrapped = [
                        _wrap(
                            cell,
                            bold if row_index == 0 or c == 0 else font,
                            cell_size,
                            widths[c] - 2 * pad_x,
                        )[:2]
                        for c, cell in enumerate(row)
                        if c < len(widths)
                    ]
                    step = max(1, *(len(w) for w in wrapped)) * line_height + 2 * cell_pad
                    if row_top - step < _H - _BODY_BOTTOM - 2:
                        break
                    if row_index == 0:
                        pdf.setFillColorRGB(*accent)
                        pdf.rect(text_left, row_top - step, text_width, step, stroke=0, fill=1)
                    elif row_index % 2 == 0:
                        pdf.setFillColorRGB(*tint)
                        pdf.rect(text_left, row_top - step, text_width, step, stroke=0, fill=1)
                    else:
                        pdf.setStrokeColorRGB(*hair)
                        pdf.setLineWidth(0.75)
                        pdf.line(text_left, row_top - step, text_left + text_width, row_top - step)
                    pdf.setFillColorRGB(*((1.0, 1.0, 1.0) if row_index == 0 else ink))
                    x = text_left
                    for c, lines in enumerate(wrapped):
                        pdf.setFont(bold if row_index == 0 or c == 0 else font, cell_size)
                        baseline = row_top - cell_pad - cell_size * 0.85
                        for offset, line in enumerate(lines):
                            pdf.drawString(x + pad_x, baseline - offset * line_height, line)
                        x += widths[c]
                    row_top -= step
            elif bullets and layout == "agenda":
                columns = _split_columns(bullets) if len(bullets) > 4 else [bullets]
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                per = max(len(column) for column in columns)
                step = min(66.0, room / max(per, 1))
                agenda_size = S(_u("agenda"))
                number = 0
                top = y
                for column_index, column in enumerate(columns):
                    left = text_left + column_index * (span + 24)
                    line_y = top - 10
                    for text in column:
                        number += 1
                        pdf.setFillColorRGB(*accent)
                        pdf.setFont(bold, S(_u("agendaNumber")))
                        pdf.drawString(left, line_y - agenda_size, f"{number:02d}")
                        pdf.setFillColorRGB(*ink)
                        pdf.setFont(font, agenda_size)
                        lines = _wrap(text, font, agenda_size, span - 64)[:2]
                        for offset, line in enumerate(lines):
                            pdf.drawString(
                                left + 64, line_y - agenda_size - offset * agenda_size * 1.3, line
                            )
                        # The rule under the entry; a wrapped entry pushes the next one down.
                        rule_y = line_y - agenda_size - (len(lines) - 1) * agenda_size * 1.3 - 12
                        pdf.setFillColorRGB(*hair)
                        pdf.rect(left, rule_y, span, 0.75, stroke=0, fill=1)
                        line_y = min(line_y - step, rule_y - 10)
            elif bullets:
                columns = _columns_of(data, bullets, layout)
                size = S(_u("bodyNarrow") if len(columns) > 1 else _u("body"))
                step = size * look.leading
                gap = size * deck_type.BULLET_GAP
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                wrapped_columns = [
                    [_wrap(text, font, size, span - 26) for text in column] for column in columns
                ]
                top = y - size * 0.85
                for column_index, column in enumerate(wrapped_columns):
                    left = text_left + column_index * (span + 24)
                    y = top
                    for wrapped in column:
                        pdf.setFillColorRGB(*accent)
                        pdf.setFont(font, size)
                        pdf.drawString(left + 4, y, "•")
                        pdf.setFillColorRGB(*ink)
                        for offset, line in enumerate(wrapped):
                            pdf.drawString(left + 26, y - offset * step, line)
                        y -= step * len(wrapped) + gap
            elif body:
                size = S(_u("paragraph"))
                step = size * look.leading
                wrapped = _wrap(body, font, size, text_width)
                y = y - size * 0.85
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, size)
                for line in wrapped:
                    pdf.drawString(text_left, y, line)
                    y -= step

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body or pattern_words)
            box = (_W - 260, room) if alone else (picture_span, room)
            fill = str((data.get("image") or {}).get("fit") or "") == "cover"
            if fill:
                width, height, *_ = _fill(image_bytes, box=box)
                box_width, box_height = box
                box_left = (
                    (72 if picture_left else 72 + text_width + 24)
                    if not alone
                    else (_W - box_width) / 2
                )
                box_bottom = _H - body_top - room
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
                box_bottom = _H - body_top - room + max(0.0, (room - height) / 2)
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
                    pdf.setFont(font, S(_u("caption")))
                    pdf.drawString(
                        box_left,
                        box_bottom - 6 - S(_u("caption")),
                        _wrap(image_caption, font, S(_u("caption")), box_width)[0],
                    )

        # Foot: logo, deck title and footer on the left, slide number on the right.
        if not cover:
            pdf.setStrokeColorRGB(*hair)
            pdf.setLineWidth(0.75)
            pdf.line(72, _FOOT_RULE, _W - 72, _FOOT_RULE)
            left = 72.0
            if mark:
                blob, mark_width, mark_height = mark
                try:
                    pdf.drawImage(
                        ImageReader(io.BytesIO(blob)),
                        left,
                        30,
                        width=mark_width,
                        height=mark_height,
                        mask="auto",
                    )
                except Exception as exc:  # noqa: BLE001 — a mark, not the deck
                    log.warning("could not draw the logo on a pdf slide: %s", exc)
                else:
                    left += mark_width + 10
            foot_size = _u("footer")
            pdf.setFillColorRGB(0.55, 0.55, 0.55)
            pdf.setFont(font, foot_size)
            foot = _wrap(title, font, foot_size, _W - 400 - (left - 72))[0] if title else ""
            pdf.drawString(left, 36, foot)
            if footer:
                pdf.drawRightString(_W - 120, 36, _wrap(footer, font, foot_size, 240)[0])
            pdf.setFillColorRGB(*accent)
            pdf.rect(_W - 108, 24, 36, 36, stroke=0, fill=1)
            pdf.setFillColorRGB(1, 1, 1)
            pdf.setFont(bold, _u("pageNumber"))
            pdf.drawCentredString(_W - 90, 24 + 18 - _u("pageNumber") * 0.35, str(index + 1))
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


__all__ = ["to_pdf", "to_pptx"]
