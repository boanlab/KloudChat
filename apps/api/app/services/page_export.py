"""Reads an HTML artifact back into the shapes `deck_export` and `report_export` render.

Structural, not visual: the markup was assembled by `design_templates.assemble`
from a closed vocabulary, so it can be read back with certainty. `printing.py`
draws the `.pdf`; this feeds the editable `.docx` and `.pptx`.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from app.services import pictures

#: Block containers whose text is collected separately.
_TEXT_TAGS = {
    "h2",
    "h3",
    "h4",
    "p",
    "li",
    "blockquote",
    "th",
    "td",
    "small",
    "dt",
    "dd",
    "figcaption",
}

#: Admitted by `design_templates` and read for structure rather than for words
#: of its own. `code` text arrives unmarked. `sup` is carried so a footnote
#: marker reaches the paragraph's text.
_CARRIED_TAGS = {
    "ul",
    "ol",
    "table",
    "thead",
    "tbody",
    "tr",
    "figure",
    "img",
    "div",
    "span",
    "section",
    "dl",
    "strong",
    "em",
    "code",
    "br",
    "sup",
}

#: Admitted and deliberately not carried. The three sets together cover every
#: tag `design_templates._ALLOWED_TAGS` admits; `test_page_export` pins that.
_DROPPED_TAGS = {"hr"}

#: Seed layout class → `deck_export` layout.
_DECK_LAYOUT = {
    "cover": "title",
    "section": "section",
    "bullets": "bullets",
    "quote": "quote",
    "split": "two-column",
    "compare": "two-column",
    "table": "table",
    "bands": "bands",
    "tiles": "tiles",
    "timeline": "timeline",
}


class _Reader(HTMLParser):
    """Collects each `<section>` of an artifact into a flat record.

    Deliberately shallow: nesting beyond heading, lines, quote and table is
    read for its text only.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._block: dict[str, Any] | None = None
        self._tag: str | None = None
        self._classes: list[str] = []
        self._buffer: list[str] = []
        self._row: list[str] = []
        self._skip = 0
        #: Depth inside `div.cols`, and the column being filled.
        self._cols = 0
        self._column: list[str] | None = None
        #: A `<dt>` waiting for the `<dd>` that defines it.
        self._term = ""
        #: Footnotes seen in the block being read; reset per block, as the seed's CSS counter is.
        self._notes = 0
        #: Which pair block is open (`kpi`, `steps` or nothing) and the halves collected so far.
        self._pairs = ""
        self._pair_buffer: list[str] = []

    # ── structure ──────────────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._skip:
            self._skip += 1
            return
        # The slide number is furniture the exporters draw themselves.
        if "num" in classes:
            self._skip = 1
            return
        # A block is a `<section>`, or the document seed's `div.cover`.
        if tag == "section" or (tag == "div" and "cover" in classes):
            self._open(classes, container=tag)
            return
        if self._block is None:
            return
        if tag == "br":
            # Without a space the two halves arrive as one run-on word.
            if self._tag is not None:
                self._buffer.append(" ")
            return
        if tag == "img":
            picture = pictures.decode(dict(attrs).get("src") or "")
            if picture:
                self._block["images"].append(
                    {"mime": picture[0], "data": picture[1], "caption": ""}
                )
            return
        # A chart carries its numbers in `data-source`.
        if tag == "figure" and "chart" in classes:
            if source := (dict(attrs).get("data-source") or "").strip():
                self._block["charts"].append(source)
            return
        # A KPI strip or a numbered procedure: `<div>`/`<ol>` with inline
        # `<strong>`/`<span>` pairs inside.
        if "kpi" in classes or "steps" in classes:
            self._flush()
            self._pairs = "kpi" if "kpi" in classes else "steps"
            self._pair_buffer = []
            return
        if self._pairs and tag in ("strong", "span"):
            self._flush()
            self._tag = tag
            self._classes = classes
            return
        if tag == "figcaption":
            self._flush()
            self._tag = tag
            self._classes = classes
            return
        if "cols" in classes:
            self._cols = 1
        elif self._cols and tag == "div":
            self._flush()
            self._column = []
            self._block["columns"].append(self._column)
        elif tag == "tr":
            self._row = []
        elif tag in _TEXT_TAGS:
            self._flush()
            self._tag = tag
            self._classes = classes

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            self._skip -= 1
            return
        if self._pairs and self._block is not None:
            # `div` closes a KPI cell and `li` a step; the outer `div`/`ol` closes the block.
            if tag in ("div", "li") and self._pair_buffer:
                self._flush()
                pair = [*self._pair_buffer, ""][:2]
                self._block["metrics" if self._pairs == "kpi" else "steps"].append(pair)
                self._pair_buffer = []
                return
            if (self._pairs == "kpi" and tag == "div") or (self._pairs == "steps" and tag == "ol"):
                self._pairs = ""
                self._pair_buffer = []
                return
            return
        if self._block is None:
            return
        if tag in _TEXT_TAGS:
            self._flush()
        elif tag == "tr" and self._row:
            self._block["rows"].append(self._row)
            self._row = []
        elif tag == "div" and self._column is not None:
            self._column = None
        elif tag in ("section", "div") and self._block.get("_container") == tag:
            self._close()

    def handle_data(self, data: str) -> None:
        if self._skip or self._block is None or self._tag is None:
            return
        self._buffer.append(data)

    # ── collection ─────────────────────────────────────────────────────
    def _open(self, classes: list[str], *, container: str) -> None:
        # A nested `.cover` inside an open block is the document seed's header.
        if self._block is not None:
            return
        layout = next((c for c in classes if c not in ("slide", "page")), "section")
        self._notes = 0
        self._block = {
            "layout": layout,
            "title": "",
            "lead": "",
            "paragraphs": [],
            "bullets": [],
            "quote": "",
            "rows": [],
            "metrics": [],
            "steps": [],
            "charts": [],
            "columns": [],
            "images": [],
            "notes": [],
            "_container": container,
        }

    def _item(self, text: str) -> None:
        """One line of a list, kept in its column as well when it is in one."""
        if self._block is None:
            return
        self._block["bullets"].append(text)
        if self._column is not None:
            self._column.append(text)

    def _pair(self) -> None:
        """A `<dt>` nothing defined, emitted on its own."""
        if self._term:
            self._item(self._term)
            self._term = ""

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        tag, classes = self._tag, self._classes
        self._tag, self._classes = None, []
        if not text or self._block is None or tag is None:
            return
        if self._pairs and tag in ("strong", "span"):
            self._pair_buffer.append(text)
            return
        if tag in ("h2", "h3", "h4"):
            if not self._block["title"]:
                self._block["title"] = text
            elif self._column is not None:
                # A column's own heading leads that column.
                self._column.append(text)
                self._block["bullets"].append(text)
            else:
                self._block["paragraphs"].append(text)
        elif tag == "li":
            self._item(text)
        elif tag == "dt":
            self._pair()
            self._term = text
        elif tag == "dd":
            # A definition list is a list of labelled items: `목적: 3세대 엔진 선정 근거 확보`.
            self._item(f"{self._term}: {text}" if self._term else text)
            self._term = ""
        elif tag == "small":
            # A footnote stays where it stood. The `*` marker is written here
            # because the seed's CSS counter is generated content and reaches
            # no export.
            self._notes += 1
            self._block["paragraphs"].append(f"{'*' * self._notes} {text}")
            self._block["notes"].append(text)
        elif tag == "blockquote":
            self._block["quote"] = text
        elif tag in ("th", "td"):
            self._row.append(text)
        elif tag == "figcaption":
            if self._block["images"]:
                self._block["images"][-1]["caption"] = text
        elif "lead" in classes and not self._block["lead"]:
            self._block["lead"] = text
        else:
            self._block["paragraphs"].append(text)

    def _close(self) -> None:
        self._flush()
        self._pair()
        if self._block:
            self._block.pop("_container", None)
            self.blocks.append(self._block)
        self._block = None

    def close(self) -> None:  # noqa: D102 — unterminated markup still yields what it had
        super().close()
        if self._block is not None:
            self._close()


def _h1_titles(html: str) -> list[str]:
    """`<h1>` sits outside the reader's tag set; the document cover uses it."""
    found = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return [re.sub(r"<[^>]+>", "", one).strip() for one in found]


def read(html: str) -> list[dict[str, Any]]:
    """Every block of an artifact, in order."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    titles = _h1_titles(html)
    for block in reader.blocks:
        if not block["title"] and titles:
            block["title"] = titles.pop(0)
    return reader.blocks


def to_slides(html: str, *, accent: str = "") -> list[dict[str, Any]]:
    """An HTML deck as the slide dicts `deck_export` draws. Empty blocks are dropped."""
    slides: list[dict[str, Any]] = []
    for block in read(html):
        layout = _DECK_LAYOUT.get(block["layout"], "bullets")
        prose = False
        slide: dict[str, Any] = {"layout": layout, "title": block["title"]}
        if accent:
            slide["accent"] = accent
        if layout == "title":
            slide["body"] = block["lead"] or (block["paragraphs"][0] if block["paragraphs"] else "")
        elif layout == "quote":
            slide["body"] = block["quote"] or (
                block["paragraphs"][0] if block["paragraphs"] else ""
            )
        elif layout == "table" and block["rows"]:
            slide["rows"] = block["rows"]
        elif layout == "two-column" and len([c for c in block["columns"] if c]) >= 2:
            slide["columns"] = [c for c in block["columns"] if c]
            # Also flattened for renderers that only know `bullets`.
            slide["bullets"] = [item for column in slide["columns"] for item in column]
        elif block["bullets"]:
            slide["bullets"] = block["bullets"]
        else:
            slide["bullets"] = block["paragraphs"]
            prose = True
        if block["notes"] and not prose:
            # No slide layout draws a source line, so footnotes become speaker notes.
            slide["notes"] = "\n".join(block["notes"])
        if block["images"]:
            # One picture per slide.
            slide["image"] = block["images"][0]
        if not (
            slide.get("bullets")
            or slide.get("body")
            or slide.get("rows")
            or slide.get("image")
            or slide["title"]
        ):
            continue
        slides.append(slide)
    return slides


def to_sections(html: str, *, cover_page: bool = True) -> list[dict[str, Any]]:
    """An HTML document as the markdown sections `report_export` draws."""
    sections: list[dict[str, str]] = []
    for block in read(html):
        lines: list[str] = []
        if block["lead"]:
            lines.append(block["lead"])
        lines.extend(block["paragraphs"])
        lines.extend(f"- {item}" for item in block["bullets"])
        if block["quote"]:
            lines.append(f"> {block['quote']}")
        # The same fences the report writer produces.
        for pairs, lang in ((block["metrics"], "kpi"), (block["steps"], "steps")):
            if rows := [pair for pair in pairs if pair and pair[0]]:
                body = "\n".join(f"{left} | {right}" for left, right in rows)
                lines.append(f"```{lang}\n{body}\n```")
        for source in block["charts"]:
            lines.append(f"```chart\n{source}\n```")
        if block["rows"]:
            # A GFM table, which the exporters draw as a real table.
            width = max(len(row) for row in block["rows"])
            padded = [[*row, *([""] * (width - len(row)))] for row in block["rows"]]
            lines.append(
                "\n".join(
                    ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
                    + ["| " + " | ".join(row) + " |" for row in padded[1:]]
                )
            )
        content = "\n\n".join(lines)
        if cover_page and block["layout"] == "cover":
            # The cover occupies its own sheet in HTML/PDF; keep that boundary in Word/HWPX.
            content = f"{content}\n\n<!-- pagebreak -->".strip()
        if not content.strip() and not block["title"] and not block["images"]:
            continue
        section: dict[str, Any] = {
            "heading": block["title"],
            "content": content,
            "level": 1,
        }
        # Placed after the section's prose by the exporters.
        if block["images"]:
            section["images"] = block["images"]
        sections.append(section)
    return sections


__all__ = ["read", "to_sections", "to_slides"]
