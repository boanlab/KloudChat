"""Turning a stored deck into something you can present from.

Two formats: `.pptx` for editing elsewhere, `.pdf` for projecting from a machine
you do not control.

Both render one slide per page at 16:9 from the same artifact fields the browser
previews. The geometry is shared — 960×540 points is exactly 13.333×7.5 inches
— so the PDF page and the PowerPoint slide are one rectangle and cannot drift.

Speaker notes go to the .pptx notes pane and are dropped from the PDF: a note is
what you say, not what the room reads.
"""

from __future__ import annotations

import io
import logging
import re

import PIL.Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services import charts, design, fonts, pictures

log = logging.getLogger(__name__)

#: 16:9 in points — EMU/12700, and reportlab's default unit. Drives both
#: exporters.
_W, _H = 960.0, 540.0

#: How wide a picture gets when it shares the slide with words. Just under half
#: the text column: smaller and it is decoration, wider and the lines beside it
#: break every few words.
_PICTURE_SPAN = 300.0
_EMU_PER_PT = 12700

#: Korean is laid out with the *East Asian* font, which python-pptx does not
#: set — see `_font`. Both pairs are present on any Windows that would open
#: this; elsewhere the theme font applies. Keyed by `design.FONTS`, so a design
#: system's face survives into PowerPoint rather than stopping at the preview.
_FACES = {
    "gothic": ("Segoe UI", "맑은 고딕"),
    "serif": ("Georgia", "바탕"),
}

_INK = RGBColor(0x1A, 0x1A, 0x1A)
_MUTED = RGBColor(0x66, 0x66, 0x66)

#: The dark deck's ground and its two neutrals. Not tokens: the design system
#: chooses colours for a document, and a projected slide inverts them.
_DARK_BG = RGBColor(0x0E, 0x11, 0x16)
_DARK_INK = RGBColor(0xF5, 0xF6, 0xF7)
_DARK_MUTED = RGBColor(0x9A, 0xA0, 0xA6)

#: The greys this file drew before design systems existed. Kept as the literal
#: fallbacks rather than folded into `design.DEFAULT_TOKENS`: a deck with no
#: design system has to export byte for byte what it exported yesterday, and
#: `#1a1a1a` is not exactly `(0.1, 0.1, 0.1)`.
_PDF_INK = (0.1, 0.1, 0.1)
_PDF_MUTED = (0.4, 0.4, 0.4)


def _rgb(value: str | None) -> RGBColor:
    """`#rrggbb` → RGBColor, falling back rather than raising.

    The accent is per-slide and editable, so it can arrive as a CSS variable or
    an empty string.
    """
    text = (value or "").strip().lstrip("#")
    if len(text) == 3 and re.fullmatch(r"[0-9a-fA-F]{3}", text):
        text = "".join(c * 2 for c in text)
    if len(text) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return RGBColor(int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    return RGBColor(0x5B, 0x5B, 0xD6)


def _hex_floats(value: str | None) -> tuple[float, float, float]:
    colour = _rgb(value)
    return colour[0] / 255, colour[1] / 255, colour[2] / 255


def _font(
    run,
    *,
    size: int,
    bold: bool = False,
    colour: RGBColor = _INK,
    faces: tuple[str, str] = _FACES["gothic"],
) -> None:
    """Sets a run's font, including the East Asian typeface.

    `font.name` writes only `a:latin`, while PowerPoint picks Hangul from
    `a:ea` — which is most of the text here.
    """
    latin, east_asian = faces
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = colour
    run.font.name = latin
    properties = run.font._rPr
    for tag in ("a:ea", "a:cs"):
        element = properties.makeelement(qn(tag), {"typeface": east_asian})
        properties.append(element)


def _pptx_chart(
    slide, chart: dict, *, accent, muted, width: float, faces: tuple[str, str]
) -> None:
    """A native PowerPoint chart, not a picture of one.

    The difference is what the person presenting can do with it. A native chart
    carries its own worksheet, so a number that turns out to be wrong the
    morning of the talk is fixed in the file they already have; a raster is a
    picture they have to come back to us to change.

    Everything but the geometry is `services.charts`, which the report's `.docx`
    charts go through as well — one accent, one zero floor, one set of
    gridlines, whichever surface the reader is on.
    """
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    payload = CategoryChartData()
    payload.categories = chart["categories"]
    for name, values in chart["series"]:
        payload.add_series(name or " ", values)

    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.LINE_MARKERS if chart["kind"] == "line" else XL_CHART_TYPE.COLUMN_CLUSTERED,
        Emu(int(72 * _EMU_PER_PT)),
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
    """A line under one table cell, and nothing anywhere else on it.

    `python-pptx` has no border API — a cell's lines live in `a:tcPr` and have
    to be written as XML. Only the bottom is drawn, so a row of cells reads as
    one rule across the table rather than as a row of boxes.

    `a:tcPr` is schema-ordered and the line elements come first — left, right,
    top, bottom — so this is inserted at the front. A cell whose `a:lnB` sits
    after its fill is a file PowerPoint opens with a repair prompt.
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


def _textbox(slide, *, left: float, top: float, width: float, height: float):
    box = slide.shapes.add_textbox(
        Emu(int(left * _EMU_PER_PT)),
        Emu(int(top * _EMU_PER_PT)),
        Emu(int(width * _EMU_PER_PT)),
        Emu(int(height * _EMU_PER_PT)),
    )
    frame = box.text_frame
    frame.word_wrap = True
    return frame


def _columns_of(data: dict, bullets: list[str], layout: str) -> list[list[str]]:
    """The lists to lay side by side.

    An HTML deck read back by `page_export` knows which column each line was
    in and says so; a JSON deck only has one list, and halving it is the best
    guess available. Preferring the explicit answer keeps a two-column slide
    from being re-divided in the wrong place on its way to a file.
    """
    given = [list(c) for c in (data.get("columns") or []) if c]
    if layout == "two-column" and len(given) >= 2:
        return given
    return _split_columns(bullets) if layout == "two-column" else [bullets]


def _split_columns(bullets: list[str]) -> list[list[str]]:
    """A list in two columns, reading top-to-bottom then across.

    Falls back to one column below five items: two columns of two is a gap
    down the middle rather than a layout, and the model does occasionally
    return a short list for a slide it planned as `two-column`.
    """
    if len(bullets) < 5:
        return [bullets]
    half = (len(bullets) + 1) // 2
    return [bullets[:half], bullets[half:]]


def _chart_of(slide: dict) -> dict | None:
    """A slide's chart, if it has one that can be drawn.

    Checked here rather than trusted, because both writers are also handed
    artifacts written before `deck._clean_chart` existed and artifacts a person
    has edited by hand. A series shorter than the categories is the one that
    matters: it is not a chart with a gap, it is a chart whose bars stand under
    the wrong labels, and every reader takes away a fact that was never in the
    data. Rather than guess the pairing, this draws only the part that pairs.
    """
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
    """The picture on a slide, whichever way it arrived.

    Two tracks put one there. `page_export` reads an HTML artifact and hands
    over decoded bytes; a JSON deck stores the `data:` URI itself, because its
    slides are JSON in a JSONB column and bytes are not.
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
    """The size a picture takes inside `box`, in points, keeping its shape.

    Read from the bytes rather than trusted from the markup: the artifact says
    nothing about pixel dimensions, and a picture drawn to a box it does not
    fit is either squashed or off the slide.
    """
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


def to_pptx(
    title: str,
    slides: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    dark: bool = False,
    template: str = "",
) -> bytes:
    """The deck as a PowerPoint file.

    Blank layout, not the built-in title/content ones: those carry placeholder
    prompts that show in the outline pane and in any empty slide.

    `tokens` is the design system the deck was written under, copied onto the
    artifact when it was made. Absent, the file is exactly what it was before
    design systems existed.
    """
    style = design.normalise_tokens(tokens) if tokens else None
    faces = _FACES[style["font"]] if style else _FACES["gothic"]
    ink = _rgb(style["ink"]) if style else _INK
    muted = _rgb(style["muted"]) if style else _MUTED
    if dark:
        # A deck written for a room with the lights down. The design system's
        # ink is a colour for paper; on this ground it would be unreadable, so
        # the two neutrals swap rather than being taken from the tokens.
        ink, muted = _DARK_INK, _DARK_MUTED

    #: The slide being drawn, so `paint` can honour its own type size. A list
    #: because `paint` closes over it and closures cannot rebind an outer name.
    typescale = [1.0]

    def paint(run, *, size: int, bold: bool = False, colour: RGBColor | None = None) -> None:
        # `textScale` is what somebody set on that slide in the panel — 크게 or
        # 작게 on one slide, not the deck. Applied here rather than at each of
        # the fifteen call sites below, which is also why every size in this
        # file goes through one function.
        _font(
            run,
            size=max(8, round(size * typescale[0])),
            bold=bold,
            colour=colour or ink,
            faces=faces,
        )

    # The 서식's own PowerPoint file, when it has one.
    #
    # Same reasoning as `report_export.to_docx` opening the 서식's `.docx`: the
    # shape reached the screen and then the file — the thing that is actually
    # presented — came out in `python-pptx`'s defaults. Opening the template
    # makes the master, its layouts and its theme the 서식's, so a deck saved
    # from here carries them and 새 슬라이드 offers the same shapes.
    #
    # The slides below are still drawn rather than placed in the layouts'
    # placeholders. That is the next thing to change and it is a bigger one;
    # this much already moves the master, the theme and the page.
    presentation = Presentation(template) if template else Presentation()
    presentation.slide_width = Emu(int(_W * _EMU_PER_PT))
    presentation.slide_height = Emu(int(_H * _EMU_PER_PT))
    blank = presentation.slide_layouts[6]

    for index, data in enumerate(slides):
        slide = presentation.slides.add_slide(blank)
        typescale[0] = _typescale(data)
        if dark:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = _DARK_BG
        accent = _rgb(data.get("accent"))
        layout = data.get("layout") or "bullets"

        # The accent bar down the left edge, matching the preview.
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Emu(0),
            Emu(0),
            Emu(int(9 * _EMU_PER_PT)),
            presentation.slide_height,
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent
        bar.line.fill.background()
        bar.shadow.inherit = False

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
        # A picture takes the right half and the words keep the left. Alone, it
        # takes the middle of the slide. Either way the text is narrowed here
        # rather than overlapping it, which is what a slide would show.
        text_width = _W - 144 - (
            _PICTURE_SPAN + 24
            if picture and (bullets or rows or metrics or chart or body)
            else 0
        )

        if layout == "title" and index == 0:
            frame = _textbox(slide, left=72, top=190, width=_W - 144, height=160)
            paint(frame.paragraphs[0].add_run(), size=40, bold=True)
            frame.paragraphs[0].runs[0].text = heading or title
            if body:
                paragraph = frame.add_paragraph()
                paragraph.space_before = Pt(14)
                run = paragraph.add_run()
                run.text = body
                paint(run, size=15, colour=muted)
        elif layout == "quote":
            frame = _textbox(slide, left=90, top=170, width=_W - 180, height=200)
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
            frame = _textbox(slide, left=72, top=64, width=text_width, height=60)
            run = frame.paragraphs[0].add_run()
            run.text = heading
            paint(run, size=26, bold=True)

            if chart:
                _pptx_chart(
                    slide, chart, accent=accent, muted=muted, width=text_width, faces=faces
                )
            elif metrics:
                # Figures set large, side by side, with what each one counts
                # under it. The size is the whole point: a number typed into a
                # bullet is read at the same weight as everything around it,
                # and this slide exists because one of them is the thing to
                # remember.
                span = (text_width - 24 * (len(metrics) - 1)) / len(metrics)
                for position, (figure, label) in enumerate(metrics):
                    box = _textbox(
                        slide,
                        left=72 + position * (span + 24),
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
                # A real table, because an HTML deck can carry one. Flattened
                # into lines it is a comparison the reader has to rebuild.
                shape = slide.shapes.add_table(
                    len(rows),
                    max(len(row) for row in rows),
                    Emu(int(72 * _EMU_PER_PT)),
                    Emu(int(150 * _EMU_PER_PT)),
                    Emu(int((_W - 144) * _EMU_PER_PT)),
                    Emu(int(min(_H - 220, 34 * len(rows)) * _EMU_PER_PT)),
                )
                table = shape.table
                # PowerPoint applies a theme table style to every new table:
                # a banded blue that owes nothing to the deck's own accent. It
                # made the .pptx the odd one out — the preview and the .pdf
                # both draw accent-coloured headings over plain rows with a
                # rule under the head — and a preview that differs from the
                # .pptx is discovered in the room.
                table.first_row = False
                table.horz_banding = False
                for r, row in enumerate(rows):
                    for c, text in enumerate(row):
                        if c >= len(table.columns):
                            continue
                        cell = table.cell(r, c)
                        cell.fill.background()
                        # The rule under the head row is what separates the
                        # labels from the values; between the rows a hairline
                        # is enough, and round the outside nothing at all — a
                        # grid of thin lines closes up at projector distance.
                        if r == 0:
                            _cell_rule(cell, accent, 1.25)
                        elif r < len(rows) - 1:
                            _cell_rule(cell, muted, 0.5)
                        cell.text = ""
                        run = cell.text_frame.paragraphs[0].add_run()
                        run.text = text
                        paint(run, size=14, bold=r == 0, colour=accent if r == 0 else ink)
            elif bullets:
                # Two boxes side by side rather than one wide one — PowerPoint
                # has no column flow, so the split has to be geometry. Matches
                # the preview's `columnCount: 2` and the .pdf below.
                columns = _columns_of(data, bullets, layout)
                span = (text_width - (24 * (len(columns) - 1))) / len(columns)
                for column_index, column in enumerate(columns):
                    listing = _textbox(
                        slide,
                        left=72 + column_index * (span + 24),
                        top=150,
                        width=span,
                        height=_H - 210,
                    )
                    for position, text in enumerate(column):
                        paragraph = (
                            listing.paragraphs[0] if position == 0 else listing.add_paragraph()
                        )
                        paragraph.space_after = Pt(12)
                        marker = paragraph.add_run()
                        marker.text = "• "
                        paint(marker, size=18, bold=True, colour=accent)
                        run = paragraph.add_run()
                        run.text = text
                        paint(run, size=16 if len(columns) > 1 else 18)
            elif body:
                paragraph_frame = _textbox(
                    slide, left=72, top=150, width=text_width, height=_H - 210
                )
                run = paragraph_frame.paragraphs[0].add_run()
                run.text = body
                paint(run, size=16, colour=muted)

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body)
            box = (_W - 260, _H - 230) if alone else (_PICTURE_SPAN, _H - 230)
            width, height = _fit(image_bytes, box=box)
            left = 72 + (text_width + 24) if not alone else (_W - width) / 2
            top = 150 + max(0.0, (_H - 230 - height) / 2)
            try:
                slide.shapes.add_picture(
                    io.BytesIO(image_bytes),
                    Emu(int(left * _EMU_PER_PT)),
                    Emu(int(top * _EMU_PER_PT)),
                    Emu(int(width * _EMU_PER_PT)),
                    Emu(int(height * _EMU_PER_PT)),
                )
            except Exception as exc:  # noqa: BLE001 — one bad picture, not a failed export
                # Bytes that are not a picture any more: truncated on the way
                # in, or a format this library refuses. The slide loses its
                # illustration; the deck still opens.
                log.warning("could not place a picture in the pptx: %s", exc)
            else:
                if image_caption:
                    frame = _textbox(
                        slide, left=left, top=top + height + 6, width=max(width, 120), height=24
                    )
                    run = frame.paragraphs[0].add_run()
                    run.text = image_caption
                    paint(run, size=11, colour=muted)

        # Slide number, bottom right — what the room refers to in a question.
        number = _textbox(slide, left=_W - 110, top=_H - 46, width=60, height=26)
        number.paragraphs[0].alignment = PP_ALIGN.RIGHT
        run = number.paragraphs[0].add_run()
        run.text = str(index + 1)
        paint(run, size=11, colour=muted)

        notes = str(data.get("notes") or "").strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _pdf_chart(
    pdf,
    chart: dict,
    *,
    accent: tuple[float, float, float],
    muted: tuple[float, float, float],
    top: float,
    width: float,
    font: str,
) -> None:
    """The same chart, drawn.

    By hand rather than with `reportlab.graphics`: that package's charts bring
    their own type scale, their own axis conventions and their own palette,
    and reconciling those with the .pptx would take more code than the twenty
    lines of arithmetic below. What has to match is the shape a reader sees —
    the same bars in the same order over a zero floor — and that is arithmetic.

    Zero floor, again. A printed chart is the one people photograph.
    """
    left = 72.0
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

    # Four gridlines and their labels. More is a grid, fewer is not a scale.
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
            # The same circles the .pptx puts on its line series. A bare line
            # hides where the readings actually are, and on a five-point series
            # that is most of what the chart says.
            pdf.setFillColorRGB(*colour)
            for x, y in points:
                pdf.circle(x, y, 3, stroke=0, fill=1)
        else:
            # Bars share the slot between two category ticks, so two series
            # stand side by side rather than one behind the other.
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

    # A legend, when there is more than one line to tell apart. Without it the
    # printed chart has two lines and no way to say which is which — the .pptx
    # has had one since it was drawn.
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
    """A top of scale that is a number a reader recognises.

    `highest * 1.15` gives gridlines at 132, 264, 397 — arithmetic nobody reads
    as a scale. PowerPoint rounds the top of its own charts, so this has to as
    well, or the same deck is read off two different scales depending on which
    file somebody opened.

    The *top* is rounded, not the step. Rounding the step is the obvious move
    and it wastes the chart: a series topping out at 460 has a step of 115,
    the next round step up is 200, and the bars end up filling a little over
    half of a chart that runs to 800.
    """
    import math

    if highest <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(highest))
    for multiple in (1, 2, 2.5, 5, 10):
        if highest <= multiple * power:
            return multiple * power
    return highest


def _tick_label(value: float) -> str:
    """A gridline's number, without a decimal point nobody asked for."""
    return f"{value:,.0f}" if abs(value) >= 10 else f"{value:,.1f}".rstrip("0").rstrip(".")


def _typescale(slide: dict) -> float:
    """This slide's own type size, as a multiple, clamped to something sane.

    Set in the panel — 크게 / 보통 / 작게 on one slide. Read here rather than
    trusted: it arrives on an artifact a person can PATCH, and a slide whose
    words are forty times the size of the paper is a file nobody can open.
    """
    try:
        value = float(slide.get("textScale") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.5, value))


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy wrap by measured width.

    Breaks between characters, not words: Korean has no spaces to break on, and
    a word-only wrap runs off the right edge.
    """
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
    """The deck as a PDF, one slide per page.

    For projecting, so it is deliberately the presentation and not the notes:
    what is on the page is what the room sees.

    Same rule as `to_pptx`: with no design system this draws what it always
    drew, down to the greys.
    """
    style = design.normalise_tokens(tokens) if tokens else None
    font = fonts.korean(style["font"] if style else "gothic")
    ink = _hex_floats(style["ink"]) if style else _PDF_INK
    muted = _hex_floats(style["muted"]) if style else _PDF_MUTED
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(_W, _H))
    pdf.setTitle(title)

    for index, data in enumerate(slides):
        accent = _hex_floats(data.get("accent"))
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
        # The same split the .pptx uses, so the printout and the projected deck
        # put the same things in the same places.
        text_width = _W - 144 - (
            _PICTURE_SPAN + 24
            if picture and (bullets or rows or metrics or chart or body)
            else 0
        )

        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
        pdf.setFillColorRGB(*accent)
        pdf.rect(0, 0, 9, _H, stroke=0, fill=1)

        # This slide's own type size, applied to the sizes *and* to the line
        # advances they are paired with. Scaling only the glyphs would set 26pt
        # words on 34pt leading and they would sit on each other — which is the
        # reason the `.pptx` and the `.pdf` are scaled in different places at
        # all: PowerPoint reflows a text box and this file does not.
        ts = _typescale(data)

        def S(n: float, _ts: float = ts) -> float:
            return n * _ts

        if layout == "title" and index == 0:
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, S(40))
            y = _H / 2 + 20
            for line in _wrap(heading or title, font, S(40), _W - 144):
                pdf.drawString(72, y, line)
                y -= S(50)
            if body:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, S(15))
                for line in _wrap(body, font, S(15), _W - 144)[:2]:
                    pdf.drawString(72, y - 6, line)
                    y -= S(22)
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
            pdf.setFont(font, S(26))
            y = _H - 96
            for line in _wrap(heading, font, S(26), text_width):
                pdf.drawString(72, y, line)
                y -= S(34)
            y -= 24

            if chart:
                _pdf_chart(
                    pdf, chart, accent=accent, muted=muted, top=y, width=text_width, font=font
                )
            elif metrics:
                # The same geometry the .pptx uses, so the printout and the
                # projected deck put the same figures in the same places.
                span = (text_width - 24 * (len(metrics) - 1)) / len(metrics)
                for position, (figure, label) in enumerate(metrics):
                    left = 72 + position * (span + 24)
                    pdf.setFillColorRGB(*accent)
                    pdf.setFont(font, S(44))
                    pdf.drawString(left, y - S(40), figure)
                    pdf.setFillColorRGB(*muted)
                    pdf.setFont(font, S(14))
                    # 30pt under a 44pt figure. At 22 the label sat on the
                    # numeral's baseline and the two read as one word.
                    pdf.drawString(left, y - S(70), label)
            elif rows:
                # Ruled rather than boxed: a printed grid of thin lines closes
                # up at projector distance, and the rule under the header is
                # what actually separates the labels from the values.
                width = text_width / max(len(row) for row in rows)
                for row_index, row in enumerate(rows):
                    pdf.setFillColorRGB(*(accent if row_index == 0 else ink))
                    pdf.setFont(font, S(15))
                    for cell_index, cell in enumerate(row):
                        text = _wrap(cell, font, S(15), width - 12)
                        pdf.drawString(72 + cell_index * width, y, text[0] if text else "")
                    if row_index == 0:
                        pdf.setStrokeColorRGB(*accent)
                        pdf.setLineWidth(1)
                        pdf.line(72, y - 8, _W - 72, y - 8)
                    y -= S(30)
                    if y < 60:
                        break
            elif bullets:
                # Same split as the .pptx, so the printout and the projected
                # deck put the same items in the same places.
                columns = _columns_of(data, bullets, layout)
                size = S(16 if len(columns) > 1 else 18)
                step = S(22 if len(columns) > 1 else 26)
                span = (text_width - 24 * (len(columns) - 1)) / len(columns)
                top = y
                for column_index, column in enumerate(columns):
                    left = 72 + column_index * (span + 24)
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
                    pdf.drawString(72, y, line)
                    y -= S(24)

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or metrics or chart or body)
            box = (_W - 260, _H - 230) if alone else (_PICTURE_SPAN, _H - 230)
            width, height = _fit(image_bytes, box=box)
            left = 72 + text_width + 24 if not alone else (_W - width) / 2
            bottom = 90 + max(0.0, (_H - 230 - height) / 2)
            try:
                pdf.drawImage(
                    ImageReader(io.BytesIO(image_bytes)),
                    left,
                    bottom,
                    width=width,
                    height=height,
                    mask="auto",
                )
            except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                log.warning("could not draw a picture into the deck pdf: %s", exc)
            else:
                if image_caption:
                    pdf.setFillColorRGB(*muted)
                    pdf.setFont(font, 11)
                    pdf.drawString(left, bottom - 16, _wrap(image_caption, font, 11, width)[0])

        # Furniture rather than content, so it keeps its own light grey instead
        # of taking the design's muted tone: a page number in brand colour
        # reads as something to look at.
        pdf.setFillColorRGB(0.55, 0.55, 0.55)
        pdf.setFont(font, 11)
        pdf.drawRightString(_W - 50, 30, str(index + 1))
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


__all__ = ["to_pdf", "to_pptx"]
