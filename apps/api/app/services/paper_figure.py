"""A paper-style figure drawn by an outside picture model, then read back and checked.

The 구조도 / 흐름도 / 개념도 chips normally go to a text model that writes mermaid, drawn by code
(`diagram`). When the person's own picture model is an outside one that draws well, it draws the
figure itself: a text model first lists the labels and the layout as JSON, code writes the English
prompt from that list (instructions and labels kept apart), the picture is drawn once, a model that
reads pictures lists the text it sees, and code compares that with the label list. One redraw at
most, naming what was wrong; the better of the two pictures is kept.

The style sentences are a short digest of the NeurIPS 2025 diagram style guide in PaperBanana
(https://github.com/dwzhu-pku/PaperBanana, Apache-2.0); see NOTICE.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services import imagegen, settings_store, thinking, usage_fields

log = logging.getLogger(__name__)

#: image-screen style chip → the figure kind the code renderer calls it
STYLES: dict[str, str] = {"구조도": "method", "흐름도": "flow", "개념도": "concept"}

_MAX_LABELS = 40
_MAX_LABEL_CHARS = 28


def routes_here(style: str, model: dict | None) -> bool:
    """A figure chip on a picture model that is outside the institution and draws from a prompt."""
    if style not in STYLES or not model:
        return False
    if model.get("strictLocal") is True or model.get("dataBoundary") == "self_hosted":
        return False
    return "image" in (model.get("kinds") or []) and imagegen.draws_images(
        str(model.get("id")), None
    )


# ---------------------------------------------------------------- plan: labels and layout


@dataclass(slots=True)
class Node:
    label: str
    #: process | data | store | loss | note
    kind: str = "process"
    emphasis: bool = False


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    label: str = ""
    #: main | aux | feedback
    style: str = "main"


@dataclass(slots=True)
class Region:
    label: str
    nodes: list[Node] = field(default_factory=list)


@dataclass(slots=True)
class Plan:
    regions: list[Region]
    edges: list[Edge]

    @property
    def labels(self) -> list[str]:
        """Every string the picture may carry, in reading order, once each."""
        seen: list[str] = []
        for region in self.regions:
            for text in (region.label, *(n.label for n in region.nodes)):
                if text and text not in seen:
                    seen.append(text)
        for edge in self.edges:
            if edge.label and edge.label not in seen:
                seen.append(edge.label)
        return seen

    @property
    def node_count(self) -> int:
        return sum(len(r.nodes) for r in self.regions)


_PLANNER = """You prepare a paper-style figure (a structure diagram, a flowchart or a concept
diagram) for a picture model. Read the request and answer with ONE JSON object and nothing else.

{{
  "regions": [ {{ "label": "<region name or empty string>",
                  "nodes": [ {{ "label": "<short name>", "kind": "process|data|store|loss|note",
                               "emphasis": false }} ] }} ],
  "edges": [ {{ "from": "<node label>", "to": "<node label>",
               "label": "<text on the line or empty>", "style": "main|aux|feedback" }} ]
}}

Rules:
- Every "label" is written in the language of the request, in the request's own words and spelling.
  Never translate, never invent a name, number or step the request does not contain. Shorten a
  long phrase to its key words (at most {max_chars} characters) but keep the request's words.
- kind: process = an operation (rounded box); data = data or a tensor; store = a database, memory
  or file store; loss = an objective, loss or evaluation; note = a plain annotation.
- Put at most one node's "emphasis" to true: the part the request is about.
- Use regions only when the request groups things; otherwise one region with an empty label.
- An edge label only when the request names that flow; otherwise "". style feedback = a loop back,
  aux = a side input or supervision.
- {size_rule}
- A node is a part, data or step the request names, never a verb phrase that belongs on a line
  (put that on the edge). Never write a caption, a title, a legend or a figure number as a node.

Request:
{request}"""

_SIZE_RULE = {
    "method": "A structure diagram: 5 to 14 nodes, at most 4 regions.",
    "flow": "A flowchart: the steps in order, 4 to 12 nodes, decisions only if asked",
    "concept": "A concept diagram: a central idea with the parts around it, 4 to 12 nodes.",
}


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


def _supported(label: str, request: str) -> bool:
    """A label made of the request's own words: a whole substring, or every token with its
    last syllable trimmed found in the request (a planner may drop an ending).
    A request with no Hangul accepts any label (it is an English request)."""
    asked = _norm(request)
    if not re.search(r"[가-힣]", request):
        return True
    if _norm(label) in asked:
        return True
    for token in label.split():
        stem = _norm(token)
        if len(stem) > 2 and re.search(r"[가-힣]", stem):
            stem = stem[:-1]
        if stem not in asked:
            return False
    return bool(label.split())


def parse_plan(text: str, request: str) -> Plan | None:
    """The planner's JSON as a Plan whose labels all come from the request; None when too little
    is left to draw."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    def ok(value: Any) -> str | None:
        label = " ".join(str(value or "").split())
        if not label or len(label) > _MAX_LABEL_CHARS * 2 or not _supported(label, request):
            return None
        return label

    regions: list[Region] = []
    known: set[str] = set()
    emphasised = False
    for entry in raw.get("regions") or []:
        if not isinstance(entry, dict):
            continue
        region = Region(label=ok(entry.get("label")) or "")
        for item in entry.get("nodes") or []:
            if not isinstance(item, dict) or not (label := ok(item.get("label"))):
                continue
            if label in known:
                continue
            kind = (
                item.get("kind")
                if item.get("kind") in {"process", "data", "store", "loss", "note"}
                else "process"
            )
            mark = bool(item.get("emphasis")) and not emphasised
            emphasised = emphasised or mark
            region.nodes.append(Node(label=label, kind=kind, emphasis=mark))
            known.add(label)
        if region.nodes:
            regions.append(region)
    edges: list[Edge] = []
    for item in raw.get("edges") or []:
        if not isinstance(item, dict):
            continue
        source, target = ok(item.get("from")), ok(item.get("to"))
        if source not in known or target not in known or source == target:
            continue
        style = item.get("style") if item.get("style") in {"main", "aux", "feedback"} else "main"
        edges.append(Edge(source, target, ok(item.get("label")) or "", style))
    plan = Plan(regions, edges)
    if plan.node_count < 2 or len(plan.labels) > _MAX_LABELS:
        return None
    return plan


async def _chat(model: str, api_key: str, content: Any, *, max_tokens: int, timeout: float = 90.0):
    """One chat call: (text, usage) or None on any failure."""
    base, _ = await settings_store.litellm_config()
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        **thinking.switch(model),
    }
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
            if thinking.refused(model, response):
                body.pop("reasoning", None)
                body.update(thinking.switch(model))
                response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("paper figure chat call failed: %s", exc)
        return None
    text = str((payload.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    return text, usage_fields.spent(payload.get("usage") or {})


async def plan(request: str, *, style: str, model: str, api_key: str) -> Plan | None:
    """The labels and layout for this request, or None (the caller draws it the usual way)."""
    prompt = _PLANNER.format(
        max_chars=_MAX_LABEL_CHARS,
        size_rule=_SIZE_RULE[STYLES[style]],
        request=request.strip()[:2000],
    )
    answer = await _chat(model, api_key, prompt, max_tokens=1500)
    return parse_plan(answer[0], request) if answer else None


# ---------------------------------------------------------------- prompt

_STYLE = (
    "Style: a clean scientific figure as in a top-venue machine-learning paper. White background. "
    "Groups are large rounded regions with a very light pastel fill and a thin dashed border. "
    "Operations are rounded rectangles with a soft pastel fill and a thin darker outline. Data and "
    "tensors are small isometric slabs, databases and stores are cylinders, objectives and losses "
    "are drawn in a muted red family with a dashed border. The main flow is a solid mid-grey arrow "
    "with a small head; side inputs and supervision are thin dashed arrows; feedback loops curve "
    "back. One element, the one marked as emphasis, is drawn in a stronger accent colour. Flat "
    "vector look, generous even spacing, aligned rows, one sans-serif font family at one size for "
    "all labels. No gradients, no shadows, no decoration, no stock icons, no title, no caption, no "
    "legend, no figure number. The figure fills most of the canvas width and the labels are "
    "large enough to read at a glance. Every arrow starts and ends touching a box"
)

_KIND = {
    "process": "rounded rectangle",
    "data": "small isometric slab",
    "store": "cylinder",
    "loss": "box with a dashed muted-red border",
    "note": "plain text without a box",
}

_LAYOUT = {
    "method": "Lay the regions out left to right, nodes inside a region flowing the same way.",
    "flow": "Lay the steps out as one clear path, left to right, a second row only if long.",
    "concept": "Put the central node in the middle and the others around it, spread evenly.",
}

_RULES = (
    "TEXT RULES: The text in the picture is exactly the strings in the LABEL LIST below, each "
    "copied character for character in its own script, each used where the layout says and "
    "nowhere else. Write no other word, letter or number anywhere: nothing from these "
    "instructions, no node kind, no colour name, no arrow description. A line without a label "
    "carries no text."
)


def build_prompt(plan: Plan, *, style: str, aspect: str, fixes: str = "") -> str:
    """The English prompt: structure instructions, the label list apart, then the style."""
    lines: list[str] = []
    for index, region in enumerate(plan.regions, 1):
        if region.label:
            lines.append(f'Region {index}, titled "{region.label}", contains:')
        else:
            lines.append(f"Group {index} contains:")
        for node in region.nodes:
            bits = [f'- "{node.label}": {_KIND[node.kind]}']
            if node.emphasis:
                bits.append("(emphasis: accent colour)")
            lines.append(" ".join(bits))
    if plan.edges:
        lines.append("Connections:")
        for edge in plan.edges:
            how = {
                "main": "solid arrow",
                "aux": "thin dashed arrow",
                "feedback": "curved feedback arrow",
            }[edge.style]
            tail = f', carrying the text "{edge.label}"' if edge.label else ", with no text on it"
            lines.append(f'- from "{edge.source}" to "{edge.target}": {how}{tail}')
    structure = "\n".join(lines)
    label_list = "\n".join(f'{i}. "{text}"' for i, text in enumerate(plan.labels, 1))
    parts = [
        "Draw ONE figure for a research paper.",
        _LAYOUT[STYLES[style]],
        "STRUCTURE (instructions for you, not text to draw):\n" + structure,
        _RULES,
        "LABEL LIST (the only text allowed in the picture):\n" + label_list,
        _STYLE,
    ]
    if aspect and aspect != "1:1":
        parts.append(imagegen._shape_note(aspect))
    if fixes:
        parts.append(fixes)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- check


@dataclass(slots=True)
class Check:
    #: expected labels not found in the picture
    missing: list[str]
    #: seen lines that are no part of any label
    extra: list[str]
    #: (expected, seen) pairs: a label drawn with another spelling
    misspelled: list[tuple[str, str]]

    @property
    def score(self) -> int:
        """Lower is better: every label missing and every stray line counts one."""
        return len(self.missing) + len(self.extra)

    @property
    def clean(self) -> bool:
        return self.score == 0


def _alnum(text: str) -> str:
    """Whitespace, case and punctuation do not count."""
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).casefold()


def compare(expected: list[str], seen: list[str]) -> Check:
    """Code's comparison of the label list with the text read from the picture."""
    wanted = [(label, _alnum(label)) for label in expected]
    lines = [(line, _alnum(line)) for line in seen if _alnum(line)]
    haystack = "".join(norm for _, norm in lines)
    missing = [label for label, norm in wanted if norm and norm not in haystack]
    joined = "|".join(norm for _, norm in wanted)
    extra = [line for line, norm in lines if norm not in joined]
    # a line that is part of a label wrapped over lines is not extra; a stray line close to a
    # missing label is that label misspelled
    misspelled: list[tuple[str, str]] = []
    for label in list(missing):
        norm = _alnum(label)
        near = [line for line, n in lines if (line in extra) and _similar(norm, n)]
        if near:
            misspelled.append((label, near[0]))
    return Check(missing, extra, misspelled)


def _similar(a: str, b: str) -> bool:
    """Same length within two and at least half the characters in common, in order."""
    if not a or not b or abs(len(a) - len(b)) > 2:
        return False
    hits = sum(1 for x, y in zip(a, b, strict=False) if x == y)
    return hits * 2 >= max(len(a), len(b))


_READER = """Read the picture. List every piece of text that is visible in it, one entry per
separate text element (a label wrapped over two lines is one entry, joined with a space), in
reading order, exactly as written: keep the script, the spelling and any mistake you see. Do
not correct, translate or guess. Answer with a JSON array of strings only; [] if no text."""


async def read_text(
    image: imagegen.GeneratedImage, *, model: str, api_key: str
) -> list[str] | None:
    """The text a picture-reading model sees in the figure, or None when it could not say."""
    url = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
    content = [
        {"type": "text", "text": _READER},
        {"type": "image_url", "image_url": {"url": url}},
    ]
    answer = await _chat(model, api_key, content, max_tokens=1500, timeout=120.0)
    if not answer:
        return None
    text = answer[0].strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return [str(item) for item in raw if isinstance(item, (str, int, float))]


_CONFIRM = """Look at the picture. For each quoted label below, say whether exactly that string,
with those very characters and spelling, is written somewhere in the picture. A different
spelling of a similar word means false. Answer with one JSON object mapping each label to true
or false, and nothing else.

Labels:
{labels}"""


async def confirm(
    image: imagegen.GeneratedImage, labels: list[str], *, model: str, api_key: str
) -> set[str] | None:
    """Which of these labels are written in the picture exactly (a second, narrower look at what the
    first reading found wrong: a reading model sometimes invents a typo it then reports)."""
    url = f"data:{image.mime};base64,{base64.b64encode(image.data).decode()}"
    listing = "\n".join(f'- "{label}"' for label in labels)
    content = [
        {"type": "text", "text": _CONFIRM.format(labels=listing)},
        {"type": "image_url", "image_url": {"url": url}},
    ]
    answer = await _chat(model, api_key, content, max_tokens=800, timeout=120.0)
    if not answer:
        return None
    text = answer[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return {label for label in labels if raw.get(label) is True}


async def examine(
    image: imagegen.GeneratedImage,
    expected: list[str],
    *,
    model: str,
    api_key: str,
    second: str | None = None,
) -> Check | None:
    """The picture's text against the label list: read blind, then each label the reading found
    wrong is asked about once more; None when the picture could not be read."""
    seen = await read_text(image, model=model, api_key=api_key)
    if seen is None:
        return None
    check = compare(expected, seen)
    if not check.missing:
        return check
    # another model when there is one: a reading model's own misreading repeats
    present = await confirm(image, check.missing, model=second or model, api_key=api_key)
    if not present:
        return check
    # a label the second look finds written exactly was misread: it and the stray line the
    # reading made of it are not faults
    cleared = {label for label in check.missing if label in present}
    strays = {seen_ for label, seen_ in check.misspelled if label in cleared}
    return Check(
        [label for label in check.missing if label not in cleared],
        [line for line in check.extra if line not in strays],
        [(e, s_) for e, s_ in check.misspelled if e not in cleared],
    )


def fixes_for(check: Check) -> str:
    """What the redraw is told: the labels that came out wrong and the text that must go."""
    notes: list[str] = []
    for label in check.missing:
        seen = next((s for e, s in check.misspelled if e == label), "")
        if seen:
            notes.append(
                f'the label "{label}" was drawn as "{seen}"; it must read exactly "{label}"'
            )
        else:
            notes.append(f'the label "{label}" was missing or unreadable; it must appear exactly')
    stray = [line for line in check.extra if all(line != s for _, s in check.misspelled)]
    if stray:
        notes.append(
            "text that is not in the label list must not appear: "
            + ", ".join(f'"{s}"' for s in stray)
        )
    return (
        "CORRECTIONS for this second attempt: "
        + "; ".join(notes)
        + ". Keep everything else the same."
    )


# ---------------------------------------------------------------- draw


@dataclass(slots=True)
class Drawn:
    image: imagegen.GeneratedImage
    prompt: str
    #: images actually drawn, each billed
    drawn: list[imagegen.GeneratedImage]
    #: ok | retried | skipped
    checked: str
    issues: int


async def draw(
    plan_: Plan,
    *,
    style: str,
    aspect: str,
    base_url: str,
    api_key: str,
    model: str,
    reader: str | None,
    second_reader: str | None = None,
) -> Drawn:
    """One picture, checked, redrawn once if wrong. `ImageError` from the first draw goes up; a
    failed redraw keeps the first picture."""
    prompt = build_prompt(plan_, style=style, aspect=aspect)
    first = await imagegen.generate(
        base_url=base_url, api_key=api_key, model=model, prompt=prompt, aspect=aspect
    )
    drawn = [first]
    one = (
        await examine(first, plan_.labels, model=reader, api_key=api_key, second=second_reader)
        if reader
        else None
    )
    if one is None:
        return Drawn(first, prompt, drawn, "skipped", 0)
    if one.clean:
        return Drawn(first, prompt, drawn, "ok", 0)

    again_prompt = build_prompt(plan_, style=style, aspect=aspect, fixes=fixes_for(one))
    try:
        second = await imagegen.generate(
            base_url=base_url, api_key=api_key, model=model, prompt=again_prompt, aspect=aspect
        )
    except imagegen.ImageError as exc:
        log.info("paper figure redraw failed, keeping the first picture: %s", exc)
        return Drawn(first, prompt, drawn, "retried", one.score)
    drawn.append(second)
    two = await examine(second, plan_.labels, model=reader, api_key=api_key, second=second_reader)
    if two is None:
        # cannot tell: the redraw was told what was wrong with the first
        return Drawn(second, again_prompt, drawn, "retried", one.score)
    if two.score <= one.score:
        return Drawn(second, again_prompt, drawn, "retried", two.score)
    return Drawn(first, prompt, drawn, "retried", one.score)


__all__ = [
    "STYLES",
    "Check",
    "Drawn",
    "Plan",
    "build_prompt",
    "compare",
    "draw",
    "parse_plan",
    "plan",
    "routes_here",
]
