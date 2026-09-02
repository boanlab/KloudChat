"""Reading an HTML artifact back into the shapes the file exporters render.

Word and PowerPoint have no CSS, so a pixel-faithful conversion of an HTML deck
is not on the table and never will be. What *is* on the table is a structural
one: this markup was assembled by `design_templates.assemble` out of a fixed
vocabulary, so it can be read back with certainty rather than guessed at.

What that buys is real. A deck exported this way opens in PowerPoint as
editable slides — one per `<section class="slide">`, same order, same words,
same accent and typeface — laid out by `deck_export`, the renderer the JSON
deck track already uses. What it does not buy is the template's own visual
design: the columns and the paper texture belong to the seed, and the seed
needs a browser. That browser now exists — `services/printing.py` — but it
draws the `.pdf`, which is a picture of the document; this draws the `.docx`
and the `.pptx`, which are documents somebody can edit. Fidelity there,
editability here, and neither is the other's poor relation.

Parsed with the standard library's `HTMLParser` rather than a dependency: the
markup is ours, the vocabulary is closed, and anything outside it was already
removed by `design_templates.sanitise` before it was stored.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from app.services import pictures

#: Block containers whose text is collected separately.
_TEXT_TAGS = {
    # `h4` sits beside `h3` because the sanitiser admits both — the document
    # editor's heading picker offers the two levels a body may carry, and a
    # tag admitted there and unread here is exactly the drift this file's own
    # coverage test exists to catch.
    "h2", "h3", "h4", "p", "li", "blockquote", "th", "td",
    "small", "dt", "dd", "figcaption",
}

#: Admitted by `design_templates` and read for structure rather than for words
#: of its own: a container this reader walks into, a list its items belong to,
#: a picture, or an inline element whose text the block around it collected.
#: `code` is in there — its words arrive unmarked, because the one marker a
#: linear export could carry is a backtick and only `report_export` strips
#: those, so a deck would show them on the slide.
_CARRIED_TAGS = {
    "ul", "ol", "table", "thead", "tbody", "tr", "figure", "img",
    "div", "span", "section", "dl", "strong", "em", "code", "br",
    # The footnote reference, whose one character *is* its meaning. Carried
    # rather than dropped so the `*` reaches the paragraph's text: the note it
    # points at is exported as its own line below, and a note with nothing
    # referring to it is a line the reader cannot place.
    "sup",
}

#: Admitted and deliberately not carried. A rule is furniture — it separates
#: two things on a page and says nothing a `.docx` reader would miss.
#:
#: Between them the three sets account for every tag
#: `design_templates._ALLOWED_TAGS` admits, and `test_page_export` pins that.
#: The way text went missing here the first time was one of the two lists
#: growing a tag the other had never heard of, and the reader found out by
#: opening the download.
_DROPPED_TAGS = {"hr"}

#: Rendered by `deck_export`, keyed by the layout class the seed uses.
_DECK_LAYOUT = {
    "cover": "title",
    # A divider is a cover for the part after it — same ground, same reversal.
    "section": "section",
    "bullets": "bullets",
    "quote": "quote",
    "split": "two-column",
    # The proposal deck's own name for the same two columns. Which side an
    # item was on is the point of that slide, so it must not flatten.
    "compare": "two-column",
    "table": "table",
    # The three paired shapes, under the names the seed's own vocabulary uses.
    "bands": "bands",
    "tiles": "tiles",
    "timeline": "timeline",
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
        #: A `<dt>` waiting for the `<dd>` that defines it.
        self._term = ""
        #: Footnotes seen in the block being read, so the marker written into
        #: the export counts the same way the seed's CSS counter does. Reset
        #: per block, which is where the seed resets it.
        self._notes = 0
        #: Which pair block is open — `kpi`, `steps` or nothing — and the
        #: halves collected inside the item being read.
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
        # A block is a `<section>` — the deck seeds class theirs by layout and
        # the document seeds leave theirs bare — plus the document cover, which
        # is a `div` because it is a header rather than a section of the text.
        if tag == "section" or (tag == "div" and "cover" in classes):
            self._open(classes, container=tag)
            return
        if self._block is None:
            return
        if tag == "br":
            # A hard line break inside a paragraph. Nothing else separates the
            # two halves once the markup is gone, so without a space here they
            # arrive in the file as one run-on word.
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
        # A chart carries its numbers in an attribute, so there is nothing to
        # collect from inside it — and nothing to lose by reading it here.
        if tag == "figure" and "chart" in classes:
            if source := (dict(attrs).get("data-source") or "").strip():
                self._block["charts"].append(source)
            return
        # A row of figures and a numbered procedure. Both are `<div>`/`<ol>`
        # with inline `<strong>`/`<span>` inside, and neither `strong` nor
        # `span` is a text tag — so without this the figures reached the
        # exporters as nothing at all. A `.docx` came out with the sentence
        # before the strip and the sentence after it and no strip.
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
            # One cell of a strip, or one step. `div` closes a figure's cell
            # and `li` closes a step; the outer `div`/`ol` closes the block.
            if tag in ("div", "li") and self._pair_buffer:
                self._flush()
                pair = [*self._pair_buffer, ""][:2]
                self._block["metrics" if self._pairs == "kpi" else "steps"].append(pair)
                self._pair_buffer = []
                return
            if (self._pairs == "kpi" and tag == "div") or (
                self._pairs == "steps" and tag == "ol"
            ):
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
        # A nested `.cover` inside an open block is the document seed's own
        # header, not a second block.
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
        """A `<dt>` nothing defined, emitted on its own.

        A term with no definition under it is still something somebody wrote
        down, and a linear export that drops it is the failure this whole file
        exists to avoid.
        """
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
            self._item(text)
        elif tag == "dt":
            self._pair()
            self._term = text
        elif tag == "dd":
            # A definition list is a list of labelled items, not a table: two
            # of its cells are a name and what the name stands for, and the
            # pair only reads as one line. `목적: 3세대 엔진 선정 근거 확보`
            # is what the cover of a report says on paper.
            self._item(f"{self._term}: {text}" if self._term else text)
            self._term = ""
        elif tag == "small":
            # A footnote: a source, or the condition a figure was measured
            # under. It is subordinate to the paragraph it follows and must not
            # arrive as the next claim, so it stays where it stood rather than
            # being gathered to the end.
            #
            # The marker is written here rather than left to the seed. On
            # screen `*`, `**`, `***` come from a CSS counter, and generated
            # content is not text — it would reach neither the `.docx` nor the
            # `.pdf`, leaving the body's `<sup>*</sup>` pointing at a line with
            # nothing on it to match. Counted per block, because that is where
            # the seed resets its counter.
            self._notes += 1
            self._block["paragraphs"].append(f"{'*' * self._notes} {text}")
            self._block["notes"].append(text)
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
    """An HTML deck as the slide dicts `deck_export` draws.

    A block with nothing in it is dropped rather than exported as an empty
    slide: the file already shows the gap, and a blank page in a deck reads as
    a mistake made during the presentation.
    """
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
            # Also flattened, so a renderer that only knows `bullets` still
            # draws every line rather than an empty slide.
            slide["bullets"] = [item for column in slide["columns"] for item in column]
        elif block["bullets"]:
            slide["bullets"] = block["bullets"]
        else:
            # A layout that came back as prose still has to say something; the
            # paragraphs become the lines rather than being thrown away.
            slide["bullets"] = block["paragraphs"]
            prose = True
        if block["notes"] and not prose:
            # No slide layout has a subordinate voice — each draws bullets, a
            # table, a quote or a lead, and none of them a source line. So the
            # note goes where a presentation keeps what has to be said about a
            # slide without being shown on it. The one exception is the slide
            # that came back as prose: there the note is already a line.
            slide["notes"] = "\n".join(block["notes"])
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
        # The same fences the report writer produces, so a document written on
        # the HTML surface exports exactly as one written on the Markdown one.
        # These three used to arrive as nothing at all: a strip of figures is
        # `<div><strong>/<span>`, neither of which is a text tag, so the
        # numbers were dropped between the page and the file.
        for pairs, lang in ((block["metrics"], "kpi"), (block["steps"], "steps")):
            if rows := [pair for pair in pairs if pair and pair[0]]:
                body = "\n".join(f"{left} | {right}" for left, right in rows)
                lines.append(f"```{lang}\n{body}\n```")
        for source in block["charts"]:
            lines.append(f"```chart\n{source}\n```")
        if block["rows"]:
            # A real table rather than `- 기준 · 값 · 값`. The exporters have
            # drawn one from a GFM table since they learned to, and a row of
            # middots is a comparison the reader has to rebuild.
            width = max(len(row) for row in block["rows"])
            padded = [[*row, *([""] * (width - len(row)))] for row in block["rows"]]
            lines.append(
                "\n".join(
                    ["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
                    + ["| " + " | ".join(row) + " |" for row in padded[1:]]
                )
            )
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


__all__ = ["read", "to_sections", "to_slides"]
