"""What an uploaded Office file offers as a form (양식): read once, on upload, by code.

Profile of what a form holds (PowerPoint layouts, theme fonts, colours; Word heading styles,
header/footer, fields; 한글 label and value cells) and how likely the file is a form (`score`,
`verdict`).

`.potx` and `.dotx` are forms by kind. Binary `.hwp` is never followed as a form: it is read as
material, and a form-looking one prompts the user to save as HWPX (`HWP_FORM_ADVICE`).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from app.services.files import open_capped_zip
from app.services.office_edit import BLANK

#: profile version; a stored profile of another version is read again
VERSION = 3

#: score at or above which a file is a form, at or below which it is material
FORM_AT = 0.6
MATERIAL_AT = 0.3

#: suffix → output kind the form shapes
KIND_OF = {
    ".pptx": "pptx", ".potx": "pptx",
    ".docx": "docx", ".dotx": "docx",
    ".hwpx": "hwpx", ".hwp": "hwp",
}

#: Office template content types and the document types they open as
_TEMPLATE_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
}

HWP_FORM_ADVICE = (
    "이 파일은 예전 한글 형식(.hwp)이라 양식으로 따를 수 없습니다. 한글에서 파일 › 다른 이름으로 "
    "저장 › 파일 형식 「한글 표준 문서 (*.hwpx)」로 저장한 뒤 다시 올려 주세요. "
    "이번에는 이 파일을 자료로만 읽었습니다."
)

#: a file name that says it is a form
_FORM_NAME = re.compile(r"양식|서식|별지|별첨\s{0,2}\d|template|form|틀", re.I)
#: guidance a form leaves for whoever fills it
_GUIDANCE = re.compile(r"^\s{0,4}※|작성\s{0,2}요령|작성\s{0,2}방법|작성\s{0,2}예시|기재\s{0,2}요령|"
                       r"\(\s{0,2}예\s{0,2}[:：]|입력하(?:십시오|세요)|여기에\s{0,2}입력")
#: placeholder strings that mark a value to fill
_FILL = re.compile(r"○{2,}|◯{2,}|_{4,}|＿{3,}|0000\.\s{0,2}00\.\s{0,2}00|20\d\d\.\s{0,2}00\.|"
                   r"홍길동|\(\s{0,3}\)|□")
#: prompt text an empty PowerPoint placeholder shows
_PPT_PROMPT = re.compile(
    r"^(?:click to (?:add|edit)|제목을 입력|텍스트를 입력|부제목을 입력|내용을 입력|"
    r"마스터 (?:제목|텍스트) 스타일|제목 입력|텍스트 입력|여기에)", re.I)
#: Word heading style names and ids, any language Office writes
_HEADING = re.compile(r"^(?:heading|제목|title|머리글)\s{0,2}(\d)$", re.I)

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
#: an OLE compound file (HWP 5, old Office)
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class FormError(ValueError):
    """A file that cannot be read as a form; the message is shown to the person."""


def kind_of(name: str) -> str:
    """`pptx`, `docx`, `hwpx`, `hwp` or ``."""
    return KIND_OF.get(Path(name).suffix.lower(), "")


def as_document(data: bytes) -> bytes:
    """A `.potx`/`.dotx` with its main part typed as a presentation/document, so the
    python-pptx and python-docx readers open it; other files unchanged."""
    try:
        archive = open_capped_zip(data)
    except (zipfile.BadZipFile, RuntimeError):
        return data
    with archive:
        try:
            types = archive.read("[Content_Types].xml").decode("utf-8")
        except KeyError:
            return data
        if not any(t in types for t in _TEMPLATE_TYPES):
            return data
        for template, document in _TEMPLATE_TYPES.items():
            types = types.replace(template, document)
        import io

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
            for info in archive.infolist():
                body = types.encode("utf-8") if info.filename == "[Content_Types].xml" \
                    else archive.read(info.filename)
                copy.writestr(info, body)
        return out.getvalue()


def _xml(archive: zipfile.ZipFile, name: str) -> etree._Element | None:
    try:
        return etree.fromstring(archive.read(name), _PARSER)
    except (KeyError, etree.XMLSyntaxError):
        return None


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship id → target part path, for `part`."""
    folder, _, leaf = part.rpartition("/")
    root = _xml(archive, f"{folder}/_rels/{leaf}.rels")
    out: dict[str, str] = {}
    if root is None:
        return out
    for rel in root.iter(f"{{{_REL}}}Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        parts = (target.lstrip("/") if target.startswith("/") else f"{folder}/{target}").split("/")
        resolved: list[str] = []
        for piece in parts:
            if piece == "..":
                if resolved:
                    resolved.pop()
            elif piece not in ("", "."):
                resolved.append(piece)
        out[str(rel.get("Id"))] = "/".join(resolved)
    return out


def _texts(root: etree._Element, tag: str) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(tag)) for p in root.iter()
            if etree.QName(p).localname == "p"]


def _verdict(score: float) -> str:
    return "form" if score >= FORM_AT else "material" if score <= MATERIAL_AT else "unsure"


def _scored(signals: list[tuple[str, float]], *, floor: float = 0.0) -> tuple[float, list[str]]:
    score = max(floor, min(1.0, sum(weight for _, weight in signals)))
    return round(score, 2), [name for name, _ in signals]


# ── PowerPoint ─────────────────────────────────────────────────────────────


#: placeholder type → role; a placeholder without a type is a body
_PH_ROLE = {
    "title": "title", "ctrTitle": "title", "subTitle": "subtitle", "body": "body",
    "obj": "body", "tbl": "body", "chart": "body", "dgm": "body", "media": "body",
    "pic": "picture", "clipArt": "picture", "dt": "date", "ftr": "footer", "sldNum": "number",
    "hdr": "header",
}

_LAYOUT_NAMES = (
    (re.compile(r"section|구역|섹션|간지", re.I), "section"),
    (re.compile(r"comparison|비교", re.I), "comparison"),
    (re.compile(r"two|2\s{0,2}개|두\s{0,2}개|2단", re.I), "two"),
    (re.compile(r"title only|제목만", re.I), "title_only"),
    (re.compile(r"blank|빈\s{0,2}화면|빈\s{0,2}슬라이드", re.I), "blank"),
    (re.compile(r"picture|그림|사진|캡션", re.I), "picture"),
    (re.compile(r"^title slide|제목\s{0,2}슬라이드|표지|cover", re.I), "title"),
)


#: what a layout is called for, by its name: the writer's kinds pick these before the generic
#: class (`pptx_form._pick`)
_ROLE_NAMES = (
    (re.compile(r"마무리|감사|끝맺|closing|thank|q\s?&\s?a|\bend\b", re.I), "closing"),
    (re.compile(r"목차|차례|agenda|contents|outline", re.I), "agenda"),
    (re.compile(r"핵심\s{0,2}(?:숫자|수치|지표)|key\s{0,2}(?:figure|number|metric)|kpi|big\s{0,2}number",
                re.I), "numbers"),
    (re.compile(r"표\s{0,2}전용|table|표$", re.I), "table"),
)


def layout_role(name: str) -> str:
    """What a layout's name says it is for (`closing`, `agenda`, `numbers`, `table`), or ``."""
    return next((role for pattern, role in _ROLE_NAMES if pattern.search(name or "")), "")


def _box(sp: etree._Element, size: tuple[int, int]) -> list[float] | None:
    off = sp.find(f".//{{{_A}}}xfrm/{{{_A}}}off")
    ext = sp.find(f".//{{{_A}}}xfrm/{{{_A}}}ext")
    if off is None or ext is None or not size[0] or not size[1]:
        return None
    try:
        x, y = int(off.get("x", 0)), int(off.get("y", 0))
        w, h = int(ext.get("cx", 0)), int(ext.get("cy", 0))
    except ValueError:
        return None
    return [round(x / size[0], 3), round(y / size[1], 3), round(w / size[0], 3),
            round(h / size[1], 3)]


def _placeholders(root: etree._Element, size: tuple[int, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sp in root.iter(f"{{{_P}}}sp", f"{{{_P}}}pic", f"{{{_P}}}graphicFrame"):
        ph = sp.find(f".//{{{_P}}}nvPr/{{{_P}}}ph")
        if ph is None:
            continue
        role = _PH_ROLE.get(ph.get("type") or "body", "body")
        item: dict[str, Any] = {"role": role, "idx": int(ph.get("idx") or 0)}
        if (box := _box(sp, size)) is not None:
            item["box"] = box
        out.append(item)
    return out


def layout_class(name: str, placeholders: list[dict[str, Any]]) -> str:
    """What a layout is for: `title`, `section`, `content`, `two`, `comparison`, `title_only`,
    `picture` or `blank`; the placeholders decide, the name breaks ties."""
    roles = [p["role"] for p in placeholders if p["role"] not in ("date", "footer", "number",
                                                                  "header")]
    titles = roles.count("title")
    bodies = roles.count("body")
    named = next((kind for pattern, kind in _LAYOUT_NAMES if pattern.search(name or "")), "")
    if not roles:
        return "blank"
    if "picture" in roles and bodies <= 1:
        return "picture"
    if titles and "subtitle" in roles and not bodies:
        return "title"
    if titles and bodies >= 4:
        return "comparison"
    if titles and bodies in (2, 3):
        return "comparison" if named == "comparison" else "two"
    if titles and bodies == 1:
        body = next(p for p in placeholders if p["role"] == "body")
        short = (body.get("box") or [0, 0, 1, 1])[3] < 0.25
        return "section" if named == "section" or short else "content"
    if titles and not bodies:
        return named if named in ("title", "section") else "title_only"
    return named or "content"


def _theme(archive: zipfile.ZipFile, master: str) -> dict[str, Any]:
    theme_part = next((t for t in _rels(archive, master).values() if "/theme/" in t), "")
    root = _xml(archive, theme_part) if theme_part else None
    if root is None:
        return {}
    colours: dict[str, str] = {}
    scheme = root.find(f".//{{{_A}}}clrScheme")
    if scheme is not None:
        for slot in scheme:
            value = slot.find(f"{{{_A}}}srgbClr")
            system = slot.find(f"{{{_A}}}sysClr")
            if value is not None and value.get("val"):
                colours[etree.QName(slot).localname] = f"#{value.get('val').upper()}"
            elif system is not None and system.get("lastClr"):
                colours[etree.QName(slot).localname] = f"#{system.get('lastClr').upper()}"
    fonts: dict[str, str] = {}
    for which in ("major", "minor"):
        node = root.find(f".//{{{_A}}}{which}Font")
        if node is None:
            continue
        latin = node.find(f"{{{_A}}}latin")
        ea = node.find(f"{{{_A}}}ea")
        hang = next((f.get("typeface") for f in node.iter(f"{{{_A}}}font")
                     if f.get("script") == "Hang"), "")
        fonts[which] = (ea.get("typeface") if ea is not None and ea.get("typeface") else hang) \
            or (latin.get("typeface") if latin is not None else "") or ""
        if latin is not None and latin.get("typeface"):
            fonts[f"{which}_latin"] = latin.get("typeface")
    return {"colours": colours, "fonts": fonts}


def _pptx(data: bytes, name: str) -> dict[str, Any]:
    try:
        archive = open_capped_zip(data)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise FormError("PowerPoint 파일을 열지 못했습니다.") from exc
    with archive:
        presentation = _xml(archive, "ppt/presentation.xml")
        if presentation is None:
            raise FormError("PowerPoint 파일의 구조를 읽지 못했습니다.")
        size_node = presentation.find(f"{{{_P}}}sldSz")
        size = (int(size_node.get("cx", 0)), int(size_node.get("cy", 0))) \
            if size_node is not None else (12192000, 6858000)
        rels = _rels(archive, "ppt/presentation.xml")
        masters = [rels.get(m.get(f"{{{_R}}}id"), "") for m in
                   presentation.iter(f"{{{_P}}}sldMasterId")]
        layouts: list[dict[str, Any]] = []
        theme: dict[str, Any] = {}
        for master_index, master in enumerate(m for m in masters if m):
            if not theme:
                theme = _theme(archive, master)
            master_root = _xml(archive, master)
            if master_root is None:
                continue
            master_rels = _rels(archive, master)
            # a layout placeholder without its own box takes the master's of its kind
            inherited = {p["role"]: p["box"] for p in _placeholders(master_root, size)
                         if p.get("box")}
            for entry in master_root.iter(f"{{{_P}}}sldLayoutId"):
                part = master_rels.get(entry.get(f"{{{_R}}}id"), "")
                root = _xml(archive, part) if part else None
                if root is None:
                    continue
                cSld = root.find(f"{{{_P}}}cSld")
                layout_name = cSld.get("name", "") if cSld is not None else ""
                holders = _placeholders(root, size)
                for holder in holders:
                    if "box" not in holder:
                        role = "body" if holder["role"] in ("subtitle", "picture") \
                            else holder["role"]
                        if role in inherited:
                            holder["box"] = inherited[role]
                layouts.append({
                    "index": len(layouts), "master": master_index, "name": layout_name,
                    "part": part, "class": layout_class(layout_name, holders),
                    "role": layout_role(layout_name),
                    "placeholders": holders,
                })
        slides: list[dict[str, Any]] = []
        for slide in presentation.iter(f"{{{_P}}}sldId"):
            part = rels.get(slide.get(f"{{{_R}}}id"), "")
            root = _xml(archive, part) if part else None
            if root is None:
                continue
            layout_part = next((t for t in _rels(archive, part).values()
                                if "/slideLayouts/" in t), "")
            texts = [t.strip() for t in _texts(root, f"{{{_A}}}t") if t.strip()]
            prompt = all(_PPT_PROMPT.search(t) for t in texts)
            slides.append({
                "layout": next((lay["index"] for lay in layouts if lay["part"] == layout_part),
                               None),
                "chars": sum(len(t) for t in texts),
                "prompt": prompt,
            })
    for layout in layouts:
        layout.pop("part", None)
    width, height = size
    ratio = "16:9" if height and abs(width / height - 16 / 9) < 0.03 else \
        "4:3" if height and abs(width / height - 4 / 3) < 0.03 else \
        f"{round(width / 360000)}x{round(height / 360000)}cm"
    filled = [s for s in slides if not s["prompt"] and s["chars"] > 0]
    classes = {lay["class"] for lay in layouts}
    signals: list[tuple[str, float]] = []
    if _FORM_NAME.search(Path(name).stem):
        signals.append(("name", 0.35))
    if not slides:
        signals.append(("no_slides", 0.5))
    elif not filled:
        signals.append(("prompt_slides", 0.6))
    elif len(filled) <= 2 and len(slides) <= 3:
        signals.append(("few_slides", 0.25))
    if len(layouts) >= 6 and len(classes) >= 4:
        signals.append(("many_layouts", 0.15))
    if theme.get("fonts") and any(theme["fonts"].get(k) for k in ("major", "minor")):
        signals.append(("own_fonts", 0.05))
    text = sum(s["chars"] for s in slides)
    if text > 400 * max(1, len(slides)) // 2 and len(filled) >= 5:
        signals.append(("much_text", -0.4))
    score, names = _scored(signals)
    return {
        "size": ratio, "slide_size": list(size), "theme": theme, "layouts": layouts,
        "slides": len(slides), "prompt_slides": sum(1 for s in slides if s["prompt"]),
        "filled_slides": len(filled), "score": score, "signals": names,
    }


# ── Word ───────────────────────────────────────────────────────────────────


def _w(name: str) -> str:
    return f"{{{_W}}}{name}"


def _paragraph_text(p: etree._Element) -> str:
    return "".join(t.text or "" for t in p.iter(_w("t")))


def _docx(data: bytes, name: str) -> dict[str, Any]:
    try:
        archive = open_capped_zip(data)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise FormError("Word 파일을 열지 못했습니다.") from exc
    with archive:
        document = _xml(archive, "word/document.xml")
        if document is None:
            raise FormError("Word 파일의 구조를 읽지 못했습니다.")
        styles_root = _xml(archive, "word/styles.xml")
        headings: dict[int, dict[str, Any]] = {}
        numbered_styles: set[str] = set()
        table_styles: list[str] = []
        fonts: dict[str, str] = {}
        if styles_root is not None:
            for style in styles_root.iter(_w("style")):
                style_id = style.get(_w("styleId")) or ""
                name_node = style.find(_w("name"))
                style_name = name_node.get(_w("val")) if name_node is not None else ""
                kind = style.get(_w("type"))
                if kind == "table" and style_id not in ("TableNormal", "a1"):
                    table_styles.append(style_name or style_id)
                if kind != "paragraph":
                    continue
                level = None
                outline = style.find(f"{_w('pPr')}/{_w('outlineLvl')}")
                if outline is not None and (outline.get(_w("val")) or "").isdigit():
                    level = int(outline.get(_w("val"))) + 1
                for label in (style_name, style_id):
                    if level is None and (m := _HEADING.match(label or "")):
                        level = int(m.group(1))
                if level and 1 <= level <= 6 and level not in headings:
                    headings[level] = {"style": style_id, "name": style_name}
                    if style.find(f"{_w('pPr')}/{_w('numPr')}") is not None:
                        numbered_styles.add(style_id)
            defaults = styles_root.find(f"{_w('docDefaults')}//{_w('rFonts')}")
            if defaults is not None:
                fonts["body"] = defaults.get(_w("eastAsia")) or defaults.get(_w("ascii")) or ""
        body = document.find(_w("body"))
        paragraphs = list(body.iter(_w("p"))) if body is not None else []
        heading_ids = {h["style"] for h in headings.values()}
        skeleton: list[dict[str, Any]] = []
        guidance = blanks = fills = 0
        first_heading: int | None = None
        for index, p in enumerate(paragraphs):
            text = _paragraph_text(p).strip()
            style = p.find(f"{_w('pPr')}/{_w('pStyle')}")
            style_id = style.get(_w("val")) if style is not None else ""
            if style_id in heading_ids and text:
                level = next(lv for lv, h in headings.items() if h["style"] == style_id)
                if len(skeleton) < 60:
                    skeleton.append({"level": level, "text": text[:80]})
                if first_heading is None:
                    first_heading = index
            if _GUIDANCE.search(text):
                guidance += 1
            blanks += len(BLANK.findall(text))
            fills += len(_FILL.findall(text))
        controls = sum(1 for _ in document.iter(_w("sdt")))
        merge_fields = sum(1 for node in document.iter(_w("instrText"))
                           if "MERGEFIELD" in (node.text or ""))
        merge_fields += sum(1 for node in document.iter(_w("fldSimple"))
                            if "MERGEFIELD" in (node.get(_w("instr")) or ""))
        label_cells = empty_values = 0
        tables = list(document.iter(_w("tbl")))
        for table in tables:
            for row in table.iter(_w("tr")):
                cells = [_paragraph_text(c).strip() for c in row.iter(_w("tc"))]
                for left, right in zip(cells, cells[1:], strict=False):
                    if left and len(left) <= 16 and not right:
                        label_cells += 1
                        empty_values += 1
        sections = list(document.iter(_w("sectPr")))
        page: dict[str, Any] = {}
        header = footer = first_page = False
        if sections:
            last = sections[-1]
            size = last.find(_w("pgSz"))
            margin = last.find(_w("pgMar"))
            if size is not None:
                try:
                    page["size_mm"] = [round(int(size.get(_w("w"))) / 56.7),
                                       round(int(size.get(_w("h"))) / 56.7)]
                except (TypeError, ValueError):
                    pass
            if margin is not None:
                try:
                    page["margins_mm"] = {side: round(int(margin.get(_w(side))) / 56.7)
                                          for side in ("top", "bottom", "left", "right")}
                except (TypeError, ValueError):
                    pass
            for section in sections:
                header = header or section.find(_w("headerReference")) is not None
                footer = footer or section.find(_w("footerReference")) is not None
                first_page = first_page or section.find(_w("titlePg")) is not None
        cover = first_heading is not None and first_heading >= 3 and any(
            p.find(f".//{_w('br')}[@{_w('type')}='page']") is not None
            or p.find(f".//{_w('sectPr')}") is not None
            for p in paragraphs[:first_heading])
        chars = sum(len(_paragraph_text(p)) for p in paragraphs)
    signals: list[tuple[str, float]] = []
    if _FORM_NAME.search(Path(name).stem):
        signals.append(("name", 0.35))
    if empty_values >= 3:
        signals.append(("label_value_cells", 0.35))
    if controls + merge_fields >= 2:
        signals.append(("fields", 0.35))
    if blanks + fills >= 3:
        signals.append(("blanks", 0.2))
    if guidance >= 1:
        signals.append(("guidance", 0.25))
    if skeleton and chars < 300 * max(1, len(skeleton)) // 3:
        signals.append(("bare_headings", 0.25))
    if chars > 6000 and not guidance and empty_values < 3:
        signals.append(("much_text", -0.4))
    score, names = _scored(signals)
    return {
        "headings": {str(level): h for level, h in sorted(headings.items())},
        "numbered_headings": bool(numbered_styles), "table_styles": table_styles[:12],
        "fonts": fonts, "page": page, "header": header, "footer": footer,
        "first_page_header": first_page, "cover": cover, "skeleton": skeleton,
        "fields": {"label_cells": label_cells, "controls": controls,
                   "merge_fields": merge_fields, "blanks": blanks + fills},
        "guidance": guidance, "tables": len(tables), "chars": chars,
        "score": score, "signals": names,
    }


# ── 한글 (HWPX) ─────────────────────────────────────────────────────────────


def _hwpx(data: bytes, name: str) -> dict[str, Any]:
    from app.services import hwpx_edit

    try:
        _, doc = hwpx_edit._load(data)
    except hwpx_edit.HwpxError as exc:
        raise FormError(f"한글 파일을 읽지 못했습니다: {exc}") from exc
    fields: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for table in doc.tables:
        cells = {(r, c): hwpx_edit._cell_text(cell).strip()
                 for r, c, cell in hwpx_edit._cells(table.element)}
        rows, cols = hwpx_edit._shape(table.element)
        empty_rows = 0
        for r in range(rows):
            row = [cells.get((r, c)) for c in range(cols) if (r, c) in cells]
            if r and row and all(not v or _FILL.fullmatch(v or "") for v in row):
                empty_rows += 1
        found = 0
        for (r, c), text in cells.items():
            if text and not _FILL.fullmatch(text):
                continue
            label = cells.get((r, c - 1)) or cells.get((r - 1, c)) or ""
            label = re.sub(r"\s+", " ", label).strip()
            if label and len(label) <= 40 and not _FILL.fullmatch(label):
                found += 1
                if len(fields) < 120:
                    fields.append({"table": table.id, "row": r, "col": c,
                                   "label": re.sub(r"\s+", " ", label)[:40]})
        tables.append({"id": table.id, "rows": rows, "cols": cols, "fields": found,
                       "empty_rows": empty_rows})
    texts = [hwpx_edit._own_text(p).strip() for p in doc.paragraphs]
    guidance = sum(1 for t in texts if _GUIDANCE.search(t))
    blanks = sum(len(BLANK.findall(t)) + len(_FILL.findall(t)) for t in texts)
    checkboxes = sum(t.count("□") for t in texts) + sum(
        hwpx_edit._cell_text(cell).count("□")
        for table in doc.tables for _, _, cell in hwpx_edit._cells(table.element))
    chars = sum(len(t) for t in texts)
    signals: list[tuple[str, float]] = []
    if _FORM_NAME.search(Path(name).stem):
        signals.append(("name", 0.35))
    if len(fields) >= 3:
        signals.append(("label_value_cells", 0.45))
    if any(t["empty_rows"] >= 2 for t in tables):
        signals.append(("empty_rows", 0.2))
    if guidance:
        signals.append(("guidance", 0.25))
    if blanks + checkboxes >= 3:
        signals.append(("blanks", 0.15))
    if chars > 6000 and not guidance and len(fields) < 3:
        signals.append(("much_text", -0.4))
    score, names = _scored(signals)
    return {
        "fields": fields, "tables": tables, "guidance": guidance, "blanks": blanks,
        "checkboxes": checkboxes, "chars": chars, "score": score, "signals": names,
    }


def _hwp(name: str, text: str) -> dict[str, Any]:
    """Binary HWP: never a form; how much it looks like one decides whether to say so."""
    lines = [line.strip() for line in (text or "").splitlines()]
    signals: list[tuple[str, float]] = []
    if _FORM_NAME.search(Path(name).stem):
        signals.append(("name", 0.35))
    if sum(1 for line in lines if _GUIDANCE.search(line)):
        signals.append(("guidance", 0.25))
    if sum(len(_FILL.findall(line)) + len(BLANK.findall(line)) for line in lines) >= 3:
        signals.append(("blanks", 0.2))
    score, names = _scored(signals)
    return {"score": score, "signals": names, "form_like": score >= MATERIAL_AT}


def profile(name: str, data: bytes, text: str = "") -> dict[str, Any] | None:
    """The form profile of an Office file, or None for other files.

    Always has `kind`, `version`, `score`, `verdict` and `signals`; a file that cannot be read
    as a form has `error` and verdict `material`."""
    kind = kind_of(name)
    if not kind:
        return None
    # the bytes decide between the two 한글 formats: 법령정보센터 serves HWP 5 files as 「….hwpx」
    if kind == "hwpx" and data[:8] == _OLE:
        kind = "hwp"
    elif kind == "hwp" and data[:4] == b"PK\x03\x04":
        kind = "hwpx"
    suffix = Path(name).suffix.lower()
    try:
        if kind == "pptx":
            out = _pptx(data, name)
        elif kind == "docx":
            out = _docx(data, name)
        elif kind == "hwpx":
            out = _hwpx(data, name)
        else:
            out = _hwp(name, text)
    except FormError as exc:
        return {"kind": kind, "version": VERSION, "score": 0.0, "verdict": "material",
                "signals": [], "error": str(exc)}
    if suffix in (".potx", ".dotx"):
        out["score"] = 1.0
        out["signals"] = ["template_file", *out["signals"]]
    out["kind"] = kind
    out["version"] = VERSION
    out["verdict"] = "material" if kind == "hwp" else _verdict(out["score"])
    return out


def summary(found: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the screen shows of a profile: kind, verdict and a few counts."""
    if not found:
        return None
    out: dict[str, Any] = {key: found[key] for key in ("kind", "verdict", "score")
                           if key in found}
    if found.get("error"):
        out["error"] = found["error"]
    kind = found.get("kind")
    if kind == "pptx":
        theme = found.get("theme") or {}
        out.update(layouts=len(found.get("layouts") or []), size=found.get("size"),
                   font=(theme.get("fonts") or {}).get("minor") or "",
                   accent=(theme.get("colours") or {}).get("accent1") or "")
    elif kind == "docx":
        out.update(headings=len(found.get("headings") or {}),
                   sections=len(found.get("skeleton") or []),
                   header=bool(found.get("header")), cover=bool(found.get("cover")),
                   fields=sum((found.get("fields") or {}).values()))
    elif kind == "hwpx":
        out.update(fields=len(found.get("fields") or []), tables=len(found.get("tables") or []))
    elif kind == "hwp" and found.get("form_like"):
        out["advice"] = HWP_FORM_ADVICE
    return out
