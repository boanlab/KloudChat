"""Document type scale in points; mirrors web docType.ts (test-enforced)."""

from __future__ import annotations

TYPE: dict[str, float] = {
    "title": 20,
    #: cover title and kind label (`head="cover"`)
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

#: line height ratio
LEADING: dict[str, float] = {
    "title": 1.25,
    "heading": 1.35,
    "body": 1.6,
    "table": 1.45,
    "note": 1.5,
}

#: per-format overrides of `TYPE`/`LEADING`; 개조식 보고 = one-page memo, 12 pt fits 30–35 lines
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

#: per-format vertical spacing (pt) where the shared layout would overflow the page
FORMAT_SPACE: dict[str, dict[str, float]] = {
    "gaejo": {"headingBefore": 7, "headingAfter": 2, "item": 0, "callout": 4, "head": 6},
}

#: per-format page margins in mm (top, bottom, left, right)
FORMAT_MARGINS: dict[str, dict[str, float]] = {
    "gaejo": {"top": 20, "bottom": 15, "left": 20, "right": 20},
}


def type_for(format_id: str | None) -> dict[str, float]:
    """`TYPE` with format overrides."""
    return {**TYPE, **FORMAT_TYPE.get(format_id or "", {})}


def leading_for(format_id: str | None) -> dict[str, float]:
    """`LEADING` with format overrides."""
    return {**LEADING, **FORMAT_LEADING.get(format_id or "", {})}


def space_for(format_id: str | None) -> dict[str, float] | None:
    """Format spacing, or None."""
    return FORMAT_SPACE.get(format_id or "")


def margins_for(format_id: str | None) -> dict[str, float] | None:
    """Format page margins in mm, or None."""
    return FORMAT_MARGINS.get(format_id or "")


#: CSS custom properties for page seeds, in pt; `--doc-leading` is a ratio
CSS_VARIABLES: tuple[tuple[str, str], ...] = tuple(
    [(f"doc-{name}", f"{size:g}pt") for name, size in TYPE.items()]
    + [(f"doc-leading-{name}", f"{ratio:g}") for name, ratio in LEADING.items()]
)


def pt(name: str) -> float:
    """Size in points."""
    return TYPE[name]


def px(name: str) -> float:
    """Size in CSS px at 96 dpi."""
    return round(TYPE[name] * 4 / 3, 2)


def hwp(name: str) -> int:
    """Size as HWP char height (1/100 pt)."""
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
