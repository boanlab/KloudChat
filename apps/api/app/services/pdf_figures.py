"""Figures and tables of a paper PDF, found by their captions and cut out as pictures.

A slide about a paper shows the paper's own figure (「Figure 2. …」) as the paper drew it,
captioned and credited, instead of a redrawn guess. PDFium (pypdfium2, Apache-2.0 /
BSD-3-Clause) reads the caption lines with their positions and the page's drawing objects;
a figure is the drawing above its caption in the caption's column, a table the ruled block
below its caption. What cannot be placed with confidence is left out rather than cut wrong."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: 「Figure 3:」, 「Fig. 3.」, 「Table 2:」, 「그림 3.」, 「표 2」 at the start of a line
_CAPTION = re.compile(
    r"^[ \t]{0,4}(?P<kind>Figure|Fig\.|Table|그림|표)[ \t]{0,2}(?P<number>\d{1,3})"
    r"(?P<tail>[ \t]{0,2}[.:|][^\n]{0,400}|(?<=[그림표] \d)[ \t]{1,2}[^\n]{0,400})$",
    re.M,
)
#: pages read at most, and pictures cut per call
MAX_PAGES = 60
MAX_CUTS = 8
#: render scale (72 dpi × scale)
_SCALE = 2.5
#: smallest region kept, in points
_MIN_W, _MIN_H = 60.0, 40.0


@dataclass(frozen=True)
class Figure:
    #: 「figure」 or 「table」
    kind: str
    number: int
    #: the caption's first lines, as printed
    caption: str
    #: 1-based
    page: int
    #: (left, bottom, right, top) in PDF points, origin bottom-left
    box: tuple[float, float, float, float]

    @property
    def label(self) -> str:
        return f"{'Figure' if self.kind == 'figure' else 'Table'} {self.number}"


def _kind(word: str) -> str:
    return "table" if word in ("Table", "표") else "figure"


def _line_box(textpage, start: int, end: int) -> tuple[float, float, float, float] | None:
    boxes = []
    for index in range(start, end):
        try:
            box = textpage.get_charbox(index)
        except Exception:  # noqa: BLE001 — a generated char (line break) has no box
            continue
        if box and box[2] > box[0]:
            boxes.append(box)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _drawings(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    """(kind, box) of the page's non-text objects (paths, images, forms)."""
    import pypdfium2.raw as raw

    width, height = page.get_size()
    out = []
    for obj in page.get_objects(max_depth=1):
        if obj.type == raw.FPDF_PAGEOBJ_TEXT:
            continue
        try:
            bounds = obj.get_bounds if hasattr(obj, "get_bounds") else obj.get_pos
            left, bottom, right, top = bounds()
        except Exception:  # noqa: BLE001 — an object without bounds is skipped
            continue
        # a form's bounds may run past the page (its clip is not applied): the page clips it
        left, right = max(left, 0.0), min(right, width)
        bottom, top = max(bottom, 0.0), min(top, height)
        if right <= left or top <= bottom:
            continue
        out.append(("image" if obj.type == raw.FPDF_PAGEOBJ_IMAGE else "path",
                    (left, bottom, right, top)))
    return out


def _column(box, width: float) -> tuple[float, float]:
    """Horizontal span the caption's figure may take: the whole page for a caption that
    crosses the middle, else the caption's half."""
    left, _bottom, right, _top = box
    middle = width / 2
    if left < middle - 20 and right > middle + 20:
        return 0.0, width
    return (0.0, middle + 6) if right <= middle + 20 else (middle - 6, width)


def _overlaps(a: tuple[float, float], left: float, right: float) -> bool:
    return min(a[1], right) - max(a[0], left) > 0.5 * min(a[1] - a[0], right - left, 40)


def _figure_box(drawings, caption_box, others, span, height):
    """Union of the drawings above the caption in its column, up to the caption above it."""
    left, right = span
    ceiling = min([b[1] for b in others if b[1] > caption_box[3] + 2] + [height])
    picked = [(max(box[0], left), box[1], min(box[2], right), box[3]) for _, box in drawings
              if _overlaps((box[0], box[2]), left, right)
              and box[1] >= caption_box[3] - 4 and box[3] <= ceiling + 2]
    # a drawing that is only a long thin rule (a header line) is no figure
    picked = [b for b in picked if (b[3] - b[1]) > 2 or (b[2] - b[0]) < (right - left) * 0.9]
    if not picked:
        return None
    # nearest block first: grow upward while the gap to the next drawing stays small
    picked.sort(key=lambda b: b[1])
    bottom = picked[0][1]
    if bottom - caption_box[3] > 60:
        return None
    top = picked[0][3]
    chosen = [picked[0]]
    for box in picked[1:]:
        if box[1] - top > 40:
            break
        chosen.append(box)
        top = max(top, box[3])
    return (min(b[0] for b in chosen) - 3, min(b[1] for b in chosen) - 3,
            max(b[2] for b in chosen) + 3, max(b[3] for b in chosen) + 3)


def _table_box(drawings, caption_box, others, span):
    """The ruled block under a table caption: from the caption down to its last rule."""
    left, right = span
    floor = max([b[3] for b in others if b[3] < caption_box[1] - 2] + [0.0])
    rules = sorted((box for _, box in drawings
                    if _overlaps((box[0], box[2]), left, right)
                    and box[3] <= caption_box[1] + 2 and box[1] >= floor - 2
                    and (box[3] - box[1]) <= 3),
                   key=lambda b: -b[3])
    if len(rules) < 2:
        return None
    last = rules[0]
    if caption_box[1] - last[3] > 40:
        return None
    for box in rules[1:]:
        if last[1] - box[3] > 220:
            break
        last = box
    top = caption_box[1] - 1
    if top - last[1] < _MIN_H:
        return None
    return (min(r[0] for r in rules) - 3, last[1] - 4,
            max(r[2] for r in rules) + 3, top)


def find(data: bytes) -> list[Figure]:
    """Every figure and table a caption names, in page order, with where it is drawn."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    found: list[Figure] = []
    seen: set[tuple[str, int]] = set()
    try:
        for index in range(min(len(document), MAX_PAGES)):
            page = document[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            captions = []
            for match in _CAPTION.finditer(text):
                box = _line_box(textpage, match.start("kind"), match.end())
                if box is None:
                    continue
                captions.append((match, box))
            if not captions:
                continue
            drawings = _drawings(page)
            boxes = [box for _, box in captions]
            for match, box in captions:
                kind = _kind(match.group("kind"))
                number = int(match.group("number"))
                if (kind, number) in seen:
                    # a later 「Figure 3」 is a mention in the text, not the caption
                    continue
                span = _column(box, width)
                others = [b for b in boxes if b is not box]
                region = (_table_box(drawings, box, others, span) if kind == "table"
                          else _figure_box(drawings, box, others, span, height))
                if region is None:
                    continue
                region = (max(region[0], 0.0), max(region[1], 0.0),
                          min(region[2], width), min(region[3], height))
                if region[2] - region[0] < _MIN_W or region[3] - region[1] < _MIN_H:
                    continue
                caption = _caption(textpage, text, match, box)
                seen.add((kind, number))
                found.append(Figure(kind, number, caption, index + 1, region))
            textpage.close()
            page.close()
    finally:
        document.close()
    return found


def _caption(textpage, text: str, match: re.Match, box) -> str:
    """The caption as printed: its first line and the lines under it that start where it
    starts (a caption wraps inside its column; text of the other column read in between is
    not part of it), up to three lines and 300 characters."""
    from app.services.pdf_text import _tidy

    lines = [match.group(0).strip()]
    left, bottom = box[0], box[1]
    at = match.end()
    for _ in range(3):
        if lines[-1].endswith("."):
            # a finished sentence: under a table caption the next line is the table
            break
        start = text.find("\n", at)
        if start < 0:
            break
        start += 1
        end = text.find("\n", start)
        end = len(text) if end < 0 else end
        line = text[start:end].strip()
        place = _line_box(textpage, start, end) if line else None
        if (not line or place is None or abs(place[0] - left) > 14 or place[3] > bottom + 1
                or bottom - place[3] > 14 or _CAPTION.match(line)):
            break
        lines.append(line)
        bottom = place[1]
        at = end
    return " ".join(_tidy("\n".join(lines)).split())[:300]


def cut(data: bytes, figure: Figure) -> bytes:
    """PNG of `figure`'s region, rendered at print-like resolution."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        page = document[figure.page - 1]
        _width, height = page.get_size()
        picture = page.render(scale=_SCALE).to_pil()
        left, bottom, right, top = figure.box
        crop = (int(left * _SCALE), int((height - top) * _SCALE),
                int(right * _SCALE), int((height - bottom) * _SCALE))
        out = io.BytesIO()
        picture.crop(crop).save(out, format="PNG", optimize=True)
        page.close()
        return out.getvalue()
    finally:
        document.close()
