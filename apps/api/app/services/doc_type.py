"""One type scale for documents.

Sizes are points, shared by the page view, PDF, `.docx` and `.hwpx` (1/100 pt).
`apps/web/src/components/report/docType.ts` mirrors the table; a test keeps them equal.
A document never shrinks its type to fit; it takes another page.
"""

from __future__ import annotations

TYPE: dict[str, float] = {
    "title": 20,
    #: A cover page's title and the kind label above it (`head="cover"`).
    "cover": 26,
    "coverLabel": 12,
    "lead": 11.5,
    "h1": 14,
    "h2": 12,
    "h3": 11,
    "body": 10.5,
    "table": 9.5,
    "caption": 9,
    "note": 9,
    "small": 9,
    "kpi": 20,
    "kpiLabel": 9,
    "sectionNumber": 10.5,
    "pageNumber": 9,
}

#: Line height as a multiple of the size.
LEADING: dict[str, float] = {
    "title": 1.25,
    "heading": 1.35,
    "body": 1.6,
    "table": 1.45,
    "note": 1.5,
}

#: A format's own scale over `TYPE` and `LEADING`, by `doc_formats` id. The 개조식 보고 is a
#: one-page 공문서-style memo: a larger body at 160%, headings two points over it, the
#: title boxed above. 15 pt, the common 휴먼명조 size, holds about 25 lines on A4; the
#: one-page memos this format writes run to 30–35, which fit at 12 pt. Its sizes replace
#: the shared ones wherever that format is drawn.
FORMAT_TYPE: dict[str, dict[str, float]] = {
    "gaejo": {
        "title": 18,
        "h1": 14,
        "h2": 13,
        "h3": 12,
        "body": 12,
        "table": 10.5,
        "caption": 10.5,
        "note": 10.5,
        "small": 10,
        "sectionNumber": 14,
    },
}

FORMAT_LEADING: dict[str, dict[str, float]] = {
    "gaejo": {"heading": 1.3, "body": 1.6, "table": 1.35, "note": 1.5},
}

#: Vertical space in points around a format's blocks, where the shared layout's own
#: spacing would take the page: before and after a section heading, after a list item,
#: around the 핵심 box, and under the title block.
FORMAT_SPACE: dict[str, dict[str, float]] = {
    "gaejo": {"headingBefore": 7, "headingAfter": 2, "item": 0, "callout": 4, "head": 6},
}

#: A format's page margins in mm (top, bottom, left, right) when the document sets none.
FORMAT_MARGINS: dict[str, dict[str, float]] = {
    "gaejo": {"top": 20, "bottom": 15, "left": 20, "right": 20},
}


def type_for(format_id: str | None) -> dict[str, float]:
    """`TYPE` with the format's own sizes over it."""
    return {**TYPE, **FORMAT_TYPE.get(format_id or "", {})}


def leading_for(format_id: str | None) -> dict[str, float]:
    """`LEADING` with the format's own ratios over it."""
    return {**LEADING, **FORMAT_LEADING.get(format_id or "", {})}


def space_for(format_id: str | None) -> dict[str, float] | None:
    """The format's own spacing, or None where the shared layout spaces it."""
    return FORMAT_SPACE.get(format_id or "")


def margins_for(format_id: str | None) -> dict[str, float] | None:
    """The format's own page margins in mm, or None."""
    return FORMAT_MARGINS.get(format_id or "")


#: CSS custom properties the page seeds read, in `pt`; `--doc-leading` is a ratio.
CSS_VARIABLES: tuple[tuple[str, str], ...] = tuple(
    [(f"doc-{name}", f"{size:g}pt") for name, size in TYPE.items()]
    + [(f"doc-leading-{name}", f"{ratio:g}") for name, ratio in LEADING.items()]
)


def pt(name: str) -> float:
    """The size in points."""
    return TYPE[name]


def px(name: str) -> float:
    """The size in CSS pixels at 96 dpi, for the web view."""
    return round(TYPE[name] * 4 / 3, 2)


def hwp(name: str) -> int:
    """The size as HWP character height, 1/100 pt."""
    return int(round(TYPE[name] * 100))


__all__ = [
    "CSS_VARIABLES",
    "FORMAT_LEADING",
    "FORMAT_MARGINS",
    "FORMAT_SPACE",
    "FORMAT_TYPE",
    "LEADING",
    "TYPE",
    "hwp",
    "leading_for",
    "margins_for",
    "pt",
    "px",
    "space_for",
    "type_for",
]
