"""A deck 서식's sample drawn at the deck's own type scale.

The gallery shows a deck 서식 as its `sample.html` inside the shared `_deck/seed.html`. Real
decks are drawn from one table (`deck_type`): titles at 32pt, the body at 22pt stepping down
18 → 16 → 14 → 12 only when a slide overflows, one step for the whole deck. The seed reads
those sizes from custom properties this module declares, in points: a slide is 960 x 540pt,
which is the 1280 x 720px frame the gallery lays a slide out in, so a size in the preview is
the size in the panel and the file.

Each slide's step is chosen the way `deck.auto_fit` and `deck.harmonize` choose it, from an
estimate of the height the slide's words take in the seed's own column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from app.services import deck_type

#: The slide page in points.
PAGE_W, PAGE_H = 960.0, 540.0
_K = deck_type.K
#: Where the panel puts the title's top, in slide units (`SlideView`'s top padding).
TITLE_TOP = 24
GUT = deck_type.PAD_X * _K
PAD_TOP = TITLE_TOP * _K
BODY_TOP = deck_type.BODY_TOP * _K
PAD_BOTTOM = (225 - deck_type.BODY_BOTTOM) * _K
#: The height the body may take under a one-line title.
ROOM = (deck_type.BODY_BOTTOM - deck_type.BODY_TOP) * _K
INNER = PAGE_W - 2 * GUT
#: 1rem in points (16px at 1280px wide).
REM = 12.0

#: Custom property → `deck_type.TYPE` key, declared in the seed's `:root`.
_SIZES = {
    "title": "title",
    "cover": "cover",
    "body": "body",
    "paragraph": "paragraph",
    "cover-body": "coverBody",
    "card-name": "cardName",
    "quote": "quote",
    "quote-by": "quoteBy",
    "big-number": "bigNumber",
    "big-number-body": "bigNumberBody",
    "caption": "caption",
    "table": "tableMax",
    "table-min": "tableMin",
    "page-number": "pageNumber",
}

#: Layouts the fit leaves alone, as `deck._STRUCTURAL` does for the panel's.
_FIXED = ("cover", "quote")


def type_declarations() -> str:
    """The seed's size and geometry properties, from `deck_type`."""
    lines = [f"    --deck-{name}: {deck_type.TYPE[key]:g}pt;" for name, key in _SIZES.items()]
    lines += [
        f"    --deck-floor: {deck_type.FLOOR_PT}pt;",
        f"    --deck-leading: {deck_type.LEADING['body']};",
        f"    --deck-bullet-gap: {deck_type.BULLET_GAP}em;",
        f"    --gut: {GUT:g}pt;",
        f"    --pad-y: {PAD_TOP:g}pt;",
        f"    --pad-bottom: {PAD_BOTTOM:g}pt;",
        f"    --body-top: {BODY_TOP:g}pt;",
    ]
    return "\n".join(lines)


# ── a small tree of the slide ──────────────────────────────────────


@dataclass
class _Node:
    tag: str
    cls: str = ""
    children: list[_Node] = field(default_factory=list)
    text: str = ""

    def all_text(self) -> str:
        return (self.text + "".join(child.all_text() for child in self.children)).strip()

    def find(self, tag: str) -> list[_Node]:
        found = [self] if self.tag == tag else []
        for child in self.children:
            found += child.find(tag)
        return found

    def has(self, name: str) -> bool:
        return name in self.cls.split()


_VOID = {"br", "img", "hr"}


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, dict(attrs).get("class") or "")
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        # Inline text joins its block; `all_text` reads it back in order.
        self.stack[-1].children.append(_Node("#text", text=data))


def _section(fragment: str) -> _Node | None:
    tree = _Tree()
    tree.feed(fragment)
    return next((n for n in tree.root.children if n.tag == "section"), None)


# ── height estimate, in points ─────────────────────────────────────


def _lines(text: str, size: float, width: float) -> int:
    return deck_type.lines(text, size, max(width, size))


def _sized(pt: float, scale: float) -> float:
    return max(float(deck_type.FLOOR_PT), pt * scale)


def _list_need(node: _Node, size: float, width: float) -> float:
    items = [li for li in node.children if li.tag == "li"]
    indent = (1.5 if node.tag == "ol" else 1.15) * size
    need = sum(
        _lines(li.all_text(), size, width - indent) * size * deck_type.LEADING["body"]
        for li in items
    )
    return need + size * deck_type.BULLET_GAP * max(0, len(items) - 1)


def _table_need(node: _Node) -> float:
    rows = [
        [cell.all_text() for cell in tr.children if cell.tag in ("th", "td")]
        for tr in node.find("tr")
    ]
    rows = [row for row in rows if row]
    if not rows:
        return 0.0
    size = deck_type.table_size(len(rows)) * _K
    pad = deck_type.table_pad(len(rows)) * _K
    shares = deck_type.column_shares(rows)
    need = 0.0
    for row in rows:
        tallest = max(
            _lines(cell, size, INNER * share - 1.2 * size)
            for cell, share in zip(row, shares, strict=False)
        )
        need += tallest * size * deck_type.LEADING["table"] + 2 * pad
    return need


def _block_need(node: _Node, scale: float, width: float, layout: str) -> float:
    """Height of one top-level block of a slide."""
    T = deck_type.TYPE
    text = node.all_text()
    if node.tag in ("ul", "ol"):
        return _list_need(node, _sized(T["body"], scale), width)
    if node.tag == "table":
        return _table_need(node)
    if node.tag == "blockquote":
        return _lines(text, T["quote"], width) * T["quote"] * 1.3
    if node.tag == "h3":
        size = _sized(T["cardName"], scale)
        return _lines(text, size, width) * size * 1.3 + 0.6 * size
    if node.tag in ("small", "figcaption"):
        return _lines(text, T["caption"], width) * T["caption"] * 1.55 + 0.5 * REM
    if node.tag == "p":
        if node.has("claim"):
            return _lines(text, T["bigNumber"], width) * T["bigNumber"] * 1.05
        if node.has("note") or node.has("lead") or node.has("meta"):
            size = T["bigNumberBody"] if layout == "figure" else T["caption"]
            return _lines(text, size, width) * size * 1.55 + REM
        if layout == "example":
            size = _sized(T["body"], scale)
            return _lines(text, size, width) * size * 1.35 + 1.1 * size
        size = _sized(T["paragraph"], scale)
        return _lines(text, size, width) * size * deck_type.LEADING["paragraph"] + 0.9 * size
    if node.tag == "div" and node.has("cols"):
        columns = [c for c in node.children if c.tag == "div"]
        if not columns:
            return 0.0
        # Two columns, a gap and the ruled column's inset between them.
        column = (width - 2.5 * REM) / len(columns) - 1.75 * REM
        return max(_stack_need(c.children, scale, column, layout) for c in columns)
    if node.tag == "div":
        # The example panel: a label column and padding around the case.
        inner = width - 2 * 1.45 * REM - 4 * REM
        return _stack_need(node.children, scale, inner, layout) + 2 * 1.3 * REM
    return 0.0


def _stack_need(nodes: list[_Node], scale: float, width: float, layout: str) -> float:
    return sum(_block_need(n, scale, width, layout) for n in nodes if n.tag != "#text")


def _measure(seed: str) -> float:
    """The body column in points: the last `--measure: min(Nrem, P%)` the 서식 declares."""
    found = re.findall(r"--measure:\s*min\(\s*([\d.]+)rem\s*,\s*([\d.]+)%\s*\)", seed)
    if not found:
        return INNER
    rem, share = found[-1]
    return min(float(rem) * REM, float(share) / 100 * INNER)


@dataclass
class SlideFit:
    layout: str
    #: The body ladder step as a `textScale`; 1.0 is 22pt.
    scale: float
    #: The title size in points.
    title: float
    #: Rows in the slide's table, 0 for none.
    rows: int = 0


def _fit(fragment: str, measure: float) -> SlideFit:
    section = _section(fragment)
    if section is None:
        return SlideFit("", 1.0, float(deck_type.TYPE["title"]))
    layout = next((c for c in section.cls.split() if c != "slide"), "bullets")
    heading = next((n for n in section.children if n.tag == "h2"), None)
    title_text = heading.all_text() if heading else ""
    # The title runs the slide's width less the corner the number sits in.
    title_width = (INNER - 4 * REM) / _K
    title = deck_type.title_pt(title_text, title_width)
    rows = len([tr for tr in section.find("tr") if tr.children])
    if layout in _FIXED:
        return SlideFit(layout, 1.0, title, rows)
    extra = max(0, deck_type.lines(title_text, title / _K, title_width) - 1) * title * 1.25
    width = INNER if layout in ("table", "split") else measure
    blocks = [n for n in section.children if n.tag not in ("h2", "#text", "span")]
    for scale in deck_type.SCALES:
        if extra + _stack_need(blocks, scale, width, layout) <= ROOM:
            break
    else:
        scale = deck_type.SCALES[-1]
    return SlideFit(layout, scale, title, rows)


def fits(slides: list[str], seed: str) -> list[SlideFit]:
    """Each slide's step and title size, with one body step for the deck (`deck.harmonize`):
    the smallest any body slide needs, not below 18pt; a slide that needs less keeps its own.
    """
    measure = _measure(seed)
    fitted = [_fit(slide, measure) for slide in slides]
    body = [f for f in fitted if f.layout not in _FIXED]
    common = max(min((f.scale for f in body), default=1.0), deck_type.SCALES[1])
    for f in body:
        f.scale = min(f.scale, common)
    return fitted


def _rule(f: SlideFit) -> str:
    rule = f"--text-scale: {f.scale:g}; --deck-title: {f.title:g}pt;"
    if f.rows:
        # The table's cell size and padding go with its row count, as in the panel.
        size = deck_type.table_size(f.rows) * _K
        pad = deck_type.table_pad(f.rows) * _K
        rule += f" --deck-table: {size:.2f}pt; --table-pad: {pad:.2f}pt;"
    return rule


def slide_style(slides: list[str], seed: str, n: int) -> str:
    """A `<style>` that sets slide `n`'s (1-based) step and title size, for one slide alone."""
    f = fits(slides, seed)[n - 1]
    return f"<style>.slide {{ {_rule(f)} }}</style>\n"


def deck_style(slides: list[str], seed: str) -> str:
    """A `<style>` that sets every slide's step and title size, for the whole sample."""
    rules = "\n".join(
        f".slide:nth-of-type({i}) {{ {_rule(f)} }}"
        for i, f in enumerate(fits(slides, seed), start=1)
    )
    return f"<style>\n{rules}\n</style>\n"


__all__ = ["SlideFit", "deck_style", "fits", "slide_style", "type_declarations"]
