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
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services import design, fonts

#: 16:9 in points — EMU/12700, and reportlab's default unit. Drives both
#: exporters.
_W, _H = 960.0, 540.0
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


def to_pptx(title: str, slides: list[dict], *, tokens: dict[str, str] | None = None) -> bytes:
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

    def paint(run, *, size: int, bold: bool = False, colour: RGBColor | None = None) -> None:
        _font(run, size=size, bold=bold, colour=colour or ink, faces=faces)

    presentation = Presentation()
    presentation.slide_width = Emu(int(_W * _EMU_PER_PT))
    presentation.slide_height = Emu(int(_H * _EMU_PER_PT))
    blank = presentation.slide_layouts[6]

    for index, data in enumerate(slides):
        slide = presentation.slides.add_slide(blank)
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
            frame = _textbox(slide, left=72, top=64, width=_W - 144, height=60)
            run = frame.paragraphs[0].add_run()
            run.text = heading
            paint(run, size=26, bold=True)

            if bullets:
                # Two boxes side by side rather than one wide one — PowerPoint
                # has no column flow, so the split has to be geometry. Matches
                # the preview's `columnCount: 2` and the .pdf below.
                columns = _split_columns(bullets) if layout == "two-column" else [bullets]
                span = (_W - 144 - (24 * (len(columns) - 1))) / len(columns)
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
                paragraph_frame = _textbox(slide, left=72, top=150, width=_W - 144, height=_H - 210)
                run = paragraph_frame.paragraphs[0].add_run()
                run.text = body
                paint(run, size=16, colour=muted)

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
            for line in _wrap(heading, font, 26, _W - 144):
                pdf.drawString(72, y, line)
                y -= 34
            y -= 24

            if bullets:
                # Same split as the .pptx, so the printout and the projected
                # deck put the same items in the same places.
                columns = _split_columns(bullets) if layout == "two-column" else [bullets]
                size = 16 if len(columns) > 1 else 18
                step = 22 if len(columns) > 1 else 26
                span = (_W - 144 - 24 * (len(columns) - 1)) / len(columns)
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
                for line in _wrap(body, font, 16, _W - 144):
                    pdf.drawString(72, y, line)
                    y -= 24

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
