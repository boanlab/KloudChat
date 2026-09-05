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

TYPE: dict[str, float] = {
    # Covers.
    "cover": 27,
    "coverPoster": 30,
    "coverMono": 32,
    "closing": 24,
    "coverBody": 13,
    "closingBody": 15,
    "closingBullets": 12,
    "sectionNumber": 15,
    "splitNumber": 34,
    # Body slides.
    "title": 18,
    "body": 12,
    "bodyNarrow": 10.5,
    "paragraph": 11.5,
    "agenda": 11,
    "agendaNumber": 13,
    "statement": 26,
    "statementBody": 11,
    "quote": 20,
    "quoteBy": 12,
    "bigNumber": 46,
    "bigNumberLabel": 11,
    "bigNumberBody": 11,
    "metric": 30,
    "metricLabel": 11,
    "cardName": 11,
    "cardText": 9.5,
    "stepBadge": 9,
    "stepName": 11,
    "stepText": 9.5,
    "tileMark": 26,
    "tileName": 10,
    "bandMin": 7,
    "bandMax": 10,
    "lineMin": 7,
    "lineMax": 10,
    "tableMin": 7.5,
    "tableMax": 12,
    "caption": 10,
    "footer": 7.5,
    "pageNumber": 8,
    "posterNumber": 28,
    "gutterNumber": 18,
}

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
    """The size in points for the exporters, at the slide's text scale."""
    return TYPE[name] * K * scale


def table_size(rows: int) -> float:
    """Table cell size from the row count, so the table stays above the footer.

    One row in reserve for a cell that wraps; the same rule in every renderer.
    """
    per_row = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
    return max(TYPE["tableMin"], min(TYPE["tableMax"], per_row / 2.05))


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
    "K",
    "LEADING",
    "PAD_X",
    "TITLE_WIDTH",
    "TYPE",
    "column_shares",
    "em",
    "lines",
    "pt",
    "table_pad",
    "table_row_height",
    "table_size",
]
