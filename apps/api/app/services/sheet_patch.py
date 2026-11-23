"""change cells of an `.xlsx` by editing the sheet XML in place.

Every other part of the package (charts, pictures, shapes, pivots, sparklines, conditional
formats, comments, tables, theme) is copied byte for byte, so a hand edit in the browser grid
cannot lose what the grid does not draw. A formula cell keeps the value the grid computed as its
cached value, and the workbook is marked to recalculate when Excel opens it.
"""

from __future__ import annotations

import copy
import io
import posixpath
import re
import zipfile
from typing import Any

from lxml import etree
from openpyxl.utils import get_column_letter

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_q = lambda tag: f"{{{NS}}}{tag}"  # noqa: E731


class PatchError(ValueError):
    pass


def _sheet_parts(files: dict[str, bytes]) -> dict[str, str]:
    book = etree.fromstring(files["xl/workbook.xml"])
    rels = etree.fromstring(files["xl/_rels/workbook.xml.rels"])
    target = {r.get("Id"): r.get("Target") for r in rels}
    out = {}
    for sheet in book.find(_q("sheets")):
        path = target[sheet.get(f"{{{RNS}}}id")]
        out[sheet.get("name")] = path.lstrip("/") if path.startswith("/") else posixpath.normpath(
            posixpath.join("xl", path))
    return out


class _Styles:
    """`styles.xml`: an xf is cloned from the cell's own and changed, never edited in place."""

    def __init__(self, raw: bytes) -> None:
        self.root = etree.fromstring(raw)
        self.fonts = self.root.find(_q("fonts"))
        self.fills = self.root.find(_q("fills"))
        self.borders = self.root.find(_q("borders"))
        self.xfs = self.root.find(_q("cellXfs"))
        self.numfmts = self.root.find(_q("numFmts"))
        self.dirty = False
        self._cache: dict[tuple, int] = {}

    def _append(self, parent: etree._Element, element: etree._Element) -> int:
        parent.append(element)
        parent.set("count", str(len(parent)))
        self.dirty = True
        return len(parent) - 1

    def _numfmt(self, code: str) -> int:
        builtin = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10}
        if code in builtin:
            return builtin[code]
        if self.numfmts is None:
            self.numfmts = etree.Element(_q("numFmts"))
            self.root.insert(0, self.numfmts)
        for fmt in self.numfmts:
            if fmt.get("formatCode") == code:
                return int(fmt.get("numFmtId"))
        new = max([int(f.get("numFmtId")) for f in self.numfmts] + [163]) + 1
        etree.SubElement(self.numfmts, _q("numFmt"), numFmtId=str(new), formatCode=code)
        self.numfmts.set("count", str(len(self.numfmts)))
        self.dirty = True
        return new

    def _border(self, base: int, sides: dict[str, Any]) -> int:
        """A border that is `base` with the given sides (`t` `b` `l` `r`: `{style, color}`, or
        None to clear one) changed."""
        border = copy.deepcopy(self.borders[base] if base < len(self.borders) else self.borders[0])
        for key, tag in (("l", "left"), ("r", "right"), ("t", "top"), ("b", "bottom")):
            if key not in sides:
                continue
            side = border.find(_q(tag))
            if side is None:
                side = etree.Element(_q(tag))
                # the schema order: left, right, top, bottom, diagonal
                after = {"left": 0, "right": 1, "top": 2, "bottom": 3}[tag]
                spot = [c for c in border if etree.QName(c).localname in
                        ("left", "right", "top", "bottom")[:after]]
                (spot[-1].addnext(side) if spot else border.insert(0, side))
            for child in list(side):
                side.remove(child)
            side.attrib.pop("style", None)
            want = sides[key]
            if want and want.get("style"):
                side.set("style", want["style"])
                etree.SubElement(side, _q("color"),
                                 rgb="FF" + (want.get("color") or "#000000").lstrip("#").upper())
        return self._append(self.borders, border)

    def derive(self, base: int, style: dict[str, Any]) -> int:
        """Index of an xf that is `base` with `style`'s fill, numFmt, font and alignment."""
        key = (base, repr(sorted(style.items(), key=lambda kv: kv[0])))
        if key in self._cache:
            return self._cache[key]
        xf = copy.deepcopy(self.xfs[base] if base < len(self.xfs) else self.xfs[0])
        if "numFmt" in style:
            xf.set("numFmtId", str(self._numfmt(style["numFmt"])))
            xf.set("applyNumberFormat", "1")
        if "fill" in style and not style["fill"]:
            xf.set("fillId", "0")
        elif "fill" in style:
            fill = etree.Element(_q("fill"))
            pattern = etree.SubElement(fill, _q("patternFill"), patternType="solid")
            rgb = "FF" + style["fill"].lstrip("#").upper()
            etree.SubElement(pattern, _q("fgColor"), rgb=rgb)
            etree.SubElement(pattern, _q("bgColor"), indexed="64")
            xf.set("fillId", str(self._append(self.fills, fill)))
            xf.set("applyFill", "1")
        if "font" in style:
            want = style["font"]
            font = copy.deepcopy(self.fonts[int(xf.get("fontId", "0"))])
            for tag, flag in (("b", "b"), ("i", "i"), ("u", "u"), ("strike", "s")):
                if flag in want:
                    for old in font.findall(_q(tag)):
                        font.remove(old)
                    if want[flag]:
                        font.insert(0, etree.Element(_q(tag)))
            if "sz" in want:
                for node in font.findall(_q("sz")):
                    node.set("val", str(want["sz"]))
            if "name" in want:
                nodes = font.findall(_q("name"))
                if not nodes:
                    nodes = [etree.SubElement(font, _q("name"))]
                for node in nodes:
                    node.set("val", str(want["name"]))
            if "color" in want:
                for node in font.findall(_q("color")):
                    font.remove(node)
                etree.SubElement(font, _q("color"), rgb="FF" + want["color"].lstrip("#").upper())
            xf.set("fontId", str(self._append(self.fonts, font)))
            xf.set("applyFont", "1")
        if "border" in style and self.borders is not None:
            xf.set("borderId", str(self._border(int(xf.get("borderId", "0")), style["border"])))
            xf.set("applyBorder", "1")
        if "align" in style:
            align = xf.find(_q("alignment"))
            if align is None:
                align = etree.SubElement(xf, _q("alignment"))
            for src, attr in (("h", "horizontal"), ("v", "vertical")):
                if src in style["align"]:
                    align.set(attr, style["align"][src])
            if "wrap" in style["align"]:
                align.set("wrapText", "1" if style["align"]["wrap"] else "0")
            xf.set("applyAlignment", "1")
        index = self._append(self.xfs, xf)
        self._cache[key] = index
        return index

    def tobytes(self) -> bytes:
        return etree.tostring(self.root, xml_declaration=True, encoding="UTF-8", standalone=True)


#: what the schema puts after <mergeCells>; a new one goes before the first of these present
AFTER_MERGES = (
    "phoneticPr", "conditionalFormatting", "dataValidations", "hyperlinks", "printOptions",
    "pageMargins", "pageSetup", "headerFooter", "rowBreaks", "colBreaks", "customProperties",
    "cellWatches", "ignoredErrors", "smartTags", "drawing", "legacyDrawing", "legacyDrawingHF",
    "drawingHF", "picture", "oleObjects", "controls", "webPublishItems", "tableParts", "extLst",
)


def _place(root: etree._Element, element: etree._Element, after: tuple[str, ...]) -> None:
    for child in root:
        if etree.QName(child).localname in after:
            child.addprevious(element)
            return
    root.append(element)


def _ref(r: int, c: int) -> str:
    return f"{get_column_letter(c + 1)}{r + 1}"


def _row(data: etree._Element, number: int) -> etree._Element:
    after = None
    for row in data:
        n = int(row.get("r"))
        if n == number:
            return row
        if n > number:
            after = row
            break
    new = etree.Element(_q("row"), r=str(number))
    if after is None:
        data.append(new)
    else:
        after.addprevious(new)
    return new


def _cell(row: etree._Element, ref: str, column: int) -> etree._Element:
    for cell in row:
        existing = column_of(cell.get("r"))
        if existing == column:
            return cell
        if existing > column:
            new = etree.Element(_q("c"), r=ref)
            cell.addprevious(new)
            return new
    new = etree.SubElement(row, _q("c"), r=ref)
    return new


def column_of(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _set_value(cell: etree._Element, op: dict[str, Any]) -> None:
    style = cell.get("s")
    for child in list(cell):
        cell.remove(child)
    for attr in [a for a in cell.attrib if a not in ("r", "s")]:
        del cell.attrib[attr]
    if style is not None:
        cell.set("s", style)
    value, formula = op.get("v"), op.get("f")
    if formula:
        etree.SubElement(cell, _q("f")).text = formula.lstrip("=")
    if value is None:
        return
    if isinstance(value, bool):
        cell.set("t", "b")
        etree.SubElement(cell, _q("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        etree.SubElement(cell, _q("v")).text = repr(value)
    elif formula:
        cell.set("t", "str")
        etree.SubElement(cell, _q("v")).text = str(value)
    else:
        cell.set("t", "inlineStr")
        inline = etree.SubElement(cell, _q("is"))
        text = etree.SubElement(inline, _q("t"))
        text.text = str(value)
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


REL_SHEET = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
CT_SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
_BAD_NAME = re.compile(r"[\[\]:*?/\\]")
#: a sheet name needs quotes in a formula unless it is a plain word that is not a cell address
_PLAIN_NAME = re.compile(r"[^\W\d][\w.]*")
_ADDRESS = re.compile(r"(?:[A-Za-z]{1,3}\d+|[Rr]\d*[Cc]\d*)")


def _check_name(book: etree._Element, name: str, own: str | None = None) -> None:
    if not name or len(name) > 31 or _BAD_NAME.search(name) or name[0] == "'" or name[-1] == "'":
        raise PatchError(f"bad_sheet_name:{name}")
    for sheet in book.find(_q("sheets")):
        if sheet.get("name").casefold() == name.casefold() and sheet.get("name") != own:
            raise PatchError(f"sheet_exists:{name}")


def _quoted(name: str) -> str:
    if _PLAIN_NAME.fullmatch(name) and not _ADDRESS.fullmatch(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def rename_in_formula(text: str, old: str, new: str) -> str:
    """`text` with references to sheet `old` pointing at `new`; string literals are left alone."""
    pattern = re.compile(r"(?<![\w.!'])(?:'" + re.escape(old.replace("'", "''")) + "'|"
                         + re.escape(old) + r")!")
    parts = re.split(r'("(?:[^"]|"")*")', text)
    return "".join(
        part if i % 2 else pattern.sub(lambda _m: _quoted(new) + "!", part)
        for i, part in enumerate(parts)
    )


def _rels_of(path: str) -> str:
    folder, name = posixpath.split(path)
    return posixpath.join(folder, "_rels", name + ".rels")


def _reachable(files: dict[str, bytes]) -> set[str]:
    """Parts the package relationships lead to, from the root."""
    seen: set[str] = set()
    todo = ["_rels/.rels"]
    while todo:
        rels = todo.pop()
        if rels not in files:
            continue
        base = posixpath.dirname(posixpath.dirname(rels))
        for rel in etree.fromstring(files[rels]):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                posixpath.join(base, target))
            if path not in seen:
                seen.add(path)
                todo.append(_rels_of(path))
    return seen


class _Package:
    """The workbook as parts: the ones this edit reads are parsed once, and written back at the end."""

    def __init__(self, files: dict[str, bytes], order: list[zipfile.ZipInfo]) -> None:
        self.files = files
        self.order = order
        self.parts = _sheet_parts(files)  # sheet name -> part path, in the file's order
        self.trees: dict[str, etree._Element] = {}
        self.book = etree.fromstring(files["xl/workbook.xml"])
        self.rels = etree.fromstring(files["xl/_rels/workbook.xml.rels"])
        self.types = etree.fromstring(files["[Content_Types].xml"])
        self.styles = _Styles(files["xl/styles.xml"])
        self.package_dirty = False

    def root(self, sheet: Any) -> etree._Element:
        path = self.parts.get(sheet)
        if path is None:
            raise PatchError(f"no_sheet:{sheet}")
        if path not in self.trees:
            self.trees[path] = etree.fromstring(self.files[path])
        return self.trees[path]

    def all_roots(self) -> list[etree._Element]:
        return [self.root(name) for name in self.parts]

    # -- sheets ------------------------------------------------------------------------------

    def add_sheet(self, name: str, index: int | None) -> None:
        _check_name(self.book, name)
        sheets = self.book.find(_q("sheets"))
        sheet_id = max([int(s.get("sheetId")) for s in sheets] + [0]) + 1
        ids = {r.get("Id") for r in self.rels}
        n = 1
        while f"rId{n}" in ids:
            n += 1
        k = 1
        while f"xl/worksheets/sheet{k}.xml" in self.files:
            k += 1
        path = f"xl/worksheets/sheet{k}.xml"
        self.files[path] = _dump(etree.fromstring(
            f'<worksheet xmlns="{NS}"><sheetData/></worksheet>'.encode()))
        self.order.append(zipfile.ZipInfo(path))
        etree.SubElement(self.rels, f"{{{PKG}}}Relationship", Id=f"rId{n}", Type=REL_SHEET,
                         Target=f"worksheets/sheet{k}.xml")
        etree.SubElement(self.types, f"{{{CT}}}Override", PartName="/" + path, ContentType=CT_SHEET)
        element = etree.Element(_q("sheet"), name=name, sheetId=str(sheet_id))
        element.set(f"{{{RNS}}}id", f"rId{n}")
        if index is None or index >= len(sheets):
            sheets.append(element)
        else:
            sheets[max(index, 0)].addprevious(element)
            self._shift_local_names(max(index, 0), +1)
        self.parts[name] = path
        self.package_dirty = True

    def rename_sheet(self, old: str, new: str) -> None:
        if old not in self.parts:
            raise PatchError(f"no_sheet:{old}")
        if old == new:
            return
        _check_name(self.book, new, own=old)
        for sheet in self.book.find(_q("sheets")):
            if sheet.get("name") == old:
                sheet.set("name", new)
        self.parts = {(new if k == old else k): v for k, v in self.parts.items()}
        rewrite = lambda text: rename_in_formula(text, old, new)  # noqa: E731
        for root in self.all_roots():
            _each_formula(root, rewrite)
        _each_formula(self.book, rewrite)
        for path in [p for p in self.files if p.startswith("xl/charts/chart") and p.endswith(".xml")]:
            tree = etree.fromstring(self.files[path])
            if _each_formula(tree, rewrite):
                self.files[path] = _dump(tree)
        self.package_dirty = True

    def delete_sheet(self, name: str) -> None:
        if name not in self.parts:
            raise PatchError(f"no_sheet:{name}")
        if len(self.parts) == 1:
            raise PatchError("last_sheet")
        before = _reachable({**self.files, **self._flushed_rels()})
        sheets = self.book.find(_q("sheets"))
        index = next(i for i, s in enumerate(sheets) if s.get("name") == name)
        rid = sheets[index].get(f"{{{RNS}}}id")
        sheets.remove(sheets[index])
        for rel in list(self.rels):
            if rel.get("Id") == rid:
                self.rels.remove(rel)
        path = self.parts.pop(name)
        self.trees.pop(path, None)
        for gone in (path, _rels_of(path)):
            self.files.pop(gone, None)
        names = self.book.find(_q("definedNames"))
        if names is not None:
            for defined in list(names):
                local = defined.get("localSheetId")
                if local is None:
                    continue
                if int(local) == index:
                    names.remove(defined)
                elif int(local) > index:
                    defined.set("localSheetId", str(int(local) - 1))
            if not len(names):
                self.book.remove(names)
        views = self.book.find(_q("bookViews"))
        if views is not None:
            for view in views:
                for attr in ("activeTab", "firstSheet"):
                    if view.get(attr) is not None and int(view.get(attr)) >= len(sheets):
                        view.set(attr, str(len(sheets) - 1))
        # what only that sheet led to (its drawing, charts, pictures, comments) goes with it
        after = _reachable({**self.files, **self._flushed_rels()})
        for orphan in before - after:
            self.files.pop(orphan, None)
            self.files.pop(_rels_of(orphan), None)
        for node in list(self.types):
            part = node.get("PartName", "").lstrip("/")
            if node.get("PartName") and part not in self.files:
                self.types.remove(node)
        self.package_dirty = True

    def _flushed_rels(self) -> dict[str, bytes]:
        """The workbook's relationships as they stand now, for walking the package."""
        return {"xl/_rels/workbook.xml.rels": etree.tostring(self.rels)}

    def _shift_local_names(self, at: int, by: int) -> None:
        names = self.book.find(_q("definedNames"))
        if names is None:
            return
        for defined in names:
            local = defined.get("localSheetId")
            if local is not None and int(local) >= at:
                defined.set("localSheetId", str(int(local) + by))

    def flush(self) -> None:
        for part, root in self.trees.items():
            self.files[part] = _dump(root)
        if self.styles.dirty:
            self.files["xl/styles.xml"] = self.styles.tobytes()
        if self.package_dirty:
            self.files["xl/workbook.xml"] = _dump(self.book)
            self.files["xl/_rels/workbook.xml.rels"] = _dump(self.rels)
            self.files["[Content_Types].xml"] = _dump(self.types)


def _dump(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _each_formula(root: etree._Element, fn) -> bool:
    """Runs `fn` over the text of every formula (`f`, `definedName`) under `root`; whether any changed."""
    changed = False
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if etree.QName(node).localname in ("f", "definedName") and node.text:
            text = fn(node.text)
            if text != node.text:
                node.text = text
                changed = True
    return changed


# -- rows and columns --------------------------------------------------------------------------

def _map_span(lo: int, hi: int, at: int, count: int, delete: bool) -> tuple[int, int] | None:
    """The span `lo..hi` (0-based, inclusive) after `count` lines are inserted at `at`, or
    deleted from it; None when a deletion takes all of it."""
    if not delete:
        return (lo + count if lo >= at else lo), (hi + count if hi >= at else hi)
    end = at + count
    new_lo = lo - count if lo >= end else (at if lo >= at else lo)
    new_hi = hi - count if hi >= end else (at - 1 if hi >= at else hi)
    return None if new_lo > new_hi else (new_lo, new_hi)


def _map_ref(ref: str, axis: str, at: int, count: int, delete: bool) -> str | None:
    """A range such as `B2:D5` after the change; None when nothing of it is left."""
    cells = ref.split(":")
    box = []
    for cell in cells:
        m = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)", cell)
        if not m:
            return ref
        box.append((column_of(m.group(1)), int(m.group(2)) - 1))
    c1, r1 = box[0]
    c2, r2 = box[-1]
    if axis == "row":
        span = _map_span(r1, r2, at, count, delete)
        if span is None:
            return None
        r1, r2 = span
    else:
        span = _map_span(c1, c2, at, count, delete)
        if span is None:
            return None
        c1, c2 = span
    first = _ref(r1, c1)
    return first if (r1, c1) == (r2, c2) else f"{first}:{_ref(r2, c2)}"


def _map_sqref(value: str, axis: str, at: int, count: int, delete: bool) -> str:
    kept = [m for part in value.split() if (m := _map_ref(part, axis, at, count, delete))]
    return " ".join(kept)


def _unshare(root: etree._Element) -> None:
    """Each cell of a shared formula gets its own text, so moving cells cannot detach it."""
    from openpyxl.formula.translate import Translator

    masters: dict[str, tuple[str, str]] = {}
    for cell in root.iter(_q("c")):
        f = cell.find(_q("f"))
        if f is not None and f.get("t") == "shared" and f.text:
            masters[f.get("si")] = (cell.get("r"), f.text)
    for cell in root.iter(_q("c")):
        f = cell.find(_q("f"))
        if f is None or f.get("t") != "shared":
            continue
        origin, text = masters.get(f.get("si"), (None, None))
        if origin is None:
            continue
        if not f.text:
            f.text = Translator("=" + text, origin=origin).translate_formula(cell.get("r"))[1:]
        for attr in ("t", "si", "ref"):
            f.attrib.pop(attr, None)


def _lines(root: etree._Element, axis: str, at: int, count: int, delete: bool) -> None:
    """Rows or columns inserted at `at` or deleted from it (0-based). Cells move with their
    lines, merges, column widths and the lists of ranges that name cells follow; formulas that
    name moved cells are the caller's to resend."""
    _unshare(root)
    data = root.find(_q("sheetData"))
    for row in list(data):
        number = int(row.get("r")) - 1
        if axis == "row":
            span = _map_span(number, number, at, count, delete)
            if span is None:
                data.remove(row)
                continue
            row.set("r", str(span[0] + 1))
            for cell in row:
                cell.set("r", _ref(span[0], column_of(cell.get("r"))))
        else:
            for cell in list(row):
                column = column_of(cell.get("r"))
                span = _map_span(column, column, at, count, delete)
                if span is None:
                    row.remove(cell)
                else:
                    cell.set("r", _ref(number, span[0]))
        row.attrib.pop("spans", None)
    dimension = root.find(_q("dimension"))
    if dimension is not None:
        root.remove(dimension)
    merges = root.find(_q("mergeCells"))
    if merges is not None:
        for merge in list(merges):
            mapped = _map_ref(merge.get("ref"), axis, at, count, delete)
            if mapped is None or ":" not in mapped:
                merges.remove(merge)
            else:
                merge.set("ref", mapped)
        if len(merges):
            merges.set("count", str(len(merges)))
        else:
            root.remove(merges)
    cols = root.find(_q("cols"))
    if axis == "col" and cols is not None:
        for col in list(cols):
            span = _map_span(int(col.get("min")) - 1, int(col.get("max")) - 1, at, count, delete)
            if span is None:
                cols.remove(col)
            else:
                col.set("min", str(span[0] + 1))
                col.set("max", str(span[1] + 1))
        if not len(cols):
            root.remove(cols)
    for tag, attr in (("conditionalFormatting", "sqref"), ("dataValidation", "sqref"),
                      ("hyperlink", "ref")):
        for node in list(root.iter(_q(tag))):
            mapped = _map_sqref(node.get(attr, ""), axis, at, count, delete)
            if mapped:
                node.set(attr, mapped)
            else:
                node.getparent().remove(node)
    for tag in ("dataValidations", "hyperlinks"):
        node = root.find(_q(tag))
        if node is not None:
            if len(node):
                node.set("count", str(len(node)))
            else:
                root.remove(node)


STRUCTURE = {"insertRows": ("row", False), "deleteRows": ("row", True),
             "insertCols": ("col", False), "deleteCols": ("col", True)}


def apply(data: bytes, ops: list[dict[str, Any]]) -> bytes:
    """`data` with the ops applied, in order. A cell op is `{sheet, r, c, v?, f?, s?}` (0-based
    `r`, `c`; `v` None clears; `s` is a style patch); the others are `{sheet, op: "width", c, px}`,
    `{sheet, op: "merge"|"unmerge", ref}`, `{sheet, op: "insertRows"|"deleteRows"|"insertCols"|
    "deleteCols", at, count}`, `{sheet, op: "renameSheet", name}`, `{sheet, op: "deleteSheet"}`
    and `{op: "addSheet", name, index?}`."""
    src = zipfile.ZipFile(io.BytesIO(data))
    order = src.infolist()
    files = {i.filename: src.read(i.filename) for i in order}
    pkg = _Package(files, order)
    for op in ops:
        kind = op.get("op", "cell")
        if kind == "addSheet":
            pkg.add_sheet(str(op.get("name", "")), op.get("index"))
            continue
        if kind == "renameSheet":
            pkg.rename_sheet(op.get("sheet"), str(op.get("name", "")))
            continue
        if kind == "deleteSheet":
            pkg.delete_sheet(op.get("sheet"))
            continue
        root = pkg.root(op.get("sheet"))
        if kind in STRUCTURE:
            axis, delete = STRUCTURE[kind]
            at, count = int(op["at"]), int(op.get("count", 1))
            if at < 0 or count < 1:
                raise PatchError("bad_range")
            _lines(root, axis, at, count, delete)
            continue
        if kind == "width":
            cols = root.find(_q("cols"))
            if cols is None:
                cols = etree.Element(_q("cols"))
                root.find(_q("sheetData")).addprevious(cols)
            index = int(op["c"]) + 1
            for col in list(cols):  # split a ranged <col> that covers this column
                lo, hi = int(col.get("min")), int(col.get("max"))
                if lo <= index <= hi and lo != hi:
                    cols.remove(col)
                    for k in range(lo, hi + 1):
                        clone = copy.deepcopy(col)
                        clone.set("min", str(k))
                        clone.set("max", str(k))
                        cols.append(clone)
            found = next((c for c in cols if int(c.get("min")) == index), None)
            if found is None:
                found = etree.SubElement(cols, _q("col"), min=str(index), max=str(index))
            found.set("width", str(round((op["px"] - 5) / 7, 2)))
            found.set("customWidth", "1")
            cols[:] = sorted(cols, key=lambda c: int(c.get("min")))
            continue
        if kind in ("merge", "unmerge"):
            merges = root.find(_q("mergeCells"))
            if kind == "merge":
                if merges is None:
                    merges = etree.Element(_q("mergeCells"))
                    _place(root, merges, AFTER_MERGES)
                etree.SubElement(merges, _q("mergeCell"), ref=op["ref"])
            elif merges is not None:
                for node in merges.findall(_q("mergeCell")):
                    if node.get("ref") == op["ref"]:
                        merges.remove(node)
            if merges is not None:
                merges.set("count", str(len(merges)))
            continue
        row = _row(root.find(_q("sheetData")), int(op["r"]) + 1)
        cell = _cell(row, _ref(op["r"], op["c"]), int(op["c"]))
        if "v" in op or "f" in op:
            _set_value(cell, op)
        if op.get("s"):
            cell.set("s", str(pkg.styles.derive(int(cell.get("s", "0")), op["s"])))
    pkg.flush()
    files, order = pkg.files, pkg.order
    if ops:
        files, order = _recalc_on_open(files, order)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in order:
            if info.filename in files:
                zout.writestr(info.filename, files[info.filename])
    return out.getvalue()


def _recalc_on_open(files: dict[str, bytes], order: list[zipfile.ZipInfo]):
    """Drops the calculation chain (it names cells that moved) and asks Excel to recalculate."""
    book = etree.fromstring(files["xl/workbook.xml"])
    calc = book.find(_q("calcPr"))
    if calc is None:
        calc = etree.SubElement(book, _q("calcPr"))
    calc.set("fullCalcOnLoad", "1")
    files["xl/workbook.xml"] = etree.tostring(book, xml_declaration=True, encoding="UTF-8",
                                              standalone=True)
    if "xl/calcChain.xml" in files:
        del files["xl/calcChain.xml"]
        rels = etree.fromstring(files["xl/_rels/workbook.xml.rels"])
        for rel in list(rels):
            if rel.get("Target", "").endswith("calcChain.xml"):
                rels.remove(rel)
        files["xl/_rels/workbook.xml.rels"] = etree.tostring(rels, xml_declaration=True,
                                                             encoding="UTF-8", standalone=True)
        types = etree.fromstring(files["[Content_Types].xml"])
        for node in list(types):
            if node.get("PartName") == "/xl/calcChain.xml":
                types.remove(node)
        files["[Content_Types].xml"] = etree.tostring(types, xml_declaration=True,
                                                      encoding="UTF-8", standalone=True)
    return files, [i for i in order if i.filename != "xl/calcChain.xml"]
