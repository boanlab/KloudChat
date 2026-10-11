"""A long flowchart laid out in rows that read at the size the figure is printed.

A flow drawn on one line grows as wide as its steps. A report prints a figure at the text
width (about 466 pt on A4), so a 900 pt line of eleven steps came out at half size, its box
text at 5 pt and its edge labels at 4. Here the steps are folded into rows instead, each
read left to right, the rows stacked. How many steps share a row follows from the text: the
most per row at which every box still takes `BOX_PT` in the frame it is drawn in, never so
few that the figure becomes a column.

The steps keep their order: layers come from the flow's own edges (a solid edge before a
dashed one when a loop has to be cut, so the feedback is what goes back), the given
phases (subgraphs) close a row where they fit, and a phase that runs over two rows gets a
frame on each. Lines stay short: within a row, from a row's end to the next row's start,
or an arc back over a near box in the row. Any other edge (back across rows, or on past
a whole row) is a short stub at its source with a note naming where it goes. Every node
and edge is still drawn; only the long lines across the whole figure go.

`diagram_shapes.layout` hands a flow here; `diagram_shapes.draw_pptx` asks `sides` and
`frames` how to connect and frame what this placed.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.services.diagram_shapes import Graph, Placed

#: The smallest box text, in points of the frame the figure is drawn in, that the row
#: count is chosen for. A document figure's frame is printed at about its own size, so
#: this is the printed size. A decision diamond holds its text in its inner half and
#: may go one point under it, still at the 9 pt a printed figure needs.
BOX_PT = 10.0
DECISION_PT = 9.0
#: Edge labels are drawn at this size (`diagram_shapes.draw_pptx`).
EDGE_PT = 8.0
#: A document figure's frame width, in points: with its trimmed margin, just under the
#: narrower of the text columns (DOCX/HWPX 150 mm = 425 pt; the A4 PDF about 466 pt), so
#: both print it at its own size.
DOCUMENT_WIDTH = 410.0

_BOX_H = 52.0  # a box's height in a row
_MIN_BOX_H = 30.0
_LANE_GAP = 16.0  # between boxes sharing a column
_COL_GAP = 24.0  # between columns, before room for an edge label
_MAX_CELL = 150.0  # a box no wider than this
_DIAMOND_WIDTH = 1.3  # a decision's column, against the others
_MIN_CELL = 60.0  # a box narrower than this reads cramped: taken only when nothing else fits
_LEAD = 16.0  # left inset that a line wrapping to the next row runs down
_ROW_GAP = 26.0  # between rows: the line to the next row runs here
_TITLE = 24.0  # extra between rows when phases are framed (the frame's title strip)
_ARC = 18.0  # extra above a row for a short back-edge arcing over it
_MARK = 22.0  # extra between rows for an edge's marker
_SPAN = 3  # a back-edge over at most this many columns arcs; a longer one is a marker
_MIN_PER_ROW = 3  # a flow is never folded thinner than this (no tall column)


@dataclass
class Plan:
    rows: list[list[list[str]]]  # rows → columns → nodes stacked in that column
    placed: dict[str, Placed]
    width: float
    height: float
    min_font: int  # the smallest box text against its floor: ≥ 0 when every box reads
    back: set[int] = field(default_factory=set)  # indices of edges that go back
    icons: bool = True  # whether boxes carry icons (only when they cost no text size)
    cell: float = 0.0  # a box's width
    smallest: int = 0  # the smallest box text, in points


def applies(graph: Graph) -> bool:
    """A flow this lays out: left-to-right, or top-to-bottom rows of phases (`direction LR`
    inside each subgraph), or a top-to-bottom chain of five steps or more. Not a
    comparison, and not a hierarchy (a top-to-bottom tree)."""
    from app.services.diagram_shapes import _compares

    if len(graph.nodes) < 2 or _compares(graph):
        return False
    if graph.direction == "LR":
        return True
    dirs = getattr(graph, "group_dirs", {}) or {}
    if graph.groups and all(dirs.get(g) == "LR" for g in graph.groups):
        return True
    layers, _back = _layers(graph)
    return len(layers) >= 5 and max(len(lay) for lay in layers) <= 2


def _layers(graph: Graph) -> tuple[list[list[str]], set[int]]:
    """Longest-path layers over the edges that go forward, and the indices of the ones that
    go back. Solid edges are taken first, then dashed ones, each in source order; an edge
    that would close a loop goes back."""
    ids = list(graph.nodes)
    succ: dict[str, list[str]] = {n: [] for n in ids}
    back: set[int] = set()

    def reaches(src: str, dst: str) -> bool:
        seen, todo = {src}, [src]
        while todo:
            n = todo.pop()
            if n == dst:
                return True
            for m in succ[n]:
                if m not in seen:
                    seen.add(m)
                    todo.append(m)
        return False

    order = sorted(range(len(graph.edges)), key=lambda i: (graph.edges[i].dashed, i))
    for i in order:
        e = graph.edges[i]
        if reaches(e.b, e.a):
            back.add(i)
        else:
            succ[e.a].append(e.b)
    preds: dict[str, list[str]] = {n: [] for n in ids}
    for a, bs in succ.items():
        for b in bs:
            preds[b].append(a)
    layer: dict[str, int] = {}

    def depth(n: str) -> int:
        if n not in layer:
            layer[n] = max((depth(p) + 1 for p in preds[n]), default=0)
        return layer[n]

    for n in ids:
        depth(n)
    # A source feeding one later step sits just before it, not at the far left.
    for n in ids:
        if not preds[n] and succ[n]:
            layer[n] = max(layer[n], min(layer[m] for m in succ[n]) - 1)
    count = max(layer.values()) + 1
    layers: list[list[str]] = [[] for _ in range(count)]
    for n in ids:
        layers[layer[n]].append(n)
    layers = [lay for lay in layers if lay]
    rank = {n: i for i, n in enumerate(ids)}
    for idx in range(1, len(layers)):
        pos = {n: i for i, n in enumerate(layers[idx - 1])}
        layers[idx].sort(key=lambda n: (
            sum(pos.get(p, 0) for p in preds[n]) / max(1, len(preds[n])) if preds[n] else len(pos),
            rank[n],
        ))
    return layers, back


def _rows(layers: list[list[str]], per_row: int, graph: Graph) -> list[list[list[str]]]:
    """Layers packed into rows of at most `per_row`, as even as the count allows, closing a
    row at a phase boundary where the phases fit."""
    total = len(layers)
    count = math.ceil(total / per_row)
    group_of = {m: g for g, (_l, members) in graph.groups.items() for m in members}
    phase = [Counter(group_of.get(n) for n in lay).most_common(1)[0][0] for lay in layers]
    if graph.groups and any(phase):
        runs: list[list[int]] = []
        for i, g in enumerate(phase):
            if runs and phase[runs[-1][0]] == g:
                runs[-1].append(i)
            else:
                runs.append([i])
        pieces: list[list[int]] = []
        for run in runs:  # a phase longer than a row is split evenly
            parts = math.ceil(len(run) / per_row)
            size = math.ceil(len(run) / parts)
            pieces += [run[k:k + size] for k in range(0, len(run), size)]
        packed: list[list[int]] = []
        for piece in pieces:
            if packed and len(packed[-1]) + len(piece) <= per_row:
                packed[-1] += piece
            else:
                packed.append(list(piece))
        if len(packed) <= count + 1:
            return [[layers[i] for i in row] for row in packed]
    sizes = [total // count + (1 if r < total % count else 0) for r in range(count)]
    rows, start = [], 0
    for size in sizes:
        rows.append(layers[start:start + size])
        start += size
    return rows


def _label_width(label: str) -> float:
    """The width an edge label takes beside its line (as `diagram_shapes.draw_pptx` sizes
    it), on one line, or on two when it is long."""
    text = label.replace("\n", " ").strip()
    if not text:
        return 0.0
    chars = len(text) if len(text) <= 8 else math.ceil(len(text) / 2)
    return min(150.0, 9.0 * chars + 12)


def _place(
    graph: Graph, layers: list[list[str]], back: set[int], per_row: int,
    width: float, height: float | None,
) -> Plan | None:
    from app.services.diagram_shapes import Placed, _font_size, text_area

    rows = _rows(layers, per_row, graph)
    # In a column of several, a step whose edge leaves for a far row or goes back takes
    # the bottom place, where its marker has room below it.
    row_of = {n: r for r, row in enumerate(rows) for column in row for n in column}
    order = {n: (r, c) for r, row in enumerate(rows) for c, column in enumerate(row)
             for n in column}
    far = {
        e.a for e in graph.edges
        if row_of[e.b] > row_of[e.a] + 1 or order[e.b] < order[e.a]
    }
    rows = [[sorted(column, key=lambda n: n in far) for column in row] for row in rows]
    where: dict[str, tuple[int, int, int]] = {}
    for r, row in enumerate(rows):
        for c, column in enumerate(row):
            for k, n in enumerate(column):
                where[n] = (r, c, k)
    # How each edge runs decides the room between rows: an arc over a row, a marker.
    lanes = [max(len(col) for col in row) for row in rows]
    probe = {
        n: Placed(0, 0, 0, 0, row=r, col=c, lane=k, lanes=len(rows[r][c]),
                  last=c == len(rows[r]) - 1)
        for n, (r, c, k) in where.items()
    }
    kinds = {kind(probe[e.a], probe[e.b]) for e in graph.edges}
    arcs, marks = "arc" in kinds, "mark" in kinds
    # Column gaps: room for the label of an edge between neighbours in a row.
    gaps: list[list[float]] = []
    for row in rows:
        col = {n: c for c, column in enumerate(row) for n in column}
        row_gaps = [_COL_GAP] * max(0, len(row) - 1)
        for e in graph.edges:
            if e.a in col and e.b in col and abs(col[e.a] - col[e.b]) == 1 and e.label:
                g = min(col[e.a], col[e.b])
                row_gaps[g] = max(row_gaps[g], min(96.0, _label_width(e.label) + 10))
        gaps.append(row_gaps)
    lead = _LEAD if len(rows) > 1 else 0.0
    # A decision's text sits in its inner half: its column is wider than the others.
    weight = [
        [_DIAMOND_WIDTH if any(graph.nodes[n].shape == "diamond" for n in column) else 1.0
         for column in row]
        for row in rows
    ]
    cell = min(
        [_MAX_CELL]
        + [(width - lead - sum(g)) / sum(wt) for wt, g in zip(weight, gaps, strict=True)]
    )
    if cell < 40:
        return None
    framed = bool(graph.groups)
    row_gap = _ROW_GAP + (_TITLE if framed else 0) + (_ARC if arcs else 0) + (_MARK if marks else 0)
    top_inset = (_TITLE if framed else 0) + (_ARC if arcs else 0) + (_MARK if marks else 0)
    bottom_inset = _MARK if marks else 0.0
    box_h = _BOX_H
    free = height is None
    fixed = top_inset + bottom_inset + row_gap * (len(rows) - 1) + _LANE_GAP * sum(
        n - 1 for n in lanes)
    if not free:
        room = (height - fixed) / sum(lanes)
        if room < _MIN_BOX_H:
            return None
        box_h = min(box_h, room)
    natural = fixed + box_h * sum(lanes)
    placed: dict[str, Placed] = {}
    used_w = 0.0
    y = top_inset if free else top_inset + max(0.0, (height - natural) / 2)
    for r, row in enumerate(rows):
        row_h = lanes[r] * box_h + (lanes[r] - 1) * _LANE_GAP
        x = lead
        for c, column in enumerate(row):
            stack = len(column) * box_h + (len(column) - 1) * _LANE_GAP
            top = y + (row_h - stack) / 2
            span = cell * weight[r][c]
            for k, n in enumerate(column):
                w, h = span, box_h
                if graph.nodes[n].shape == "circle":
                    w = h = min(w, h) * 1.15
                placed[n] = Placed(x + span / 2, top + k * (box_h + _LANE_GAP) + box_h / 2, w, h,
                                   row=r, col=c, lane=k, lanes=len(column),
                                   last=c == len(row) - 1)
            x += span + (gaps[r][c] if c < len(row) - 1 else 0.0)
        used_w = max(used_w, x)
        y += row_h + row_gap
    if free:
        height = natural
    elif used_w < width:  # the block sits in the middle of a wider box
        for p in placed.values():
            p.x += (width - used_w) / 2
    def sizes(icons: bool) -> list[int]:
        return [
            _font_size(graph.nodes[n].label, *text_area(graph.nodes[n], p.w, p.h, icons)[:2])
            for n, p in placed.items()
        ]

    # Icons only where none of them makes its box's text smaller: all boxes alike.
    bare, with_icons = sizes(False), sizes(True)
    icons = with_icons == bare
    floors = [
        DECISION_PT if graph.nodes[n].shape == "diamond" else BOX_PT for n in placed
    ]
    return Plan(rows=rows, placed=placed, width=used_w, height=height,
                min_font=min(b - f for b, f in zip(bare, floors, strict=True)),
                back=back, icons=icons, cell=cell, smallest=min(bare))


def plan(graph: Graph, width: float, height: float | None = None) -> Plan | None:
    """The rows for `graph` in a frame `width` wide (and `height` high, when the frame is
    fixed): the most steps per row at which every box keeps `BOX_PT`; when none does, the
    count whose smallest text is largest. Never thinner than `_MIN_PER_ROW` a row unless
    the frame's height leaves nothing else. None when the flow cannot be placed at all."""
    layers, back = _layers(graph)
    total = len(layers)
    floor = min(total, _MIN_PER_ROW)
    best: Plan | None = None
    for per_row in range(total, 0, -1):
        if per_row < floor and best is not None:
            break
        placed = _place(graph, layers, back, per_row, width, height)
        if placed is None:
            continue
        if placed.min_font >= 0 and placed.cell >= _MIN_CELL:
            return placed
        if best is None or placed.min_font > best.min_font:
            best = placed
    return best


def frame_size(graph: Graph) -> tuple[float, float] | None:
    """A document figure's box in points: `DOCUMENT_WIDTH` wide (or narrower when its
    boxes do not need it), as high as its rows. None when this does not lay it out."""
    if not applies(graph):
        return None
    made = plan(graph, DOCUMENT_WIDTH)
    if made is None:
        return None
    return max(made.width, 160.0), made.height


def place(graph: Graph, width: float, height: float) -> dict[str, Placed] | None:
    """Node boxes for `graph` inside a `width` × `height` box, or None when this does not
    lay it out (the caller's own layout then does)."""
    if not applies(graph):
        return None
    made = plan(graph, width, height)
    if made is None:
        return None
    graph.icons = made.icons
    return made.placed


def kind(a: Placed, b: Placed) -> str:
    """How the edge between two placed boxes runs: `lane` within a column, `forward`,
    `wrap` to the next row, `arc` back over a near box in the row, `mark` back further or
    forward past a whole row (a note at the source, not a line over the figure)."""
    if a.row == b.row and a.col == b.col:
        return "lane"
    if (b.row, b.col) > (a.row, a.col):
        if a.row == b.row:
            return "forward"
        # Only the turn from a row's end to the next row's start, or straight down, is a
        # short line; any other edge to a later row would run across the figure.
        if b.row == a.row + 1 and ((a.last and b.col == 0) or a.col == b.col):
            return "wrap"
        return "mark"
    near = a.row == b.row and abs(a.col - b.col) <= _SPAN
    top = a.lane == 0 and b.lane == 0
    bottom = a.lane == a.lanes - 1 and b.lane == b.lanes - 1
    return "arc" if near and (top or bottom) else "mark"


def sides(a: Placed, b: Placed) -> tuple[str, str]:
    """Which sides a line leaves `a` by and enters `b` by, in rows."""
    how = kind(a, b)
    if how == "lane":
        return ("bottom", "top") if b.y > a.y else ("top", "bottom")
    if how == "forward":
        return "right", "left"
    if how == "wrap":
        if a.col == b.col and b.col != 0:
            return "bottom", "top"
        # Down out of the last lane, else out to the right; into the next row from the left.
        return ("bottom" if a.lane == a.lanes - 1 else "right"), "left"
    if how == "arc":
        return ("top", "top") if a.lane == 0 and b.lane == 0 else ("bottom", "bottom")
    return "bottom", "top"


def marker_text(label: str, target: str) -> str:
    """What a marker says: the edge's label, then the step it goes to."""
    target = " ".join(target.split())
    if len(target) > 12:
        target = target[:11] + "…"
    label = " ".join((label or "").split())
    return f"{label} → {target}" if label else f"→ {target}"


def frames(graph: Graph, placed: dict[str, Placed]) -> list[tuple[str, list[str]]]:
    """The phase frames to draw: one per phase, or one per row a phase runs over when the
    flow is in rows (the later ones titled as continuing)."""
    if not placed or all(p.row < 0 for p in placed.values()):
        return list(graph.groups.values())
    out: list[tuple[str, list[str]]] = []
    for label, members in graph.groups.values():
        by_row: dict[int, list[str]] = {}
        for m in members:
            if m in placed:
                by_row.setdefault(placed[m].row, []).append(m)
        for k, row in enumerate(sorted(by_row)):
            out.append((label if k == 0 else f"{label} (이어서)", by_row[row]))
    return out


__all__ = [
    "BOX_PT", "DOCUMENT_WIDTH", "EDGE_PT", "Plan", "applies", "frame_size", "frames", "kind",
    "marker_text", "place", "plan", "sides",
]
