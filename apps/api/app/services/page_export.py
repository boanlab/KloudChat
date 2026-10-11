"""HTML artifact read back structurally into deck_export/report_export shapes."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from app.services import pictures

#: block containers, text collected separately
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

#: read for structure; sup kept for footnote markers
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

#: admitted, not carried; three sets cover _ALLOWED_TAGS (pinned by test)
_DROPPED_TAGS = {"hr"}

#: seed layout class → deck_export layout
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
    """Each section as a flat record; deeper nesting read as text only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self._block: dict[str, Any] | None = None
        self._tag: str | None = None
        self._classes: list[str] = []
        self._buffer: list[str] = []
        self._row: list[str] = []
        self._skip = 0
        #: div.cols depth and current column
        self._cols = 0
        self._column: list[str] | None = None
        #: dt awaiting its dd
        self._term = ""
        #: footnotes per block (reset like the CSS counter)
        self._notes = 0
        #: open pair block (kpi/steps) and halves so far
        self._pairs = ""
        self._pair_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if self._skip:
            self._skip += 1
            return
        # slide number drawn by exporters
        if "num" in classes:
            self._skip = 1
            return
        # block: section or div.cover
        if tag == "section" or (tag == "div" and "cover" in classes):
            self._open(classes, container=tag)
            return
        if self._block is None:
            return
        if tag == "br":
            # space prevents run-on words
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
        # chart numbers in data-source
        if tag == "figure" and "chart" in classes:
            if source := (dict(attrs).get("data-source") or "").strip():
                self._block["charts"].append(source)
            return
        # KPI strip / numbered steps: strong/span pairs
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
            # div closes a KPI cell, li a step; outer closes the block
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

    def _open(self, classes: list[str], *, container: str) -> None:
        # nested .cover = document seed header
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
        """One list line, also kept in its column."""
        if self._block is None:
            return
        self._block["bullets"].append(text)
        if self._column is not None:
            self._column.append(text)

    def _pair(self) -> None:
        """Undefined dt emitted alone."""
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
                # column heading leads the column
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
            # definition list → labelled items
            self._item(f"{self._term}: {text}" if self._term else text)
            self._term = ""
        elif tag == "small":
            # footnote kept in place; * written here (CSS counter isn't exported)
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
    """h1 outside the tag set; used by the document cover."""
    found = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    return [re.sub(r"<[^>]+>", "", one).strip() for one in found]


def read(html: str) -> list[dict[str, Any]]:
    """Every block, in order."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    titles = _h1_titles(html)
    for block in reader.blocks:
        if not block["title"] and titles:
            block["title"] = titles.pop(0)
    return reader.blocks


def to_slides(html: str, *, accent: str = "") -> list[dict[str, Any]]:
    """HTML deck as deck_export slide dicts; empty blocks dropped."""
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
            # flattened for bullets-only renderers
            slide["bullets"] = [item for column in slide["columns"] for item in column]
        elif block["bullets"]:
            slide["bullets"] = block["bullets"]
        else:
            slide["bullets"] = block["paragraphs"]
            prose = True
        if block["notes"] and not prose:
            # no layout draws sources: footnotes → speaker notes
            slide["notes"] = "\n".join(block["notes"])
        if block["images"]:
            # one picture per slide
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
    """HTML document as report_export markdown sections."""
    sections: list[dict[str, str]] = []
    for block in read(html):
        lines: list[str] = []
        if block["lead"]:
            lines.append(block["lead"])
        lines.extend(block["paragraphs"])
        lines.extend(f"- {item}" for item in block["bullets"])
        if block["quote"]:
            lines.append(f"> {block['quote']}")
        # same fences as the report writer
        for pairs, lang in ((block["metrics"], "kpi"), (block["steps"], "steps")):
            if rows := [pair for pair in pairs if pair and pair[0]]:
                body = "\n".join(f"{left} | {right}" for left, right in rows)
                lines.append(f"```{lang}\n{body}\n```")
        for source in block["charts"]:
            lines.append(f"```chart\n{source}\n```")
        if block["rows"]:
            # GFM table
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
            # keep the cover's own page in Word/HWPX
            content = f"{content}\n\n<!-- pagebreak -->".strip()
        if not content.strip() and not block["title"] and not block["images"]:
            continue
        section: dict[str, Any] = {
            "heading": block["title"],
            "content": content,
            "level": 1,
        }
        # placed after the prose by exporters
        if block["images"]:
            section["images"] = block["images"]
        sections.append(section)
    return sections


__all__ = ["read", "to_sections", "to_slides"]
