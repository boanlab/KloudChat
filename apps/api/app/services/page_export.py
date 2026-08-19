"""Reading an HTML artifact back into the shapes the file exporters render.

There is no rendering engine in this image, so a faithful pixel conversion of
an HTML deck is not on the table. What *is* on the table is a structural one:
this markup was assembled by `design_templates.assemble` out of a fixed
vocabulary, so it can be read back with certainty rather than guessed at.

What that buys is real. A deck exported this way opens in PowerPoint as
editable slides — one per `<section class="slide">`, same order, same words,
same accent and typeface — laid out by `deck_export`, the renderer the JSON
deck track already uses. What it does not buy is the template's own visual
design: the columns and the paper texture belong to the seed, and the seed
needs a browser. The `.html` file is still the faithful copy; this is the
editable one.

Parsed with the standard library's `HTMLParser` rather than a dependency: the
markup is ours, the vocabulary is closed, and anything outside it was already
removed by `design_templates.sanitise` before it was stored.
"""

from __future__ import annotations

import base64
import binascii
import re
from html.parser import HTMLParser
from typing import Any

#: Block containers whose text is collected separately.
_TEXT_TAGS = {"h2", "h3", "p", "li", "blockquote", "th", "td"}

#: A picture inside an artifact is a `data:` URI — `design_templates.sanitise`
#: allows no other kind — so it is already the bytes, and no fetch is involved
#: in reading one back out.
_DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,(.+)$", re.S | re.I)


def decode_picture(src: str) -> tuple[str, bytes] | None:
    """`(mime, bytes)` for an embedded picture, or `None` for anything else.

    Anything else includes a remote address, which cannot appear in a stored
    artifact and must not be fetched if it somehow does.
    """
    match = _DATA_URI.match((src or "").strip())
    if not match:
        return None
    try:
        return match.group(1).lower().replace("jpg", "jpeg"), base64.b64decode(
            re.sub(r"\s+", "", match.group(2)), validate=True
        )
    except (binascii.Error, ValueError):
        return None

#: Rendered by `deck_export`, keyed by the layout class the seed uses.
_DECK_LAYOUT = {
    "cover": "title",
    "bullets": "bullets",
    "quote": "quote",
    "split": "two-column",
    # The proposal deck's own name for the same two columns. Which side an
    # item was on is the point of that slide, so it must not flatten.
    "compare": "two-column",
    "table": "table",
}


class _Reader(HTMLParser):
    """Collects each `<section>` of an artifact into a flat record.

    Deliberately shallow: what a slide is made of — a heading, some lines, a
    quote, a table — is all the exporters can draw, so nesting beyond that is
    read for its text and nothing else.
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
        #: Depth inside `div.cols`, and the column being filled. A split slide
        #: keeps its two lists rather than one merged one — which column an
        #: item was in is the whole point of that layout.
        self._cols = 0
        self._column: list[str] | None = None

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
        # A block is a `<section>` — the deck seeds class theirs by layout and
        # the document seeds leave theirs bare — plus the document cover, which
        # is a `div` because it is a header rather than a section of the text.
        if tag == "section" or (tag == "div" and "cover" in classes):
            self._open(classes, container=tag)
            return
        if self._block is None:
            return
        if tag == "img":
            picture = decode_picture(dict(attrs).get("src") or "")
            if picture:
                self._block["images"].append(
                    {"mime": picture[0], "data": picture[1], "caption": ""}
                )
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
        if self._block is None:
            return
        if tag in _TEXT_TAGS or tag == "figcaption":
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
        # A nested `.cover` inside an open block is the document seed's own
        # header, not a second block.
        if self._block is not None:
            return
        layout = next((c for c in classes if c not in ("slide", "page")), "section")
        self._block = {
            "layout": layout,
            "title": "",
            "lead": "",
            "paragraphs": [],
            "bullets": [],
            "quote": "",
            "rows": [],
            "columns": [],
            "images": [],
            "_container": container,
        }

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        tag, classes = self._tag, self._classes
        self._tag, self._classes = None, []
        if not text or self._block is None or tag is None:
            return
        if tag in ("h2", "h3"):
            # The document seed's cover writes `h1`; both land as the title.
            if not self._block["title"]:
                self._block["title"] = text
            elif self._column is not None:
                # A column's own heading leads that column rather than joining
                # the slide's prose, where it would read as a stray line.
                self._column.append(text)
                self._block["bullets"].append(text)
            else:
                self._block["paragraphs"].append(text)
        elif tag == "li":
            self._block["bullets"].append(text)
            if self._column is not None:
                self._column.append(text)
        elif tag == "blockquote":
            self._block["quote"] = text
        elif tag in ("th", "td"):
            self._row.append(text)
        elif tag == "figcaption":
            # The caption belongs to the picture above it, not to the prose.
            if self._block["images"]:
                self._block["images"][-1]["caption"] = text
        elif "lead" in classes and not self._block["lead"]:
            self._block["lead"] = text
        else:
            self._block["paragraphs"].append(text)

    def _close(self) -> None:
        self._flush()
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
    """An HTML deck as the slide dicts `deck_export` draws.

    A block with nothing in it is dropped rather than exported as an empty
    slide: the file already shows the gap, and a blank page in a deck reads as
    a mistake made during the presentation.
    """
    slides: list[dict[str, Any]] = []
    for block in read(html):
        layout = _DECK_LAYOUT.get(block["layout"], "bullets")
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
            # Also flattened, so a renderer that only knows `bullets` still
            # draws every line rather than an empty slide.
            slide["bullets"] = [item for column in slide["columns"] for item in column]
        elif block["bullets"]:
            slide["bullets"] = block["bullets"]
        else:
            # A layout that came back as prose still has to say something; the
            # paragraphs become the lines rather than being thrown away.
            slide["bullets"] = block["paragraphs"]
        if block["images"]:
            # One per slide: a screen holds a picture and its point, and two
            # pictures on one slide is a slide that should have been two.
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


def to_sections(html: str) -> list[dict[str, Any]]:
    """An HTML document as the sections `report_export` draws.

    Markdown, because that is what the report exporters read: `_markdown_to_
    lines` already turns `- ` into a bullet and a blank line into a paragraph
    break, so the shape survives into `.docx`, `.pdf` and `.hwpx` alike.
    """
    sections: list[dict[str, str]] = []
    for block in read(html):
        lines: list[str] = []
        if block["lead"]:
            lines.append(block["lead"])
        lines.extend(block["paragraphs"])
        lines.extend(f"- {item}" for item in block["bullets"])
        if block["quote"]:
            lines.append(f"> {block['quote']}")
        for row in block["rows"]:
            lines.append("- " + " · ".join(row))
        content = "\n\n".join(lines)
        if not content.strip() and not block["title"] and not block["images"]:
            continue
        section: dict[str, Any] = {
            "heading": block["title"],
            "content": content,
            "level": 1,
        }
        # A page can hold several, and where they were is lost either way —
        # the exporters place them after the section's prose, which is where
        # a figure in a written document usually belongs.
        if block["images"]:
            section["images"] = block["images"]
        sections.append(section)
    return sections


__all__ = ["decode_picture", "read", "to_sections", "to_slides"]
