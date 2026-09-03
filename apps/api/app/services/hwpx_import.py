"""`.hwpx` 를 편집할 수 있는 문서로 읽어들이기.

`files._from_hwpx` reads the same archive and answers a different question. It
produces the flat text a model is given as reference material, where losing the
headings and turning a table into tab-separated lines costs nothing: the model
reads prose either way.

This one has to keep the shape, because what it produces is handed to a person
to edit. A 계획서 that arrives as one wall of paragraphs has lost the thing it
was — the outline is how anybody finds the part they came to change — and a
table flattened into lines cannot be edited back into a table.

## What OWPML actually gives us

`.hwpx` is a zip of XML. `Contents/section*.xml` holds the body as `<hp:p>`
paragraphs; `Contents/header.xml` holds the styles and character properties
they point at by id. Namespaces vary by producer version, so everything here
matches on local names.

Headings are the hard part and there is no single flag for one. Three signals,
in order of how much they can be trusted:

1. **The style name.** `개요 1`..`개요 10` are Hangul Word Processor's own
   outline styles and mean exactly what they say. `제목`/`Heading`/`Title` are
   near enough.
2. **Numbering in the text.** `2-3.`, `가.`, `1)` at the head of a short
   paragraph is how Korean official documents mark their levels, and it is
   often the only mark a document has.
3. **Type size.** A paragraph set larger than the body's commonest size is a
   heading, and the sizes rank into levels.

The real 2027년 AI중심대학 계획서 this was built against uses none of the
outline styles — its headings are `+제목` and `*□-볼드`, names its author
invented — which is why one signal was never going to be enough.
"""

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

#: HWPUNIT is 1/100 pt, which is what `hh:charPr height` is in.
_PT = 100.0

#: Style names that say what they are without being read.
_OUTLINE = re.compile(r"^(?:개요|Outline)\s*(\d+)", re.I)
_HEADING_NAME = re.compile(r"제목|개요|heading|title", re.I)

#: How Korean official documents number their levels, outermost first. A
#: paragraph opening with one of these is a heading at that depth — 2-3. above
#: 가. above 1) — which is the only marking most of them carry.
_NUMBERING = (
    re.compile(r"^\s*(?:제\s*)?\d+\s*장\b"),
    re.compile(r"^\s*\d+(?:[-.]\d+)+\.?\s"),
    re.compile(r"^\s*[가나다라마바사아자차카타파하]\s*\.\s"),
    re.compile(r"^\s*\d+\s*\)\s"),
    re.compile(r"^\s*[가나다라마바사아자차카타파하]\s*\)\s"),
)

#: A line that opens with one of these is an item in a list, not a paragraph.
_BULLET = re.compile(r"^\s*[□■○●▪▫◦·•\-–]\s*")

#: Long enough that it is prose whatever it is set in. Guards the size and
#: numbering signals, which would otherwise promote a long numbered sentence.
_HEADING_MAX = 60


@dataclass(slots=True)
class Block:
    """One paragraph or one table, in the order it appears."""

    kind: str  # "text" | "table" | "image"
    text: str = ""
    size: float = 0.0
    style: str = ""
    #: `hh:heading` on this paragraph's properties — OWPML's own answer to
    #: what kind of paragraph this is. `OUTLINE` with a level is a heading and
    #: outranks every guess below; `BULLET` and `NUMBER` are list items, which
    #: is the one thing no amount of reading the text can tell you: the mark in
    #: front of them is drawn by the word processor and is not in the text.
    mark: str = ""
    mark_level: int = 0
    #: `hh:align horizontal` is CENTER. A centred heading at the very top of a
    #: file is the document's title, not its first section — see `read`.
    centred: bool = False
    #: Rows of cells, each cell already HTML-escaped. Tables only.
    grid: richtext.Grid | None = None


@dataclass(slots=True)
class Section:
    heading: str
    level: int
    html: str


@dataclass(slots=True)
class Document:
    """A read file: its title, and the sections under it."""

    title: str
    sections: list[Section]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(node: ET.Element) -> str:
    """Every `<hp:t>` run under this node, in order.

    `itertext` would also pick up the contents of a nested table, which belongs
    to the table and not to the paragraph carrying it.
    """
    out: list[str] = []
    for element in node.iter():
        if _local(element.tag) == "t":
            out.append("".join(element.itertext()))
    return "".join(out)


def _centred_shapes(header: ET.Element) -> set[str]:
    """`paraPrIDRef`s whose paragraphs are centred."""
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
    """`paraPrIDRef` → (heading type, level).

    The authoritative signal, where a document uses it. This one does for its
    lists and not for its headings, which is exactly why both this and the type
    ranking have to be here.
    """
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
    """`styleIDRef` → name, and `charPrIDRef` → point size."""
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
    """A table as a grid, with every cell where the document says it is.

    OWPML puts the answer on the cell: `hp:cellAddr` carries `colAddr`/`rowAddr`
    and `hp:cellSpan` carries how far it reaches. Reading the cells in document
    order and appending them in turn — which is what this did — is right only
    for a table with no merges in it, and wrong in a way that is hard to see: a
    계열 cell merged down four rows means the four rows under it hold one cell
    fewer, so 법과대학 landed in the 계열 column and every value on those rows
    was one column to the left of where it belonged. The table still looked
    like a table. It was just not this table.

    Addresses are used where they are given and the running position where they
    are not — a producer is not obliged to write them, and a reader that drops
    every cell of a table without them is worse than one that guesses in
    document order, which is what the guess amounts to.

    What a merge covers is left empty rather than filled with a repeat: the
    value belongs to the row the document put it on, and copying it down would
    state four times something stated once.

    The spans come back out with the cells. Flattened away here they could not
    be put back by anything downstream, and a real 재난 상황 보고서 opened for
    editing has 123 merged cells in it — every one of which used to become a
    filled cell followed by empty ones the moment it was exported.
    """
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
            # Without an address, step over what a merge from above already
            # holds — otherwise the guess lands on an occupied square.
            while (at_row, at_col) in taken:
                at_col += 1
            # Per paragraph, not per cell: a cell holding two lines ran them
            # together into 실습 중심 교육AI이론, which is a word that does not
            # exist and a sentence nobody can edit back apart. Kept as two
            # lines rather than joined with a space — the writer put the break
            # there, and only the cell can hold it.
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
    #: Anchors only, in reading order — the shape `richtext.Grid` describes and
    #: every exporter's own table model wants.
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
    """Paragraphs in document order, without descending into tables.

    `iter()` walks everything, and a table's cells are `<hp:p>` like any other
    paragraph — so a four-by-four grid arrived as sixteen one-word paragraphs
    between the sentences around it, and 구분 / 기존 / 개선 was read as three
    headings. A table is a block; what is inside it belongs to that block.
    """
    for child in node:
        tag = _local(child.tag)
        if tag == "tbl":
            grid = _cells(child)
            if grid.rows:
                out.append(Block(kind="table", grid=grid))
            continue
        if tag == "p":
            # A paragraph that carries a table has no text of its own worth
            # keeping — the table is what it is for.
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
    """The size most of the words are set in — the baseline headings stand above.

    By character count rather than by paragraph count: a document with forty
    one-line table captions and twenty real paragraphs has its body in the
    paragraphs.
    """
    weight: dict[float, int] = {}
    for block in blocks:
        if block.kind == "text" and block.size:
            weight[block.size] = weight.get(block.size, 0) + len(block.text)
    if not weight:
        return 0.0
    return max(weight.items(), key=lambda pair: pair[1])[0]


def _is_heading(block: Block, body: float) -> bool:
    """Whether this paragraph is a heading at all. Level is decided after.

    Two signals, and deliberately not the numbering: `1) 항목` at body size is
    an item in a list, and this document's own bullets are numbered that way.
    Numbering only breaks ties between headings of the same size, below.

    The size bar is a ratio rather than a difference. In a 9pt document the
    headings are 13 and 14 and the sub-bullets are 11 and 12; `body + 1.5`
    swept all four in and turned half the prose into an outline.
    """
    if block.kind != "text":
        return False
    if block.mark == "OUTLINE":
        return True
    if len(block.text) > _HEADING_MAX:
        return False
    # A paragraph the document itself calls a list item is not a heading, no
    # matter how it is set.
    if block.mark in ("BULLET", "NUMBER"):
        return False
    if _OUTLINE.match(block.style) or _HEADING_NAME.search(block.style):
        return True
    return bool(body and block.size >= body * 1.3)


def _levels(blocks: list[Block], body: float) -> dict[int, int]:
    """Heading level per block index, by the sizes the document actually used.

    Ranked rather than assigned from a table of point sizes: one author's 제목
    is 14pt and another's is 20pt, and what makes a heading a *sub*-heading is
    that it is smaller than the one above it in the same document. `개요 N`
    says its own level and outranks the ranking.
    """
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
        # Same size, different depth: 가. under 2-3. is how these documents
        # mark a sub-heading without changing the type.
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
    """One section's blocks as the markup the document editor stores."""
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


#: `<table>` … `</table>`, and one `<tr>` inside it.
_LIST = re.compile(r"<ul>(.*?)</ul>", re.S)
_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
_PARA = re.compile(r"<p>(.*?)</p>", re.S)


def shape(parts: list[Section]) -> dict[str, Any]:
    """What a document is made of, in the terms a round trip has to keep.

    Written for comparing a document with itself after it has been through a
    file: read the original, write it back out, read that, and the two shapes
    have to match. Three times today a real defect — a column shifted one place
    left under a vertical merge — survived being looked at, because a table
    with everything one column over still looks like a table. Compared as data
    it is one line of output.

    Cells carry their spans. Without them the comparison was blind to exactly
    the thing a round trip loses first — a merged header opened out into one
    filled cell and three empty ones is a different table that reads as the
    same one — and a check that cannot see a defect is a check that reports the
    defect fixed.

    Deliberately not a hash. What a reader needs from a failed comparison is
    which table and which cell, so this is the structure itself and the caller
    subtracts.
    """
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
    """Where two shapes disagree, said in the terms somebody can act on.

    Empty means the document survived the trip. Anything else names the table,
    the row and the column — not "the shapes differ".
    """
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
    """Which block, if any, is the document's title rather than a section.

    A file we wrote opens with its title centred and set large, and reading it
    back turned that into a heading — so a document exported, reopened and
    exported again grew a second copy of its own title, and a third, one per
    trip. The user who edits in 한글 and comes back is exactly the person who
    makes that trip repeatedly.

    Three conditions together, because each alone is wrong. Centred: a section
    heading in these documents is left-aligned. First: a centred heading in the
    middle of a file is a heading someone centred. And followed by another
    heading: a one-heading document has a section, not a title and no body.

    Found before the levels are ranked, not after. Ranking with the title still
    in the list makes it the largest heading and pushes every real one down a
    step, which is how a 1급 절 came back a 2급 절 on the first trip.
    """
    first = next((i for i, b in enumerate(blocks) if b.text or b.kind != "text"), None)
    if first is None or not _is_heading(blocks[first], body):
        return None
    if not blocks[first].centred:
        return None
    if not any(_is_heading(b, body) for b in blocks[first + 1 :]):
        return None
    return first


def read(data: bytes) -> Document:
    """The document as a title and the headings with editable bodies under it.

    Never raises for a shape it did not expect: a file with no headings at all
    comes back as one section holding everything, which is still editable and
    still exports. Raising there would refuse a document over the absence of a
    convention its author never used.
    """
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

    # Everything before the first heading is the document's opening, not a
    # section called nothing.
    if out and not out[0].heading:
        out[0] = Section(heading="개요", level=1, html=out[0].html)
    return Document(title=title, sections=out)


def sections(data: bytes) -> list[Section]:
    """`read`, for the callers that only want the body."""
    return read(data).sections
