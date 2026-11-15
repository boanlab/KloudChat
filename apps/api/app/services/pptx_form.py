"""A deck written on a PowerPoint form (양식): the form's slide master, layouts, theme fonts and
colours, its own placeholders filled, and KloudChat's drawn content (tables, charts, patterns,
figures) set inside each layout's body area.

How: the house renderer (`deck_export`) draws the deck into the form's own package in its bare
mode (no ornaments, no foot, theme colours as tokens), each slide on a scratch slide. Every
slide then gets a new slide on the form layout its kind maps to (`layout_class`): the title,
subtitle and plain bullets go into the layout's placeholders, where the form's type and bullets
apply; anything else drawn is moved, scaled into the layout's body area, its colours written as
theme colours and its typefaces left to the theme. The scratch slides and the form's own sample
slides are removed, and the form's slide size is kept.

`write` returns the file and what was followed, for the answer's 「양식을 따름」 line.
"""

from __future__ import annotations

import copy
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from lxml import etree
from pptx import Presentation
from pptx.util import Emu

from app.services import form_profile

log = logging.getLogger(__name__)

#: deck slide kind → form layout classes, best first
_WANTS: dict[str, tuple[str, ...]] = {
    "cover": ("title", "section", "title_only", "content"),
    "section": ("section", "title", "title_only", "content"),
    "closing": ("title", "section", "title_only", "content"),
    "text": ("content", "two", "title_only"),
    "two": ("two", "comparison", "content", "title_only"),
    "drawn": ("title_only", "content", "two", "blank"),
    "free": ("blank", "title_only", "content"),
}
#: deck layouts written into placeholders when they hold only words
_TEXT_LAYOUTS = {"bullets", "numbered", "agenda", "text", "summary", "checklist"}
_TWO_LAYOUTS = {"two-column", "compare", "pros-cons", "before-after"}
#: deck layouts whose whole face is drawn, title included
_FREE_LAYOUTS = {"statement", "quote"}

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_ATTRS = (f"{{{_R_NS}}}embed", f"{{{_R_NS}}}link", f"{{{_R_NS}}}id",
              f"{{{_R_NS}}}pict")
_TITLES = {"title", "ctrTitle"}
#: smallest type a moved shape is scaled to, in hundredths of a point
_FLOOR_SZ = 1000


class FormWriteError(ValueError):
    """The form could not be followed; the message is shown to the person."""


@dataclass
class Followed:
    """What the written deck took from the form, for the 「양식을 따름」 line."""

    #: form layout name → slides written on it
    layouts: dict[str, int] = field(default_factory=dict)
    #: slides whose kind had no layout of its own in the form: `[(slide no, kind, used)]`
    stand_ins: list[tuple[int, str, str]] = field(default_factory=list)
    slides: int = 0
    size: str = ""
    fonts: dict[str, str] = field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        return {
            "layouts": dict(self.layouts), "slides": self.slides, "size": self.size,
            "fonts": dict(self.fonts),
            "standIns": [{"slide": n, "kind": k, "layout": used} for n, k, used in
                         self.stand_ins],
        }


def kind_of(data: dict) -> str:
    """A deck slide's kind for layout choice."""
    layout = str(data.get("layout") or "bullets")
    if layout == "title":
        return "cover"
    if layout in ("section", "closing"):
        return layout
    if layout in _FREE_LAYOUTS:
        return "free"
    has_more = bool(data.get("rows") or data.get("metrics") or data.get("chart")
                    or data.get("image") or data.get("diagram"))
    if layout in _TWO_LAYOUTS and not has_more:
        return "two"
    if layout in _TEXT_LAYOUTS and not has_more:
        return "text"
    return "drawn"


def _layouts_by_class(profile: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for layout in profile.get("layouts") or []:
        out.setdefault(layout["class"], []).append(layout)
    return out


def _pick(by_class: dict[str, list[dict]], kind: str) -> tuple[dict | None, bool]:
    """(layout, exact) for a slide kind."""
    wanted = _WANTS[kind]
    for rank, name in enumerate(wanted):
        if by_class.get(name):
            return by_class[name][0], rank == 0
    every = [lay for group in by_class.values() for lay in group]
    return (every[0] if every else None), False


def theme_tokens(profile: dict, tokens: dict[str, str] | None) -> dict[str, str]:
    """The renderer's tokens with the form's theme colours and face laid over."""
    from app.services import design

    theme = profile.get("theme") or {}
    colours = theme.get("colours") or {}
    fonts = theme.get("fonts") or {}
    raw = dict(tokens or {})
    if colours.get("accent1"):
        raw["accent"] = colours["accent1"]
    if colours.get("dk1"):
        raw["ink"] = colours["dk1"]
    face = (fonts.get("minor") or "") + " " + (fonts.get("minor_latin") or "")
    raw["font"] = "serif" if any(w in face.lower() for w in (
        "바탕", "명조", "batang", "myeongjo", "serif", "times", "georgia")) else "gothic"
    raw["visualStyle"] = "form"
    raw.pop("logo", None)
    raw.pop("footer", None)
    return design.normalise_tokens(raw) | {"visualStyle": "form"}


# ── shapes ─────────────────────────────────────────────────────────────


def _xfrm(element: etree._Element) -> etree._Element | None:
    tag = etree.QName(element).localname
    if tag == "graphicFrame":
        return element.find(f"{{{_P_NS}}}xfrm")
    holder = element.find(f"{{{_P_NS}}}grpSpPr" if tag == "grpSp" else f"{{{_P_NS}}}spPr")
    return holder.find(f"{{{_A_NS}}}xfrm") if holder is not None else None


def _box(element: etree._Element) -> tuple[int, int, int, int] | None:
    xfrm = _xfrm(element)
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{{{_A_NS}}}off"), xfrm.find(f"{{{_A_NS}}}ext")
    if off is None or ext is None:
        return None
    return (int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)),
            int(ext.get("cy", 0)))


def _ph(element: etree._Element) -> etree._Element | None:
    return element.find(f".//{{{_P_NS}}}nvPr/{{{_P_NS}}}ph")


def _hidden(element: etree._Element) -> bool:
    return any(node.get("hidden") in ("1", "true")
               for node in element.iter(f"{{{_P_NS}}}cNvPr"))


def _moved(element: etree._Element, scale: float, dx: int, dy: int, ox: int, oy: int) -> None:
    """`element` moved from origin (ox, oy) to (dx, dy), scaled by `scale`, type included."""
    xfrm = _xfrm(element)
    if xfrm is not None:
        off, ext = xfrm.find(f"{{{_A_NS}}}off"), xfrm.find(f"{{{_A_NS}}}ext")
        if off is not None:
            off.set("x", str(int(dx + (int(off.get("x", 0)) - ox) * scale)))
            off.set("y", str(int(dy + (int(off.get("y", 0)) - oy) * scale)))
        if ext is not None:
            ext.set("cx", str(int(int(ext.get("cx", 0)) * scale)))
            ext.set("cy", str(int(int(ext.get("cy", 0)) * scale)))
    if scale < 0.98:
        # a table's own grid and rows, which its frame does not size
        for node in element.iter(f"{{{_A_NS}}}gridCol"):
            if node.get("w"):
                node.set("w", str(int(int(node.get("w")) * scale)))
        for node in element.iter(f"{{{_A_NS}}}tr"):
            if node.get("h"):
                node.set("h", str(int(int(node.get("h")) * scale)))
        for node in element.iter(f"{{{_A_NS}}}rPr", f"{{{_A_NS}}}defRPr",
                                 f"{{{_A_NS}}}endParaRPr"):
            if node.get("sz"):
                node.set("sz", str(max(_FLOOR_SZ, int(int(node.get("sz")) * scale))))


def _themed(element: etree._Element, accent: str, ink: str) -> None:
    """Colours the renderer wrote as the form's accent and ink become theme colours, and its
    typefaces are dropped so the form's theme fonts apply."""
    targets = {accent.lstrip("#").upper(): "accent1", ink.lstrip("#").upper(): "tx1"}
    for node in list(element.iter(f"{{{_A_NS}}}srgbClr")):
        scheme = targets.get((node.get("val") or "").upper())
        if scheme is None:
            continue
        made = etree.Element(f"{{{_A_NS}}}schemeClr", val=scheme)
        for child in node:
            made.append(child)
        node.getparent().replace(node, made)
    for tag in ("latin", "ea", "cs"):
        for node in list(element.iter(f"{{{_A_NS}}}{tag}")):
            if etree.QName(node.getparent()).localname in ("rPr", "defRPr", "endParaRPr"):
                node.getparent().remove(node)


def _relink(element: etree._Element, source, target) -> None:
    """Relationship ids in a moved element re-made on the slide it moves to."""
    for node in element.iter():
        for attr in _REL_ATTRS:
            rid = node.get(attr)
            if not rid or rid not in source.part.rels:
                continue
            rel = source.part.rels[rid]
            if rel.is_external:
                new = target.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                new = target.part.relate_to(rel.target_part, rel.reltype)
            node.set(attr, new)


def _remove(shape) -> None:
    parent = shape._element.getparent()
    if parent is not None:
        parent.remove(shape._element)


def _drop_placeholders(slide, roles: set[str]) -> None:
    for shape in list(slide.placeholders):
        kind = shape.placeholder_format.type
        name = getattr(kind, "name", "") or ""
        role = "title" if name in ("TITLE", "CENTER_TITLE") else "subtitle" \
            if name == "SUBTITLE" else "body" if name in ("BODY", "OBJECT", "TABLE", "CHART",
                                                          "PICTURE", "") else "other"
        if role in roles:
            _remove(shape)


def _set_lines(frame, lines: list[str]) -> None:
    frame.clear()
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = line


def _holders(slide) -> dict[str, list]:
    out: dict[str, list] = {"title": [], "subtitle": [], "body": []}
    for shape in slide.placeholders:
        name = getattr(shape.placeholder_format.type, "name", "") or ""
        if name in ("TITLE", "CENTER_TITLE"):
            out["title"].append(shape)
        elif name == "SUBTITLE":
            out["subtitle"].append(shape)
        elif name in ("BODY", "OBJECT") and shape.has_text_frame:
            out["body"].append(shape)
    out["body"].sort(key=lambda s: (s.left or 0, s.top or 0))
    return out


def _body_area(layout: dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The layout's area for drawn content, in EMU: its body placeholders, else below the
    title down to the foot."""
    width, height = size
    holders = layout.get("placeholders") or []
    bodies = [p["box"] for p in holders if p["role"] in ("body", "picture") and p.get("box")]
    if bodies:
        left = min(b[0] for b in bodies)
        top = min(b[1] for b in bodies)
        right = max(b[0] + b[2] for b in bodies)
        bottom = max(b[1] + b[3] for b in bodies)
    else:
        title = next((p["box"] for p in holders if p["role"] == "title" and p.get("box")), None)
        feet = [p["box"][1] for p in holders
                if p["role"] in ("date", "footer", "number") and p.get("box")]
        left, right = (title[0], title[0] + title[2]) if title else (0.07, 0.93)
        top = (title[1] + title[3] + 0.02) if title else 0.08
        bottom = (min(feet) - 0.01) if feet else 0.92
    return (int(left * width), int(top * height), int((right - left) * width),
            int((bottom - top) * height))


def _move_drawn(rendered, slide, area: tuple[int, int, int, int], *, keep_title: bool,
                accent: str, ink: str) -> int:
    """Shapes drawn on `rendered` (the title aside unless `keep_title`) moved into `area` of
    `slide`; returns how many."""
    elements = []
    for element in list(rendered.shapes._spTree):
        if etree.QName(element).localname not in ("sp", "pic", "grpSp", "graphicFrame",
                                                   "cxnSp"):
            continue
        if _hidden(element):
            continue
        ph = _ph(element)
        if ph is not None and (ph.get("type") or "body") in _TITLES and not keep_title:
            continue
        elements.append(element)
    boxes = [b for e in elements if (b := _box(e)) is not None and (b[2] or b[3])]
    if not boxes:
        return 0
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    width = max(b[0] + b[2] for b in boxes) - left
    height = max(b[1] + b[3] for b in boxes) - top
    scale = min(1.0, area[2] / max(width, 1), area[3] / max(height, 1))
    dx = area[0] + int((area[2] - width * scale) / 2)
    dy = area[1]
    tree = slide.shapes._spTree
    for element in elements:
        made = copy.deepcopy(element)
        ph = _ph(made)
        if ph is not None:
            ph.getparent().remove(ph)
        _moved(made, scale, dx, dy, left, top)
        _themed(made, accent, ink)
        _relink(made, rendered, slide)
        tree.append(made)
    return len(elements)


def _remove_slides(prs, keep: set[int]) -> None:
    ids = prs.slides._sldIdLst
    for index, entry in reversed(list(enumerate(list(ids)))):
        if index in keep:
            continue
        prs.part.drop_rel(entry.rId)
        ids.remove(entry)


def write(form: bytes, title: str, slides: list[dict], *,
          tokens: dict[str, str] | None = None, profile: dict | None = None,
          name: str = "form.pptx") -> tuple[bytes, Followed]:
    """`slides` (the deck's slide data) written on the PowerPoint `form`."""
    from app.services import deck_export

    found = profile or form_profile.profile(name, form)
    if not found or found.get("kind") != "pptx" or found.get("error"):
        raise FormWriteError((found or {}).get("error") or "PowerPoint 양식이 아닙니다.")
    if not found.get("layouts"):
        raise FormWriteError("양식에 슬라이드 레이아웃이 없습니다.")
    data = form_profile.as_document(form)
    try:
        original = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any reader failure is an unreadable form
        raise FormWriteError("PowerPoint 양식을 열지 못했습니다.") from exc
    size = (int(original.slide_width), int(original.slide_height))
    look = theme_tokens(found, tokens)
    # the house renderer draws each slide into the form's own package, bare
    drawn = deck_export.render_on(data, title, slides, tokens=look)
    prs = Presentation(io.BytesIO(drawn))
    written = deck_export.written_slides(slides)
    first_drawn = len(prs.slides) - len(written)
    rendered = list(prs.slides)[first_drawn:]
    every = [lay for master in prs.slide_masters for lay in master.slide_layouts]
    by_class = _layouts_by_class(found)
    followed = Followed(size=found.get("size") or "",
                        fonts={k: v for k, v in ((found.get("theme") or {}).get("fonts")
                                                 or {}).items() if k in ("major", "minor")})
    targets: list[int] = []
    for number, (data_slide, scratch) in enumerate(zip(written, rendered, strict=True), 1):
        kind = kind_of(data_slide)
        chosen, exact = _pick(by_class, kind)
        if chosen is None or chosen["index"] >= len(every):
            raise FormWriteError("양식의 레이아웃을 읽지 못했습니다.")
        layout = every[chosen["index"]]
        slide = prs.slides.add_slide(layout)
        targets.append(len(prs.slides) - 1)
        followed.layouts[chosen["name"] or f"#{chosen['index'] + 1}"] = \
            followed.layouts.get(chosen["name"] or f"#{chosen['index'] + 1}", 0) + 1
        if not exact:
            followed.stand_ins.append((number, kind, chosen["name"]))
        holders = _holders(slide)
        heading = str(data_slide.get("title") or "")
        body = str(data_slide.get("body") or "")
        bullets = [str(b) for b in data_slide.get("bullets") or [] if str(b).strip()]
        area = _body_area(chosen, size)
        accent, ink = look["accent"], look["ink"]
        if kind in ("cover", "closing", "section"):
            if holders["title"]:
                holders["title"][0].text_frame.text = heading or (title if kind == "cover"
                                                                  else "")
            under = holders["subtitle"] or holders["body"]
            if under and body:
                _set_lines(under[0].text_frame, body.splitlines())
                under = under[1:]
            _drop_placeholders(slide, {"subtitle", "body"} if not body else set())
            for left_over in under:
                if not left_over.text_frame.text.strip():
                    _remove(left_over)
        elif kind == "free":
            _drop_placeholders(slide, {"title", "subtitle", "body"})
            _move_drawn(scratch, slide, area, keep_title=True, accent=accent, ink=ink)
        else:
            if holders["title"]:
                holders["title"][0].text_frame.text = heading
            if kind == "text" and holders["body"]:
                _set_lines(holders["body"][0].text_frame, ([body] if body else []) + bullets)
                for extra in holders["body"][1:]:
                    _remove(extra)
            elif kind == "two" and len(holders["body"]) >= 2:
                columns = deck_export.columns_of(data_slide, bullets)
                for holder, lines in zip(holders["body"], columns, strict=False):
                    _set_lines(holder.text_frame, lines)
                for extra in holders["body"][len(columns):]:
                    _remove(extra)
            else:
                _drop_placeholders(slide, {"subtitle", "body"})
                _move_drawn(scratch, slide, area, keep_title=False, accent=accent, ink=ink)
        notes = str(data_slide.get("notes") or "").strip()
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    _remove_slides(prs, set(targets))
    prs.slide_width, prs.slide_height = Emu(size[0]), Emu(size[1])
    followed.slides = len(targets)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue(), followed


def blank(form: bytes, *, name: str = "form.pptx", title: str = "") -> bytes:
    """The form with its sample slides removed and one cover on its title layout: where the
    PowerPoint tools start a deck on this form."""
    made, _ = write(form, title, [{"layout": "title", "title": title, "body": ""}], name=name)
    return made
