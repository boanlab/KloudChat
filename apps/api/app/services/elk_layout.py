"""Structures (an architecture's tiers, a cluster's nodes, a short figure that forks and joins)
laid out by ELK's layered algorithm, which the printer runs (`/layout`); drawn here with
python-pptx (`diagram_shapes.draw_pptx`).

ELK keeps each group's members inside its frame, spreads the links over a box's sides and routes
them square round the boxes. The layout is fetched ahead (`prepare`, async) and kept; drawing is
sync and, without a layout at hand (the printer down), falls back to the built-in layouts.

Boxes are sized to their labels at TEXT_PT and the whole figure is scaled to its box, so the
text size is the scale; each flow and group direction is laid out and the one whose text comes
out largest is kept.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from app.core.config import settings
from app.services import figure_text

if TYPE_CHECKING:
    from app.services.diagram_shapes import Edge, Graph

log = logging.getLogger(__name__)

#: label sizes the boxes are measured at, before the figure is scaled to its box (pt)
TEXT_PT, EDGE_PT, TITLE_PT = 12.0, 9.0, 10.0
#: a label's widest line before it wraps, a box's least size, and its padding around the text
LINE_W = 132.0
MIN_W, MIN_H = 84.0, 32.0
#: each layout is also tried compact (narrower labels wrapped, gaps closed in): a narrow box,
#: the document column, keeps larger text so
COMPACT = (60.0, 0.5)
PAD_X, PAD_Y = 10.0, 7.0
#: a group's frame: sides, and the title strip on top
FRAME_PAD, TITLE = 12.0, 20.0
#: the most a figure is enlarged past its measured size
GROW = 1.15
#: a figure without groups is laid out here only when it is this short (layers along the flow)
#: and comes out with text at least LEGIBLE pt; longer ones fold into rows (`flow_layout`)
SHORT, LEGIBLE = 6, 10.0
#: the document figure box: the printed column, and a page's room
FIGURE = (410.0, 680.0)
_DIRECTION = {"LR": "RIGHT", "TB": "DOWN"}
_SPACING = {
    "elk.layered.spacing.nodeNodeBetweenLayers": 44,
    "elk.layered.spacing.edgeNodeBetweenLayers": 16,
    "elk.layered.spacing.edgeEdgeBetweenLayers": 10,
    "elk.spacing.nodeNode": 24,
    "elk.spacing.edgeNode": 14,
    "elk.spacing.edgeEdge": 10,
    "elk.spacing.edgeLabel": 4,
    "elk.spacing.componentComponent": 36,
}


def _spacing(factor: float) -> dict[str, str]:
    return {k: str(round(v * factor)) for k, v in _SPACING.items()}
_TIMEOUT = httpx.Timeout(15.0, connect=3.0)
#: the printer's batch limit (`apps/print/main.py`)
_BATCH = 16
_CACHE: OrderedDict[str, list[dict]] = OrderedDict()
_CACHE_SIZE = 256


def _forward(nodes: list[str], edges: list[tuple[str, str]],
             weak: set[tuple[str, str]] = frozenset()) -> list[tuple[str, str]]:
    """`edges` without the ones that close a cycle. The solid links are walked first, from the
    nodes nothing points to, in source order, and each link back to a node still on its path is
    cut; a `weak` (dashed) link is then kept only if it does not run back against what is kept
    (a feedback arrow, 「역전파」, is the one cut)."""
    known = set(nodes)
    solid = [(a, b) for a, b in edges if (a, b) not in weak and a in known and b in known
             and a != b]
    succ: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in solid:
        succ[a].append(b)
    pointed = {b for _a, b in solid}
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}  # 1 on the path, 2 done
    for root in [n for n in nodes if n not in pointed] + nodes:
        if root in state:
            continue
        stack = [(root, iter(succ[root]))]
        state[root] = 1
        while stack:
            node, rest = stack[-1]
            nxt = next(rest, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                back.add((node, nxt))
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(succ[nxt])))
    kept = {n: [b for b in succ[n] if (n, b) not in back] for n in nodes}

    def reaches(start: str, goal: str) -> bool:
        seen, todo = {start}, [start]
        while todo:
            here = todo.pop()
            if here == goal:
                return True
            for nxt in kept[here]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return False

    for a, b in edges:
        if (a, b) in weak and a in known and b in known and a != b:
            if reaches(b, a):
                back.add((a, b))
            else:
                kept[a].append(b)
    return [e for e in edges if e not in back]


def _edges(graph: Graph) -> tuple[list[tuple[str, str]], set[tuple[str, str]]]:
    """The links as pairs, and the dashed ones (feedback is cut there first)."""
    return ([(e.a, e.b) for e in graph.edges],
            {(e.a, e.b) for e in graph.edges if e.dashed})


def _layers(nodes: list[str], edges: list[tuple[str, str]],
            weak: set[tuple[str, str]] = frozenset()) -> list[list[str]]:
    """Longest-path layers in source order over the links that do not close a cycle."""
    preds: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in _forward(nodes, edges, weak):
        if a in preds and b in preds and a != b:
            preds[b].append(a)
    layer: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(n: str) -> int:
        if n in layer:
            return layer[n]
        if n in visiting:
            return -1
        visiting.add(n)
        layer[n] = max((depth(p) + 1 for p in preds[n]), default=0)
        visiting.discard(n)
        return layer[n]

    for n in nodes:
        depth(n)
    out: list[list[str]] = [[] for _ in range(max(layer.values(), default=-1) + 1)]
    for n in nodes:
        out[layer[n]].append(n)
    return [lay for lay in out if lay]


def _is_line(nodes: list[str], edges: list[tuple[str, str]],
             weak: set[tuple[str, str]] = frozenset()) -> bool:
    """Whether `nodes` run one after another by `edges` (back edges aside)."""
    return all(len(lay) == 1 for lay in _layers(nodes, edges, weak))


def feedback(graph: Graph) -> set[tuple[str, str]]:
    """The links that run back against the flow (`_forward` cuts them)."""
    edges, weak = _edges(graph)
    return set(edges) - set(_forward(list(graph.nodes), edges, weak))


def _branchy(graph: Graph) -> bool:
    """Two or more places where the figure forks or joins (links that close a cycle aside)."""
    nodes = list(graph.nodes)
    edges, weak = _edges(graph)
    layer = {n: k for k, lay in enumerate(_layers(nodes, edges, weak)) for n in lay}
    ahead = [(a, b) for a, b in edges if layer[b] > layer[a]]
    outs = {n: sum(a == n for a, _ in ahead) for n in nodes}
    ins = {n: sum(b == n for _, b in ahead) for n in nodes}
    return sum(outs[n] >= 2 or ins[n] >= 2 for n in nodes) >= 2


def _owners(graph: Graph) -> dict[str, str]:
    """Each node's group, or the node itself outside any group."""
    owner = {m: gid for gid, (_l, members) in graph.groups.items() for m in members
             if m in graph.nodes}
    return {n: owner.get(n, n) for n in graph.nodes}


def applies(graph: Graph) -> bool:
    """A structure with groups: not a comparison, and not a flow of phases (the groups and the
    lone nodes one after another, each group a line of its own); or a short figure without
    groups that forks and joins. Longer figures without groups fold into rows instead."""
    from app.services.diagram_shapes import _compares

    if _compares(graph):
        return False
    edges, weak = _edges(graph)
    if not graph.groups:
        return (len(graph.nodes) >= 5 and _branchy(graph)
                and len(_layers(list(graph.nodes), edges, weak)) <= SHORT)
    owner = _owners(graph)
    blocks = list(dict.fromkeys(owner.values()))
    linked = [(owner[a], owner[b]) for a, b in edges if owner[a] != owner[b]]
    solid = {(owner[a], owner[b]) for a, b in edges if (a, b) not in weak}
    if not _is_line(blocks, linked, set(linked) - solid):
        return True
    return any(
        not _is_line(members, [(a, c) for a, c in edges if a in members and c in members], weak)
        for _gid, (_l, members) in graph.groups.items()
    )


def lines(text: str, size: float, bold: bool = False, line_w: float = LINE_W) -> list[str]:
    """A label in lines no wider than `line_w` at `size` (the writer's own breaks kept)."""
    found, _size = figure_text.fit(text, line_w, 10_000.0, size, bold, min_size=size, pad=0.0)
    return found


def _box(graph: Graph, nid: str, height: float, line_w: float) -> tuple[float, float]:
    node = graph.nodes[nid]
    text = lines(node.label, TEXT_PT, node.hot, line_w)
    w = max(figure_text.width(line, TEXT_PT, node.hot) for line in text) + 2 * PAD_X
    if node.shape == "diamond":
        return max(MIN_W * 1.3, w * 1.7), max(MIN_H * 1.4, height * 1.5)
    if node.shape == "circle":
        side = max(w, height) * 1.15
        return side, side
    return max(min(MIN_W, line_w), w), height


def _height(graph: Graph, line_w: float) -> float:
    """One height for every box: the tallest label's."""
    most = max((len(lines(n.label, TEXT_PT, n.hot, line_w)) for n in graph.nodes.values()),
               default=1)
    return max(MIN_H, most * TEXT_PT * figure_text.LINE + 2 * PAD_Y)


def _label(text: str, size: float, bold: bool = False) -> dict:
    parts = lines(text, size, bold)
    return {"text": text,
            "width": max(figure_text.width(p, size, bold) for p in parts) + 6,
            "height": len(parts) * size * figure_text.LINE + 2}


def _requests(graph: Graph) -> list[dict]:
    """ELK graphs for `graph`, one per flow direction, each roomy and compact (a group's own
    `direction` is not sent: ELK lays the groups out with the figure's flow); links
    back against the flow are sent reversed, so the cycle is cut where the writer meant
    (`feedback`)."""
    against = feedback(graph)
    owner = _owners(graph)

    edges = []
    for k, e in enumerate(graph.edges):
        a, b = (e.b, e.a) if (e.a, e.b) in against else (e.a, e.b)
        item = {"id": f"e{k}", "sources": [a], "targets": [b]}
        if e.label:
            item["labels"] = [_label(e.label, EDGE_PT)]
        edges.append(item)
    own = _DIRECTION.get(graph.direction, "DOWN")
    other = "DOWN" if own == "RIGHT" else "RIGHT"
    out = []
    for (line_w, factor), flow in itertools.product(((LINE_W, 1.0), COMPACT), (own, other)):
        height = _height(graph, line_w)
        spacing = _spacing(factor)

        def node(nid: str, height: float = height, line_w: float = line_w) -> dict:
            w, h = _box(graph, nid, height, line_w)
            return {"id": nid, "width": round(w, 1), "height": round(h, 1)}

        children = [node(n) for n in graph.nodes if owner[n] == n]
        for gid, (title, members) in graph.groups.items():
            kept = [m for m in members if m in graph.nodes]
            if not kept:
                continue
            children.append({
                "id": f"g:{gid}",
                "labels": [_label(title, TITLE_PT, True)],
                "layoutOptions": {
                    "elk.padding": f"[top={TITLE},left={FRAME_PAD},bottom={FRAME_PAD},"
                                   f"right={FRAME_PAD}]",
                    "elk.nodeLabels.placement": "[H_CENTER, V_TOP, INSIDE]",
                    "elk.nodeSize.constraints": "[NODE_LABELS]",
                    **spacing,
                },
                "children": [node(m) for m in kept],
            })
        out.append({
            "id": "root",
            "layoutOptions": {
                "elk.algorithm": "layered",
                "elk.direction": flow,
                "elk.hierarchyHandling": "INCLUDE_CHILDREN",
                "elk.edgeRouting": "ORTHOGONAL",
                "elk.edgeLabels.placement": "CENTER",
                "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
                "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
                **spacing,
            },
            "children": children,
            "edges": edges,
        })
    return out


def _key(requests: list[dict]) -> str:
    return hashlib.sha256(json.dumps(requests, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


async def prepare(sources: Iterable[str]) -> None:
    """Lay out ahead, through the printer, each figure source here that ELK draws; a failure
    leaves those figures to the built-in layouts."""
    from app.services import diagram_shapes, figure_kinds

    base = settings.print_base_url.strip().rstrip("/")
    if not base:
        return
    wanted: dict[str, list[dict]] = {}
    for source in sources:
        if not source or figure_kinds.from_source(source) is not None:
            continue
        graph = diagram_shapes.parse(source)
        if graph is None or not applies(graph):
            continue
        requests = _requests(graph)
        key = _key(requests)
        if key in _CACHE:
            _CACHE.move_to_end(key)
        else:
            wanted[key] = requests
    if not wanted:
        return
    flat = [(key, r) for key, requests in wanted.items() for r in requests]
    got: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for start in range(0, len(flat), _BATCH):
                chunk = flat[start:start + _BATCH]
                response = await client.post(
                    f"{base}/layout", json={"graphs": [r for _k, r in chunk]})
                response.raise_for_status()
                for (key, _r), laid in zip(chunk, response.json()["graphs"], strict=True):
                    got.setdefault(key, []).append(laid)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.info("figures not laid out by ELK, keeping the built-in layouts: %s", exc)
    for key, laid in got.items():
        if len(laid) == len(wanted[key]):
            _CACHE[key] = laid
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)


@dataclass
class Laid:
    """A figure fitted to its box: boxes and frames as (x0, y0, x1, y1), links as polylines
    from source to target with their label boxes, all from the box's top-left corner (pt)."""

    boxes: dict[str, tuple[float, float, float, float]]
    frames: list[tuple[str, tuple[float, float, float, float]]]
    links: list[tuple[Edge, list[tuple[float, float]], tuple[float, float, float, float] | None]]
    scale: float

    @property
    def text_pt(self) -> float:
        return TEXT_PT * self.scale


def _fit(laid: dict, box: tuple[float, float]) -> float:
    return min(box[0] / laid["width"], box[1] / laid["height"], GROW)


def _chosen(graph: Graph, box: tuple[float, float]) -> tuple[dict, float] | None:
    """The cached layout whose text comes out largest in `box` (the writer's direction on a
    tie), with its scale; None when there is none, or a figure without groups would read small."""
    if not applies(graph):
        return None
    laid = _CACHE.get(_key(_requests(graph)))
    if laid is None:
        return None
    found = None
    for rank, out in enumerate(laid):
        scale = _fit(out, box)
        text = TEXT_PT * scale
        score = (min(text, LEGIBLE), round(text, 1), -rank)
        if found is None or score > found[0]:
            found = (score, out, scale)
    assert found is not None
    if not graph.groups and found[2] * TEXT_PT < LEGIBLE:
        return None
    return found[1], found[2]


def place(graph: Graph, width: float, height: float) -> Laid | None:
    """`graph` laid out by ELK and fitted to `width` × `height`, centred; None when it is not
    drawn this way, or its layout was not fetched (`prepare`)."""
    chosen = _chosen(graph, (width, height))
    if chosen is None:
        return None
    out, scale = chosen
    left = (width - out["width"] * scale) / 2
    top = (height - out["height"] * scale) / 2

    def rect(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return (left + x * scale, top + y * scale, left + (x + w) * scale,
                top + (y + h) * scale)

    boxes: dict[str, tuple[float, float, float, float]] = {}
    frames: list[tuple[str, tuple[float, float, float, float]]] = []
    # positions are from the parent's corner; edges from their container's
    offsets: dict[str, tuple[float, float]] = {"root": (0.0, 0.0)}

    def walk(parent: dict, ox: float, oy: float) -> None:
        for child in parent.get("children", []):
            x, y = ox + child.get("x", 0.0), oy + child.get("y", 0.0)
            offsets[child["id"]] = (x, y)
            if child["id"].startswith("g:"):
                gid = child["id"][2:]
                frames.append((graph.groups[gid][0], rect(x, y, child["width"], child["height"])))
                walk(child, x, y)
            else:
                boxes[child["id"]] = rect(x, y, child["width"], child["height"])

    walk(out, 0.0, 0.0)
    against = feedback(graph)
    by_id = {e["id"]: e for e in out.get("edges", [])}
    links = []
    for k, edge in enumerate(graph.edges):
        laid = by_id.get(f"e{k}")
        if laid is None or not laid.get("sections"):
            return None
        ox, oy = offsets.get(laid.get("container", "root"), (0.0, 0.0))
        points: list[tuple[float, float]] = []
        for section in laid["sections"]:
            run = [section["startPoint"], *section.get("bendPoints", []), section["endPoint"]]
            for p in run:
                x, y = left + (ox + p["x"]) * scale, top + (oy + p["y"]) * scale
                if points:
                    # ELK's sections meet a hair apart: square the run again
                    px, py = points[-1]
                    x = px if abs(x - px) < 1.0 else x
                    y = py if abs(y - py) < 1.0 else y
                if not points or points[-1] != (x, y):
                    points.append((x, y))
        if (edge.a, edge.b) in against:
            points.reverse()
        label = None
        if edge.label and laid.get("labels"):
            lb = laid["labels"][0]
            label = rect(ox + lb["x"], oy + lb["y"], lb["width"], lb["height"])
        links.append((edge, points, label))
    return Laid(boxes, frames, links, scale)


def frame_size(graph: Graph, box: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """The figure's size in points fitted to `box` (the document figure box by default), or None
    when it is not drawn this way."""
    box = box or FIGURE
    chosen = _chosen(graph, box)
    if chosen is None:
        return None
    out, scale = chosen
    return out["width"] * scale, out["height"] * scale


__all__ = ["FIGURE", "Laid", "applies", "feedback", "frame_size", "place", "prepare"]
