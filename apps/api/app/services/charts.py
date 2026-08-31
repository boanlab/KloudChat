"""One chart, drawn the same way wherever it is drawn.

A chart part is DrawingML and identical in Word and PowerPoint — same
`c:chartSpace`, same embedded workbook — so the deck and the report can share
not just the format but the *look*: one accent, one zero floor, one set of
gridlines, one face for the labels.

They did not share it at first, and the difference was visible immediately. The
deck's charts were painted in the deck's accent; the report's were whatever
Word's theme says a chart is, which is a blue-and-red pair inside a black box.
Two surfaces of one product, disagreeing about what a chart looks like.

`apply` styles a chart that already exists — the deck adds one to a slide and
then calls this. `part` builds one from nothing, through a throwaway
presentation, and hands back the two blobs a `.docx` needs. The second is a
strange-looking way to make a Word chart and it is the honest one: the styling
below is the part worth having, and writing it twice is how the two drift.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: (latin, East Asian) pairs. `font.name` writes `a:latin` only and both Word
#: and PowerPoint take Hangul from `a:ea`, which is what a Korean axis label is
#: made of — so an axis set without the second one comes back in whatever face
#: the theme happens to carry.
FACES = {
    "gothic": ("Segoe UI", "맑은 고딕"),
    "serif": ("Georgia", "바탕"),
}

#: The gridlines and the axis rules. Light enough to read a value against and
#: not so dark that the chart reads as a table with lines missing.
_GRID = "E5E5E5"
_AXIS = "C8C8C8"


def apply(chart, *, kind: str, unit: str, accent, muted, faces: tuple[str, str]) -> None:
    """Everything about a chart except its numbers.

    The value axis starts at zero and that is not a preference. A bar chart
    with its floor cut off exaggerates every difference on it, and it is the
    easiest way there is to mislead a reader by accident.
    """
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_LEGEND_POSITION, XL_MARKER_STYLE
    from pptx.util import Pt

    _plain_frame(chart)
    chart.has_title = False
    # A legend naming one series names the only thing on the chart, which the
    # heading above it has already done.
    chart.has_legend = len(chart.plots[0].series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        _text(chart.legend.font, muted, 12, faces)

    value_axis = chart.value_axis
    value_axis.minimum_scale = 0
    value_axis.has_major_gridlines = True
    value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(_GRID)
    value_axis.format.line.color.rgb = RGBColor.from_string(_GRID)
    _text(value_axis.tick_labels.font, muted, 12, faces)
    if unit:
        value_axis.has_title = True
        frame = value_axis.axis_title.text_frame
        frame.text = unit
        _text(frame.paragraphs[0].font, muted, 12, faces)
        # Upright. A value-axis title is turned on its side by default, which
        # for a Latin word is a convention and for a single Hangul syllable is
        # a character lying on its back halfway up the chart.
        frame._txBody.bodyPr.set("rot", "0")
        frame._txBody.bodyPr.set("vert", "horz")

    category_axis = chart.category_axis
    category_axis.has_major_gridlines = False
    category_axis.format.line.color.rgb = RGBColor.from_string(_AXIS)
    _text(category_axis.tick_labels.font, muted, 12, faces)

    # The document's accent, and a lighter mix of it for a second series, so
    # the chart belongs to the page it is on rather than to a theme palette.
    for position, series in enumerate(chart.plots[0].series):
        colour = accent if position == 0 else mix(accent, RGBColor(0xFF, 0xFF, 0xFF), 0.55)
        if kind == "line":
            series.format.line.color.rgb = colour
            series.format.line.width = Pt(2.5)
            # The markers too, or they keep the theme's palette — which put a
            # red square on the second series of a navy deck, and the same red
            # square in the legend.
            series.marker.style = XL_MARKER_STYLE.CIRCLE
            series.marker.size = 6
            series.marker.format.fill.solid()
            series.marker.format.fill.fore_color.rgb = colour
            series.marker.format.line.color.rgb = colour
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = colour
            series.format.line.fill.background()


def _plain_frame(chart) -> None:
    """No box round the chart, and no rounded corners on the box there isn't.

    Word draws a rounded grey rectangle round a chart it has been told nothing
    about, which on a report page reads as a widget dropped into the document —
    every other figure on that page, picture or table, has no frame at all.
    The default is a default rather than a decision, so this makes the decision.

    `c:spPr` is schema-ordered and belongs after `c:chart`; put before it, the
    part is one Word offers to repair.
    """
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls, qn

    space = chart._chartSpace
    for tag in ("c:roundedCorners", "c:spPr"):
        for existing in space.findall(qn(tag)):
            space.remove(existing)

    corners = parse_xml(f'<c:roundedCorners {nsdecls("c")} val="0"/>')
    space.insert(0, corners)

    plot = space.find(qn("c:chart"))
    shape = parse_xml(
        f'<c:spPr {nsdecls("c", "a")}>'
        "<a:noFill/><a:ln><a:noFill/></a:ln>"
        "</c:spPr>"
    )
    space.insert(list(space).index(plot) + 1, shape)


def part(
    kind: str,
    categories: list[str],
    series: list[tuple[str, list[float]]],
    *,
    unit: str,
    accent,
    muted,
    faces: tuple[str, str],
) -> tuple[bytes, bytes] | None:
    """A styled chart part and its workbook, for a format with no chart API.

    Built by adding a chart to a presentation nobody will ever open and taking
    the part back out. The alternative is a second styling implementation
    written against raw XML, which is the thing this module exists to prevent.
    """
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Emu

    try:
        payload = CategoryChartData()
        payload.categories = categories
        for name, values in series:
            payload.add_series(name or " ", values)

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        frame = slide.shapes.add_chart(
            XL_CHART_TYPE.LINE_MARKERS if kind == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED,
            Emu(0),
            Emu(0),
            Emu(5486400),
            Emu(3200400),
            payload,
        )
        apply(frame.chart, kind=kind, unit=unit, accent=accent, muted=muted, faces=faces)
        chart_part = frame.chart.part
        return chart_part.blob, chart_part.chart_workbook.xlsx_part.blob
    except Exception as exc:  # noqa: BLE001 — a chart is not worth a failed export
        log.warning("could not build a chart part: %s", exc)
        return None


def mix(colour, toward, amount: float):
    """One colour moved toward another — a second series, from one accent."""
    from pptx.dml.color import RGBColor

    return RGBColor(*(round(a + (b - a) * amount) for a, b in zip(colour, toward, strict=True)))


def _text(font, colour, size: int, faces: tuple[str, str]) -> None:
    """A chart label in the document's own face and colour, not the theme's."""
    from pptx.oxml.ns import qn
    from pptx.util import Pt

    font.size = Pt(size)
    font.color.rgb = colour
    font.name = faces[0]
    properties = font._rPr
    for tag in ("a:ea", "a:cs"):
        properties.append(properties.makeelement(qn(tag), {"typeface": faces[1]}))


__all__ = ["FACES", "apply", "mix", "part"]
