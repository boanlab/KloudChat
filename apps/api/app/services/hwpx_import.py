"""`.hwpx` (OWPML) into an editable document with headings and tables."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Any

from app.services import richtext

log = logging.getLogger(__name__)

#: `hh:charPr height` in HWPUNIT (1/100 pt)
_PT = 100.0

_OUTLINE = re.compile(r"^(?:개요|Outline)\s*(\d+)", re.I)
_HEADING_NAME = re.compile(r"제목|개요|heading|title", re.I)

#: official level numbering, outermost first
_NUMBERING = (
    re.compile(r"^\s*(?:제\s*)?\d+\s*장\b"),
    re.compile(r"^\s*\d+(?:[-.]\d+)+\.?\s"),
    re.compile(r"^\s*[가나다라마바사아자차카타파하]\s*\.\s"),
    re.compile(r"^\s*\d+\s*\)\s"),
    re.compile(r"^\s*[가나다라마바사아자차카타파하]\s*\)\s"),
)

#: bullet marks making a list item
_BULLET = re.compile(r"^\s*[□■○●▪▫◦·•\-–]\s*")

#: longer = prose regardless
_HEADING_MAX = 60


@dataclass(slots=True)
class Block:
    """One paragraph or table, in order."""

    kind: str  # "text" | "table" | "image"
    text: str = ""
    size: float = 0.0
    style: str = ""
    #: `hh:heading type`: OUTLINE (heading, beats guesses), BULLET/NUMBER (list; mark not in text)
    mark: str = ""
    mark_level: int = 0
    #: CENTER alignment; see `_title_index`
    centred: bool = False
    #: tables only
    grid: richtext.Grid | None = None


@dataclass(slots=True)
class Section:
    heading: str
    level: int
    html: str


@dataclass(slots=True)
class Document:
    """Read file: title and sections."""

    title: str
    sections: list[Section]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(node: ET.Element) -> str:
    """Every `<hp:t>` run under this node."""
    out: list[str] = []
    for element in node.iter():
        if _local(element.tag) == "t":
            out.append("".join(element.itertext()))
    return "".join(out)


def _centred_shapes(header: ET.Element) -> set[str]:
    """`paraPrIDRef`s of centred paragraphs."""
    out: set[str] = set()
    for element in header.iter():
        if _local(element.tag) != "paraPr":
            continue
        ident = element.get("id")
        align = next((c for c in element if _local(c.tag) == "align"), None)
        if ident and align is not None and (align.get("horizontal") or "").upper() == "CENTER":
            out.add(ident)
    return out


def _paragraph_kinds(header: ET.Element) -> dict[str, tuple[str, int]]:
    """`paraPrIDRef` → (`hh:heading` type, level)."""
    out: dict[str, tuple[str, int]] = {}
    for element in header.iter():
        if _local(element.tag) != "paraPr":
            continue
        ident = element.get("id")
        heading = next((c for c in element if _local(c.tag) == "heading"), None)
        if not ident or heading is None:
            continue
        try:
            level = int(heading.get("level") or 0)
        except ValueError:
            level = 0
        out[ident] = (heading.get("type") or "NONE", level)
    return out


def _styles(header: ET.Element) -> tuple[dict[str, str], dict[str, float]]:
    """`styleIDRef` → name, `charPrIDRef` → point size."""
    names: dict[str, str] = {}
    sizes: dict[str, float] = {}
    for element in header.iter():
        tag = _local(element.tag)
        if tag == "style":
            ident = element.get("id")
            if ident:
                names[ident] = element.get("name") or ""
        elif tag == "charPr":
            ident = element.get("id")
            height = element.get("height")
            if ident and height:
                try:
                    sizes[ident] = float(height) / _PT
                except ValueError:
                    continue
    return names, sizes


def _cells(table: ET.Element) -> richtext.Grid:
    """Table as an anchor-cell grid by `hp:cellAddr`/`hp:cellSpan`, else running position."""
    grid: dict[tuple[int, int], richtext.Cell] = {}
    taken: set[tuple[int, int]] = set()
    width = 0
    for row_index, row in enumerate(e for e in table.iter() if _local(e.tag) == "tr"):
        column = 0
        for cell in row:
            if _local(cell.tag) != "tc":
                continue
            addr = next((c for c in cell if _local(c.tag) == "cellAddr"), None)
            span = next((c for c in cell if _local(c.tag) == "cellSpan"), None)
            try:
                at_row = int(addr.get("rowAddr") or row_index) if addr is not None else row_index
                at_col = int(addr.get("colAddr") or column) if addr is not None else column
                across = max(1, int(span.get("colSpan") or 1)) if span is not None else 1
                down = max(1, int(span.get("rowSpan") or 1)) if span is not None else 1
            except ValueError:
                at_row, at_col, across, down = row_index, column, 1, 1
            # skip squares held by a merge from above
            while (at_row, at_col) in taken:
                at_col += 1
            # one line per paragraph
            lines = [
                " ".join(_text_of(para).split()) for para in cell.iter() if _local(para.tag) == "p"
            ]
            grid[(at_row, at_col)] = richtext.Cell(
                "\n".join(line for line in lines if line), across, down
            )
            for r in range(at_row, at_row + down):
                for c in range(at_col, at_col + across):
                    taken.add((r, c))
            column = at_col + across
            width = max(width, column)

    if not grid:
        return richtext.Grid()
    height = max(r for r, _ in grid) + 1
    # anchors only, reading order
    rows = [[grid[(r, c)] for c in range(width) if (r, c) in grid] for r in range(height)]
    return richtext.Grid(rows=[row for row in rows if any(c.text for c in row)])


def _walk(
    node: ET.Element,
    names: dict[str, str],
    sizes: dict[str, float],
    kinds: dict[str, tuple[str, int]],
    centred: set[str],
    out: list[Block],
) -> None:
    """Blocks in order; tables as one block, cell paragraphs not walked."""
    for child in node:
        tag = _local(child.tag)
        if tag == "tbl":
            grid = _cells(child)
            if grid.rows:
                out.append(Block(kind="table", grid=grid))
            continue
        if tag == "p":
            # table paragraph contributes only the table
            if any(_local(e.tag) == "tbl" for e in child.iter()):
                _walk(child, names, sizes, kinds, centred, out)
                continue
            text = " ".join(_text_of(child).split())
            if text:
                size = 0.0
                for run in child:
                    if _local(run.tag) == "run":
                        size = sizes.get(run.get("charPrIDRef") or "", 0.0)
                        if size:
                            break
                mark, level = kinds.get(child.get("paraPrIDRef") or "", ("NONE", 0))
                out.append(
                    Block(
                        kind="text",
                        text=text,
                        size=size,
                        style=names.get(child.get("styleIDRef") or "", ""),
                        mark=mark,
                        mark_level=level,
                        centred=(child.get("paraPrIDRef") or "") in centred,
                    )
                )
            continue
        _walk(child, names, sizes, kinds, centred, out)


def _blocks(archive: zipfile.ZipFile) -> tuple[list[Block], dict[str, float]]:
    try:
        header = ET.fromstring(archive.read("Contents/header.xml"))
    except (KeyError, ET.ParseError):
        names, sizes = {}, {}
        kinds: dict[str, tuple[str, int]] = {}
        centred: set[str] = set()
    else:
        names, sizes = _styles(header)
        kinds = _paragraph_kinds(header)
        centred = _centred_shapes(header)

    sections = sorted(
        n for n in archive.namelist() if n.startswith("Contents/section") and n.endswith(".xml")
    )
    blocks: list[Block] = []
    for name in sections:
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        _walk(root, names, sizes, kinds, centred, blocks)
    return blocks, sizes


def _body_size(blocks: list[Block]) -> float:
    """Point size with the most characters (body baseline)."""
    weight: dict[float, int] = {}
    for block in blocks:
        if block.kind == "text" and block.size:
            weight[block.size] = weight.get(block.size, 0) + len(block.text)
    if not weight:
        return 0.0
    return max(weight.items(), key=lambda pair: pair[1])[0]


def _is_heading(block: Block, body: float) -> bool:
    """Heading by mark, style name, or size ratio (not numbering)."""
    if block.kind != "text":
        return False
    if block.mark == "OUTLINE":
        return True
    if len(block.text) > _HEADING_MAX:
        return False
    if block.mark in ("BULLET", "NUMBER"):
        return False
    if _OUTLINE.match(block.style) or _HEADING_NAME.search(block.style):
        return True
    return bool(body and block.size >= body * 1.3)


def _levels(blocks: list[Block], body: float) -> dict[int, int]:
    """Heading level (1..3) per block: OUTLINE/`개요 N` own level, rest by size."""
    marks = [i for i, b in enumerate(blocks) if _is_heading(b, body)]
    ranks = sorted({blocks[i].size for i in marks}, reverse=True)
    out: dict[int, int] = {}
    for i in marks:
        if blocks[i].mark == "OUTLINE":
            out[i] = min(3, blocks[i].mark_level + 1)
            continue
        if match := _OUTLINE.match(blocks[i].style):
            out[i] = min(3, int(match.group(1)))
            continue
        depth = ranks.index(blocks[i].size) + 1 if blocks[i].size in ranks else 1
        # numbering deepens same-size headings (가. under 2-3.)
        if depth < 3:
            for offset, pattern in enumerate(_NUMBERING):
                if pattern.match(blocks[i].text):
                    depth = max(depth, min(3, offset))
                    break
        out[i] = min(3, max(1, depth))
    return out


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html(blocks: list[Block]) -> str:
    """One section's blocks as editor markup."""
    out: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for block in blocks:
        if block.kind == "table":
            flush()
            head, *rest = (block.grid or richtext.Grid()).rows

            def _cell(tag: str, cell: richtext.Cell) -> str:
                span = "".join(
                    f' {name}="{value}"'
                    for name, value in (("colspan", cell.colspan), ("rowspan", cell.rowspan))
                    if value > 1
                )
                body = "<br>".join(_escape(line) for line in cell.text.split("\n"))
                return f"<{tag}{span}>{body}</{tag}>"

            cells = "".join(_cell("th", c) for c in head)
            body = "".join("<tr>" + "".join(_cell("td", c) for c in row) + "</tr>" for row in rest)
            out.append(f"<table><thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if block.mark in ("BULLET", "NUMBER") or _BULLET.match(block.text):
            items.append(_escape(_BULLET.sub("", block.text)))
            continue
        flush()
        out.append(f"<p>{_escape(block.text)}</p>")
    flush()
    return "".join(out)


_LIST = re.compile(r"<ul>(.*?)</ul>", re.S)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
_PARA = re.compile(r"<p>(.*?)</p>", re.S)


def shape(parts: list[Section]) -> dict[str, Any]:
    """Structural summary for round-trip comparison."""
    tables: list[list[list[tuple[str, int, int]]]] = []
    lists: list[list[str]] = []
    paragraphs: list[str] = []
    for part in parts:
        for grid in richtext.grids(part.html):
            tables.append([[(c.text, c.colspan, c.rowspan) for c in row] for row in grid.rows])
        for listing in _LIST.findall(part.html):
            lists.append(_ITEM.findall(listing))
        paragraphs.extend(_PARA.findall(part.html))
    return {
        "headings": [(part.level, part.heading) for part in parts if part.heading],
        "tables": tables,
        "lists": lists,
        "paragraphs": paragraphs,
    }


def differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Differences between two `shape` results; empty when equal."""
    out: list[str] = []
    if before["headings"] != after["headings"]:
        lost = [h for h in before["headings"] if h not in after["headings"]]
        gained = [h for h in after["headings"] if h not in before["headings"]]
        out.append(f"제목이 달라졌다 — 사라짐 {lost}, 생김 {gained}")
    for name, key in (("표", "tables"), ("목록", "lists")):
        if len(before[key]) != len(after[key]):
            out.append(f"{name} 수가 {len(before[key])} → {len(after[key])}")
    for index, (was, now) in enumerate(zip(before["tables"], after["tables"], strict=False), 1):
        if len(was) != len(now):
            out.append(f"표{index} 행이 {len(was)} → {len(now)}")
            continue
        for r, (row_was, row_now) in enumerate(zip(was, now, strict=False)):
            if len(row_was) != len(row_now):
                out.append(f"표{index} {r + 1}행 칸이 {len(row_was)} → {len(row_now)}")
                continue
            for c, (cell_was, cell_now) in enumerate(zip(row_was, row_now, strict=False)):
                if cell_was[0] != cell_now[0]:
                    out.append(f"표{index} {r + 1}행 {c + 1}열: {cell_was[0]!r} → {cell_now[0]!r}")
                elif cell_was[1:] != cell_now[1:]:
                    out.append(
                        f"표{index} {r + 1}행 {c + 1}열 병합이 "
                        f"{cell_was[1]}×{cell_was[2]} → {cell_now[1]}×{cell_now[2]}"
                    )
    for index, (was, now) in enumerate(zip(before["lists"], after["lists"], strict=False), 1):
        if was != now:
            out.append(f"목록{index}이 달라졌다: {[i for i in was if i not in now]}")
    return out


def _title_index(blocks: list[Block], body: float) -> int | None:
    """Title block index (centred heading followed by a heading); must run before `_levels`."""
    first = next((i for i, b in enumerate(blocks) if b.text or b.kind != "text"), None)
    if first is None or not _is_heading(blocks[first], body):
        return None
    if not blocks[first].centred:
        return None
    if not any(_is_heading(b, body) for b in blocks[first + 1 :]):
        return None
    return first


def read(data: bytes) -> Document:
    """Parse into a `Document`; headingless file = one section. `RuntimeError` on bad archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "한글 문서(.hwpx)를 열지 못했습니다. 파일이 손상되었을 수 있습니다."
        ) from exc

    blocks, _ = _blocks(archive)
    if not blocks:
        raise RuntimeError("한글 문서(.hwpx)에서 본문을 찾지 못했습니다.")

    body = _body_size(blocks)
    title = ""
    if (at := _title_index(blocks, body)) is not None:
        title, blocks = blocks[at].text, blocks[at + 1 :]
    depths = _levels(blocks, body)

    out: list[Section] = []
    current: list[Block] = []
    heading = ""
    level = 1
    for index, block in enumerate(blocks):
        depth = depths.get(index, 0)
        if depth:
            if heading or current:
                out.append(Section(heading=heading, level=level, html=_html(current)))
            heading, level, current = block.text, depth, []
            continue
        current.append(block)
    if heading or current:
        out.append(Section(heading=heading, level=level, html=_html(current)))

    if out and not out[0].heading:
        out[0] = Section(heading="개요", level=1, html=out[0].html)
    return Document(title=title, sections=out)


def sections(data: bytes) -> list[Section]:
    """`read(data).sections`."""
    return read(data).sections
