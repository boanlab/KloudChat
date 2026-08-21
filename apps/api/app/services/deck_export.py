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

from app.services import design, fonts, pictures

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

    def paint(run, *, size: int, bold: bool = False, colour: RGBColor | None = None) -> None:
        _font(run, size=size, bold=bold, colour=colour or ink, faces=faces)

    presentation = Presentation()
    presentation.slide_width = Emu(int(_W * _EMU_PER_PT))
    presentation.slide_height = Emu(int(_H * _EMU_PER_PT))
    blank = presentation.slide_layouts[6]

    for index, data in enumerate(slides):
        slide = presentation.slides.add_slide(blank)
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
        picture = _picture_of(data)
        # A picture takes the right half and the words keep the left. Alone, it
        # takes the middle of the slide. Either way the text is narrowed here
        # rather than overlapping it, which is what a slide would show.
        text_width = _W - 144 - (_PICTURE_SPAN + 24 if picture and (bullets or rows or body) else 0)

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

            if rows:
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
                for r, row in enumerate(rows):
                    for c, text in enumerate(row):
                        if c >= len(table.columns):
                            continue
                        cell = table.cell(r, c)
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
            alone = not (bullets or rows or body)
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
        picture = _picture_of(data)
        # The same split the .pptx uses, so the printout and the projected deck
        # put the same things in the same places.
        text_width = _W - 144 - (_PICTURE_SPAN + 24 if picture and (bullets or rows or body) else 0)

        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(0, 0, _W, _H, stroke=0, fill=1)
        pdf.setFillColorRGB(*accent)
        pdf.rect(0, 0, 9, _H, stroke=0, fill=1)

        if layout == "title" and index == 0:
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, 40)
            y = _H / 2 + 20
            for line in _wrap(heading or title, font, 40, _W - 144):
                pdf.drawString(72, y, line)
                y -= 50
            if body:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, 15)
                for line in _wrap(body, font, 15, _W - 144)[:2]:
                    pdf.drawString(72, y - 6, line)
                    y -= 22
        elif layout == "quote":
            pdf.setFillColorRGB(*accent)
            pdf.setFont(font, 30)
            y = _H / 2 + 40
            for line in _wrap(f"“{body or heading}”", font, 30, _W - 200):
                pdf.drawString(90, y, line)
                y -= 40
            if body and heading:
                pdf.setFillColorRGB(*muted)
                pdf.setFont(font, 13)
                pdf.drawString(90, y - 8, heading)
        else:
            pdf.setFillColorRGB(*ink)
            pdf.setFont(font, 26)
            y = _H - 96
            for line in _wrap(heading, font, 26, text_width):
                pdf.drawString(72, y, line)
                y -= 34
            y -= 24

            if rows:
                # Ruled rather than boxed: a printed grid of thin lines closes
                # up at projector distance, and the rule under the header is
                # what actually separates the labels from the values.
                width = text_width / max(len(row) for row in rows)
                for row_index, row in enumerate(rows):
                    pdf.setFillColorRGB(*(accent if row_index == 0 else ink))
                    pdf.setFont(font, 15)
                    for cell_index, cell in enumerate(row):
                        text = _wrap(cell, font, 15, width - 12)
                        pdf.drawString(72 + cell_index * width, y, text[0] if text else "")
                    if row_index == 0:
                        pdf.setStrokeColorRGB(*accent)
                        pdf.setLineWidth(1)
                        pdf.line(72, y - 8, _W - 72, y - 8)
                    y -= 30
                    if y < 60:
                        break
            elif bullets:
                # Same split as the .pptx, so the printout and the projected
                # deck put the same items in the same places.
                columns = _columns_of(data, bullets, layout)
                size = 16 if len(columns) > 1 else 18
                step = 22 if len(columns) > 1 else 26
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
                pdf.setFont(font, 16)
                for line in _wrap(body, font, 16, text_width):
                    pdf.drawString(72, y, line)
                    y -= 24

        if picture:
            image_bytes, image_caption = picture
            alone = not (bullets or rows or body)
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
