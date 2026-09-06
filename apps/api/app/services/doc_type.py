"""One type scale for documents.

Sizes are PowerPoint-style points, the unit every renderer of a report already thinks in:
the A4 page view and the printed PDF (CSS `pt`), the reportlab fallback, the `.docx`
(`Pt`) and the `.hwpx` (1/100 pt). `apps/web/src/components/report/docType.ts` carries
the same table for the web view, and a test keeps the two equal. A document never
shrinks its type to fit; it takes another page.
"""

from __future__ import annotations

TYPE: dict[str, float] = {
    "title": 20,
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


__all__ = ["CSS_VARIABLES", "LEADING", "TYPE", "hwp", "pt", "px"]
