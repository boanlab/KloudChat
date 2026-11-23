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

    def derive(self, base: int, style: dict[str, Any]) -> int:
        """Index of an xf that is `base` with `style`'s fill, numFmt, font and alignment."""
        key = (base, repr(sorted(style.items(), key=lambda kv: kv[0])))
        if key in self._cache:
            return self._cache[key]
        xf = copy.deepcopy(self.xfs[base] if base < len(self.xfs) else self.xfs[0])
        if "numFmt" in style:
            xf.set("numFmtId", str(self._numfmt(style["numFmt"])))
            xf.set("applyNumberFormat", "1")
        if "fill" in style:
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
            if "color" in want:
                for node in font.findall(_q("color")):
                    font.remove(node)
                etree.SubElement(font, _q("color"), rgb="FF" + want["color"].lstrip("#").upper())
            xf.set("fontId", str(self._append(self.fonts, font)))
            xf.set("applyFont", "1")
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


def apply(data: bytes, ops: list[dict[str, Any]]) -> bytes:
    """`data` with the ops applied. An op is `{sheet, r, c, v?, f?, s?}` (0-based `r`, `c`;
    `v` None clears; `s` is a style patch), `{sheet, op: "width", c, px}` or
    `{sheet, op: "merge"|"unmerge", ref}`."""
    src = zipfile.ZipFile(io.BytesIO(data))
    order = src.infolist()
    files = {i.filename: src.read(i.filename) for i in order}
    parts = _sheet_parts(files)
    styles = _Styles(files["xl/styles.xml"])
    trees: dict[str, etree._Element] = {}
    for op in ops:
        part = parts.get(op.get("sheet"))
        if part is None:
            raise PatchError(f"no_sheet:{op.get('sheet')}")
        root = trees.setdefault(part, etree.fromstring(files[part]))
        kind = op.get("op", "cell")
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
            cell.set("s", str(styles.derive(int(cell.get("s", "0")), op["s"])))
    for part, root in trees.items():
        files[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if styles.dirty:
        files["xl/styles.xml"] = styles.tobytes()
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
