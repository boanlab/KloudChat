"""A section written by hand, in the shape the exporters already read.

A report section used to be one thing: Markdown, written by the model, rendered
by the browser and drawn by three exporters that parse it a line at a time.
That works for as long as the only author is the model, because Markdown can
say everything a model has to say about a paragraph.

It stops working the moment somebody edits the document. The four things a
person reaches for first — size, face, alignment, and a colour for emphasis —
have no Markdown at all, and a table typed into a word processor is not a run
of `|` characters. So an edited section is stored as HTML, marked `format:
"html"`, and this module is the bridge back: every path that consumed Markdown
still does.

Two directions, and only one of them lives here:

* **HTML → Markdown**, for the exporters and for the model's own context. That
  is this file. It is a fixed vocabulary — `design_templates._ALLOWED_TAGS` —
  rather than a general parser, which is why 200 lines is enough.
* **Markdown → HTML**, for opening an existing report in the editor. That is
  the browser's, because the browser already renders exactly this Markdown to
  exactly this DOM, and a second implementation here would be a second answer
  to what a list looks like.

Nothing here trusts its input. `design_templates.sanitise` runs first, at the
boundary where the HTML arrives; this reads what survived.
"""

from __future__ import annotations

import html
import re
from html import unescape

#: Block tags that end a paragraph. Everything else is inline and folds into
#: the line being built.
_BLOCK = re.compile(
    r"</?(p|div|section|h[1-6]|ul|ol|li|blockquote|table|thead|tbody|tr|th|td|figure"
    r"|figcaption|dl|dt|dd|hr|br)\b[^>]*>",
    re.I,
)
_TAG = re.compile(r"<[^>]+>")
#: `<strong>가</strong>` → `**가**`. Nested emphasis is left to the outer pass;
#: a bold italic renders as bold, which is what the exporters do with it anyway.
_EMPHASIS = (
    (re.compile(r"<(strong|b)\b[^>]*>(.*?)</\1\s*>", re.S | re.I), r"**\2**"),
    (re.compile(r"<(em|i)\b[^>]*>(.*?)</\1\s*>", re.S | re.I), r"*\2*"),
    (re.compile(r"<code\b[^>]*>(.*?)</code\s*>", re.S | re.I), r"`\1`"),
)


def _inline(fragment: str) -> str:
    """One run of inline markup as Markdown text."""
    text = fragment
    for pattern, replacement in _EMPHASIS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = _TAG.sub("", text)
    return re.sub(r"[ \t]+", " ", unescape(text)).strip()


def _cells(row: str) -> list[str]:
    """The text of one table row's cells, in order."""
    return [
        _inline(cell)
        for cell in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]\s*>", row, re.S | re.I)
    ]


def _table(markup: str) -> str:
    """A table as a GFM table, or `''` when it has no rows.

    GFM rather than the `- 가 · 나` line the deck exporter falls back to: the
    report exporters read Markdown, the browser renders GFM tables already, and
    a table flattened to a bullet is a table somebody has to rebuild by hand
    after every export.
    """
    rows = [_cells(row) for row in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", markup, re.S | re.I)]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    head, *body = padded
    lines = [
        "| " + " | ".join(cell.replace("|", "\\|") for cell in head) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend(
        "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in body
    )
    return "\n".join(lines)


def _list(markup: str, ordered: bool) -> str:
    """A list as Markdown lines. Nested lists flatten — see the module note."""
    items = [_inline(item) for item in re.findall(r"<li\b[^>]*>(.*?)</li\s*>", markup, re.S | re.I)]
    items = [item for item in items if item]
    if not items:
        return ""
    if ordered:
        return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))
    return "\n".join(f"- {item}" for item in items)


#: One top-level construct: a table, a list, a quote, a sub-heading, or a
#: paragraph. Matched in this order so a list inside a table cell is consumed
#: by the table rather than lifted out of it.
_CONSTRUCT = re.compile(
    # Ahead of the plain list: a procedure is an `<ol>` too, and matched as one
    # it would come back as `1. 자료 수집` — an ordinary list, with the fence
    # and its numbering gone for good on the next save.
    r"<div\b[^>]*\bclass=\"[^\"]*\bkpi\b[^\"]*\"[^>]*>.*?</div\s*>"
    r"|<ol\b[^>]*\bclass=\"[^\"]*\bsteps\b[^\"]*\"[^>]*>.*?</ol\s*>"
    # Ahead of the plain figure, which would keep the picture and drop the
    # source — and a diagram whose source is gone cannot be changed again by
    # anyone, here or in the browser.
    r"|<figure\b[^>]*\bclass=\"[^\"]*\bdiagram\b[^\"]*\"[^>]*>.*?</figure\s*>"
    r"|<figure\b[^>]*\bclass=\"[^\"]*\bchart\b[^\"]*\"[^>]*>.*?</figure\s*>"
    # A footnote body. Without this it is inline text, so two notes in a row
    # ran together into one sentence and the first — beginning `* ` — was read
    # back as a bullet by the exporters.
    r"|<small\b[^>]*>.*?</small\s*>"
    r"|<table\b.*?</table\s*>"
    r"|<ul\b.*?</ul\s*>"
    r"|<ol\b.*?</ol\s*>"
    r"|<blockquote\b.*?</blockquote\s*>"
    r"|<h[1-6]\b.*?</h[1-6]\s*>"
    r"|<figure\b.*?</figure\s*>"
    # Tiptap writes a bare `<img>`; the seeds' own markup wraps one in a
    # `<figure>`. Both are pictures and both have to come out.
    r"|<img\b[^>]*>"
    r"|<hr\s*/?>",
    re.S | re.I,
)


def _render(match: re.Match[str], notes: int = 0) -> str:
    markup = match.group(0)
    lowered = markup[:12].lower()
    if _KPI_OPEN.match(markup):
        return _pairs_fence("kpi", markup, r"<div\b[^>]*>(.*?)</div\s*>")
    if _STEPS_OPEN.match(markup):
        return _pairs_fence("steps", markup, r"<li\b[^>]*>(.*?)</li\s*>")
    if _DIAGRAM_OPEN.match(markup):
        return _diagram_fence(markup)
    if _CHART_OPEN.match(markup):
        # Back as its own fence. The figure carries the numbers rather than a
        # drawing of them, so nothing is lost and the exporters rebuild the
        # chart from the same text the writer produced.
        found = _DIAGRAM_SOURCE.search(markup)
        source = html.unescape(found.group(1)[1:-1]).strip() if found else ""
        return "```chart\n" + source + "\n```" if source else ""
    if lowered.startswith("<small"):
        # Numbered by the caller, which counts them for this fragment alone.
        return _note(markup, notes)
    if lowered.startswith("<table"):
        return _table(markup)
    if lowered.startswith("<ul"):
        return _list(markup, ordered=False)
    if lowered.startswith("<ol"):
        return _list(markup, ordered=True)
    if lowered.startswith("<blockquote"):
        body = _inline(markup)
        return f"> {body}" if body else ""
    if lowered.startswith("<hr"):
        return "---"
    if lowered.startswith("<img"):
        return _picture(markup)
    if lowered.startswith("<figure"):
        return _picture(markup)
    level = int(lowered[2])
    body = _inline(markup)
    # `##` at minimum: the section's own heading is drawn by the wrapper, so a
    # heading inside the body is always a sub-heading. `_markdown_to_lines`
    # reads two or more hashes and nothing else.
    return f"{'#' * max(level, 2)} {body}" if body else ""


#: A footnote's own mark, which the 서식 asks the writer to put at the front of
#: the note: `*`, `**`, `***`. Stripped on the way out — the exporters number
#: the notes themselves, and a note carrying both its old mark and its new
#: number reads as two footnotes.
_NOTE_MARK = re.compile(r"^\s*(\*{1,4}|\d{1,3}[).]?)\s+")


def _note(markup: str, number: int) -> str:
    """A footnote body, as a line the exporters can tell from prose.

    GFM's own footnote syntax rather than an invention. It is a real notation,
    it survives every Markdown reader in the way this needs, and — the reason
    it is here — a line beginning `[^1]:` is not a bullet, while a line
    beginning `* ` is. That was the bug: the first footnote of every section
    came out of the exporters as a list item.

    The number comes from the order the notes appear in, which is the order the
    marks in the prose appear in too. The 서식's own checklist asks for exactly
    that correspondence — "각주 표시와 절 발치의 각주가 개수와 순서로 맞아
    떨어지는가" — so pairing by position is not a guess about what the writer
    meant; it is the rule the writer was given.
    """
    text = _NOTE_MARK.sub("", _inline(markup))
    return f"[^{number}]: {text}" if text else ""


#: The two structured blocks, told apart from a plain `<div>` or `<ol>`.
_KPI_OPEN = re.compile(r"<div\b[^>]*\bclass=\"[^\"]*\bkpi\b", re.I)
_STEPS_OPEN = re.compile(r"<ol\b[^>]*\bclass=\"[^\"]*\bsteps\b", re.I)
_STRONG = re.compile(r"<strong\b[^>]*>(.*?)</strong\s*>", re.S | re.I)
_DIAGRAM_OPEN = re.compile(r"<figure\b[^>]*\bclass=\"[^\"]*\bdiagram\b", re.I)
_CHART_OPEN = re.compile(r"<figure\b[^>]*\bclass=\"[^\"]*\bchart\b", re.I)
_DIAGRAM_SOURCE = re.compile(r"\bdata-source=(\"[^\"]*\"|'[^']*')", re.I)


def _diagram_fence(markup: str) -> str:
    """A diagram back as its own mermaid fence.

    The figure carries both the source it was drawn from and, once somebody has
    looked at the document, the picture. Only the source comes back: the
    exporters look the picture up for themselves under the digest of exactly
    this text, and writing the picture into the prose as well would put it in
    the file twice.

    A figure with no source is one that arrived as a plain picture rather than
    as a diagram, so it goes through `_picture` like any other.
    """
    found = _DIAGRAM_SOURCE.search(markup)
    if not found:
        return _picture(markup)
    source = html.unescape(found.group(1)[1:-1]).strip()
    return "```mermaid\n" + source + "\n```" if source else _picture(markup)
_SPAN = re.compile(r"<span\b[^>]*>(.*?)</span\s*>", re.S | re.I)


def _pairs_fence(lang: str, markup: str, row_pattern: str) -> str:
    """A strip of figures or a numbered procedure, back as its own fence.

    These come back through here whenever somebody has touched the section in
    the document editor, because an edited section is stored as HTML and the
    exporters read Markdown. Rendered as anything else they are lost: a
    procedure matched as a plain `<ol>` returns as `1. 자료 수집`, and the
    numbering the editor was drawing is now literal text in the source — which
    the next save makes permanent.

    The inner text is stripped of markup rather than converted. Both blocks are
    atoms in the editor: nothing inside them can be made bold or linked, so
    there is nothing to carry, and a `|` in the text has to go because it is
    the separator.
    """
    rows: list[str] = []
    # Past the opening tag before matching rows: the strip's wrapper is a
    # `<div>` too, and the row pattern would otherwise match it first and take
    # the whole block as one row.
    body = markup[markup.index(">") + 1 :]
    for row in re.finditer(row_pattern, body, re.S | re.I):
        inner = row.group(1)
        left = _STRONG.search(inner)
        right = _SPAN.search(inner)
        name = _inline(left.group(1)) if left else _inline(inner)
        detail = _inline(right.group(1)) if right else ""
        name, detail = name.replace("|", "／"), detail.replace("|", "／")
        if name:
            rows.append(f"{name} | {detail}" if detail else name)
    if not rows:
        return ""
    return "```" + lang + "\n" + "\n".join(rows) + "\n```"


#: A picture and, when it has one, the caption under it.
_IMG = re.compile(r"<img\b[^>]*?src=(\"[^\"]*\"|'[^']*'|[^\s>]+)[^>]*>", re.I)
_CAPTION = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption\s*>", re.S | re.I)


def _picture(markup: str) -> str:
    """A figure as Markdown, or `''` when there is nothing to carry.

    The picture used to be dropped here and only its caption kept, on the
    reasoning that the exporters read images off the section rather than out of
    the prose. That was true for a figure the *writer* produced and false for
    one a *person* pasted into the document editor — those live in the body,
    and dropping them meant a picture somebody put in their report was on the
    screen and missing from the file they submitted.

    So both go out the same way, as a Markdown image, and the exporters draw
    them where they stand.
    """
    found = _IMG.search(markup)
    caption = _CAPTION.search(markup)
    label = _inline(caption.group(1)) if caption else ""
    if not found:
        # A caption with no picture still tells the reader what was there.
        return label
    src = found.group(1).strip().strip("\"'")
    return f"![{label}]({src})"


def to_markdown(fragment: str) -> str:
    """One section's HTML as the Markdown every consumer already reads.

    Not a general converter — the vocabulary is `_ALLOWED_TAGS`, which is what
    `sanitise` leaves standing, and anything outside it has already been
    removed by the time this runs.

    Inline `style=` is dropped rather than approximated. A size or a face has
    no Markdown, and half-applied formatting reads worse than none — the same
    call `_strip_inline` makes in the exporters.
    """
    if not fragment or "<" not in fragment:
        return (fragment or "").strip()

    fragment = _numbered_marks(fragment)

    parts: list[str] = []
    cursor = 0
    notes = 0
    for match in _CONSTRUCT.finditer(fragment):
        parts.extend(_paragraphs(fragment[cursor : match.start()]))
        if match.group(0)[:6].lower().startswith("<small"):
            notes += 1
        rendered = _render(match, notes)
        if rendered:
            parts.append(rendered)
        cursor = match.end()
    parts.extend(_paragraphs(fragment[cursor:]))
    return "\n\n".join(part for part in parts if part.strip())


#: A footnote mark in the prose. The 서식 asks for `<sup>*</sup>`, `<sup>**</sup>`.
_SUP = re.compile(r"<sup\b[^>]*>.*?</sup\s*>", re.S | re.I)


def _numbered_marks(fragment: str) -> str:
    """`<sup>*</sup>` → `[^1]`, in the order they appear.

    The mark the writer typed is thrown away rather than carried across. It is
    `*`, `**`, `***` — a notation that runs out at three and that a reader of
    the exported file has no way to match to a note, because Word will renumber
    the notes anyway. Position is the pairing the 서식 asked for and position is
    what survives.

    Counted separately from the notes so a fragment with a mark and no note, or
    a note and no mark, still converts — mismatched halves are a fault for the
    checks to report, not a reason to lose the text of either.
    """
    counter = iter(range(1, 1000))
    return _SUP.sub(lambda _m: f"[^{next(counter)}]", fragment)


def _paragraphs(fragment: str) -> list[str]:
    """Whatever sits between two constructs, split on block boundaries."""
    if not fragment.strip():
        return []
    # Block tags become the split points; everything else is inline and stays
    # with the line it belongs to.
    chunks = _BLOCK.split(fragment)
    # `re.split` on a pattern with groups interleaves the captures; the tag
    # names are of no use here, so only the text between them is kept.
    text_chunks = chunks[::2] if len(chunks) > 1 else chunks
    out = [_inline(chunk) for chunk in text_chunks]
    return [line for line in out if line]


#: A GFM table row. The rule row under the head matches too.
_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def tidy_tables(text: str) -> str:
    """Close the gaps a model leaves between table rows.

    GFM wants a table's rows on consecutive lines. Models write them with a
    blank line between each one, which is legible in the raw Markdown and is
    not a table to any renderer — `remark-gfm` in the web view drew it as
    literal pipes, one paragraph per row, and so did everything downstream.

    Fixed here rather than only in the renderers because the stored text is
    what the web view reads, what the exporters read, and what somebody sees if
    they open the Markdown. One of those rendering a table while the others
    print pipes is worse than all of them printing pipes.

    Only blank lines *between* pipe rows go. A blank line after the last row
    still ends the table, or two tables in a row would merge into one.
    """
    lines = (text or "").split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        if line.strip():
            out.append(line)
            continue
        # A blank line survives unless it sits between two rows of one table.
        before = next((out[i] for i in range(len(out) - 1, -1, -1) if out[i].strip()), "")
        after = next((lines[i] for i in range(index + 1, len(lines)) if lines[i].strip()), "")
        if _PIPE_ROW.match(before) and _PIPE_ROW.match(after):
            continue
        out.append(line)
    return "\n".join(out)


def as_markdown(section: dict) -> str:
    """One section's body as Markdown, whichever way it was stored.

    The single place that knows about `format`. Every export path calls this
    instead of reading `content` directly, so a report somebody edited in the
    document editor draws the same as one the model wrote.
    """
    body = str(section.get("content") or "")
    return to_markdown(body) if section.get("format") == "html" else body


def normalise(sections: list[dict]) -> list[dict]:
    """Sections with every body as Markdown. For the exporters and the model."""
    return [
        {**section, "content": as_markdown(section), "format": "markdown"}
        for section in sections
    ]


__all__ = ["as_markdown", "normalise", "tidy_tables", "to_markdown"]
