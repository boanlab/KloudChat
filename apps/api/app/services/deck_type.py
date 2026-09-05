"""One type scale for the deck.

Sizes are in the panel's 400x225 slide units. The panel draws them as pixels times its
zoom; the `.pptx` and `.pdf` draw a 960x540-point page, so a size crosses over
multiplied by `K`. `apps/web/src/components/slides/typeScale.ts` carries the same table
for the panel, and a test keeps the two equal, so a slide that fits in the panel fits in
the file.
"""

from __future__ import annotations

#: Points per slide unit: a 960-point page over a 400-unit panel.
K = 2.4

#: Sizes in PowerPoint points. Slide titles are 32pt; the body starts at 22pt and steps
#: down the `STEPS` ladder when a slide overflows, never up; nothing is drawn under 12pt.
TYPE: dict[str, float] = {
    # Covers.
    "cover": 36,
    "coverPoster": 40,
    "coverMono": 40,
    "closing": 32,
    "coverBody": 18,
    "closingBody": 18,
    "closingBullets": 18,
    "sectionNumber": 18,
    "splitNumber": 80,
    # Body slides.
    "title": 32,
    "body": 22,
    "bodyNarrow": 18,
    "paragraph": 18,
    "agenda": 18,
    "agendaNumber": 22,
    "statement": 32,
    "statementBody": 18,
    "quote": 28,
    "quoteBy": 16,
    "bigNumber": 64,
    "bigNumberLabel": 18,
    "bigNumberBody": 18,
    "metric": 44,
    "metricLabel": 16,
    "cardName": 18,
    "cardText": 16,
    "stepBadge": 16,
    "stepName": 18,
    "stepText": 16,
    "tileMark": 36,
    "tileName": 16,
    "bandMin": 14,
    "bandMax": 18,
    "lineMin": 14,
    "lineMax": 18,
    "tableMin": 12,
    "tableMax": 16,
    "caption": 14,
    "footer": 12,
    "pageNumber": 12,
    "posterNumber": 30,
    "gutterNumber": 22,
}

#: The body ladder, in points: a slide that overflows at 22 tries 18, then 16, 14, 12.
STEPS: tuple[int, ...] = (22, 18, 16, 14, 12)
#: The same ladder as `textScale` values, which every renderer multiplies its sizes by.
SCALES: tuple[float, ...] = tuple(round(step / STEPS[0], 4) for step in STEPS)
#: No text is drawn smaller than this, whatever the scale.
FLOOR_PT = 12
#: Slide titles are 32pt; one that would wrap steps down to 30, then 28, and stays at 28
#: on two lines if it still does not fit.
TITLE_STEPS: tuple[int, ...] = (32, 30, 28)

#: Line heights, as a multiple of the size.
LEADING: dict[str, float] = {
    "title": 1.25,
    "body": 1.6,
    "paragraph": 1.6,
    "agenda": 1.5,
    "cardText": 1.5,
    "stepText": 1.5,
    "band": 1.5,
    "line": 1.5,
    "table": 1.4,
}

#: Space between bullet items, as a multiple of the body size.
BULLET_GAP = 0.35

#: Body-slide geometry, in slide units: where the body box starts and ends, and the
#: text column's side padding. The title sits above the box; each extra title line
#: pushes the box down by one title line.
BODY_TOP = 66.5
BODY_BOTTOM = 190
PAD_X = 28
TITLE_WIDTH = 400 - 2 * PAD_X


def em(text: str) -> float:
    """Width of `text` in ems: a Hangul or CJK glyph is one em, anything else about half."""
    width = 0.0
    for char in str(text or ""):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3 or 0x3000 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
            width += 1.0
        elif char == " ":
            width += 0.3
        else:
            width += 0.55
    return width


def lines(text: str, size: float, width: float) -> int:
    """How many lines `text` takes at `size` in a column `width` units wide."""
    if not str(text or "").strip():
        return 0
    per_line = max(1.0, width / size)
    return max(1, -(-int(em(text) * 100) // int(per_line * 100)))


def pt(name: str, scale: float = 1.0) -> float:
    """The size in points for the exporters, at the slide's text scale, never under the floor."""
    return max(FLOOR_PT, TYPE[name] * scale) if scale < 1 else TYPE[name]


def units(name: str) -> float:
    """The size in the panel's slide units."""
    return TYPE[name] / K


def title_pt(title: str, width: float | None = None) -> float:
    """The title's size in points: 32 when it fits one line, else the first of 30 and 28
    that does, else 28. `width` is the title column in slide units.
    """
    column = TITLE_WIDTH if width is None else width
    for size in TITLE_STEPS:
        if lines(title, size / K, column) <= 1:
            return float(size)
    return float(TITLE_STEPS[-1])


def table_size(rows: int) -> float:
    """Table cell size from the row count, so the table stays above the footer.

    One row in reserve for a cell that wraps; the same rule in every renderer.
    """
    per_row = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
    return max(units("tableMin"), min(units("tableMax"), per_row / 2.05))


def table_pad(rows: int) -> float:
    """Cell padding above and below the text that goes with `table_size`, in slide units."""
    per_row = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
    return max(2.0, (per_row - table_size(rows) * LEADING["table"]) / 2)


def table_row_height(rows: int) -> float:
    """The row height that goes with `table_size`, in slide units."""
    return table_size(rows) * LEADING["table"] + 2 * table_pad(rows)


def column_shares(rows: list[list[str]]) -> list[float]:
    """Each column's share of the table width, from its widest cell.

    Columns are weighted by the widest cell's em width, held between three and twenty-two
    ems so one long cell cannot starve the others, plus two ems of padding.
    """
    count = max((len(row) for row in rows), default=0)
    if not count:
        return []
    weights = []
    for column in range(count):
        widest = max((em(row[column]) for row in rows if column < len(row)), default=0.0)
        weights.append(max(3.0, min(22.0, widest)) + 2.0)
    total = sum(weights)
    return [weight / total for weight in weights]


__all__ = [
    "BODY_BOTTOM",
    "BODY_TOP",
    "BULLET_GAP",
    "FLOOR_PT",
    "K",
    "SCALES",
    "STEPS",
    "LEADING",
    "PAD_X",
    "TITLE_STEPS",
    "TITLE_WIDTH",
    "TYPE",
    "column_shares",
    "em",
    "lines",
    "pt",
    "table_pad",
    "table_row_height",
    "table_size",
    "title_pt",
    "units",
]
