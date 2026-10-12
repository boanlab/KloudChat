"""Report export to `.docx`, `.pdf` and `.hwpx`, built from the artifact's own sections."""

from __future__ import annotations

import contextvars
import hashlib
import io
import logging
import math
import re
import unicodedata
import zipfile
from collections.abc import Callable
from typing import Any

import PIL.Image
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph as _Paragraph

from app.services import charts as chartkit
from app.services import (
    design,
    doc_formats,
    doc_type,
    fonts,
    pictures,
    placeholders,
    richtext,
    table_figures,
)

log = logging.getLogger(__name__)

_MARKUP = re.compile(r"(<[^>]*>)")


def _glyphs(font: str) -> frozenset[int] | None:
    """Code points a registered TTF draws; `None` for a CID or built-in face."""
    try:
        face = pdfmetrics.getFont(font).face
    except Exception:  # noqa: BLE001 — an unknown name has nothing to say
        return None
    return frozenset(face.charToGlyph) if hasattr(face, "charToGlyph") else None


_GLYPHS: dict[str, frozenset[int] | None] = {}


_TABLE_CAPTION = re.compile(r"^\s*(?:<[^>]+>\s*)*(?:표|Table)\s?\d{1,3}\s?[.:]")


def _is_table_caption(flowable: object) -> bool:
    """Whether a story item is a 「표 1. …」 caption paragraph."""
    text = getattr(flowable, "text", None)
    if text is None:
        return False
    return bool(_TABLE_CAPTION.match(str(text)))


#: sub/superscript letters the faces lack (digits ¹²³ excluded)
_SCRIPT_LETTER = re.compile("[\u1d62-\u1d6a\u2090-\u209c\u2080-\u208e\u2071\u207f\u2c7c\u1d43-\u1d5b]")


def _script_markup(character: str) -> str:
    """`ᵢ` → `<sub>i</sub>`, `ⁿ` → `<super>n</super>`; empty for any other character."""
    tag, _, code = unicodedata.decomposition(character).partition(" ")
    if tag not in ("<sub>", "<super>") or not code:
        return ""
    base = _escape(chr(int(code.split()[0], 16)))
    return f"<sub>{base}</sub>" if tag == "<sub>" else f"<super>{base}</super>"


def _with_fallback(markup: str, font: str) -> str:
    """`markup` with runs `font` cannot draw (e.g. Hanja in NanumMyeongjo) set in the gothic face."""
    if _SCRIPT_LETTER.search(markup):
        markup = "".join(
            part if part.startswith("<") else _SCRIPT_LETTER.sub(
                lambda m: _script_markup(m.group(0)) or m.group(0), part
            )
            for part in _MARKUP.split(markup)
        )
    if font not in _GLYPHS:
        _GLYPHS[font] = _glyphs(font)
    have = _GLYPHS[font]
    if not have or markup.isascii():
        return markup
    bold = font.endswith("-bold")
    fallback = fonts.korean("gothic", bold=bold)
    if fallback == font:
        return markup
    if fallback not in _GLYPHS:
        _GLYPHS[fallback] = _glyphs(fallback)
    spare = _GLYPHS[fallback] or frozenset()

    def swap(text: str) -> str:
        out: list[str] = []
        run: list[str] = []
        for character in text:
            code = ord(character)
            if code > 0x2000 and code not in have and code in spare:
                run.append(character)
                continue
            if run:
                out.append(f'<font name="{fallback}">{"".join(run)}</font>')
                run = []
            out.append(character)
        if run:
            out.append(f'<font name="{fallback}">{"".join(run)}</font>')
        return "".join(out)

    return "".join(part if part.startswith("<") else swap(part) for part in _MARKUP.split(markup))


class Paragraph(_Paragraph):
    """reportlab `Paragraph` with per-glyph face fallback."""

    def __init__(self, text, style=None, *args, **kwargs) -> None:
        if isinstance(text, str) and style is not None:
            text = _with_fallback(text, style.fontName)
        super().__init__(text, style, *args, **kwargs)


def citation_text(source: dict[str, Any], style: str = "APA") -> str:
    """One source as a reference entry (APA, IEEE, MLA, Chicago)."""
    title = str(source.get("title") or "제목 없음").strip()
    author = str(source.get("author") or "").strip()
    publisher = str(source.get("publisher") or "").strip()
    year = str(source.get("year") or "").strip()
    url = str(source.get("url") or "").strip()
    doi = str(source.get("doi") or "").strip()
    if doi:
        url = f"https://doi.org/{doi}"
    style = style.upper()

    if style == "IEEE":
        ordinal = source.get("ordinal") or 1
        lead = f"[{ordinal}] " + (f"{author}, " if author else "")
        details = ", ".join(part for part in (publisher, year) if part)
        return (
            lead
            + f"“{title}.”"
            + (f" {details}." if details else "")
            + (f" [온라인]. {url}" if url else "")
        )
    if style == "MLA":
        parts = [f"{author}." if author else "", f"“{title}.”", publisher, year, url]
        return " ".join(part for part in parts if part).rstrip(".") + "."
    if style == "CHICAGO":
        lead = f"{author}. " if author else ""
        details = ", ".join(part for part in (publisher, year) if part)
        return (
            lead + f"“{title}.”" + (f" {details}." if details else "") + (f" {url}" if url else "")
        )

    # APA fallback; title replaces a missing author
    dated = f"({year}). " if year else ""
    tail = ". ".join(part for part in (publisher, url) if part)
    if author:
        return f"{author}. {dated}{title}" + (f". {tail}" if tail else "")
    return (f"{title}. {dated}" + tail).rstrip() if tail or dated else title


def with_references(
    sections: list[dict], sources: list[dict] | None, style: str = "APA"
) -> list[dict]:
    """Export sections plus the reference list."""
    if not sources:
        return sections
    from app.services.citations import drop_restated_references

    entries = [citation_text(source, style) for source in sources]
    return [
        # writer's restated references dropped
        *drop_restated_references(sections, sources),
        {
            "id": "references",
            "heading": "참고문헌",
            "level": 1,
            "status": "done",
            # IEEE entries carry their own 「[n]」: not numbered twice
            "content": ("\n\n".join(entries) if style.upper() == "IEEE" else
                        "\n".join(f"{index}. {entry}" for index, entry in enumerate(entries, 1))),
        },
    ]


_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
_FENCE = re.compile(r"^\s*```\s*([A-Za-z0-9_-]*)\s*$")
_PAGE_BREAK = re.compile(r"^\s*<!--\s*pagebreak\s*-->\s*$", re.I)


_MARK = re.compile(r"\[\^([^\]]{1,16})\]")

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _raised_marks(text: str) -> str:
    """`[^1]` → `¹`, for the formats with no footnote model."""

    def raise_one(found: re.Match[str]) -> str:
        mark = found.group(1)
        return mark.translate(_SUPERSCRIPT) if mark.isdigit() else f"({mark})"

    return _MARK.sub(raise_one, text)


def _marks_in(text: str) -> set[str]:
    """Footnote numbers cited in a line."""
    return {found.group(1) for found in _MARK.finditer(text)}


_NOTE = re.compile(r"^\[\^([^\]]{1,16})\]:\s*(.+)$")


def _chart_as_rows(chart: dict) -> list[list[str]]:
    """Chart numbers as a table, for formats without charts."""
    head = ["항목", *[name or "값" for name, _ in chart["series"]]]
    body = [
        [category, *[_number(series[row]) for _, series in chart["series"]]]
        for row, category in enumerate(chart["categories"])
    ]
    return [head, *body]


def _number(value: float) -> str:
    """Value without a spurious decimal point."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0")


def _chart_block(source: str) -> dict | None:
    """`chart` fence (`kind | unit`, `axis | series…`, rows) as numbers, or `None`."""
    lines = [line.strip() for line in (source or "").splitlines() if line.strip()]
    if len(lines) < 3:
        return None

    head = [cell.strip() for cell in lines[0].split("|")]
    kind = head[0].lower() if head else "bar"
    if kind not in ("bar", "line"):
        kind = "bar"
    unit = head[1] if len(head) > 1 else ""

    names = [cell.strip() for cell in lines[1].split("|")][1:]
    if not names:
        return None

    categories: list[str] = []
    columns: list[list[float]] = [[] for _ in names]
    for line in lines[2:]:
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < len(names) + 1 or not cells[0]:
            continue
        try:
            values = [float(cell.replace(",", "")) for cell in cells[1 : len(names) + 1]]
        except ValueError:
            continue
        categories.append(cells[0])
        for column, value in zip(columns, values, strict=True):
            column.append(value)

    if len(categories) < 2:
        return None
    return {
        "kind": kind,
        "unit": unit,
        "categories": categories[:8],
        "series": [(name, column[:8]) for name, column in zip(names[:2], columns, strict=False)],
    }


def _kpi_rows(source: str, *, limit: int = 4) -> list[tuple[str, str]]:
    """`[(값, 이름)]` from a `kpi`/`steps` fence, split on the first `|`."""
    out: list[tuple[str, str]] = []
    for line in (source or "").splitlines():
        if "|" not in line:
            continue
        value, _, label = line.partition("|")
        value, label = value.strip(), label.strip()
        if value and label:
            out.append((value, label))
    return out[:limit]


def _in_pairs(
    cards: list[tuple[str, list[str]]],
) -> list[list[tuple[str, list[str]]]]:
    """Cards in pairs; odd last card padded."""
    padded: list[tuple[str, list[str]]] = list(cards)
    if len(padded) % 2:
        padded.append(("", []))
    return [padded[i : i + 2] for i in range(0, len(padded), 2)]


def _cards(source: str, *, limit: int = 6) -> list[tuple[str, list[str]]]:
    """`[(제목, [줄, …])]` from a `cards` fence: `## 제목` then `- 줄` lines; six cards at most."""
    cards: list[tuple[str, list[str]]] = []
    for raw in (source or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            cards.append((line.lstrip("#").strip(), []))
        elif cards:
            cards[-1][1].append(line.lstrip("-*").strip() or line)
    return [(title, items) for title, items in cards if title][:limit]


def _callout(source: str) -> tuple[str, list[str]]:
    """`(제목, [줄, …])` from a `callout` fence; the first line is the title."""
    lines = [raw.strip() for raw in (source or "").splitlines() if raw.strip()]
    if not lines:
        return ("", [])
    return (lines[0].lstrip("#").strip(), [line.lstrip("-*").strip() for line in lines[1:]])


def diagram_key(source: str) -> str:
    """Whitespace-insensitive diagram source key, shared with the browser's raster store."""
    normalised = "\n".join(line.rstrip() for line in (source or "").strip().splitlines())
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _cells(line: str) -> list[str]:
    """One row's cells; escaped pipes kept."""
    body = _ROW.match(line).group(1)  # type: ignore[union-attr]
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def _as_grid(rows: richtext.Grid | list[list[str]]) -> richtext.Grid:
    """Any grid input as a grid."""
    if isinstance(rows, richtext.Grid):
        return rows
    return richtext.Grid(
        rows=[[richtext.Cell(cell.replace("<br>", "\n")) for cell in row] for row in rows]
    )


def _export_grid(rows: richtext.Grid | list[list[str]]) -> richtext.Grid:
    """Structural rows kept; only unstructured rows lose empty lines."""
    grid = _as_grid(rows)
    if not any(cell.text.strip() for row in grid.rows for cell in row):
        return richtext.Grid()
    if isinstance(rows, richtext.Grid):
        return grid
    return richtext.Grid(rows=[row for row in grid.rows if any(c.text.strip() for c in row)])


def _matched(
    written: list[list[str]], grids: list[richtext.Grid], used: set[int]
) -> richtext.Grid | None:
    """Source grid of a GFM table, matched by text."""
    for index, grid in enumerate(grids):
        if index in used:
            continue
        if grid.flat(newline="<br>") == written:
            used.add(index)
            return grid
    return None


_MARKERS = ("•", "–", "·")
#: indent per level, in points
_INDENT = 14.0


def _unfenced(lines: list[str]) -> list[tuple[str, Any, str, int]]:
    """Plain fence lines as prose; pictures as pictures."""
    out: list[tuple[str, Any, str, int]] = []
    for one in lines:
        if not one.strip():
            continue
        if "](data:image/" in one or re.match(r"^\s*!\[[^\]]*\]\(", one):
            out.extend(_markdown_to_lines(one.strip()))
        else:
            out.append(("body", one, "", 0))
    return out


def _markdown_to_lines(
    text: str, grids: list[richtext.Grid] | None = None, levels: int = len(_MARKERS)
) -> list[tuple[str, Any, str, int]]:
    """`(kind, payload, marker, depth)` per line; depth capped at `levels`."""
    text = richtext.detach_tables(text)
    out: list[tuple[str, Any, str, int]] = []
    number = 0
    counts: dict[int, int] = {}
    rows: list[list[str]] = []
    table_source: list[str] = []
    plain_source: list[str] = []
    last_row_offset = 0
    spare = list(grids or [])
    used: set[int] = set()
    ruled = False
    fence: list[str] | None = None
    fence_lang = ""

    def flush_plain() -> None:
        if plain_source:
            # unmatched run: plain Markdown fallback (no grids, no recursion)
            out.extend(_markdown_to_lines("\n".join(plain_source)))
            plain_source.clear()

    def close_table(*, final: bool = True) -> None:
        nonlocal rows, ruled, table_source
        ruled = False
        if rows:
            width = max(len(row) for row in rows)
            padded = [row + [""] * (width - len(row)) for row in rows]
            found = _matched(padded, spare, used)
            if found is not None:
                flush_plain()
                out.append(("table", found, "", 0))
            elif spare:
                plain_source.extend(table_source)
            else:
                plain = [row for row in padded if any(cell.strip() for cell in row)]
                out.append(("table", _as_grid(plain), "", 0))
        rows = []
        table_source = []
        if final:
            flush_plain()

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if fence is not None:
            if _FENCE.match(line):
                source = "\n".join(fence)
                if fence_lang.lower() == "chart":
                    if drawn := _chart_block(source):
                        # source in marker slot for `.hwpx` raster lookup
                        out.append(("chart", drawn, source, 0))
                elif fence_lang.lower() == "steps":
                    if steps := _kpi_rows(source, limit=8):
                        out.append(("steps", steps, "", 0))
                elif fence_lang.lower() == "kpi":
                    if figures := _kpi_rows(source):
                        out.append(("kpi", figures, "", 0))
                elif fence_lang.lower() == "cards":
                    if cards := _cards(source):
                        out.append(("cards", cards, "", 0))
                elif fence_lang.lower() == "callout":
                    if callout := _callout(source):
                        if callout[0]:
                            out.append(("callout", callout, "", 0))
                elif fence_lang.lower() == "mermaid":
                    out.append(("diagram", {"source": source, "key": diagram_key(source)}, "", 0))
                else:
                    # other fences as prose; embedded pictures drawn, not printed as base64
                    out.extend(_unfenced(fence))
                fence = None
                fence_lang = ""
            else:
                fence.append(raw)
            continue
        if found := _FENCE.match(line):
            close_table()
            fence = []
            fence_lang = found.group(1)
            continue
        if _PAGE_BREAK.match(line):
            close_table()
            number = 0
            counts.clear()
            out.append(("pagebreak", "", "", 0))
            continue
        if not line.strip():
            # blank line does not end a table
            if not rows:
                close_table()
            continue
        if _RULE.match(line) and rows and (not spare or "-" in line):
            # HTML grids: empty rows for covered cells; second rule row starts a new table
            if ruled:
                head = rows.pop()
                head_source = table_source[last_row_offset:]
                table_source = table_source[:last_row_offset]
                close_table(final=False)
                rows.append(head)
                table_source.extend(head_source)
                last_row_offset = 0
            table_source.append(line)
            ruled = True
            continue
        if _ROW.match(line):
            number = 0
            counts.clear()
            rows.append(_cells(line))
            last_row_offset = len(table_source)
            table_source.append(line)
            continue
        close_table()
        if picture := _IMAGE.match(line):
            # embedded pictures only: no network calls in export
            decoded = pictures.decode(picture.group(2).strip())
            if decoded:
                mime, data = decoded
                out.append(
                    ("image", {"data": data, "mime": mime, "caption": picture.group(1)}, "", 0)
                )
            continue
        if heading := re.match(r"^#{2,6}\s+(.*)$", line):
            number = 0
            counts.clear()
            out.append(("heading", heading.group(1).strip(), "", 0))
        elif note := _NOTE.match(line):
            number = 0
            counts.clear()
            out.append(("note", note.group(2).strip(), note.group(1), 0))
        elif bullet := re.match(r"^(\s*)[-*+]\s+(.*)$", line):
            number = 0
            counts.clear()
            depth = min(len(bullet.group(1)) // 2, levels - 1)
            out.append(
                ("bullet", bullet.group(2).strip(), _MARKERS[min(depth, len(_MARKERS) - 1)], depth)
            )
        elif numbered := re.match(r"^(\s*)(\d{1,9})[.)]\s+(.*)$", line):
            depth = min(len(numbered.group(1)) // 2, len(_MARKERS) - 1)
            # per-level counts; `is None` since a list may start at 0
            counts[depth] = (
                counts[depth] + 1 if counts.get(depth) is not None else int(numbered.group(2))
            )
            for deeper in [d for d in counts if d > depth]:
                del counts[deeper]
            number = counts[depth]
            out.append(("number", numbered.group(3).strip(), f"{number}.", depth))
        else:
            number = 0
            counts.clear()
            out.append(("body", line.strip(), "", 0))
    close_table()
    if fence:
        # unclosed fence: truncated document
        out.extend(_unfenced(fence))
    return out


#: 개조식 geometry in body ems (편람 p.52): 2타 per depth, hanging marker
_GAEJO_STEP = 1.0
_GAEJO_HANG = 1.5
#: 붙임 hang: six 타
_ATTACH_HANG = 3.0
def _hang(kind: str, depth: int) -> tuple[float, float]:
    """`(start, hang)` in body ems for a 개조식, ※ or 붙임 line."""
    if kind == "attach":
        return (0.0, _ATTACH_HANG) if depth == 0 else (_ATTACH_HANG, 0.0)
    return depth * _GAEJO_STEP, _GAEJO_HANG


_TEXT_KINDS = ("body", "bullet", "number", "gaejo", "remark", "attach")
_ATTACH_LINE = re.compile(r"^붙임(?:[\s\u00a0]{1,8}(.*))?$")


def _gaejo_lines(lines: list[tuple[str, Any, str, int]]) -> list[tuple[str, Any, str, int]]:
    """개조식 body lines: bullets by depth, ※ remarks, 붙임 items."""
    out: list[tuple[str, Any, str, int]] = []
    attaching = False
    for kind, text, marker, depth in lines:
        if kind == "bullet":
            level = min(depth, len(doc_formats.GAEJO_MARKERS) - 1)
            if str(text).startswith("※"):
                out.append(("remark", str(text)[1:].strip(), "※", level))
            else:
                out.append(("gaejo", text, doc_formats.GAEJO_MARKERS[level], level))
            attaching = False
            continue
        if kind == "body" and str(text).startswith("※"):
            # orphan ※ at ○ depth
            out.append(("remark", str(text)[1:].strip(), "※", 1))
            attaching = False
            continue
        if kind == "body" and (found := _ATTACH_LINE.match(str(text))):
            out.append(("attach", (found.group(1) or "").strip(), "붙임", 0))
            attaching = True
            continue
        if kind == "number" and attaching:
            out.append(("attach", f"{marker} {text}", "", 1))
            continue
        attaching = False
        out.append((kind, text, marker, depth))
    return out


def _closed(lines: list[tuple[str, Any, str, int]], gap: str) -> list[tuple[str, Any, str, int]]:
    """Lines ending in 「끝.」 (시행규칙 제4조제5항); a typed 「끝.」 is replaced."""
    kept: list[tuple[str, Any, str, int]] = []
    for kind, text, marker, depth in lines:
        if kind in _TEXT_KINDS:
            text = doc_formats.without_closing(str(text))
            if not text.strip() and kind != "attach":
                continue
        kept.append((kind, text, marker, depth))
    tail = next(
        (i for i in range(len(kept) - 1, -1, -1) if kept[i][0] not in ("note", "pagebreak")), None
    )
    if tail is not None and kept[tail][0] in _TEXT_KINDS:
        kind, text, marker, depth = kept[tail]
        kept[tail] = (kind, f"{str(text).rstrip()}{gap}끝.", marker, depth)
    else:
        kept.append(("body", f"{gap}끝.", "", 0))
    return kept


#: 개조식 붙임 section heading
_ATTACH_HEADING = re.compile(r"^\s{0,4}붙\s{0,2}임\s{0,4}$")
_NO_ATTACHMENT = re.compile(r"^[\s\-*·]*(?:없음|해당\s?없음|-|—)?[\s.]*$")


#: kinds a 붙임 carries in full after its list
_ATTACHED_KINDS = ("table", "image", "chart", "diagram")


def _is_attachment(section: dict, block: dict | None) -> bool:
    """Whether a 개조식 section is the 붙임 list."""
    return bool(
        block
        and block.get("lists") == "gaejo"
        and _ATTACH_HEADING.match(str(section.get("heading") or ""))
    )


def _drawn_sections(sections: list[dict], block: dict | None) -> list[dict]:
    """Drawn sections; an empty 개조식 붙임 dropped."""
    return [
        s
        for s in sections
        if not (_is_attachment(s, block) and _NO_ATTACHMENT.match(str(s.get("content") or "")))
    ]


def _format_of(block: dict | None) -> str | None:
    """`doc_formats` id whose scale the document takes."""
    return (block or {}).get("format") or None


def _format_page(page_settings: dict | None, title_block: dict | None) -> dict | None:
    """Page settings with format default margins."""
    own = doc_type.margins_for(_format_of(_title_block(title_block)))
    if not own:
        return page_settings
    settings = dict(page_settings or {})
    given = settings.get("margins") if isinstance(settings.get("margins"), dict) else {}
    settings["margins"] = {**own, **given}
    return settings


def _last_body(sections: list[dict]) -> int:
    """Index of the section 「끝.」 closes (last non-reference)."""
    return next(
        (i for i in range(len(sections) - 1, -1, -1) if sections[i].get("id") != "references"),
        -1,
    )


def _section_lines(
    section: dict, block: dict | None, last: bool, gap: str
) -> list[tuple[str, Any, str, int]]:
    """`_markdown_to_lines` for one section in its format (개조식, 「끝.」)."""
    content = section.get("content") or ""
    gaejo = bool(block and block.get("lists") == "gaejo")
    attachment = _is_attachment(section, block)
    if attachment:
        content = doc_formats.attachment_body(content)
    if not gaejo:
        lines = _markdown_to_lines(content, section.get("tables"))
    else:
        lines = _gaejo_lines(
            _markdown_to_lines(
                doc_formats.gaejo_markdown(content),
                section.get("tables"),
                levels=len(doc_formats.GAEJO_MARKERS),
            )
        )
    tail: list[tuple[str, Any, str, int]] = []
    if attachment:
        # 「끝.」 closes the 붙임 list; attached items follow on a new page
        end = next(
            (
                i
                for i, line in enumerate(lines)
                if line[0] != "attach" or (i and line[2] == "붙임")
            ),
            len(lines),
        )
        if end and any(line[0] in _ATTACHED_KINDS for line in lines[end:]):
            lines, tail = lines[:end], [("pagebreak", "", "", 0), *lines[end:]]
    if block and block.get("closing") and last:
        lines = _closed(lines, gap)
    return lines + tail


def _strip_inline(text: str) -> str:
    """Inline markers stripped; code spans may not open/close on a space."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    return re.sub(r"`(\S(?:[^`]*\S)?)`", r"\1", text)


def _format_key(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_inline(str(text))).strip()


def _next_format(blocks: list[dict], text: str, cursor: int) -> tuple[dict | None, int]:
    """Next editable-HTML block for a prose line, in document order."""
    wanted = _format_key(text)
    for index in range(cursor, len(blocks)):
        block = blocks[index]
        if _format_key(str(block.get("text") or "")) == wanted:
            return block, index + 1
    return None, cursor


def _docx_styled(paragraph, block: dict, fallback: str) -> None:
    """Editable HTML block as Word paragraph/run properties."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    block_style = block.get("style") or {}
    alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }.get(str(block_style.get("text-align") or "").lower())
    if alignment is not None:
        paragraph.alignment = alignment
    try:
        if line_height := block_style.get("line-height"):
            paragraph.paragraph_format.line_spacing = float(line_height)
    except (TypeError, ValueError):
        pass

    runs = block.get("runs") or [{"text": fallback, "style": {}}]
    for item in runs:
        run = paragraph.add_run(str(item.get("text") or ""))
        style = item.get("style") or {}
        weight = str(style.get("font-weight") or "").lower()
        run.bold = weight in {"bold", "600", "700", "800", "900"}
        run.italic = str(style.get("font-style") or "").lower() == "italic"
        decoration = str(style.get("text-decoration") or "").lower()
        run.underline = "underline" in decoration
        run.font.strike = "line-through" in decoration
        if family := str(style.get("font-family") or "").split(",")[0].strip(" '\""):
            run.font.name = family
        size = str(style.get("font-size") or "")
        if found := re.fullmatch(r"(\d+(?:\.\d+)?)pt", size, re.I):
            run.font.size = Pt(max(6, min(72, float(found.group(1)))))
        colour = str(style.get("color") or "")
        if found := re.fullmatch(r"#([0-9a-f]{6})", colour, re.I):
            run.font.color.rgb = RGBColor.from_string(found.group(1).upper())
        elif found := re.fullmatch(r"#([0-9a-f]{3})", colour, re.I):
            value = "".join(character * 2 for character in found.group(1))
            run.font.color.rgb = RGBColor.from_string(value.upper())
        background = str(style.get("background-color") or "")
        if found := re.fullmatch(r"#([0-9a-f]{6}|[0-9a-f]{3})", background, re.I):
            value = found.group(1)
            if len(value) == 3:
                value = "".join(character * 2 for character in value)
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), value.upper())
            run._r.get_or_add_rPr().append(shading)


def _pdf_styled(block: dict, fallback: str) -> str:
    """ReportLab-safe markup for editor formatting."""
    out: list[str] = []
    for item in block.get("runs") or [{"text": fallback, "style": {}}]:
        text = _escape(str(item.get("text") or "")).replace("\n", "<br/>")
        style = item.get("style") or {}
        tags: list[tuple[str, str]] = []
        attributes: list[str] = []
        size = str(style.get("font-size") or "")
        if found := re.fullmatch(r"(\d+(?:\.\d+)?)pt", size, re.I):
            attributes.append(f'size="{max(6, min(72, float(found.group(1)))):g}"')
        for source, target in (("color", "color"), ("background-color", "backColor")):
            value = str(style.get(source) or "")
            if re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?", value, re.I):
                attributes.append(f'{target}="{value}"')
        if attributes:
            tags.append(("font", " ".join(attributes)))
        weight = str(style.get("font-weight") or "").lower()
        if weight in {"bold", "600", "700", "800", "900"}:
            tags.append(("b", ""))
        if str(style.get("font-style") or "").lower() == "italic":
            tags.append(("i", ""))
        decoration = str(style.get("text-decoration") or "").lower()
        if "underline" in decoration:
            tags.append(("u", ""))
        if "line-through" in decoration:
            tags.append(("strike", ""))
        for tag, attributes in tags:
            text = f"<{tag}{(' ' + attributes) if attributes else ''}>{text}</{tag}>"
        out.append(text)
    return "".join(out)


def _pdf_block_style(base: ParagraphStyle, block: dict) -> ParagraphStyle:
    style = block.get("style") or {}
    alignment = {
        "left": TA_LEFT,
        "center": TA_CENTER,
        "right": TA_RIGHT,
        "justify": TA_JUSTIFY,
    }.get(str(style.get("text-align") or "").lower(), base.alignment)
    leading = base.leading
    try:
        if line_height := style.get("line-height"):
            leading = base.fontSize * float(line_height)
    except (TypeError, ValueError):
        pass
    return ParagraphStyle(
        f"{base.name}-edited-{id(block)}", parent=base, alignment=alignment, leading=leading
    )


def _field(paragraph, instruction: str, placeholder: str = "") -> None:
    """Word field as `w:fldSimple` (no python-docx API)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    if placeholder:
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = placeholder
        run.append(text)
        field.append(run)
    paragraph._p.append(field)


def _page_setup(document, settings: dict | None = None) -> None:
    """A4, 20mm margins (python-docx defaults to US Letter)."""
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm

    settings = settings or {}
    margins = settings.get("margins") if isinstance(settings.get("margins"), dict) else {}
    for section in document.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.left_margin = Mm(float(margins.get("left", 20)))
        section.right_margin = Mm(float(margins.get("right", 20)))
        section.top_margin = Mm(float(margins.get("top", 20)))
        section.bottom_margin = Mm(float(margins.get("bottom", 20)))
        header_text = str(settings.get("header") or "")
        if header_text:
            header = section.header.paragraphs[0]
            header.text = header_text
        if not bool(settings.get("firstPageHeader", False)):
            section.different_first_page_header_footer = True
            section.first_page_header.paragraphs[0].text = ""
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer_text = str(settings.get("footer") or "")
        if footer_text:
            footer.add_run(footer_text + " · ")
        page_numbers = str(settings.get("pageNumbers") or "page-total")
        if page_numbers != "none":
            _field(footer, "PAGE", "1")
            if page_numbers == "page-total":
                footer.add_run(" / ")
                _field(footer, "NUMPAGES", "1")


_TOC_SECTIONS = 6
_TOC_CHARS = 6_000


def _wants_toc(sections: list[dict]) -> bool:
    length = sum(len(str(s.get("content") or "")) for s in sections)
    return len(sections) >= _TOC_SECTIONS and length >= _TOC_CHARS


def _update_fields_on_open(document) -> None:
    """Word field refresh on open, for the 목차."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    settings_element = document.settings.element
    if settings_element.find(qn("w:updateFields")) is None:
        flag = OxmlElement("w:updateFields")
        flag.set(qn("w:val"), "true")
        settings_element.append(flag)


def _docx_margins(document, margins: dict[str, float]) -> None:
    """Page margins in mm on every section."""
    from docx.shared import Mm

    for section in document.sections:
        section.top_margin = Mm(margins["top"])
        section.bottom_margin = Mm(margins["bottom"])
        section.left_margin = Mm(margins["left"])
        section.right_margin = Mm(margins["right"])


def _docx_type_scale(document, fmt: str | None = None) -> None:
    """Word built-in styles at document scale; template faces/colours kept."""
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    scale, leading = doc_type.type_for(fmt), doc_type.leading_for(fmt)
    space = doc_type.space_for(fmt)

    sizes = {
        "Title": ("title", True),
        "Heading 1": ("h1", True),
        "Heading 2": ("h2", True),
        "Heading 3": ("h3", True),
        "Heading 4": ("body", True),
        "Normal": ("body", False),
        "Body Text": ("body", False),
        "List Bullet": ("body", False),
        "List Number": ("body", False),
        "Caption": ("caption", False),
        "List Bullet 2": ("body", False),
        "List Bullet 3": ("body", False),
        "표 머리": ("table", True),
        "표 본문": ("table", False),
        "표 숫자": ("table", False),
    }
    for name, (key, bold) in sizes.items():
        try:
            style = document.styles[name]
        except KeyError:
            continue
        style.font.size = Pt(scale[key])
        if bold:
            style.font.bold = True
        if key == "body":
            style.paragraph_format.line_spacing = leading["body"]
        elif key == "table":
            style.paragraph_format.line_spacing = leading["table"]
        if space is None:
            continue
        # Word ratio multiplies the face's line height (~1.3 Hangul): 160% as points instead
        form = style.paragraph_format
        if name.startswith("Heading"):
            form.space_before = Pt(space["headingBefore"])
            form.space_after = Pt(space["headingAfter"])
            ratio = leading["heading"]
        elif name == "Title":
            form.space_before = form.space_after = Pt(0)
            ratio = leading["title"]
        else:
            if key == "body" and not bold:
                form.space_after = Pt(space["item"])
            ratio = leading["table" if key == "table" else "body"]
        form.line_spacing = Pt(round(scale[key] * ratio, 1))
        form.line_spacing_rule = WD_LINE_SPACING.AT_LEAST


def _korean_fonts(document) -> None:
    """맑은 고딕/Calibri on default styles (python-docx sets no East Asian font)."""
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    for name in (
        "Normal",
        "Body Text",
        "Title",
        "Heading 1",
        "Heading 2",
        "Heading 3",
        "Heading 4",
        "List Bullet",
        "List Bullet 2",
        "List Bullet 3",
    ):
        try:
            style = document.styles[name]
        except KeyError:
            continue
        style.font.name = "Calibri"
        # theme colour overridden with ink; accent per heading
        if name.startswith("Heading") or name == "Title":
            style.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
            style.font.italic = False
            colour = style.element.rPr.find(qn("w:color"))
            if colour is not None:
                for attribute in ("w:themeColor", "w:themeShade", "w:themeTint"):
                    colour.attrib.pop(qn(attribute), None)
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            from docx.oxml import OxmlElement

            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        fonts.set(qn("w:eastAsia"), "맑은 고딕")
        fonts.set(qn("w:ascii"), "Calibri")
        fonts.set(qn("w:hAnsi"), "Calibri")
        # Word reads theme faces first
        for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(attribute), None)


_BODY_STYLES = (
    "Normal", "Body Text", "List Bullet", "List Bullet 2", "List Bullet 3", "List Number",
    "List Number 2", "List Number 3", "Quote", "Caption", "안내", "안내 목록", "표 본문",
)


def _docx_body_face(document, serif: bool) -> None:
    """Body styles in 바탕/맑은 고딕; headings keep the file's face."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    face = "바탕" if serif else "맑은 고딕"
    for name in _BODY_STYLES:
        style = _docx_style(document, name)
        if style is None:
            continue
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        fonts.set(qn("w:eastAsia"), face)
        fonts.attrib.pop(qn("w:eastAsiaTheme"), None)


def _docx_heading_face(run) -> None:
    """Run in the heading face (맑은 고딕)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    rpr = run._r.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), "맑은 고딕")


def _table_of_contents(document) -> None:
    """목차 field over headings; placeholder until Word updates."""
    from docx.shared import Pt

    heading = document.add_paragraph("목차", style="Heading 1")
    heading.paragraph_format.space_after = Pt(6)
    _field(
        document.add_paragraph(),
        'TOC \\o "1-2" \\h \\z \\u',
        "목차를 보려면 이 줄에서 F9 를 누르세요.",
    )
    # no page break: unexpanded field would leave page 1 empty


def _docx_cell_margins(table, vertical_pt: float = 3.0, horizontal_pt: float = 6.0) -> None:
    """Cell padding; Word's default is a hair."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table._tbl.tblPr
    margins = properties.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        # schema order: `tblLook` last in `tblPr`
        look = properties.find(qn("w:tblLook"))
        if look is not None:
            look.addprevious(margins)
        else:
            properties.append(margins)
    for side, points in (
        ("top", vertical_pt),
        ("bottom", vertical_pt),
        ("left", horizontal_pt),
        ("right", horizontal_pt),
    ):
        node = margins.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            margins.append(node)
        node.set(qn("w:w"), str(int(points * 20)))
        node.set(qn("w:type"), "dxa")


def _numeric_columns(rows: list[list[str]]) -> set[int]:
    """Right-aligned figure columns."""
    return table_figures.figure_columns(rows)


def _docx_style(document, name: str):
    """Named style or `None`."""
    try:
        return document.styles[name]
    except KeyError:
        return None


def _docx_table_styles(document) -> None:
    """Fallback `표 머리`/`표 본문`/`표 숫자` paragraph styles."""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    base = _docx_style(document, _BODY) or document.styles["Normal"]
    for name, bold, right in (
        ("표 머리", True, False),
        ("표 본문", False, False),
        ("표 숫자", False, True),
    ):
        if _docx_style(document, name) is not None:
            continue
        style = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = document.styles["표 본문"] if right else base
        style.font.size = Pt(doc_type.TYPE["table"])
        style.font.bold = bold
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.line_spacing = doc_type.LEADING["table"]
        if right:
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT


def _docx_table(document, rows: richtext.Grid | list[list[str]], accent: str = "") -> None:
    """Word table: repeating head row, width shares, merges; text written after merging."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Pt

    grid = _export_grid(rows)
    if not grid.rows:
        return
    width = grid.width
    flat = grid.flat()
    numeric = _numeric_columns(flat)
    _docx_table_styles(document)
    table = document.add_table(rows=len(grid.rows), cols=width)
    table.style = _docx_style(document, "보고서 표") or "Table Grid"
    _docx_cell_margins(table)

    # widths before merge so merged cells sum their parts
    section = document.sections[-1]
    text_width = int(section.page_width - section.left_margin - section.right_margin)
    em = Pt(doc_type.TYPE["table"])
    padding = Pt(12)
    room = max(0, text_width - width * padding)
    shares = _column_shares(flat, width, max(width * 2, int(room // em)))
    total = sum(shares) or width
    column_widths = [int(padding + room * share // total) for share in shares]
    column_widths[-1] += text_width - sum(column_widths)
    table.autofit = False
    for c, size in enumerate(column_widths):
        table.columns[c].width = Emu(size)
        for row in table.rows:
            row.cells[c].width = Emu(size)
    properties = table._tbl.tblPr
    table_width = properties.find(qn("w:tblW"))
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        properties.append(table_width)
    table_width.set(qn("w:w"), str(text_width * 1440 // 914400))
    table_width.set(qn("w:type"), "dxa")
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)

    head_style = _docx_style(document, "표 머리")
    cell_style = _docx_style(document, "표 본문")
    figure_style = _docx_style(document, "표 숫자")
    covered: set[tuple[int, int]] = set()
    for r, row in enumerate(grid.rows):
        column = 0
        for source in row:
            while (r, column) in covered:
                column += 1
            if column >= width:
                break
            across = min(source.colspan, width - column)
            down = min(source.rowspan, len(grid.rows) - r)
            for dr in range(down):
                for dc in range(across):
                    if (dr, dc) != (0, 0):
                        covered.add((r + dr, column + dc))
            cell = table.cell(r, column)
            if across > 1 or down > 1:
                cell = cell.merge(table.cell(r + down - 1, column + across - 1))
            lines = [_strip_inline(line) for line in source.text.split("\n")] or [""]
            cell.text = lines[0]
            for extra in lines[1:]:
                cell.add_paragraph(extra)
            right = column in numeric and across == 1
            style = head_style if r == 0 else figure_style if right else cell_style
            for paragraph in cell.paragraphs:
                paragraph.style = style
                if r == 0 and right:
                    paragraph.alignment = figure_style.paragraph_format.alignment
            if r == 0 and accent:
                # schema order: `tcBorders` before `shd`, after width/spans
                properties = cell._tc.get_or_add_tcPr()
                borders = OxmlElement("w:tcBorders")
                bottom = OxmlElement("w:bottom")
                bottom.set(qn("w:val"), "single")
                bottom.set(qn("w:sz"), "10")
                bottom.set(qn("w:color"), accent.lstrip("#").upper())
                borders.append(bottom)
                properties.append(borders)
                shade = OxmlElement("w:shd")
                shade.set(qn("w:val"), "clear")
                shade.set(qn("w:fill"), "F2F2F2")
                properties.append(shade)
            column += across
    document.add_paragraph(style=_BODY).paragraph_format.space_after = Pt(4)


#: separator notes (ids -1, 0) required, or Word repairs the file
_DOCX_FOOTNOTE_HEAD = (
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:pPr><w:spacing w:after="0"'
    ' w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:pPr><w:spacing'
    ' w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:r>'
    "<w:continuationSeparator/></w:r></w:p></w:footnote>"
)


_URL = re.compile(r"https?://[^\s<>()\[\]「」]{1,500}[^\s<>()\[\]「」.,;:]")


def _linked_runs(paragraph, text: str) -> None:
    """`text` as runs with live hyperlinks."""
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    cursor = 0
    for found in _URL.finditer(text):
        if before := text[cursor : found.start()]:
            paragraph.add_run(before)
        url = found.group(0)
        rel = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rel)
        run = OxmlElement("w:r")
        props = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "1F4E99")
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        props.append(color)
        props.append(underline)
        run.append(props)
        written = OxmlElement("w:t")
        written.text = url
        written.set(qn("xml:space"), "preserve")
        run.append(written)
        link.append(run)
        paragraph._p.append(link)
        cursor = found.end()
    if rest := text[cursor:]:
        paragraph.add_run(rest)


class _Footnotes:
    """Hand-written footnotes part (no python-docx API); invalid XML triggers Word repair."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def write(self, paragraph, text: str, notes: dict[str, str]) -> None:
        """Prose line with real footnote references; unmatched marks dropped."""
        cursor = 0
        for found in _MARK.finditer(text):
            if before := text[cursor : found.start()]:
                _linked_runs(paragraph, before)
            if note := notes.get(found.group(1)):
                self.add(paragraph, note)
            cursor = found.end()
        if rest := text[cursor:]:
            _linked_runs(paragraph, rest)

    def add(self, paragraph, text: str) -> None:
        """One note and its prose mark."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        self.notes.append(text)
        run = paragraph.add_run()
        properties = OxmlElement("w:rPr")
        style = OxmlElement("w:rStyle")
        style.set(qn("w:val"), "FootnoteReference")
        properties.append(style)
        vertical = OxmlElement("w:vertAlign")
        vertical.set(qn("w:val"), "superscript")
        properties.append(vertical)
        run._r.append(properties)
        reference = OxmlElement("w:footnoteReference")
        # ids -1, 0 are separators
        reference.set(qn("w:id"), str(len(self.notes)))
        run._r.append(reference)

    def attach(self, document) -> None:
        """Writes and relates the part, if any notes."""
        if not self.notes:
            return
        from docx.opc.constants import RELATIONSHIP_TYPE as RT  # noqa: N814
        from docx.opc.packuri import PackURI
        from docx.opc.part import Part

        body = "".join(
            f'<w:footnote w:id="{index}"><w:p><w:pPr>'
            '<w:pStyle w:val="FootnoteText"/></w:pPr><w:r><w:rPr>'
            '<w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>'
            f'<w:r><w:t xml:space="preserve"> {_escape(note)}</w:t></w:r>'
            "</w:p></w:footnote>"
            for index, note in enumerate(self.notes, start=1)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            + _DOCX_FOOTNOTE_HEAD
            + body
            + "</w:footnotes>"
        )
        part = Part(
            PackURI("/word/footnotes.xml"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
            xml.encode("utf-8"),
            document.part.package,
        )
        document.part.relate_to(part, RT.FOOTNOTES)


_DOCX_CHART_DRAWING = (
    '<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<wp:inline distT="0" distB="0" distL="0" distR="0">'
    '<wp:extent cx="{cx}" cy="{cy}"/>'
    '<wp:docPr id="{n}" name="Chart {n}"/>'
    "<a:graphic><a:graphicData"
    ' uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
    '<c:chart r:id="{rid}"/>'
    "</a:graphicData></a:graphic>"
    "</wp:inline></w:drawing>"
)


def _docx_chart(document, chart: dict, index: int, style: dict | None) -> bool:
    """Native Word chart, or `False`; part, rels and content types must agree."""
    from docx.opc.constants import CONTENT_TYPE as CT
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml import parse_xml
    from pptx.dml.color import RGBColor

    built = chartkit.part(
        chart["kind"],
        chart["categories"],
        chart["series"],
        unit=chart.get("unit") or "",
        accent=RGBColor.from_string((style["accent"] if style else "#5b5bd6").lstrip("#").upper()),
        muted=RGBColor.from_string((style["muted"] if style else "#666666").lstrip("#").upper()),
        faces=chartkit.FACES["serif" if style and style.get("font") == "serif" else "gothic"],
    )
    if built is None:
        return False
    chart_xml, workbook = built

    try:
        package = document.part.package
        chart_part = Part(
            PackURI(f"/word/charts/chart{index}.xml"), CT.DML_CHART, chart_xml, package
        )
        # workbook rel needed for editable data
        chart_part.relate_to(
            Part(
                PackURI(f"/word/embeddings/chart{index}.xlsx"),
                CT.SML_SHEET,
                workbook,
                package,
            ),
            RT.PACKAGE,
        )
        relationship_id = document.part.relate_to(chart_part, RT.CHART)
    except Exception as exc:  # noqa: BLE001 — a chart is not worth a failed export
        log.warning("could not attach a chart to the docx: %s", exc)
        return False

    paragraph = document.add_paragraph()
    paragraph.alignment = 1
    run = paragraph.add_run()
    run._r.append(
        parse_xml(
            _DOCX_CHART_DRAWING.format(
                cx=int(150 * 36000),
                cy=int(85 * 36000),
                n=index,
                rid=relationship_id,
            )
        )
    )
    return True


def _docx_steps(document, steps: list[tuple[str, str]]) -> None:
    """Numbered procedure as a two-column table; literal numbers."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    if not steps:
        return
    table = document.add_table(rows=len(steps), cols=2)
    table.style = "Table Grid"
    for row, (name, detail) in enumerate(steps):
        left = table.cell(row, 0).paragraphs[0]
        left.alignment = WD_ALIGN_PARAGRAPH.CENTER
        number = left.add_run(str(row + 1))
        number.bold = True
        number.font.size = Pt(12)

        right = table.cell(row, 1).paragraphs[0]
        title = right.add_run(name)
        title.bold = True
        if detail:
            right.add_run(f"  {detail}")
        table.rows[row].cells[0].width = Mm(12)
    document.add_paragraph().paragraph_format.space_after = Pt(4)


def _docx_kpi(document, figures: list[tuple[str, str]]) -> None:
    """Figure strip as a borderless Word table."""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    if not figures:
        return
    table = document.add_table(rows=2, cols=len(figures))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for column, (value, label) in enumerate(figures):
        top = table.cell(0, column).paragraphs[0]
        top.alignment = WD_ALIGN_PARAGRAPH.CENTER
        top.paragraph_format.keep_with_next = True
        run = top.add_run(value)
        run.bold = True
        run.font.size = Pt(doc_type.TYPE["kpi"])

        bottom = table.cell(1, column).paragraphs[0]
        bottom.alignment = WD_ALIGN_PARAGRAPH.CENTER
        note = bottom.add_run(label)
        note.font.size = Pt(doc_type.TYPE["kpiLabel"])
        note.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    document.add_paragraph().paragraph_format.space_after = Pt(4)


def _docx_cards(document, cards: list[tuple[str, list[str]]]) -> None:
    """Card grid as a two-column Word table."""
    from docx.shared import Pt

    if not cards:
        return
    rows = (len(cards) + 1) // 2
    table = document.add_table(rows=rows, cols=2)
    table.style = "Table Grid"
    for index, (title, items) in enumerate(cards):
        cell = table.cell(index // 2, index % 2)
        head = cell.paragraphs[0]
        head.paragraph_format.space_after = Pt(3)
        run = head.add_run(title)
        run.bold = True
        run.font.size = Pt(11)
        for item in items:
            line = cell.add_paragraph()
            line.paragraph_format.space_after = Pt(2)
            written = line.add_run(f"· {item}")
            written.font.size = Pt(9.5)
    document.add_paragraph().paragraph_format.space_after = Pt(4)


def _docx_core_box(document, callout: tuple[str, list[str]], space: dict[str, float]) -> None:
    """1쪽 보고 핵심 lines in a thin black box (`_pdf_core_box`)."""
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    title, lines = callout
    if not title:
        return
    _docx_gap(document, space["callout"])
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    _docx_box(table, 6)
    table.autofit = False
    label, body = table.cell(0, 0), table.cell(0, 1)
    # widths on grid and cells: LibreOffice reads one, Word the other
    table.columns[0].width, table.columns[1].width = Cm(2.2), Cm(14.8)
    label.width, body.width = Cm(2.2), Cm(14.8)
    label.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    head = label.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    head.paragraph_format.space_after = Pt(0)
    head.add_run(title).bold = True
    for index, line in enumerate(lines or [""]):
        written = body.paragraphs[0] if index == 0 else body.add_paragraph()
        written.paragraph_format.space_after = Pt(0)
        written.add_run(line)
    _docx_gap(document, space["callout"])


def _docx_gap(document, points: float) -> None:
    """Empty paragraph `points` tall."""
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    gap = document.add_paragraph(style=_BODY)
    form = gap.paragraph_format
    form.space_before = form.space_after = Pt(0)
    form.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    form.line_spacing = Pt(max(1.0, points))


def _docx_box(table, size: int) -> None:
    """Black rules round every cell."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    properties = table._tbl.tblPr
    borders = properties.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        properties.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        found = borders.find(qn(f"w:{edge}"))
        if found is None:
            found = OxmlElement(f"w:{edge}")
            borders.append(found)
        found.set(qn("w:val"), "single")
        found.set(qn("w:sz"), str(size))
        found.set(qn("w:space"), "0")
        found.set(qn("w:color"), "000000")


def _docx_callout(document, callout: tuple[str, list[str]]) -> None:
    """Boxed line as a one-cell table."""
    from docx.shared import Pt

    title, lines = callout
    if not title:
        return
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.cell(0, 0)
    head = cell.paragraphs[0]
    head.paragraph_format.space_after = Pt(2)
    run = head.add_run(title)
    run.bold = True
    for line in lines:
        written = cell.add_paragraph()
        written.paragraph_format.space_after = Pt(2)
        written.add_run(line).font.size = Pt(9.5)
    document.add_paragraph().paragraph_format.space_after = Pt(4)


def _diagram_picture(section: dict, drawn: dict) -> dict | None:
    """Browser-stored diagram raster, or `None`."""
    store = section.get("diagrams")
    if not isinstance(store, dict):
        return None
    decoded = pictures.decode(str(store.get(drawn.get("key")) or ""))
    if not decoded:
        return None
    mime, data = decoded
    return {"data": data, "mime": mime, "caption": ""}


def _docx_picture(document, picture: dict) -> None:
    """Centred figure with caption."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    data = picture.get("data")
    if not data:
        return
    width_pt, height_pt = _picture_size(data)
    try:
        # both dimensions, else Word lets portrait pictures fill the page
        document.add_picture(io.BytesIO(data), width=Pt(width_pt), height=Pt(height_pt))
    except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
        log.warning("could not place a picture in the docx: %s", exc)
        return
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption = str(picture.get("caption") or "")
    if caption:
        paragraph = document.add_paragraph(caption)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(8)
        for run in paragraph.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


# ── documents by purpose ──

_HEADS = ("cover", "header", "memo", "press", "paper", "draft")

#: cover page geometry, matching `docFormats.ts` TITLE_BLOCK_CSS
_COVER_TOP = 0.30
_COVER_GAP_MM = 16.0
_COVER_TABLE = 0.64
_COVER_LABEL_COLUMN = 0.28


def _title_block(block: Any) -> dict | None:
    """Cleaned `titleBlock`, or None."""
    if not isinstance(block, dict) or not block:
        return None
    fields: list[tuple[str, str]] = []
    for pair in block.get("fields") or []:
        if not isinstance(pair, (list, tuple)) or not pair:
            continue
        label = " ".join(str(pair[0] or "").split())
        value = " ".join(str((pair[1] if len(pair) > 1 else "") or "").split())
        if label:
            fields.append((label, value))
    head = str(block.get("head") or "header")
    keywords = block.get("keywords")
    return {
        "head": head if head in _HEADS else "header",
        "label": " ".join(str(block.get("label") or "").split()),
        "numbering": str(block.get("numbering") or "none"),
        "fields": fields,
        "subtitle": " ".join(str(block.get("subtitle") or "").split()),
        # None: no abstract; "": abstract still to write
        "abstract": str(block.get("abstract") or "").strip() if "abstract" in block else None,
        "keywords": (
            [" ".join(str(word).split()) for word in keywords if str(word).strip()]
            if isinstance(keywords, list)
            else None
        ),
        "lists": "gaejo" if block.get("lists") == "gaejo" else "",
        "closing": block.get("closing") is True,
        "format": str(block.get("format") or ""),
    }


def _draft_grid(fields: list[tuple[str, str]]) -> richtext.Grid:
    """간이기안문 head grid: register fields left, 결재란 right."""
    register = [pair for pair in fields if pair[0] not in doc_formats.APPROVERS]
    signers = dict(pair for pair in fields if pair[0] in doc_formats.APPROVERS)
    height = max(len(register), 3)
    rows: list[list[richtext.Cell]] = []
    for index in range(height):
        row: list[richtext.Cell] = []
        label, value = register[index] if index < len(register) else ("", "")
        row += [richtext.Cell(label), richtext.Cell(_shown(value, label) if label else "")]
        if index == 0:
            row += [richtext.Cell(name) for name in doc_formats.APPROVERS]
        elif index == 1:
            row += [richtext.Cell("", rowspan=height - 2) for _ in doc_formats.APPROVERS]
        elif index == height - 1:
            row += [richtext.Cell(_shown(signers.get(name, ""), name)) for name in doc_formats.APPROVERS]
        rows.append(row)
    return richtext.Grid(rows=rows)


def _shown(value: str, label: str = "") -> str:
    """Value or its named blank 「[작성자]」."""
    return value or doc_formats.blank(label)


def _paper_line(label: str, value: str) -> str:
    """Author/affiliation line under a paper title."""
    return value if value and label in ("저자", "소속") else f"{label}: {_shown(value, label)}"


def _dateline(fields: list[tuple[str, str]]) -> str:
    """Press release 배포일 · 보도 시점 · 문의처 row."""
    return "  |  ".join(f"{label} {_shown(value, label)}" for label, value in fields)


class _HeadingNumbers:
    """Running heading numbers (`doc_formats.number_heading`); reference list unnumbered."""

    def __init__(self, block: dict | None) -> None:
        self.block = block
        self.style = block["numbering"] if block else "none"
        self.counters = [0, 0, 0]

    def section(self, section: dict) -> str:
        if section.get("id") == "references" or _is_attachment(section, self.block):
            return ""
        return self.next(2 if section.get("level") == 2 else 1)

    def next(self, level: int) -> str:
        if self.style == "none":
            return ""
        self.counters[level - 1] += 1
        for deeper in range(level, 3):
            self.counters[deeper] = 0
        try:
            number = doc_formats.number_heading(self.style, level, self.counters)
        except IndexError:  # past XII: plain digits
            number = f"{self.counters[level - 1]}."
        return f"{number} " if number else ""


def _docx_title_block(
    document, title: str, block: dict, visualise_heading, accent: str | None = None
) -> None:
    """Document head by purpose (cover, field table, memo, press, paper)."""
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    grey = RGBColor(0x99, 0x99, 0x99)
    muted = RGBColor(0x66, 0x66, 0x66)
    head = block["head"]
    centred = head in ("cover", "paper")

    def value_run(paragraph, value: str, label: str = "", **kwargs):
        run = paragraph.add_run(_shown(value, label))
        if not value or placeholders.is_blank(value):
            run.font.color.rgb = grey
        for key, setting in kwargs.items():
            setattr(run, key, setting)
        return run

    def line(
        text: str = "",
        *,
        size: float = 0,
        bold: bool = False,
        colour=None,
        before: float = 0,
        after: float = 4,
        center: bool = centred,
    ):
        paragraph = document.add_paragraph(style=_BODY)
        if text:
            run = paragraph.add_run(text)
            run.bold = bold
            if size:
                run.font.size = Pt(size)
            if colour is not None:
                run.font.color.rgb = colour
        paragraph.paragraph_format.space_before = Pt(before)
        paragraph.paragraph_format.space_after = Pt(after)
        if center:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        return paragraph

    def heading(prefix: str = "") -> None:
        paragraph = document.add_heading("", level=0)
        if prefix:
            paragraph.add_run(prefix)
        paragraph.add_run(title)
        visualise_heading(paragraph, 0)
        if prefix:
            paragraph.runs[0].font.size = Pt(doc_type.TYPE["h1"])
        if centred:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def table(pairs: list[tuple[str, str]], per_row: int, widths: tuple[float, float]) -> None:
        if not pairs:
            return
        rows = -(-len(pairs) // per_row)
        grid = document.add_table(rows=rows, cols=per_row * 2)
        try:
            grid.style = "Table Grid"
        except Exception:  # noqa: BLE001 — a 서식 file without the style keeps its own
            pass
        _docx_cell_margins(grid)
        if head == "cover":
            grid.alignment = WD_TABLE_ALIGNMENT.CENTER
        for index, (label, value) in enumerate(pairs):
            row, column = divmod(index, per_row)
            label_cell = grid.cell(row, column * 2)
            value_cell = grid.cell(row, column * 2 + 1)
            label_cell.width, value_cell.width = Cm(widths[0]), Cm(widths[1])
            label_run = label_cell.paragraphs[0].add_run(label)
            label_run.bold = True
            value_run(value_cell.paragraphs[0], value, label)

    fields = block["fields"]
    if space := doc_type.space_for(block.get("format")):
        # 1쪽 보고: boxed title, head fields right-aligned below
        scale = doc_type.type_for(block.get("format"))
        frame = document.add_table(rows=1, cols=1)
        frame.style = "Table Grid"
        _docx_box(frame, 12)
        boxed = frame.cell(0, 0).paragraphs[0]
        boxed.alignment = WD_ALIGN_PARAGRAPH.CENTER
        boxed.paragraph_format.space_before = Pt(3)
        boxed.paragraph_format.space_after = Pt(3)
        run = boxed.add_run(title)
        run.bold = True
        run.font.size = Pt(scale["title"])
        _docx_heading_face(run)
        if fields:
            row = line(size=scale["small"], before=3, after=space["head"], center=False)
            row.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            for index, (label, value) in enumerate(fields):
                if index:
                    row.add_run("  ·  ").font.size = Pt(scale["small"])
                key = row.add_run(f"{label} ")
                key.bold = True
                key.font.size = Pt(scale["small"])
                value_run(row, value, label).font.size = Pt(scale["small"])
        else:
            line(after=space["head"])
        return
    if head == "cover":
        _docx_cover(document, title, block, visualise_heading, accent, line, table)
    elif head == "memo":
        table(fields, 1, (3.5, 12.5))
        line(after=2)
        heading("제목  ")
    elif head == "draft":
        _docx_draft_grid(document, fields, value_run)
        line(after=2)
        heading("제목  ")
    elif head == "press":
        line("보도자료", size=doc_type.TYPE["h1"], bold=True, after=6)
        heading()
        if block["subtitle"]:
            line(block["subtitle"], size=doc_type.TYPE["h1"], colour=muted, after=8)
        dateline = line(after=12)
        for index, (label, value) in enumerate(fields):
            if index:
                dateline.add_run("  |  ").font.color.rgb = grey
            dateline.add_run(f"{label} ").bold = True
            value_run(dateline, value, label)
    elif head == "paper":
        heading()
        if block["subtitle"]:
            line(block["subtitle"], size=doc_type.TYPE["h1"], colour=muted)
        for label, value in fields:
            paragraph = line(after=2)
            if value and label in ("저자", "소속"):
                paragraph.add_run(value)
            else:
                paragraph.add_run(f"{label}: ")
                value_run(paragraph, value, label)
        if block["abstract"] is not None:
            line("초록", bold=True, before=12, after=2, center=False)
            value_run(line(after=6, center=False), block["abstract"], "초록")
        if block["keywords"] is not None:
            paragraph = line(after=10, center=False)
            paragraph.add_run("핵심어: ").bold = True
            value_run(paragraph, ", ".join(block["keywords"]), "핵심어")
    else:  # header
        if block["label"]:
            line(block["label"], size=doc_type.TYPE["caption"], colour=muted, after=2)
        heading()
        if block["subtitle"]:
            line(block["subtitle"], size=doc_type.TYPE["h2"], colour=muted, after=6)
        table(fields, 2, (2.6, 5.4))
        line(after=4)


#: `w:pPr` children after `w:pBdr`, in schema order
_PPR_AFTER_BORDERS = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


def _docx_put_borders(properties, borders) -> None:
    """Inserts `borders` at its schema place."""
    properties.insert_element_before(borders, *_PPR_AFTER_BORDERS)


def _docx_cover(document, title: str, block: dict, visualise_heading, accent, line, table) -> None:
    """Cover page: accent bar, label and title on the upper third, centred head table, page break."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Emu, Pt, RGBColor

    page = document.sections[-1]
    top = page.top_margin if page.top_margin is not None else Emu(int(20 * 36000))
    width = (page.page_width or Emu(int(210 * 36000))) - (page.left_margin or 0) - (
        page.right_margin or 0
    )

    bar = line(after=0, center=False)
    borders = OxmlElement("w:pBdr")
    edge = OxmlElement("w:top")
    edge.set(qn("w:val"), "single")
    edge.set(qn("w:sz"), "24")  # 3 pt
    edge.set(qn("w:space"), "0")
    edge.set(qn("w:color"), (accent or "#333333").lstrip("#").upper())
    borders.append(edge)
    _docx_put_borders(bar._p.get_or_add_pPr(), borders)
    bar.paragraph_format.line_spacing = Pt(2)
    room = max(0.0, _COVER_TOP * 297 / 25.4 * 72 - Emu(top).pt - 2)
    if block["label"]:
        line(block["label"], size=doc_type.TYPE["coverLabel"], bold=True,
             colour=RGBColor(0x77, 0x77, 0x77), before=room, after=10)
    paragraph = document.add_heading("", level=0)
    paragraph.add_run(title)
    visualise_heading(paragraph, 0)
    # bar only, no title rule (also cleared in the Title style)
    properties = paragraph._p.get_or_add_pPr()
    for found in properties.findall(qn("w:pBdr")):
        properties.remove(found)
    clear = OxmlElement("w:pBdr")
    for side in ("top", "bottom"):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:val"), "nil")
        clear.append(edge)
    _docx_put_borders(properties, clear)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(0)
    if not block["label"]:
        paragraph.paragraph_format.space_before = Pt(room)
    for run in paragraph.runs:
        run.font.size = Pt(doc_type.TYPE["cover"])
    if block["subtitle"]:
        line(block["subtitle"], size=doc_type.TYPE["h1"], colour=RGBColor(0x66, 0x66, 0x66),
             before=8, after=0)
    line(after=0).paragraph_format.space_before = Pt(_COVER_GAP_MM * 72 / 25.4 - 12)
    across = width * _COVER_TABLE / 360000  # cm
    table(block["fields"], 1, (across * _COVER_LABEL_COLUMN, across * (1 - _COVER_LABEL_COLUMN)))
    if block["fields"] and document.tables:
        grid = document.tables[-1]
        grid.autofit = False
        twips = [round(across * share * 567) for share in (_COVER_LABEL_COLUMN, 1 - _COVER_LABEL_COLUMN)]
        for column, size in zip(grid._tbl.tblGrid.findall(qn("w:gridCol")), twips, strict=False):
            column.set(qn("w:w"), str(size))
        wide = grid._tbl.tblPr.find(qn("w:tblW"))
        if wide is None:
            wide = OxmlElement("w:tblW")
            grid._tbl.tblPr.append(wide)
        wide.set(qn("w:w"), str(sum(twips)))
        wide.set(qn("w:type"), "dxa")
        for row in grid.rows:
            shade = OxmlElement("w:shd")
            shade.set(qn("w:val"), "clear")
            shade.set(qn("w:fill"), "F2F2F2")
            row.cells[0]._tc.get_or_add_tcPr().append(shade)
    document.add_page_break()


def _docx_draft_grid(document, fields: list[tuple[str, str]], value_run) -> None:
    """`_draft_grid` as a Word table."""
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm

    grid = _draft_grid(fields)
    flat = grid.flat()
    height, width = len(flat), grid.width
    table = document.add_table(rows=height, cols=width)
    try:
        table.style = "Table Grid"
    except Exception:  # noqa: BLE001 — a 서식 file without the style keeps its own
        pass
    _docx_cell_margins(table)
    for column, size in enumerate((2.6, 3.6, 3.0, 3.0, 3.0)[:width]):
        for row in table.rows:
            row.cells[column].width = Cm(size)
    labels = {label for label, _ in fields}
    for r, cells in enumerate(flat):
        for c, text in enumerate(cells):
            paragraph = table.cell(r, c).paragraphs[0]
            if c >= 2:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if not text:
                continue
            if text in labels and (c == 0 or r == 0):
                paragraph.add_run(text).bold = True
            else:
                value_run(paragraph, text)
    if height > 2:
        for c in range(2, width):
            table.cell(1, c).merge(table.cell(height - 2, c))
    table.rows[1].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    table.rows[1].height = Cm(1.0)


_BODY = "Body Text"


def to_docx(
    title: str,
    sections: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    template: str = "",
    page_settings: dict | None = None,
    title_block: dict | None = None,
    body_from_design: bool = False,
) -> bytes:
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor

    style = design.normalise_tokens(tokens) if tokens else None
    head_block = _title_block(title_block)
    sections = _drawn_sections(sections, head_block)
    fmt = _format_of(head_block)
    space = doc_type.space_for(fmt)
    #: colour only (a run typeface would override the template); none for black memos
    accent = (
        RGBColor.from_string(style["accent"].lstrip("#").upper())
        if style and space is None
        else None
    )

    def recolour(heading) -> None:
        if accent is None:
            return
        for run in heading.runs:
            run.font.color.rgb = accent

    # 서식 Word file as template; unreadable falls back to defaults
    document = None
    if template:
        try:
            document = Document(template)
        except Exception as exc:  # noqa: BLE001 — a plain export beats none
            log.warning("docx template unreadable (%s): %s", template, exc)
    document_is_plain = document is None
    if document is None:
        document = Document()
        _page_setup(document, _format_page(page_settings, title_block))
    elif page_settings:
        _page_setup(document, _format_page(page_settings, title_block))
    elif own := doc_type.margins_for(fmt):
        _docx_margins(document, own)
    visual_style = (style or {}).get("visualStyle") or "editorial"

    def visualise_heading(paragraph, level: int) -> None:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        if visual_style == "editorial":
            recolour(paragraph)
            if level == 0 and accent is not None:
                properties = paragraph._p.get_or_add_pPr()
                borders = properties.find(qn("w:pBdr"))
                if borders is None:
                    borders = OxmlElement("w:pBdr")
                    properties.append(borders)
                bottom = OxmlElement("w:bottom")
                bottom.set(qn("w:val"), "single")
                bottom.set(qn("w:sz"), "10")
                bottom.set(qn("w:space"), "6")
                bottom.set(qn("w:color"), (style or {})["accent"].lstrip("#").upper())
                borders.append(bottom)
            return

        for run in paragraph.runs:
            if visual_style == "poster":
                run.font.size = Pt(doc_type.TYPE["title" if level == 0 else "h1"])
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF) if level == 0 else accent
                run.font.bold = True
            else:
                run.font.size = Pt(doc_type.TYPE["title" if level == 0 else "h1"])
                run.font.color.rgb = (
                    RGBColor(0x1A, 0x1A, 0x1A) if level == 0 else RGBColor(0x66, 0x66, 0x66)
                )
                run.font.bold = level == 0
        if visual_style == "poster" and level == 0:
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), (style or {}).get("accent", "#5b5bd6").lstrip("#"))
            paragraph._p.get_or_add_pPr().append(shade)

    numbers = _HeadingNumbers(head_block)
    if head_block:
        _docx_title_block(
            document, title, head_block, visualise_heading,
            accent=style["accent"] if style else None,
        )
    else:
        title_heading = document.add_heading(title, level=0)
        visualise_heading(title_heading, 0)
    if _wants_toc(sections):
        _table_of_contents(document)
        _update_fields_on_open(document)
    if document_is_plain:
        _korean_fonts(document)
    elif body_from_design:
        _docx_body_face(document, serif=bool(style and style.get("font") == "serif"))
    # document scale applies to 서식 files too
    _docx_type_scale(document, fmt)

    footnotes = _Footnotes()
    charts = 0
    closing_at = _last_body(sections)
    for at, section in enumerate(sections):
        number = numbers.section(section)
        # no empty heading for a heading-less body
        if not _is_attachment(section, head_block) and str(section.get("heading") or "").strip():
            section_heading = document.add_heading(
                f"{number}{section.get('heading') or ''}" if section.get("heading") else "",
                level=1,
            )
            visualise_heading(section_heading, 1)
        lines = _section_lines(section, head_block, at == closing_at, "  ")
        formatted = list(section.get("_formatting") or [])
        format_cursor = 0
        #: notes read ahead, written after the citing sentence
        notes = {mark: str(body) for kind, body, mark, _ in lines if kind == "note"}
        cited: set[str] = set()
        for kind, text, marker, depth in lines:
            if kind == "pagebreak":
                document.add_page_break()
                continue
            if kind == "table":
                _docx_table(document, text, (style or {}).get("accent", ""))
                continue
            if kind == "image":
                _docx_picture(document, text)
                continue
            if kind == "kpi":
                _docx_kpi(document, text)
                continue
            if kind == "steps":
                _docx_steps(document, text)
                continue
            if kind == "cards":
                _docx_cards(document, text)
                continue
            if kind == "callout":
                if space is not None:
                    _docx_core_box(document, text, space)
                else:
                    _docx_callout(document, text)
                continue
            if kind == "chart":
                charts += 1
                if not _docx_chart(document, text, charts, style):
                    _docx_table(document, _chart_as_rows(text), (style or {}).get("accent", ""))
                continue
            if kind == "diagram":
                if picture := _diagram_picture(section, text):
                    _docx_picture(document, picture)
                else:
                    note = document.add_paragraph(
                        "[다이어그램 — 화면에서 한 번 열면 그림으로 저장됩니다]", style=_BODY
                    )
                    note.paragraph_format.space_after = Pt(6)
                continue
            clean = _strip_inline(text)
            if kind == "note":
                # uncited notes as prose
                if marker not in cited:
                    document.add_paragraph(clean, style=_BODY)
                continue
            block, format_cursor = _next_format(formatted, clean, format_cursor)
            if kind == "heading":
                paragraph = document.add_heading("", level=2)
                if number := numbers.next(2):
                    paragraph.add_run(number)
                if block:
                    _docx_styled(paragraph, block, clean)
                else:
                    paragraph.add_run(clean)
            elif kind == "bullet":
                nested = _docx_style(
                    document, ("List Bullet", "List Bullet 2", "List Bullet 3")[min(depth, 2)]
                )
                paragraph = document.add_paragraph(style=nested or "List Bullet")
                if block and not _MARK.search(clean):
                    _docx_styled(paragraph, block, clean)
                else:
                    footnotes.write(paragraph, clean, notes)
                if depth and nested is None:
                    paragraph.paragraph_format.left_indent = Inches(0.25 * (depth + 1))
                cited |= _marks_in(clean)
            elif kind in ("gaejo", "remark", "attach"):
                paragraph = document.add_paragraph(style=_BODY)
                _docx_hanging(paragraph, kind, marker, depth, fmt)
                if block and not _MARK.search(clean):
                    _docx_styled(paragraph, block, clean)
                else:
                    footnotes.write(paragraph, clean, notes)
                if kind == "remark":
                    for run in paragraph.runs:
                        run.font.size = Pt(doc_type.type_for(fmt)["note"])
                cited |= _marks_in(clean)
            elif kind == "number":
                # not "List Number": numbering runs on across lists
                paragraph = document.add_paragraph(f"{marker} ", style=_BODY)
                if block and not _MARK.search(clean):
                    _docx_styled(paragraph, block, clean)
                else:
                    footnotes.write(paragraph, clean, notes)
                paragraph.paragraph_format.left_indent = Inches(0.25 * (depth + 1))
                paragraph.paragraph_format.space_after = Pt(3)
                cited |= _marks_in(clean)
            else:
                paragraph = document.add_paragraph(style=_BODY)
                if block and not _MARK.search(clean):
                    _docx_styled(paragraph, block, clean)
                else:
                    footnotes.write(paragraph, clean, notes)
                paragraph.paragraph_format.space_after = Pt(6)
                cited |= _marks_in(clean)
        for picture in section.get("images") or []:
            _docx_picture(document, picture)

    footnotes.attach(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _docx_hanging(
    paragraph, kind: str, marker: str, depth: int, fmt: str | None = None
) -> None:
    """개조식 item, ※ note or 붙임 line: marker, tab, hanging text."""
    from docx.shared import Pt

    scale, space = doc_type.type_for(fmt), doc_type.space_for(fmt)
    em = scale["body"]
    start, hang = _hang(kind, depth)
    form = paragraph.paragraph_format
    form.left_indent = Pt(em * (start + hang))
    form.first_line_indent = Pt(-em * hang)
    form.space_after = Pt(space["item"] if space is not None else 3)
    if space is not None and kind == "attach" and not depth:
        form.space_before = Pt(em / 2)
    if marker:
        form.tab_stops.add_tab_stop(Pt(em * (start + hang)))
        run = paragraph.add_run(f"{marker}\t")
        if kind == "remark":
            run.font.size = Pt(scale["note"])


def _at_depth(style, depth: int):
    """List style indented by `depth`; cloned since styles are shared."""
    if not depth:
        return style
    return ParagraphStyle(
        f"{style.name}-{depth}",
        parent=style,
        leftIndent=style.leftIndent + depth * (_INDENT / 72 * 25.4) * mm,
        bulletIndent=style.bulletIndent + depth * (_INDENT / 72 * 25.4) * mm,
    )


def _table_as_lines(rows: richtext.Grid | list[list[str]]) -> list[str]:
    """Table as `기준 · A사 · B사` lines, for the `.hwpx` fallback."""
    kept = [row for row in _as_grid(rows).flat() if any(cell.strip() for cell in row)]
    return [" · ".join(_strip_inline(cell) for cell in row) for row in kept]


def _pdf_chart(chart: dict, style: dict | None):
    """Chart as a reportlab drawing; value axis from zero."""
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.legends import Legend
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.graphics.widgets.markers import makeMarker

    accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    muted = HexColor(style["muted"]) if style else HexColor("#666666")
    korean = fonts.korean(style["font"] if style else "serif")
    width, height = 170 * mm, 80 * mm

    drawing = Drawing(width, height)
    plot = (HorizontalLineChart if chart["kind"] == "line" else VerticalBarChart)()
    plot.x, plot.y = 22 * mm, 14 * mm
    plot.width, plot.height = width - 30 * mm, height - 24 * mm
    plot.data = [list(values) for _name, values in chart["series"]]
    plot.categoryAxis.categoryNames = list(chart["categories"])
    plot.categoryAxis.labels.fontName = korean
    plot.categoryAxis.labels.fontSize = 8
    plot.categoryAxis.labels.fillColor = muted
    plot.valueAxis.valueMin = 0
    plot.valueAxis.labels.fontName = korean
    plot.valueAxis.labels.fontSize = 8
    plot.valueAxis.labels.fillColor = muted

    shades = [accent, _lighter(accent, 0.55)]
    for index in range(len(plot.data)):
        colour = shades[index % len(shades)]
        if chart["kind"] == "line":
            plot.lines[index].strokeColor = colour
            plot.lines[index].strokeWidth = 1.6
            plot.lines[index].symbol = makeMarker("FilledCircle")
            plot.lines[index].symbol.fillColor = colour
            plot.lines[index].symbol.strokeColor = colour
            plot.lines[index].symbol.size = 4
        else:
            plot.bars[index].fillColor = colour
            plot.bars[index].strokeColor = None
    drawing.add(plot)

    if unit := chart.get("unit"):
        drawing.add(String(4, height - 12, unit, fontName=korean, fontSize=8, fillColor=muted))
    if len(chart["series"]) > 1:
        legend = Legend()
        legend.x, legend.y = 22 * mm, 4 * mm
        legend.alignment = "right"
        legend.fontName, legend.fontSize = korean, 8
        legend.dxTextSpace, legend.deltax = 4, 60
        legend.columnMaximum = 1
        legend.colorNamePairs = [
            (shades[index % len(shades)], name or "값")
            for index, (name, _values) in enumerate(chart["series"])
        ]
        drawing.add(legend)
    return drawing


def _lighter(colour, amount: float):
    """Accent mixed toward white."""
    from reportlab.lib.colors import Color

    return Color(
        colour.red + (1 - colour.red) * amount,
        colour.green + (1 - colour.green) * amount,
        colour.blue + (1 - colour.blue) * amount,
    )


def _pdf_cards(
    cards: list[tuple[str, list[str]]], styles: dict, style: dict | None
) -> Table | None:
    """Card grid, drawn."""
    if not cards:
        return None
    accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    hair = HexColor(style["muted"]) if style else HexColor("#666666")
    head = ParagraphStyle(
        "cardTitle",
        parent=styles["body"],
        fontSize=11,
        leading=15,
        alignment=TA_LEFT,
        textColor=accent,
        spaceAfter=3,
    )
    item = ParagraphStyle(
        "cardItem",
        parent=styles["body"],
        fontSize=9.5,
        leading=13.5,
        alignment=TA_LEFT,
        spaceAfter=1,
    )

    def cell(card: tuple[str, list[str]] | None) -> list:
        if card is None:
            return []
        title, items = card
        return [Paragraph(f"<b>{_escape(title)}</b>", head)] + [
            Paragraph(f"· {_escape(line)}", item) for line in items
        ]

    padded: list[tuple[str, list[str]] | None] = list(cards)
    if len(padded) % 2:
        padded.append(None)
    rows = [[cell(padded[i]), cell(padded[i + 1])] for i in range(0, len(padded), 2)]
    table = Table(rows, colWidths=[85 * mm, 85 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.4, hair),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, hair),
            ]
        )
    )
    return table


def _pdf_callout(callout: tuple[str, list[str]], styles: dict, style: dict | None) -> Table | None:
    """Callout box with a left bar."""
    title, lines = callout
    if not title:
        return None
    accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    head = ParagraphStyle(
        "calloutTitle",
        parent=styles["body"],
        fontSize=11,
        leading=15,
        alignment=TA_LEFT,
        textColor=accent,
        spaceAfter=2,
    )
    body = ParagraphStyle(
        "calloutBody", parent=styles["body"], fontSize=10, leading=15, alignment=TA_LEFT
    )
    inner = [Paragraph(f"<b>{_escape(title)}</b>", head)] + [
        Paragraph(_escape(line), body) for line in lines
    ]
    table = Table([[inner]], colWidths=[170 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, accent),
            ]
        )
    )
    return table


def _pdf_steps(steps: list[tuple[str, str]], styles: dict, style: dict | None) -> Table | None:
    """Procedure, drawn as a rail."""
    if not steps:
        return None
    accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    muted = HexColor(style["muted"]) if style else HexColor("#666666")
    number = ParagraphStyle(
        "stepNumber",
        parent=styles["body"],
        fontSize=12,
        leading=15,
        alignment=TA_CENTER,
        textColor=accent,
    )
    rows = [
        [
            Paragraph(str(i + 1), number),
            Paragraph(
                f"<b>{_escape(name)}</b>" + (f"  {_escape(detail)}" if detail else ""),
                styles["body"],
            ),
        ]
        for i, (name, detail) in enumerate(steps)
    ]
    table = Table(rows, colWidths=[12 * mm, 158 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (1, 0), (1, -1), 6),
                ("LINEAFTER", (0, 0), (0, -1), 0.8, accent),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, muted),
            ]
        )
    )
    return table


def _pdf_kpi(figures: list[tuple[str, str]], styles: dict, style: dict | None) -> Table | None:
    """Figure strip, drawn."""
    if not figures:
        return None
    accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    muted = HexColor(style["muted"]) if style else HexColor("#666666")
    big = ParagraphStyle(
        "kpiValue",
        parent=styles["body"],
        fontSize=doc_type.TYPE["kpi"],
        leading=doc_type.TYPE["kpi"] * 1.2,
        alignment=TA_CENTER,
        textColor=accent,
    )
    small = ParagraphStyle(
        "kpiLabel",
        parent=styles["body"],
        fontSize=doc_type.TYPE["kpiLabel"],
        leading=doc_type.TYPE["kpiLabel"] * 1.3,
        alignment=TA_CENTER,
        textColor=muted,
    )
    table = Table(
        [
            [Paragraph(_escape(value), big) for value, _ in figures],
            [Paragraph(_escape(label), small) for _, label in figures],
        ],
        colWidths=[(170 * mm) / len(figures)] * len(figures),
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 2),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 2),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, muted),
                ("LINEBELOW", (0, 1), (-1, 1), 0.6, muted),
            ]
        )
    )
    return table


def _pdf_picture(picture: dict, styles: dict) -> list:
    """Figure flowables, or `[]`."""
    data = picture.get("data")
    if not data:
        return []
    try:
        width_pt, height_pt = _picture_size(data)
        out: list = [
            Spacer(1, 3 * mm),
            RLImage(io.BytesIO(data), width=width_pt, height=height_pt),
        ]
    except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
        log.warning("could not place a picture in the pdf: %s", exc)
        return []
    if caption := str(picture.get("caption") or ""):
        out.append(Paragraph(_escape(caption), styles["caption"]))
    out.append(Spacer(1, 3 * mm))
    return out


def _pdf_table(
    rows: richtext.Grid | list[list[str]], styles: dict, accent, text_width: float = 170 * mm
) -> Table | None:
    """Drawn table with wrapping cells and spans, or `None`."""
    grid = _export_grid(rows)
    if not grid.rows:
        return None
    width = grid.width
    flat = grid.flat()
    numeric = _numeric_columns(flat)
    plain = styles.get("cell", styles["body"])
    head_style = styles.get("cell-head", plain)
    figure_style = styles.get("cell-figure", plain)
    padding = 8  # LEFTPADDING + RIGHTPADDING below
    room = max(0.0, text_width - width * padding)
    shares = _column_shares(flat, width, max(width * 2, int(room // plain.fontSize)))
    total = sum(shares) or width
    column_widths = [padding + room * share / total for share in shares]
    body = [[Paragraph("", plain) for _ in range(width)] for _ in grid.rows]
    spans: list[tuple] = []
    covered: set[tuple[int, int]] = set()
    for r, row in enumerate(grid.rows):
        column = 0
        for source in row:
            while (r, column) in covered:
                column += 1
            if column >= width:
                break
            across = min(source.colspan, width - column)
            down = min(source.rowspan, len(grid.rows) - r)
            for dr in range(down):
                for dc in range(across):
                    if (dr, dc) != (0, 0):
                        covered.add((r + dr, column + dc))
            if across > 1 or down > 1:
                spans.append(("SPAN", (column, r), (column + across - 1, r + down - 1)))
            text = "<br/>".join(_escape(_strip_inline(line)) for line in source.text.split("\n"))
            right = column in numeric and across == 1
            cell_style = head_style if r == 0 else figure_style if right else plain
            if r == 0 and right:
                cell_style = ParagraphStyle(
                    f"{head_style.name}-r", parent=head_style, alignment=TA_RIGHT
                )
            body[r][column] = Paragraph(text, cell_style)
            column += across

    table = Table(body, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                *spans,
                ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#d0d0d0")),
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f2f2f2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, 0), 1.0, accent),
            ]
        )
    )
    return table


def _fields_line(fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """개조식 보고 head as `(label, value)` pairs."""
    return [(label, _shown(value, label)) for label, value in fields]


def _pdf_core_box(callout: tuple[str, list[str]], styles: dict, width: float) -> Table | None:
    """1쪽 보고 핵심 lines in a thin box."""
    title, lines = callout
    if not title:
        return None
    label = ParagraphStyle("coreLabel", parent=styles["h1"], spaceBefore=0, spaceAfter=0)
    body = ParagraphStyle("coreBody", parent=styles["body"], spaceAfter=0)
    label_width = label.fontSize * 4
    cells = [Paragraph(_escape(line), body) for line in lines] or [Paragraph("", body)]
    table = Table(
        [[Paragraph(_escape(title), label), cells]],
        colWidths=[label_width, width - label_width],
    )
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, HexColor("#1a1a1a")),
                ("LINEAFTER", (0, 0), (0, 0), 0.4, HexColor("#1a1a1a")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _pdf_title_block(
    title: str,
    block: dict,
    styles: dict,
    bold_face: str,
    width: float = 170 * mm,
    accent=None,
    top_mm: float = 22.0,
) -> list:
    """`_docx_title_block` for reportlab; `top_mm` places a cover title."""
    head = block["head"]
    T = doc_type.type_for(block.get("format"))
    if space := doc_type.space_for(block.get("format")):
        boxed = ParagraphStyle(
            "tb-boxed", parent=styles["title"], fontName=bold_face, alignment=TA_CENTER,
            spaceBefore=0, spaceAfter=0,
        )
        frame = Table([[Paragraph(_escape(title), boxed)]], colWidths=[width])
        frame.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1.5, HexColor("#1a1a1a")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story: list = [frame]
        if block["fields"]:
            line = ParagraphStyle(
                "tb-line", parent=styles["body"], fontSize=T["small"],
                leading=T["small"] * 1.4, alignment=TA_RIGHT, spaceBefore=3, spaceAfter=0,
            )
            shown = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(
                f'<font name="{bold_face}">{_escape(label)}</font>&nbsp;'
                + (
                    _escape(value)
                    if not placeholders.is_blank(value)
                    else f'<font color="#999999">{_escape(value)}</font>'
                )
                for label, value in _fields_line(block["fields"])
            )
            story.append(Paragraph(shown, line))
        story.append(Spacer(1, space["head"]))
        return story
    centred = head in ("cover", "paper")
    align = TA_CENTER if centred else TA_LEFT

    def styled(name: str, parent: str, **options) -> ParagraphStyle:
        return ParagraphStyle(name, parent=styles[parent], alignment=align, **options)

    title_style = styled("tb-title", "title")
    small = styled("tb-small", "body", textColor=HexColor("#666666"))
    subtitle = styled(
        "tb-sub", "body", fontSize=T["h1"], leading=T["h1"] * 1.4, textColor=HexColor("#555555")
    )
    cell = ParagraphStyle("tb-cell", parent=styles["body"], spaceAfter=0)
    label_cell = ParagraphStyle("tb-label", parent=cell, fontName=bold_face)

    def value(text: str, label: str = "") -> str:
        if text and not placeholders.is_blank(text):
            return _escape(text)
        return f'<font color="#999999">{_escape(text or doc_formats.blank(label))}</font>'

    def fields_table(
        per_row: int, widths: tuple[float, float], ruled: bool = False
    ) -> Table | None:
        pairs = block["fields"]
        if not pairs:
            return None
        rows: list[list] = []
        for start in range(0, len(pairs), per_row):
            row: list = []
            for label, text in pairs[start : start + per_row]:
                row += [Paragraph(_escape(label), label_cell), Paragraph(value(text, label), cell)]
            row += [""] * (per_row * 2 - len(row))
            rows.append(row)
        table = Table(rows, colWidths=[w * mm for w in widths] * per_row)
        lines = (
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1.0, HexColor("#333333")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor("#bbbbbb")),
                ("LINEBELOW", (0, -1), (-1, -1), 1.0, HexColor("#333333")),
            ]
            if ruled
            else [("GRID", (0, 0), (-1, -1), 0.4, HexColor("#bbbbbb"))]
        )
        table.setStyle(
            TableStyle(
                [
                    *lines,
                    *[
                        ("BACKGROUND", (c, 0), (c, -1), HexColor("#f4f4f4"))
                        for c in range(0, per_row * 2, 2)
                    ],
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        if head == "cover":
            table.hAlign = "CENTER"
        return table

    story: list = []
    if head == "cover":
        # own page: accent bar, upper-third title, body from page two
        bar = 2.2
        story.append(
            HRFlowable(width="100%", thickness=bar, color=accent or HexColor("#333333"),
                       spaceBefore=0, spaceAfter=0)
        )
        frame_top = top_mm * mm + 6  # reportlab's frame keeps 6 pt inside the margin
        story.append(Spacer(1, max(0.0, _COVER_TOP * A4[1] - frame_top - bar)))
        if block["label"]:
            story.append(
                Paragraph(
                    _escape(block["label"]),
                    styled("tb-kind", "body", fontName=bold_face, fontSize=T["coverLabel"],
                           leading=T["coverLabel"] * 1.4, textColor=HexColor("#777777"),
                           spaceAfter=0),
                )
            )
            story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                _escape(title),
                styled("tb-cover", "title", fontSize=T["cover"], leading=T["cover"] * 1.3,
                       spaceBefore=0, spaceAfter=0),
            )
        )
        if block["subtitle"]:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph(_escape(block["subtitle"]), subtitle))
        story.append(Spacer(1, _COVER_GAP_MM * mm))
        across = width * _COVER_TABLE / mm
        label_mm = across * _COVER_LABEL_COLUMN
        if table := fields_table(1, (label_mm, across - label_mm)):
            story.append(table)
        story.append(PageBreak())
    elif head == "memo":
        if table := fields_table(1, (35, 135), ruled=True):
            story.append(table)
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                f'<font size="{T["h1"]}">제목</font>&nbsp;&nbsp;{_escape(title)}', title_style
            )
        )
        story.append(Spacer(1, 4 * mm))
    elif head == "draft":
        story.append(_pdf_draft_grid(block["fields"], cell, label_cell, value))
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                f'<font size="{T["h1"]}">제목</font>&nbsp;&nbsp;{_escape(title)}', title_style
            )
        )
        story.append(Spacer(1, 4 * mm))
    elif head == "press":
        story.append(
            Paragraph(
                "보도자료",
                styled("tb-press", "h1", spaceBefore=0, textColor=HexColor("#666666")),
            )
        )
        story.append(Paragraph(_escape(title), title_style))
        if block["subtitle"]:
            story.append(Paragraph(_escape(block["subtitle"]), subtitle))
        story.append(Spacer(1, 2 * mm))
        story.append(HRFlowable(width="100%", thickness=0.8, color=HexColor("#333333")))
        dateline = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(
            f'<font name="{bold_face}">{_escape(label)}</font> {value(text, label)}'
            for label, text in block["fields"]
        )
        story.append(Paragraph(dateline, small))
        story.append(HRFlowable(width="100%", thickness=0.4, color=HexColor("#bbbbbb")))
        story.append(Spacer(1, 6 * mm))
    elif head == "paper":
        story.append(Paragraph(_escape(title), title_style))
        if block["subtitle"]:
            story.append(Paragraph(_escape(block["subtitle"]), subtitle))
        for label, text in block["fields"]:
            shown = (
                _escape(text)
                if text and label in ("저자", "소속")
                else f"{_escape(label)}: {value(text, label)}"
            )
            story.append(Paragraph(shown, styled(f"tb-line-{label}", "body", spaceAfter=1)))
        story.append(Spacer(1, 5 * mm))
        if block["abstract"] is not None:
            story.append(Paragraph(f'<font name="{bold_face}">초록</font>', styles["body"]))
            story.append(Paragraph(value(block["abstract"], "초록"), styles["body"]))
        if block["keywords"] is not None:
            story.append(
                Paragraph(
                    f'<font name="{bold_face}">핵심어:</font> '
                    + value(", ".join(block["keywords"]), "핵심어"),
                    styles["body"],
                )
            )
        story.append(Spacer(1, 4 * mm))
    else:  # header
        if block["label"]:
            story.append(Paragraph(_escape(block["label"]), small))
        story.append(Paragraph(_escape(title), title_style))
        if block["subtitle"]:
            story.append(Paragraph(_escape(block["subtitle"]), subtitle))
        if table := fields_table(2, (28, 57)):
            story.append(Spacer(1, 2 * mm))
            story.append(table)
        story.append(Spacer(1, 6 * mm))
    return story


def to_pdf(
    title: str,
    sections: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    page_settings: dict | None = None,
    title_block: dict | None = None,
) -> bytes:
    # embedded Korean face (services/fonts.py); gothic unless serif design
    style = design.normalise_tokens(tokens) if tokens else None
    korean = fonts.korean(style["font"] if style else "gothic")
    bold_face = fonts.korean(style["font"] if style else "gothic", bold=True)
    head_block = _title_block(title_block)
    sections = _drawn_sections(sections, head_block)
    fmt = _format_of(head_block)
    T, L = doc_type.type_for(fmt), doc_type.leading_for(fmt)
    space = doc_type.space_for(fmt)
    heading_colour = {"textColor": HexColor(style["accent"])} if style else {}
    if space is not None:
        # black memo: ink headings in gothic
        heading_colour = {"textColor": HexColor(style["ink"] if style else "#1a1a1a")}
        bold_face = fonts.korean("gothic", bold=True)

    visual_style = (style or {}).get("visualStyle") or "editorial"
    accent_colour = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
    base = getSampleStyleSheet()
    title_size, h1_size = T["title"], T["h1"]
    title_options = (
        {
            "fontSize": title_size,
            "leading": title_size * L["title"],
            "textColor": HexColor("#ffffff"),
            "backColor": accent_colour,
            "borderPadding": 18,
        }
        if visual_style == "poster"
        else {
            "fontSize": title_size,
            "leading": title_size * L["title"],
            "textColor": HexColor(style["ink"] if style else "#1a1a1a"),
        }
        if visual_style == "minimal"
        else {"fontSize": title_size, "leading": title_size * L["title"], **heading_colour}
    )
    h1_options = (
        {
            "fontSize": h1_size,
            "leading": h1_size * L["heading"],
            "textColor": accent_colour,
            "spaceBefore": 18,
            "spaceAfter": 9,
        }
        if visual_style == "poster"
        else {
            "fontSize": h1_size,
            "leading": h1_size * L["heading"],
            "textColor": HexColor(style["muted"] if style else "#666666"),
            "spaceBefore": 16,
            "spaceAfter": 5,
        }
        if visual_style == "minimal"
        else {
            "fontSize": h1_size,
            "leading": h1_size * L["heading"],
            "spaceBefore": 14,
            "spaceAfter": 6,
            **heading_colour,
        }
    )
    styles = {
        # left-aligned, unlike reportlab's Title
        "title": ParagraphStyle(
            "t",
            parent=base["Title"],
            fontName=bold_face,
            alignment=TA_LEFT,
            **title_options,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName=bold_face,
            keepWithNext=1,
            **h1_options,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName=bold_face,
            keepWithNext=1,
            fontSize=T["h2"],
            leading=T["h2"] * L["heading"],
            spaceBefore=10,
            spaceAfter=4,
        ),
        # left-aligned: no Korean hyphenation, justified Latin gaps
        "body": ParagraphStyle(
            "b",
            parent=base["BodyText"],
            fontName=korean,
            fontSize=T["body"],
            leading=T["body"] * L["body"],
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "li",
            parent=base["BodyText"],
            fontName=korean,
            fontSize=T["body"],
            leading=T["body"] * L["body"],
            leftIndent=10 * mm,
            bulletIndent=4 * mm,
            spaceAfter=3,
        ),
        "note": ParagraphStyle(
            "note",
            parent=base["BodyText"],
            fontName=korean,
            fontSize=T["note"],
            leading=T["note"] * L["note"],
            textColor=HexColor(style["muted"]) if style else HexColor("#666666"),
            leftIndent=6 * mm,
            firstLineIndent=-6 * mm,
            spaceAfter=1,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["BodyText"],
            fontName=korean,
            fontSize=T["table"],
            leading=T["table"] * L["table"],
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "caption": ParagraphStyle(
            "cap",
            parent=base["BodyText"],
            fontName=korean,
            fontSize=T["caption"],
            leading=T["caption"] * L["note"],
            alignment=TA_CENTER,
            textColor=HexColor(style["muted"]) if style else HexColor("#666666"),
            spaceBefore=2,
            spaceAfter=2,
        ),
    }

    styles["cell-head"] = ParagraphStyle("cell-head", parent=styles["cell"], fontName=bold_face)
    styles["cell-figure"] = ParagraphStyle("cell-figure", parent=styles["cell"], alignment=TA_RIGHT)
    if space is not None:
        styles["h1"] = ParagraphStyle(
            "h1-format",
            parent=styles["h1"],
            spaceBefore=space["headingBefore"],
            spaceAfter=space["headingAfter"],
        )
    page_settings = page_settings or {}
    margins = page_settings.get("margins") if isinstance(page_settings.get("margins"), dict) else {}
    margins = {**(doc_type.margins_for(fmt) or {}), **margins}
    left_mm, right_mm = float(margins.get("left", 22)), float(margins.get("right", 22))
    text_width = A4[0] - (left_mm + right_mm) * mm - 12  # the frame's own padding

    numbers = _HeadingNumbers(head_block)
    story: list = (
        _pdf_title_block(
            title, head_block, styles, bold_face, text_width,
            accent=accent_colour, top_mm=float(margins.get("top", 22)),
        )
        if head_block
        else [Paragraph(_escape(title), styles["title"]), Spacer(1, 8 * mm)]
    )
    closing_at = _last_body(sections)
    em = T["body"]

    def hanging(kind: str, depth: int) -> ParagraphStyle:
        """개조식/※/붙임 line with a hanging marker (`_docx_hanging`)."""
        start, hang = _hang(kind, depth)
        size = T["note"] if kind == "remark" else T["body"]
        return ParagraphStyle(
            f"{kind}-{depth}",
            parent=styles["body"],
            fontSize=size,
            leading=size * (L["note"] if kind == "remark" else L["body"]),
            leftIndent=em * (start + hang),
            bulletIndent=em * start,
            bulletFontName=korean,
            bulletFontSize=size,
            spaceAfter=space["item"] if space is not None else 3,
            spaceBefore=size / 2 if space is not None and kind == "attach" and not depth else 0,
        )

    for index, section in enumerate(sections):
        if _is_attachment(section, head_block) or not str(section.get("heading") or "").strip():
            pass  # no heading: the 붙임 lines close the body, or a body with no chapters
        else:
            if index and space is None:
                story.append(Spacer(1, 4 * mm))
            number = numbers.section(section)
            story.append(
                Paragraph(
                    _escape(f"{number}{section.get('heading')}" if section.get("heading") else ""),
                    styles["h1"],
                )
            )
        notes: list[tuple[str, str]] = []
        formatted = list(section.get("_formatting") or [])
        format_cursor = 0
        # non-breaking: reportlab folds plain spaces
        for kind, text, marker, depth in _section_lines(
            section, head_block, index == closing_at, "\u00a0\u00a0"
        ):
            if kind == "pagebreak":
                story.append(PageBreak())
                continue
            if kind == "table":
                accent = HexColor(style["accent"]) if style else HexColor("#5b5bd6")
                if drawn := _pdf_table(text, styles, accent, text_width):
                    gap = Spacer(1, 2 * mm)
                    # 「표 1.」 caption moves with its table
                    if story and _is_table_caption(story[-1]):
                        story[-1].keepWithNext = 1
                        gap.keepWithNext = 1
                    story.append(gap)
                    story.append(drawn)
                    story.append(Spacer(1, 3 * mm))
                continue
            if kind == "image":
                for flowable in _pdf_picture(text, styles):
                    story.append(flowable)
                continue
            if kind == "note":
                # no reportlab footnote model: gathered under the section
                notes.append((marker, _strip_inline(text)))
                continue
            if kind == "chart":
                story.append(Spacer(1, 3 * mm))
                story.append(_pdf_chart(text, style))
                story.append(Spacer(1, 4 * mm))
                continue
            if kind == "steps":
                if drawn := _pdf_steps(text, styles, style):
                    story.append(Spacer(1, 2 * mm))
                    story.append(drawn)
                    story.append(Spacer(1, 4 * mm))
                continue
            if kind == "kpi":
                if strip := _pdf_kpi(text, styles, style):
                    strip = KeepTogether(strip)
                    story.append(Spacer(1, 3 * mm))
                    story.append(strip)
                    story.append(Spacer(1, 4 * mm))
                continue
            if kind == "cards":
                if grid := _pdf_cards(text, styles, style):
                    story.append(Spacer(1, 3 * mm))
                    story.append(grid)
                    story.append(Spacer(1, 4 * mm))
                continue
            if kind == "callout":
                if space is not None and (boxed := _pdf_core_box(text, styles, text_width)):
                    story.append(Spacer(1, space["callout"]))
                    story.append(boxed)
                    story.append(Spacer(1, space["callout"]))
                elif boxed := _pdf_callout(text, styles, style):
                    story.append(Spacer(1, 3 * mm))
                    story.append(boxed)
                    story.append(Spacer(1, 4 * mm))
                continue
            if kind == "diagram":
                if picture := _diagram_picture(section, text):
                    for flowable in _pdf_picture(picture, styles):
                        story.append(flowable)
                else:
                    story.append(
                        Paragraph(
                            _escape("[다이어그램 — 화면에서 한 번 열면 그림으로 저장됩니다]"),
                            styles["caption"],
                        )
                    )
                continue
            plain = _strip_inline(text)
            block, format_cursor = _next_format(formatted, plain, format_cursor)
            clean = _MARK.sub(
                lambda found: f"<super>{_escape(found.group(1))}</super>",
                _escape(plain),
            )
            if block and not _MARK.search(plain):
                clean = _pdf_styled(block, plain)
            if kind == "heading":
                paragraph_style = _pdf_block_style(styles["h2"], block) if block else styles["h2"]
                story.append(Paragraph(_escape(numbers.next(2)) + clean, paragraph_style))
            elif kind in ("gaejo", "remark", "attach"):
                style_for = hanging(kind, depth)
                story.append(
                    Paragraph(
                        clean,
                        _pdf_block_style(style_for, block) if block else style_for,
                        bulletText=marker or None,
                    )
                )
            elif kind in ("bullet", "number"):
                # `bulletText` hangs marker; aligns multi-digit numbers
                story.append(
                    Paragraph(
                        clean,
                        _pdf_block_style(_at_depth(styles["bullet"], depth), block)
                        if block
                        else _at_depth(styles["bullet"], depth),
                        bulletText=marker,
                    )
                )
            else:
                paragraph_style = (
                    _pdf_block_style(styles["body"], block) if block else styles["body"]
                )
                story.append(Paragraph(clean, paragraph_style))
        for picture in section.get("images") or []:
            data = picture.get("data")
            if not data:
                continue
            width, height = _picture_size(data)
            caption = str(picture.get("caption") or "")
            try:
                image = RLImage(io.BytesIO(data), width=width, height=height)
                image.hAlign = "CENTER"
                figure: list = [image]
            except Exception as exc:  # noqa: BLE001 — a bad picture is not a failed export
                log.warning("could not place a picture in the report pdf: %s", exc)
                continue
            if caption:
                figure.append(Paragraph(_escape(caption), styles["caption"]))
            story.append(Spacer(1, 3 * mm))
            story.append(KeepTogether(figure))
            story.append(Spacer(1, 3 * mm))

        if notes:
            story.append(Spacer(1, 3 * mm))
            story.append(
                HRFlowable(
                    width="35%",
                    thickness=0.5,
                    color=HexColor(style["muted"]) if style else HexColor("#666666"),
                    spaceAfter=2,
                    hAlign="LEFT",
                )
            )
            for mark, note in notes:
                story.append(
                    Paragraph(f"<super>{_escape(mark)}</super> {_escape(note)}", styles["note"])
                )

    buffer = io.BytesIO()

    def decorate(pdf, document, page: int, total: int) -> None:
        pdf.saveState()
        pdf.setFont(korean, 8)
        pdf.setFillColor(HexColor("#777777"))
        header = str(page_settings.get("header") or title)
        if header and (page > 1 or bool(page_settings.get("firstPageHeader", False))):
            pdf.drawString(document.leftMargin, A4[1] - 10 * mm, header)
        footer = str(page_settings.get("footer") or "")
        if footer:
            pdf.drawString(document.leftMargin, 9 * mm, footer)
        numbering = str(page_settings.get("pageNumbers") or "page-total")
        if numbering != "none":
            label = f"{page} / {total}" if numbering == "page-total" else str(page)
            pdf.drawRightString(A4[0] - document.rightMargin, 9 * mm, label)
        pdf.restoreState()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=left_mm * mm,
        rightMargin=right_mm * mm,
        topMargin=float(margins.get("top", 22)) * mm,
        bottomMargin=float(margins.get("bottom", 20)) * mm,
        title=title,
    )

    class NumberedCanvas(pdfcanvas.Canvas):
        """Two-pass page replay for correct `1 / N` numbering."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._page_states: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 - ReportLab API
            self._page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._page_states)
            for page, state in enumerate(self._page_states, 1):
                self.__dict__.update(state)
                decorate(self, document, page, total)
                super().showPage()
            super().save()

    document.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


_FRAME_PADDING_MM = 6 / 72 * 25.4
#: start of the 붙임 list (편람 p.52)
_ATTACH_START = re.compile(r"^\s*붙임(?:[\s\u00a0]|$)", re.M)


def pdf_pages(data: bytes, until: re.Pattern[str] | None = _ATTACH_START) -> tuple[int, int]:
    """`(total, body)` PDF pages; body ends at the first `until` match."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    if until is not None:
        for number, page in enumerate(reader.pages, 1):
            if until.search(page.extract_text() or ""):
                return total, number
    return total, total


def hwpx_pages(
    title: str,
    sections: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    page_settings: dict | None = None,
    title_block: dict | None = None,
    until: re.Pattern[str] | None = _ATTACH_START,
) -> tuple[int, int]:
    """Estimated `.hwpx` page count via `to_pdf` in the hwpx page box; approximate (Nanum vs 함초롬)."""
    settings = dict(_format_page(page_settings, title_block) or {})
    given = settings.get("margins") if isinstance(settings.get("margins"), dict) else {}
    margins = {}
    for side, fallback in (("top", 20.0), ("bottom", 15.0), ("left", 30.0), ("right", 30.0)):
        try:
            value = max(10.0, min(35.0, float(given.get(side, fallback))))
        except (TypeError, ValueError):
            value = fallback
        margins[side] = value - _FRAME_PADDING_MM
    settings["margins"] = margins
    data = to_pdf(title, sections, tokens=tokens, page_settings=settings, title_block=title_block)
    return pdf_pages(data, until)


_PICTURE_MM = 150.0
#: below this column fraction: icon, size kept
_PICTURE_GROW_FROM = 0.2
#: small pictures grown to this column fraction
_PICTURE_FLOOR = 0.75
_PICTURE_MAX_MM = 170.0

#: 96 DPI, as Hancom
_POINTS_PER_PIXEL = 72 / 96


def _picture_size(data: bytes) -> tuple[float, float]:
    """`(width, height)` in points, fitted to the column."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            pixels_wide, pixels_high = picture.size
    except Exception:  # noqa: BLE001 — an unreadable picture still gets a box
        pixels_wide, pixels_high = 480, 320
    width = max(1, pixels_wide) * _POINTS_PER_PIXEL
    height = max(1, pixels_high) * _POINTS_PER_PIXEL
    column = _PICTURE_MM * mm
    if width < column * _PICTURE_GROW_FROM:
        target = width
    else:
        target = min(column, max(width, column * _PICTURE_FLOOR))
    scale = min(target / width, _PICTURE_MAX_MM * mm / height)
    return width * scale, height * scale


def _escape(text: str) -> str:
    """reportlab Paragraph markup escape."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── HWPX (OWPML, KS X 6101): stdlib zip; Hancom rejects rather than repairs ──

_HWPX_MIMETYPE = "application/hwp+zip"

_HWPX_VERSION = """<?xml version="1.0" encoding="UTF-8"?>
<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR"
 major="5" minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.4" application="KloudChat"/>"""

_HWPX_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf">
 <ocf:rootfiles>
  <ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>
 </ocf:rootfiles>
</ocf:container>"""

_HWPX_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest">
 <odf:file-entry odf:full-path="/" odf:media-type="application/hwp+zip"/>
 <odf:file-entry odf:full-path="Contents/header.xml" odf:media-type="application/xml"/>
 <odf:file-entry odf:full-path="Contents/section0.xml" odf:media-type="application/xml"/>
</odf:manifest>"""

_HWPX_CONTENT_HPF = """<?xml version="1.0" encoding="UTF-8"?>
<hpf:package xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"
 xmlns:opf="http://www.idpf.org/2007/opf/" version="" unique-identifier="" id="">
 <opf:metadata><opf:title>{title}</opf:title></opf:metadata>
 <opf:manifest>
  <opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>
  <opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>
 </opf:manifest>
 <opf:spine><opf:itemref idref="header"/><opf:itemref idref="section0"/></opf:spine>
</hpf:package>"""

#: shapes referenced by index (order matters); `<hh:lineSpacing>` required; align as `<hh:align>` child
_HWPX_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"
 xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"
 xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" version="1.4" secCnt="1">
 <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
 <hh:refList>
{font_faces}
{border_fills}
{char_properties}
{para_properties}
  <hh:styles itemCnt="1">
   <hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="3" charPrIDRef="0" nextStyleIDRef="0" langID="1042"/>
  </hh:styles>
 </hh:refList>
</hh:head>"""

#: Hancom's bundled faces, by id: 바탕 serif body, 돋움 headings/gothic body
_HWPX_FACES = ("함초롬바탕", "함초롬돋움")
_HWPX_SERIF, _HWPX_GOTHIC = 0, 1
_HWPX_HEADING_SHAPES = (2, 3, 4)
#: every `fontRef` language slot needs a list, or Hancom substitutes
_HWPX_LANGS = ("HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER")


def _hwpx_font_faces() -> str:
    fonts_xml = "".join(
        f'    <hh:font id="{index}" face="{face}" type="TTF" isEmbedded="0"/>\n'
        for index, face in enumerate(_HWPX_FACES)
    )
    return (
        f'  <hh:fontfaces itemCnt="{len(_HWPX_LANGS)}">\n'
        + "".join(
            f'   <hh:fontface lang="{lang}" fontCnt="{len(_HWPX_FACES)}">\n{fonts_xml}   </hh:fontface>\n'
            for lang in _HWPX_LANGS
        )
        + "  </hh:fontfaces>"
    )


#: `borderFillIDRef` targets (1-based); undefined id breaks the file; between `fontfaces` and `charProperties`
_HWPX_BORDER_FILLS = """  <hh:borderFills itemCnt="6">
   <hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:topBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
   <hh:borderFill id="2" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:rightBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:topBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:bottomBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
   <hh:borderFill id="3" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:rightBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <hh:topBorder type="SOLID" width="0.1 mm" color="#BBBBBB"/>
    <!-- 굵은 아래선만. fillBrush 머리행 음영은 한컴이 무시한다. -->
    <hh:bottomBorder type="SOLID" width="0.5 mm" color="#444444"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
   <hh:borderFill id="4" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="SOLID" width="0.25 mm" color="#000000"/>
    <hh:rightBorder type="SOLID" width="0.25 mm" color="#000000"/>
    <hh:topBorder type="SOLID" width="0.25 mm" color="#000000"/>
    <hh:bottomBorder type="SOLID" width="0.25 mm" color="#000000"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
   <hh:borderFill id="5" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="SOLID" width="0.5 mm" color="#000000"/>
    <hh:rightBorder type="SOLID" width="0.5 mm" color="#000000"/>
    <hh:topBorder type="SOLID" width="0.5 mm" color="#000000"/>
    <hh:bottomBorder type="SOLID" width="0.5 mm" color="#000000"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
   <hh:borderFill id="6" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
    <hh:slash type="NONE" Crooked="0" isCounter="0"/>
    <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
    <hh:leftBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:rightBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:topBorder type="SOLID" width="1.0 mm" color="{accent}"/>
    <hh:bottomBorder type="NONE" width="0.1 mm" color="#000000"/>
    <hh:diagonal type="NONE" width="0.1 mm" color="#000000"/>
   </hh:borderFill>
  </hh:borderFills>"""
_HWPX_THIN_BOX, _HWPX_HEAVY_BOX, _HWPX_COVER_BAR = 4, 5, 6

#: text column in HWPUNIT; wider tables pushed off the page
_HWPX_TEXT_WIDTH = 59528 - 8504 - 8504

#: full-width Hangul char at body size, for column sizing
_HWPX_BODY_CHAR = doc_type.hwp("body")
#: max inline table height; taller ones anchored to split across pages
_HWPX_INLINE_TABLE = 25000
_HWPX_ROW_HEIGHT = 2000
_HWPX_CELL_MARGIN = 141


def _hwpx_char_shapes(scale: dict[str, float]) -> tuple[tuple[int, int, bool], ...]:
    """`(id, height, bold)` per base character shape at `scale`."""

    def hwp(name: str) -> int:
        return int(round(scale[name] * 100))

    return (
        (0, hwp("body"), False),  # body
        (1, hwp("body"), True),  # body bold
        (2, hwp("title"), True),  # title
        (3, hwp("h1"), True),  # h1
        (4, hwp("h2"), True),  # h2
        (5, hwp("note"), False),  # footnote
    )


_HWPX_CHAR_SHAPES = _hwpx_char_shapes(doc_type.TYPE)

#: (id, align, left indent, space-before, space-after) in HWPUNIT (1000 = 10 pt)
_HWPX_PARA_SHAPES = (
    (0, "CENTER", 0, 0, 600),  # title
    (1, "LEFT", 0, 600, 300),  # h1
    (2, "LEFT", 0, 400, 200),  # h2
    (3, "JUSTIFY", 0, 0, 150),  # body
    (4, "JUSTIFY", 1000, 0, 100),  # bullet
    (5, "CENTER", 0, 300, 100),  # figure, caption
    (6, "CENTER", 0, 0, 0),  # centred cell
    # left: justified Hangul spreads in narrow cells
    (7, "LEFT", 0, 0, 0),
    # sub-bullets appended: ids are positions, tests read by index
    (8, "JUSTIFY", 2000, 0, 100),
    (9, "JUSTIFY", 3000, 0, 100),
)

_HWPX_LINE_SPACING = 160


def _hwpx_para_shapes(space: dict[str, float] | None) -> tuple[tuple, ...]:
    """`_HWPX_PARA_SHAPES` with a format's own spacing."""
    if space is None:
        return _HWPX_PARA_SHAPES
    before, after = round(space["headingBefore"] * 100), round(space["headingAfter"] * 100)
    item = round(space["item"] * 100)
    own = {
        0: (0, "CENTER", 0, 0, 0),
        1: (1, "LEFT", 0, before, after),
        3: (3, "JUSTIFY", 0, 0, item),
        4: (4, "JUSTIFY", 1000, 0, item),
        8: (8, "JUSTIFY", 2000, 0, item),
        9: (9, "JUSTIFY", 3000, 0, item),
    }
    return tuple(own.get(shape[0], shape) for shape in _HWPX_PARA_SHAPES)


_HWPX_ACCENT_SHAPES = (2, 3)


def _hwpx_char_properties(
    accent: str | None = None,
    extras: list[tuple] | None = None,
    visual_style: str = "editorial",
    body_face: int = _HWPX_SERIF,
    base: tuple[tuple[int, int, bool], ...] = _HWPX_CHAR_SHAPES,
) -> str:
    """`charPr` per shape; optional ninth field is a face id."""
    items = []
    shapes = [
        (
            cid,
            height,
            bold,
            False,
            False,
            False,
            accent if (accent and cid in _HWPX_ACCENT_SHAPES) else "#000000",
            "none",
        )
        for cid, height, bold in base
    ]
    if visual_style == "poster":
        shapes = [
            (
                cid,
                2400 if cid == 2 else 1600 if cid == 3 else height,
                bold,
                italic,
                underline,
                strike,
                "#FFFFFF" if cid == 2 else colour,
                accent if cid == 2 and accent else shade,
            )
            for cid, height, bold, italic, underline, strike, colour, shade in shapes
        ]
    elif visual_style == "minimal":
        shapes = [
            (
                cid,
                1800 if cid == 2 else 1100 if cid == 3 else height,
                bold if cid != 3 else False,
                italic,
                underline,
                strike,
                "#666666" if cid == 3 else colour,
                shade,
            )
            for cid, height, bold, italic, underline, strike, colour, shade in shapes
        ]
    shapes += list(extras or [])
    for cid, height, bold, italic, underline, strike, colour, shade, *face in shapes:
        ref = face[0] if face else _HWPX_GOTHIC if cid in _HWPX_HEADING_SHAPES else body_face
        items.append(
            f'   <hh:charPr id="{cid}" height="{height}" textColor="{colour}"'
            f' shadeColor="{shade}" useFontSpace="0" useKerning="0">\n'
            f'    <hh:fontRef hangul="{ref}" latin="{ref}" hanja="{ref}" japanese="{ref}"'
            f' other="{ref}" symbol="{ref}" user="{ref}"/>\n'
            '    <hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>\n'
            '    <hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>\n'
            '    <hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>\n'
            '    <hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>\n'
            + ("    <hh:bold/>\n" if bold else "")
            + ("    <hh:italic/>\n" if italic else "")
            + (
                '    <hh:underline type="BOTTOM" shape="SOLID" color="#000000"/>\n'
                if underline
                else ""
            )
            + ('    <hh:strikeout shape="SOLID" color="#000000"/>\n' if strike else "")
            + "   </hh:charPr>"
        )
    return (
        f'  <hh:charProperties itemCnt="{len(items)}">\n'
        + "\n".join(items)
        + "\n  </hh:charProperties>"
    )


def _hwpx_para_properties(
    extras: list[tuple[int, ...]] | None = None,
    base: tuple[tuple, ...] = _HWPX_PARA_SHAPES,
) -> str:
    """`paraPr` per shape; margins as `hp:switch` (true lengths, doubled default) per Hancom's storage."""

    def margin(intent: int, left: int, prev: int, nxt: int, scale: int, line_spacing: int) -> str:
        return (
            "<hh:margin>"
            f'<hc:intent value="{intent * scale}" unit="HWPUNIT"/>'
            f'<hc:left value="{left * scale}" unit="HWPUNIT"/>'
            '<hc:right value="0" unit="HWPUNIT"/>'
            f'<hc:prev value="{prev * scale}" unit="HWPUNIT"/>'
            f'<hc:next value="{nxt * scale}" unit="HWPUNIT"/>'
            "</hh:margin>"
            f'<hh:lineSpacing type="PERCENT" value="{line_spacing}" unit="HWPUNIT"/>'
        )

    items = []
    shapes = [(*shape, _HWPX_LINE_SPACING) for shape in base] + list(extras or [])
    for pid, align, left, prev, nxt, line_spacing, *hang in shapes:
        intent = hang[0] if hang else 0
        items.append(
            f'   <hh:paraPr id="{pid}" tabPrIDRef="0" condense="0" fontLineHeight="0"'
            ' snapToGrid="1" suppressLineNumbers="0" checked="0">\n'
            f'    <hh:align horizontal="{align}" vertical="BASELINE"/>\n'
            '    <hh:heading type="NONE" idRef="0" level="0"/>\n'
            '    <hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD"'
            f' widowOrphan="0" keepWithNext="{1 if pid in (1, 2) else 0}" keepLines="0" pageBreakBefore="0"'
            ' lineWrap="BREAK"/>\n'
            '    <hh:autoSpacing eAsianEng="0" eAsianNum="0"/>\n'
            "    <hp:switch>"
            '<hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
            f"{margin(intent, left, prev, nxt, 1, line_spacing)}</hp:case>"
            f"<hp:default>{margin(intent, left, prev, nxt, 2, line_spacing)}</hp:default>"
            "</hp:switch>\n"
            "   </hh:paraPr>"
        )
    return (
        f'  <hh:paraProperties itemCnt="{len(items)}">\n'
        + "\n".join(items)
        + "\n  </hh:paraProperties>"
    )


def _hwpx_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


#: 머리말·꼬리말 area, 10 mm
_HWPX_FURNITURE = round(10 * 7200 / 25.4)


def _hwpx_text_width(page_settings: dict | None = None) -> int:
    """Text column width in HWPUNIT."""
    margins = (page_settings or {}).get("margins")
    margins = margins if isinstance(margins, dict) else {}

    def side(name: str) -> float:
        try:
            return max(10.0, min(35.0, float(margins.get(name, 30))))
        except (TypeError, ValueError):
            return 30.0

    return 59528 - round(side("left") * 7200 / 25.4) - round(side("right") * 7200 / 25.4)


def _hwpx_secpr(page_settings: dict | None = None) -> str:
    """OWPML section properties (A4, margins, header, page numbers); required for sized pictures."""
    settings = page_settings or {}
    margins = settings.get("margins") if isinstance(settings.get("margins"), dict) else {}

    def unit(name: str, fallback: float) -> int:
        try:
            value = max(10.0, min(35.0, float(margins.get(name, fallback))))
        except (TypeError, ValueError):
            value = fallback
        return round(value * 7200 / 25.4)

    first_header = "0" if bool(settings.get("firstPageHeader", False)) else "1"
    hide_page_number = "1" if str(settings.get("pageNumbers") or "page-total") == "none" else "0"
    # Hancom body spans `top + header` to `bottom + footer`: margin split between them
    top, bottom = unit("top", 20), unit("bottom", 15)
    header, footer = min(_HWPX_FURNITURE, top // 2), min(_HWPX_FURNITURE, bottom // 2)
    return (
        '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000"'
        ' tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="0" memoShapeIDRef="0"'
        ' textVerticalWidthHead="0" masterPageCnt="0">'
        '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
        '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
        f'<hp:visibility hideFirstHeader="{first_header}" hideFirstFooter="0" hideFirstMasterPage="0"'
        f' border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="{hide_page_number}" hideFirstEmptyLine="0"'
        ' showLineNumber="0"/>'
        '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
        f'<hp:margin header="{header}" footer="{footer}" gutter="0"'
        f' left="{unit("left", 30)}" right="{unit("right", 30)}"'
        f' top="{top - header}" bottom="{bottom - footer}"/></hp:pagePr>'
        "</hp:secPr>"
    )


#: Hancom reads pixels at 96 DPI regardless of metadata: 75 HWPUNIT each
_HWPUNIT_PER_PIXEL = 75

_HWPX_MAX_WIDTH = 59528 - 8504 * 2
_HWPX_MAX_HEIGHT = int(170 / 25.4 * 7200)

#: `binaryItemIDRef` resolves to the `<opf:item>` id in `content.hpf`
_HWPX_PIC = (
    '<hp:pic id="{n}" zOrder="0" numberingType="PICTURE" textWrap="SQUARE"'
    ' textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0"'
    ' instid="{n}" reverse="0">'
    '<hp:offset x="0" y="0"/>'
    '<hp:orgSz width="{w}" height="{h}"/>'
    '<hp:curSz width="{w}" height="{h}"/>'
    '<hp:flip horizontal="0" vertical="0"/>'
    '<hp:rotationInfo angle="0" centerX="{cx}" centerY="{cy}" rotateimage="1"/>'
    "<hp:renderingInfo>"
    '<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
    "</hp:renderingInfo>"
    '<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{w}" y="0"/>'
    '<hc:pt2 x="{w}" y="{h}"/><hc:pt3 x="0" y="{h}"/></hp:imgRect>'
    '<hp:imgClip left="0" right="{dw}" top="0" bottom="{dh}"/>'
    '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
    '<hp:imgDim dimwidth="{dw}" dimheight="{dh}"/>'
    '<hc:img binaryItemIDRef="{ref}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>'
    "<hp:effects/>"
    '<hp:sz width="{w}" widthRelTo="ABSOLUTE" height="{h}" heightRelTo="ABSOLUTE" protect="0"/>'
    '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0"'
    ' holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP"'
    ' horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
    '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
    "</hp:pic>"
)


def _hwpx_picture(index: int, data: bytes) -> str:
    """Picture paragraph; `imgDim`/`imgClip` in source pixels, sizes on the page."""
    try:
        with PIL.Image.open(io.BytesIO(data)) as picture:
            pixels_wide, pixels_high = picture.size
    except Exception:  # noqa: BLE001 — an unreadable picture gets a plain box
        pixels_wide, pixels_high = 480, 320
    native_w = max(1, pixels_wide) * _HWPUNIT_PER_PIXEL
    native_h = max(1, pixels_high) * _HWPUNIT_PER_PIXEL
    scale = min(1.0, _HWPX_MAX_WIDTH / native_w, _HWPX_MAX_HEIGHT / native_h)
    width, height = int(native_w * scale), int(native_h * scale)
    return (
        '<hp:p paraPrIDRef="5" styleIDRef="0"><hp:run charPrIDRef="0">'
        + _HWPX_PIC.format(
            n=index,
            ref=f"image{index}",
            w=width,
            h=height,
            dw=native_w,
            dh=native_h,
            cx=width // 2,
            cy=height // 2,
        )
        + "<hp:t/></hp:run></hp:p>"
    )


def _hwpx_para(text: str, para_pr: int, char_pr: int = 0) -> str:
    """One `<hp:p>`; empty runs need `<hp:t/>` or Hancom drops the line."""
    return (
        f'<hp:p paraPrIDRef="{para_pr}" styleIDRef="0">'
        f'<hp:run charPrIDRef="{char_pr}">'
        f"<hp:t>{_hwpx_escape(text)}</hp:t>"
        f"</hp:run></hp:p>"
    )


def _hwpx_page_furniture(title: str, page_settings: dict | None) -> str:
    """Header, footer and page-number controls."""
    if not page_settings:
        return ""
    header = str(page_settings.get("header") or title).strip()
    footer = str(page_settings.get("footer") or "").strip()

    def control(kind: str, text: str, identifier: int) -> str:
        if not text:
            return ""
        paragraph = _hwpx_para(text, 3, 5)
        return (
            f'<hp:ctrl><hp:{kind} id="{identifier}" applyPageType="BOTH">'
            '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK"'
            ' vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0"'
            ' textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
            f"{paragraph}</hp:subList></hp:{kind}></hp:ctrl>"
        )

    numbering = str(page_settings.get("pageNumbers") or "page-total")
    page_number = (
        '<hp:ctrl><hp:pageNum pos="BOTTOM_RIGHT" formatType="DIGIT" sideChar=""/></hp:ctrl>'
        if numbering != "none"
        else ""
    )
    return control("header", header, 10001) + control("footer", footer, 10002) + page_number


def _column_weights(
    rows: list[list[str]], cols: int, text_width: int = _HWPX_TEXT_WIDTH
) -> list[int]:
    """Column widths in characters fitting the `.hwpx` text column."""
    budget = (text_width - cols * _HWPX_CELL_MARGIN * 2) // _HWPX_BODY_CHAR
    return _column_shares(rows, cols, budget)


def _ems(text: str) -> float:
    """Width of `text` in ems (Hangul 1, Latin caps/digits ~0.65, other ASCII ~0.5)."""
    width = 0.0
    for character in text:
        if unicodedata.east_asian_width(character) in ("W", "F"):
            width += 1.0
        elif character.isdigit() or character.isupper():
            width += 0.65
        elif character == " ":
            width += 0.3
        else:
            width += 0.55 if ord(character) < 0x2000 else 1.0
    return width


def _column_shares(rows: list[list[str]], cols: int, budget: int) -> list[int]:
    """Column widths in ems summing to `budget`: longest word first, then by text length."""
    rows = [[_strip_inline(cell) for cell in row] for row in rows]

    def longest_word(column: int) -> int:
        return max(
            (
                # bold Latin/digits wider: half an em spare
                math.ceil(_ems(word) + (0.5 if re.search(r"[0-9A-Za-z]", word) else 0))
                for row in rows
                if column < len(row)
                for word in row[column].split()
            ),
            default=1,
        )

    budget = max(budget, cols * 2)
    need = [max(2, min(longest_word(c), budget // 2)) for c in range(cols)]
    if sum(need) >= budget:
        out = [0] * cols
        rest = budget
        for taken, column in enumerate(sorted(range(cols), key=lambda c: need[c])):
            share = rest // (cols - taken)
            out[column] = min(need[column], share)
            rest -= out[column]
        return out
    want = [
        max(
            (math.ceil(_ems(line)) for row in rows if c < len(row) for line in row[c].split("\n")),
            default=1,
        )
        for c in range(cols)
    ]
    # short columns get a full line before prose shares the rest
    for column in sorted(range(cols), key=lambda c: want[c]):
        grown = max(need[column], want[column])
        if want[column] <= budget // cols and sum(need) - need[column] + grown <= budget:
            need[column] = grown
    spare = budget - sum(need)
    extra = [max(0, w - n) for w, n in zip(want, need, strict=True)]
    total = sum(extra)
    if total == 0:
        return need
    out = [n + spare * e // total for n, e in zip(need, extra, strict=True)]
    out[-1] += budget - sum(out)
    return out


def _pdf_draft_grid(fields: list[tuple[str, str]], cell, label_cell, value) -> Table:
    """`_draft_grid` for reportlab."""
    grid = _draft_grid(fields)
    flat = grid.flat()
    labels = {label for label, _ in fields}
    centred = ParagraphStyle("tb-sign", parent=cell, alignment=TA_CENTER)
    centred_label = ParagraphStyle("tb-sign-label", parent=label_cell, alignment=TA_CENTER)
    rows = []
    for r, cells in enumerate(flat):
        row = []
        for c, text in enumerate(cells):
            if text in labels and (c == 0 or r == 0):
                row.append(Paragraph(_escape(text), centred_label if c >= 2 else label_cell))
            elif c == 1 or (c >= 2 and text):
                shown = value(text)
                row.append(Paragraph(shown, centred if c >= 2 else cell))
            else:
                row.append("")
        rows.append(row)
    height = len(flat)
    table = Table(
        rows,
        colWidths=[w * mm for w in (26, 40, 30, 30, 30)][: grid.width],
        rowHeights=[None, 12 * mm, *([None] * (height - 2))] if height > 2 else None,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#555555")),
                ("BACKGROUND", (0, 0), (0, -1), HexColor("#f4f4f4")),
                ("BACKGROUND", (2, 0), (-1, 0), HexColor("#f4f4f4")),
                *(
                    [("SPAN", (c, 1), (c, height - 2)) for c in range(2, grid.width)]
                    if height > 2
                    else []
                ),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


_HWPX_COLUMN: contextvars.ContextVar[int] = contextvars.ContextVar(
    "hwpx_column", default=_HWPX_TEXT_WIDTH
)
_HWPX_CHAR: contextvars.ContextVar[int] = contextvars.ContextVar(
    "hwpx_char", default=_HWPX_BODY_CHAR
)


def _hwpx_table(
    rows: richtext.Grid | list[list[str]],
    *,
    char_pr: int = 0,
    head_char_pr: int = 1,
    widths: list[int] | None = None,
    cell_para_pr: int | list[int] = 7,
    header_row: bool = True,
    figure_para_pr: int | None = None,
    cell_char_pr: list[int] | None = None,
    border_fill: int = 2,
) -> str:
    """GFM table as an OWPML table paragraph; child order load-bearing; raises for line fallback."""
    grid = _export_grid(rows)
    if not grid.rows:
        raise ValueError("빈 표")
    kept = grid.flat()
    cols = grid.width
    if cols < 1 or cols > 12:
        raise ValueError(f"열이 {cols}개")
    text_width = _HWPX_COLUMN.get()
    # exact text width; rounding remainder shows
    weights = (widths or _column_weights(kept, cols, text_width))[:cols]
    weights += [1] * (cols - len(weights))
    total = sum(weights) or cols
    room = max(0, text_width - cols * _HWPX_CELL_MARGIN * 2)
    column_widths = [_HWPX_CELL_MARGIN * 2 + room * w // total for w in weights]
    column_widths[-1] += text_width - sum(column_widths)
    numeric = _numeric_columns(kept) if figure_para_pr is not None and header_row else set()

    # declared row heights place and split the table (rhwp trusts them)
    em = _HWPX_CHAR.get()
    pitch = em * _HWPX_LINE_SPACING // 100

    def lines(text: str, width: int) -> int:
        room = max(em, width - _HWPX_CELL_MARGIN * 2)
        return sum(
            max(1, math.ceil(_ems(_strip_inline(line)) * em / room))
            for line in text.split("\n")
        )

    row_heights = [_HWPX_ROW_HEIGHT] * len(grid.rows)
    taken: set[tuple[int, int]] = set()
    for r, row in enumerate(grid.rows):
        column = 0
        for cell in row:
            while (r, column) in taken:
                column += 1
            if column >= cols:
                break
            across = min(cell.colspan, cols - column)
            down = min(cell.rowspan, len(grid.rows) - r)
            for dr in range(down):
                for dc in range(across):
                    taken.add((r + dr, column + dc))
            if down == 1:
                width = sum(column_widths[column : column + across])
                need = lines(cell.text, width) * pitch + _HWPX_CELL_MARGIN * 2
                row_heights[r] = max(row_heights[r], need)
            column += across

    body: list[str] = []
    #: merge-covered addresses; OWPML wants them absent
    covered: set[tuple[int, int]] = set()
    for r, row in enumerate(grid.rows):
        cells: list[str] = []
        column = 0
        for cell in row:
            while (r, column) in covered:
                column += 1
            if column >= cols:
                break
            across = min(cell.colspan, cols - column)
            down = min(cell.rowspan, len(grid.rows) - r)
            for dr in range(down):
                for dc in range(across):
                    if (dr, dc) != (0, 0):
                        covered.add((r + dr, column + dc))
            head = r == 0 and header_row
            fill = 3 if head else border_fill
            shape = cell_para_pr[column] if isinstance(cell_para_pr, list) else cell_para_pr
            if column in numeric and across == 1:
                shape = figure_para_pr
            lines = [_strip_inline(line) for line in cell.text.split("\n")] or [""]
            char = head_char_pr if head else char_pr
            if cell_char_pr is not None and column < len(cell_char_pr):
                char = cell_char_pr[column]
            para = "".join(_hwpx_para(line, shape, char) for line in lines)
            width = sum(column_widths[column : column + across])
            cells.append(
                f'<hp:tc name="" header="{1 if head else 0}" hasMargin="1" protect="0"'
                f' editable="0" dirty="0" borderFillIDRef="{fill}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK"'
                ' vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0"'
                ' textHeight="0" hasTextRef="0" hasNumRef="0">'
                f"{para}"
                "</hp:subList>"
                f'<hp:cellAddr colAddr="{column}" rowAddr="{r}"/>'
                f'<hp:cellSpan colSpan="{across}" rowSpan="{down}"/>'
                f'<hp:cellSz width="{width}" height="{sum(row_heights[r : r + down])}"/>'
                f'<hp:cellMargin left="{_HWPX_CELL_MARGIN}" right="{_HWPX_CELL_MARGIN}"'
                f' top="{_HWPX_CELL_MARGIN}" bottom="{_HWPX_CELL_MARGIN}"/>'
                "</hp:tc>"
            )
            column += across
        body.append("<hp:tr>" + "".join(cells) + "</hp:tr>")

    height = sum(row_heights)
    # inline tables never split (rhwp dropped rows): tall ones anchored, head row repeated
    inline = "1" if height <= _HWPX_INLINE_TABLE else "0"
    table = (
        f'<hp:tbl id="0" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM"'
        ' textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL"'
        f' repeatHeader="1" rowCnt="{len(grid.rows)}" colCnt="{cols}" cellSpacing="0"'
        ' borderFillIDRef="1" noAdjust="0">'
        f'<hp:sz width="{text_width}" widthRelTo="ABSOLUTE" height="{height}"'
        ' heightRelTo="ABSOLUTE" protect="0"/>'
        f'<hp:pos treatAsChar="{inline}" affectLSpacing="0" flowWithText="1" allowOverlap="0"'
        ' holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP"'
        ' horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="0" right="0" top="0" bottom="283"/>'
        f'<hp:inMargin left="{_HWPX_CELL_MARGIN}" right="{_HWPX_CELL_MARGIN}"'
        f' top="{_HWPX_CELL_MARGIN}" bottom="{_HWPX_CELL_MARGIN}"/>' + "".join(body) + "</hp:tbl>"
    )
    return f'<hp:p paraPrIDRef="3" styleIDRef="0"><hp:run charPrIDRef="{char_pr}">{table}</hp:run></hp:p>'


def _hwpx_title_block(
    title: str,
    block: dict,
    lead: str,
    right: int = 3,
    cover: Callable[[], dict[str, int]] | None = None,
) -> list[str]:
    """`_docx_title_block` as OWPML; `lead` rides in the first run."""
    head = block["head"]
    out: list[str] = []

    def para(text: str, para_pr: int, char_pr: int = 0) -> None:
        nonlocal lead
        if lead:
            out.append(
                f'<hp:p paraPrIDRef="{para_pr}" styleIDRef="0"><hp:run charPrIDRef="{char_pr}">'
                f"{lead}<hp:t>{_hwpx_escape(text)}</hp:t></hp:run></hp:p>"
            )
            lead = ""
        else:
            out.append(_hwpx_para(text, para_pr, char_pr))

    def table(per_row: int, widths: list[int]) -> None:
        pairs = block["fields"]
        if not pairs:
            return
        rows: list[list[str]] = []
        for start in range(0, len(pairs), per_row):
            row: list[str] = []
            for label, value in pairs[start : start + per_row]:
                row += [label, _shown(value, label)]
            rows.append(row + [""] * (per_row * 2 - len(row)))
        try:
            out.append(_hwpx_table(rows, head_char_pr=0, widths=widths * per_row, header_row=False))
        except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
            log.warning("hwpx title block fell back to lines: %s", exc)
            out.extend(_hwpx_para(" · ".join(row), 3) for row in rows)

    if doc_type.space_for(block.get("format")) is not None:
        try:
            box = _hwpx_table(
                [[title]], header_row=False, cell_para_pr=6, cell_char_pr=[2],
                border_fill=_HWPX_HEAVY_BOX,
            )
        except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
            log.warning("hwpx title box fell back to a line: %s", exc)
            para(title, 0, 2)
        else:
            run = '<hp:run charPrIDRef="0">'
            out.append(box.replace(run, run + lead, 1))
            lead = ""
        if block["fields"]:
            para(
                "  ·  ".join(f"{label} {value}" for label, value in _fields_line(block["fields"])),
                right, 5,
            )
        return out
    if head == "cover":
        ids = cover() if cover else {"space": 3, "centre": 0, "gap": 3, "label": 0, "title": 2}
        try:
            # zero-width space: empty rows dropped
            bar = _hwpx_table([["\u200b"]], header_row=False, border_fill=_HWPX_COVER_BAR)
        except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
            log.warning("hwpx cover bar left out: %s", exc)
            para("", ids["space"])
        else:
            run = '<hp:run charPrIDRef="0">'
            out.append(bar.replace(run, run + lead, 1))
            lead = ""
            para("", ids["space"])
        if block["label"]:
            para(block["label"], ids["centre"], ids["label"])
        para(title, ids["centre"], ids["title"])
        if block["subtitle"]:
            para(block["subtitle"], ids["centre"], 4)
        para("", ids["gap"])
        column = _HWPX_COLUMN.set(round(_HWPX_COLUMN.get() * _COVER_TABLE))
        try:
            share = round(_COVER_LABEL_COLUMN * 100)
            start = len(out)
            table(1, [share, 100 - share])
            if len(out) > start:
                # anchored and centred, like Hancom's narrow tables
                out[start] = out[start].replace('treatAsChar="1"', 'treatAsChar="0"', 1).replace(
                    'horzAlign="LEFT"', 'horzAlign="CENTER"', 1
                )
        finally:
            _HWPX_COLUMN.reset(column)
        out.append(
            '<hp:p paraPrIDRef="3" styleIDRef="0" pageBreak="1">'
            '<hp:run charPrIDRef="0"><hp:t/></hp:run></hp:p>'
        )
    elif head == "memo":
        para("", 3)
        table(1, [1, 4])
        para(f"제목  {title}", 0, 2)
    elif head == "draft":
        para("", 3)
        try:
            out.append(
                _hwpx_table(
                    _draft_grid(block["fields"]),
                    widths=[26, 40, 30, 30, 30],
                    header_row=False,
                    cell_para_pr=[7, 7, 6, 6, 6],
                )
            )
        except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
            log.warning("hwpx 결재란 fell back to lines: %s", exc)
            out.extend(
                _hwpx_para(" · ".join(row), 3) for row in _draft_grid(block["fields"]).flat()
            )
        para(f"제목  {title}", 0, 2)
    elif head == "press":
        para("보도자료", 1, 3)
        para(title, 0, 2)
        if block["subtitle"]:
            para(block["subtitle"], 0, 4)
        para(_dateline(block["fields"]), 3, 0)
    elif head == "paper":
        para(title, 0, 2)
        if block["subtitle"]:
            para(block["subtitle"], 0, 4)
        for label, value in block["fields"]:
            para(_paper_line(label, value), 6, 0)
        if block["abstract"] is not None:
            para("초록", 2, 4)
            para(_shown(block["abstract"], "초록"), 3, 0)
        if block["keywords"] is not None:
            para(f"핵심어: {_shown(', '.join(block['keywords']), '핵심어')}", 3, 0)
    else:  # header
        if block["label"]:
            para(block["label"], 2, 0)
        para(title, 0, 2)
        if block["subtitle"]:
            para(block["subtitle"], 0, 4)
        table(2, [1, 2])
    return out


def to_hwpx(
    title: str,
    sections: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    page_settings: dict | None = None,
    title_block: dict | None = None,
) -> bytes:
    """`to_docx` document as OWPML, tables sized to the text column."""
    column = _HWPX_COLUMN.set(_hwpx_text_width(_format_page(page_settings, title_block)))
    char = _HWPX_CHAR.set(
        round(doc_type.type_for(_format_of(_title_block(title_block)))["body"] * 100)
    )
    try:
        return _hwpx_document(
            title, sections, tokens=tokens, page_settings=page_settings, title_block=title_block
        )
    finally:
        _HWPX_CHAR.reset(char)
        _HWPX_COLUMN.reset(column)


def _hwpx_document(
    title: str,
    sections: list[dict],
    *,
    tokens: dict[str, str] | None = None,
    page_settings: dict | None = None,
    title_block: dict | None = None,
) -> bytes:
    """`to_docx` document as OWPML; bullets literal, pictures in `BinData/`."""
    style = design.normalise_tokens(tokens) if tokens else None
    body_face = _HWPX_GOTHIC if style and style.get("font") != "serif" else _HWPX_SERIF
    head_block = _title_block(title_block)
    sections = _drawn_sections(sections, head_block)
    fmt = _format_of(head_block)
    scale, space = doc_type.type_for(fmt), doc_type.space_for(fmt)
    char_shapes = _hwpx_char_shapes(scale)
    para_shapes = _hwpx_para_shapes(space)
    char_extras: list[tuple] = []
    char_ids: dict[tuple, int] = {}
    para_extras: list[tuple[int, ...]] = []
    para_ids: dict[tuple, int] = {}

    def colour(value: str, fallback: str) -> str:
        if found := re.fullmatch(r"#([0-9a-f]{6})", value, re.I):
            return "#" + found.group(1).upper()
        if found := re.fullmatch(r"#([0-9a-f]{3})", value, re.I):
            return "#" + "".join(character * 2 for character in found.group(1)).upper()
        return fallback

    def styled_char_id(run_style: dict, base: int) -> int:
        _, base_height, base_bold = char_shapes[base]
        size = str(run_style.get("font-size") or "")
        height = (
            round(float(found.group(1)) * 100)
            if (found := re.fullmatch(r"(\d+(?:\.\d+)?)pt", size, re.I))
            else base_height
        )
        weight = str(run_style.get("font-weight") or "").lower()
        decoration = str(run_style.get("text-decoration") or "").lower()
        key = (
            max(600, min(7200, height)),
            base_bold or weight in {"bold", "600", "700", "800", "900"},
            str(run_style.get("font-style") or "").lower() == "italic",
            "underline" in decoration,
            "line-through" in decoration,
            colour(str(run_style.get("color") or ""), "#000000"),
            colour(str(run_style.get("background-color") or ""), "none"),
            _HWPX_GOTHIC if base in _HWPX_HEADING_SHAPES else body_face,
        )
        if key not in char_ids:
            identifier = len(_HWPX_CHAR_SHAPES) + len(char_extras)
            char_ids[key] = identifier
            char_extras.append((identifier, *key))
        return char_ids[key]

    def styled_para_id(block: dict, base: int) -> int:
        if base >= len(_HWPX_PARA_SHAPES):
            return base
        _, default_align, left, prev, nxt = para_shapes[base]
        block_style = block.get("style") or {}
        align = {
            "left": "LEFT",
            "center": "CENTER",
            "right": "RIGHT",
            "justify": "JUSTIFY",
        }.get(str(block_style.get("text-align") or "").lower(), default_align)
        try:
            spacing = round(float(block_style.get("line-height") or "") * 100)
        except (TypeError, ValueError):
            spacing = _HWPX_LINE_SPACING
        key = (align, left, prev, nxt, max(100, min(300, spacing)))
        if key == (default_align, left, prev, nxt, _HWPX_LINE_SPACING):
            return base
        if key not in para_ids:
            identifier = len(_HWPX_PARA_SHAPES) + len(para_extras)
            para_ids[key] = identifier
            para_extras.append((identifier, *key))
        return para_ids[key]

    def figure_shape() -> int:
        """Right-aligned table-cell shape for figures."""
        key = ("figure",)
        if key not in para_ids:
            identifier = len(_HWPX_PARA_SHAPES) + len(para_extras)
            para_ids[key] = identifier
            para_extras.append((identifier, "RIGHT", 0, 0, 0, _HWPX_LINE_SPACING))
        return para_ids[key]

    def cover_shapes() -> dict[str, int]:
        """Cover page shapes (`_hwpx_title_block`)."""
        given = (paged or {}).get("margins") if isinstance((paged or {}).get("margins"), dict) else {}
        try:
            top = max(10.0, min(35.0, float(given.get("top", 20))))
        except (TypeError, ValueError):
            top = 20.0
        unit = 7200 / 25.4
        # bar is a one-row table
        down = round((_COVER_TOP * 297 - top) * unit) - _HWPX_ROW_HEIGHT - 283

        def shape(key: str, align: str, prev: int, nxt: int) -> int:
            if key not in para_ids:
                para_ids[key] = len(_HWPX_PARA_SHAPES) + len(para_extras)
                para_extras.append((para_ids[key], align, 0, prev, nxt, _HWPX_LINE_SPACING))
            return para_ids[key]

        accent = colour(style["accent"], "#000000") if style and space is None else "#000000"
        return {
            "space": shape("cover-space", "CENTER", max(0, down), 0),
            "centre": shape("cover-centre", "CENTER", 0, 0),
            "gap": shape("cover-gap", "CENTER", round((_COVER_GAP_MM - 6) * unit), 0),
            "label": styled_char_id(
                {"font-size": f"{scale['coverLabel']:g}pt", "color": "#777777"}, 2
            ),
            "title": styled_char_id({"font-size": f"{scale['cover']:g}pt", "color": accent}, 2),
        }

    def hanging_para_id(kind: str, depth: int) -> int:
        """Hanging shape for 개조식/※/붙임 lines (`_docx_hanging`)."""
        em = round(scale["body"] * 100)
        start, hang = _hang(kind, depth)
        after = round(space["item"] * 100) if space is not None else 100
        before = em // 2 if space is not None and kind == "attach" and not depth else 0
        key = ("hang", round(em * start), -round(em * hang), before, after)
        if key not in para_ids:
            identifier = len(_HWPX_PARA_SHAPES) + len(para_extras)
            para_ids[key] = identifier
            para_extras.append(
                (identifier, "LEFT", key[1], before, after, _HWPX_LINE_SPACING, key[2])
            )
        return para_ids[key]

    def styled_para(block: dict, base_para: int, base_char: int, prefix: str = "") -> str:
        runs: list[str] = []
        if prefix:
            runs.append(
                f'<hp:run charPrIDRef="{base_char}"><hp:t>{_hwpx_escape(prefix)}</hp:t></hp:run>'
            )
        for item in block.get("runs") or []:
            text = _hwpx_escape(str(item.get("text") or "")).replace("\n", "<hp:lineBreak/>")
            runs.append(
                f'<hp:run charPrIDRef="{styled_char_id(item.get("style") or {}, base_char)}">'
                f"<hp:t>{text}</hp:t></hp:run>"
            )
        return (
            f'<hp:p paraPrIDRef="{styled_para_id(block, base_para)}" styleIDRef="0">'
            + "".join(runs)
            + "</hp:p>"
        )

    paged = _format_page(page_settings, title_block)
    lead = f"{_hwpx_secpr(paged)}{_hwpx_page_furniture(title, page_settings)}"
    numbers = _HeadingNumbers(head_block)
    body: list[str] = (
        _hwpx_title_block(
            title, head_block, lead, figure_shape() if space is not None else 3,
            cover=cover_shapes,
        )
        if head_block
        else [
            f'<hp:p paraPrIDRef="0" styleIDRef="0"><hp:run charPrIDRef="2">'
            f"{lead}<hp:t>{_hwpx_escape(title)}</hp:t></hp:run></hp:p>"
        ]
    )
    embedded: list[tuple[str, bytes, str]] = []

    def place(picture: dict) -> list[str]:
        """Picture embedded in place, captioned."""
        data = picture.get("data")
        if not data:
            return []
        mime = str(picture.get("mime") or "image/png").lower()
        # `image/jpg`: Hancom's spelling
        extension = {"image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(mime, "png")
        index = len(embedded) + 1
        embedded.append((f"image{index}", data, extension))
        out = [_hwpx_picture(index, data)]
        caption = str(picture.get("caption") or "").strip()
        if caption:
            out.append(_hwpx_para(caption, 5, 4))
        return out

    closing_at = _last_body(sections)
    for at, section in enumerate(sections):
        heading = (section.get("heading") or "").strip()
        number = numbers.section(section)
        if heading and not _is_attachment(section, head_block):
            body.append(_hwpx_para(f"{number}{heading}", 1, 3))
        formatted = list(section.get("_formatting") or [])
        format_cursor = 0
        #: footnotes under the section; malformed `<hp:footNote>` won't open
        notes: list[tuple[str, str]] = []
        for kind, text, marker, depth in _section_lines(
            section, head_block, at == closing_at, "  "
        ):
            if kind == "pagebreak":
                body.append(
                    '<hp:p paraPrIDRef="3" styleIDRef="0" pageBreak="1">'
                    '<hp:run charPrIDRef="0"><hp:t/></hp:run></hp:p>'
                )
                continue
            if kind == "note":
                notes.append((marker, _strip_inline(text)))
                continue
            if kind == "table":
                try:
                    figures = bool(_numeric_columns(_export_grid(text).flat()))
                    body.append(
                        _hwpx_table(text, figure_para_pr=figure_shape() if figures else None)
                    )
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx table fell back to lines: %s", exc)
                    for line in _table_as_lines(text):
                        body.append(_hwpx_para(line, 3))
                continue
            if kind == "image":
                body.extend(place(text))
                continue
            if kind == "chart":
                # browser raster or table; no OWPML chart element
                if drawn := _diagram_picture(section, {"key": diagram_key(marker)}):
                    body.extend(place(drawn))
                    continue
                try:
                    body.append(_hwpx_table(_chart_as_rows(text), head_char_pr=2))
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx chart fell back to lines: %s", exc)
                    for line in _table_as_lines(_chart_as_rows(text)):
                        body.append(_hwpx_para(line, 3))
                continue
            if kind == "steps":
                try:
                    body.append(
                        _hwpx_table(
                            [["", "단계", "내용"]]
                            + [[str(i + 1), name, detail] for i, (name, detail) in enumerate(text)],
                            head_char_pr=2,
                            widths=[1, 5, 12],
                            cell_para_pr=[6, 7, 7],
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx steps fell back to lines: %s", exc)
                    for i, (name, detail) in enumerate(text):
                        body.append(_hwpx_para(f"{i + 1}. {name} {detail}".strip(), 3))
                continue
            if kind == "cards":
                try:
                    grid = [
                        [
                            "\n".join([title] + [f"· {line}" for line in items])
                            for title, items in pair
                        ]
                        for pair in _in_pairs(text)
                    ]
                    body.append(_hwpx_table(grid, cell_para_pr=3))
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx cards fell back to lines: %s", exc)
                    for title, items in text:
                        body.append(_hwpx_para(title, 2))
                        for line in items:
                            body.append(_hwpx_para(f"· {line}", 3))
                continue
            if kind == "callout" and space is not None and text[0]:
                label, lines = text
                try:
                    body.append(
                        _hwpx_table(
                            [[label, "\n".join(lines)]], header_row=False, widths=[1, 9],
                            cell_para_pr=[6, 7], cell_char_pr=[3, 0],
                            border_fill=_HWPX_THIN_BOX,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx core box fell back to lines: %s", exc)
                    body.extend(_hwpx_para(line, 3) for line in [label, *lines])
                continue
            if kind == "callout":
                # one cell: 한글 drops left rules on re-save
                title, lines = text
                try:
                    body.append(_hwpx_table([["\n".join([title] + list(lines))]], cell_para_pr=3))
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx callout fell back to lines: %s", exc)
                    body.append(_hwpx_para(title, 2))
                    for line in lines:
                        body.append(_hwpx_para(line, 3))
                continue
            if kind == "kpi":
                try:
                    body.append(
                        _hwpx_table(
                            [[v for v, _ in text], [label for _, label in text]],
                            head_char_pr=2,
                            cell_para_pr=6,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — see `_hwpx_table`
                    log.warning("hwpx kpi fell back to lines: %s", exc)
                    body.append(_hwpx_para(" · ".join(f"{v} {label}" for v, label in text), 3))
                continue
            if kind == "diagram":
                if drawn := _diagram_picture(section, text):
                    body.extend(place(drawn))
                else:
                    body.append(_hwpx_para("[다이어그램]", 5, 0))
                continue
            clean = _raised_marks(_strip_inline(text))
            block, format_cursor = _next_format(formatted, _strip_inline(text), format_cursor)
            if kind == "heading":
                number = numbers.next(2)
                body.append(
                    styled_para(block, 2, 4, number)
                    if block
                    else _hwpx_para(f"{number}{clean}", 2, 4)
                )
            elif kind in ("gaejo", "remark", "attach"):
                # marker plus space: Hancom tab stops differ
                lead_text = (
                    f"{marker}  "
                    if kind == "attach" and marker
                    else (f"{marker} " if marker else "")
                )
                shape = hanging_para_id(kind, depth)
                char = 5 if kind == "remark" else 0
                body.append(
                    styled_para(block, shape, char, lead_text)
                    if block
                    else _hwpx_para(f"{lead_text}{clean}", shape, char)
                )
            elif kind in ("bullet", "number"):
                base_para = (4, 8, 9)[min(depth, 2)]
                body.append(
                    styled_para(block, base_para, 0, f"{marker} ")
                    if block
                    else _hwpx_para(f"{marker} {clean}", base_para)
                )
            else:
                body.append(styled_para(block, 3, 0) if block else _hwpx_para(clean, 3))
        for picture in section.get("images") or []:
            body.extend(place(picture))
        for mark, note in notes:
            body.append(_hwpx_para(f"{_raised_marks(f'[^{mark}]')} {note}", 3, 5))

    section_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"'
        ' xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
        ' xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core">' + "".join(body) + "</hs:sec>"
    )

    preview = "\n".join([title] + [(s.get("heading") or "") for s in sections])[:1000]

    # `isEmbeded` (sic) keeps Hancom from dropping it
    items = "".join(
        f'  <opf:item id="{name}" href="BinData/{name}.{extension}"'
        f' media-type="image/{"jpg" if extension == "jpg" else extension}" isEmbeded="1"/>\n'
        for name, _, extension in embedded
    )
    content_hpf = _HWPX_CONTENT_HPF.format(title=_hwpx_escape(title)).replace(
        '  <opf:item id="section0"', items + '  <opf:item id="section0"', 1
    )
    manifest = _HWPX_MANIFEST.replace(
        "</odf:manifest>",
        "".join(
            f' <odf:file-entry odf:full-path="BinData/{name}.{extension}"'
            f' odf:media-type="image/{"jpg" if extension == "jpg" else extension}"/>\n'
            for name, _, extension in embedded
        )
        + "</odf:manifest>",
        1,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # `mimetype` first and STORED
        archive.writestr(
            zipfile.ZipInfo("mimetype"), _HWPX_MIMETYPE, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("version.xml", _HWPX_VERSION)
        archive.writestr("META-INF/container.xml", _HWPX_CONTAINER)
        archive.writestr("META-INF/manifest.xml", manifest)
        archive.writestr("Contents/content.hpf", content_hpf)
        archive.writestr(
            "Contents/header.xml",
            _HWPX_HEADER.format(
                font_faces=_hwpx_font_faces(),
                border_fills=_HWPX_BORDER_FILLS.replace(
                    "{accent}", colour(style["accent"], "#333333") if style else "#333333"
                ),
                char_properties=_hwpx_char_properties(
                    style["accent"] if style and space is None else None,
                    char_extras,
                    (style or {}).get("visualStyle") or "editorial",
                    body_face,
                    char_shapes,
                ),
                para_properties=_hwpx_para_properties(para_extras, para_shapes),
            ),
        )
        archive.writestr("Contents/section0.xml", section_xml)
        for name, data, extension in embedded:
            archive.writestr(
                zipfile.ZipInfo(f"BinData/{name}.{extension}"),
                data,
                compress_type=zipfile.ZIP_STORED,
            )
        archive.writestr("Preview/PrvText.txt", preview)
    return buffer.getvalue()


__all__ = ["hwpx_pages", "pdf_pages", "to_docx", "to_hwpx", "to_pdf"]
