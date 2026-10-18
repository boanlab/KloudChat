"""Deck type scale in 400x225 panel units (×`K` → pt); mirrors web typeScale.ts."""

from __future__ import annotations

#: points per slide unit (960 / 400)
K = 2.4

#: PowerPoint pt; title 26, body 18 (22 on a sparse deck) down the `STEPS` ladder, floor 12
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
    "title": 26,
    "body": 18,
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
    # a card row whose texts are three lines or fewer: number, larger name and text fill the card
    "cardMark": 32,
    "cardNameShort": 22,
    "cardTextShort": 18,
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

#: body ladder (pt); 18 is the body size, 22 a sparse deck's
STEPS: tuple[int, ...] = (22, 18, 16, 14, 12)
#: ladder as `textScale` multipliers of the 18 pt body (1.0 = 18 pt)
SCALES: tuple[float, ...] = tuple(round(step / TYPE["body"], 4) for step in STEPS)
FLOOR_PT = 12
#: title 26, wrapping to a second line rather than shrinking
TITLE_STEPS: tuple[int, ...] = (26,)

#: line height ratio
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

#: bullet gap, × body size
BULLET_GAP = 0.35

#: body box geometry (slide units); each extra title line pushes the box down
BODY_TOP = 60
BODY_BOTTOM = 190
PAD_X = 28
TITLE_WIDTH = 400 - 2 * PAD_X


def em(text: str) -> float:
    """Width in ems: Hangul/CJK 1, else ~0.5."""
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
    """Line count of `text` at `size` in a `width`-unit column."""
    if not str(text or "").strip():
        return 0
    per_line = max(1.0, width / size)
    return max(1, -(-int(em(text) * 100) // int(per_line * 100)))


def pt(name: str, scale: float = 1.0) -> float:
    """Exporter size in pt at the slide's text scale, floored."""
    return max(FLOOR_PT, TYPE[name] * scale)


def units(name: str) -> float:
    """Size in panel slide units."""
    return TYPE[name] / K


def title_pt(title: str, width: float | None = None) -> float:
    """Title size in pt: 26 (a long title wraps)."""
    column = TITLE_WIDTH if width is None else width
    for size in TITLE_STEPS:
        if lines(title, size / K, column) <= 1:
            return float(size)
    return float(TITLE_STEPS[-1])


def table_size(rows: int) -> float:
    """Table cell size from row count, one row in reserve; same rule in every renderer."""
    per_row = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
    return max(units("tableMin"), min(units("tableMax"), per_row / 2.05))


def table_pad(rows: int) -> float:
    """Cell padding for `table_size`, in slide units."""
    per_row = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
    return max(2.0, (per_row - table_size(rows) * LEADING["table"]) / 2)


def table_row_height(rows: int) -> float:
    """Row height for `table_size`, in slide units."""
    return table_size(rows) * LEADING["table"] + 2 * table_pad(rows)


def column_shares(rows: list[list[str]]) -> list[float]:
    """Column width shares by widest cell (3–22 ems, plus 2 padding)."""
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
