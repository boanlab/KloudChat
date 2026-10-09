"""A document format's own CSS for pages the server renders: the gallery preview.

The web draws a formatted document with `formatCss` (`apps/web/src/components/report/
docFormats.ts`): the head block's look, heading numbers, 개조식 lists and, for the 1쪽 보고,
its own scale. The fixed rules are kept verbatim in `design_templates/_document/format.css`
and the rule builders are ported below; `tests/test_template_previews.py` keeps both equal
to the TypeScript, so the card shows what a finished document looks like.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import doc_formats, doc_type, placeholders

_SOURCE = Path(__file__).resolve().parents[1] / "design_templates" / "_document" / "format.css"


def _part(name: str) -> str:
    """The text between `/* @begin name */` and `/* @end name */` in `format.css`."""
    found = re.search(
        rf"/\* @begin {name} \*/(.*?)/\* @end {name} \*/",
        _SOURCE.read_text(encoding="utf-8"),
        re.S,
    )
    if found is None:
        raise RuntimeError(f"format.css has no {name}")
    return found.group(1)


#: `placeholders.ts` PLACEHOLDER_CSS.
PLACEHOLDER_CSS = _part("PLACEHOLDER_CSS")
#: `docFormats.ts` TITLE_BLOCK_CSS, PLACEHOLDER_CSS interpolated.
TITLE_BLOCK_CSS = _part("TITLE_BLOCK_CSS")
#: `docFormats.ts` GAEJO_CSS.
GAEJO_CSS = _part("GAEJO_CSS")
#: `docFormats.ts` CORE_CSS.
CORE_CSS = _part("CORE_CSS")

#: `docFormats.ts` CONTENT_NUMBER.
_CONTENT_NUMBER = {
    "decimal": 'counter(tba) "." counter(tbb)',
    "roman": 'counter(tbb, upper-alpha) "."',
    "korean": 'counter(tbb, hangul) "."',
    "official": 'counter(tbb, hangul) "."',
}


def numbering_css(numbering: str) -> str:
    """`docFormats.ts` numberingCss."""
    off = ".fmt h2::before { content: none !important; }"
    if numbering == "none":
        return off
    return (
        f"{off}\n"
        ".fmt :is(h1, h2, h3, h4, h5, h6)[data-num]::before { content: attr(data-num) !important; "
        "margin-right: 0.45em; }\n"
        ".fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6):not(.callout *, .cards *) "
        "{ counter-increment: tbb; }\n"
        ".fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6):not(.callout *, .cards *)"
        f"::before {{ content: {_CONTENT_NUMBER[numbering]} !important; margin-right: 0.45em; }}"
    )


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def format_scale_css(format_id: str) -> str:
    """`docType.ts` formatScaleCss: the format's own sizes and spacing, or ``""``."""
    sizes = doc_type.FORMAT_TYPE.get(format_id)
    space = doc_type.FORMAT_SPACE.get(format_id)
    if not sizes or not space:
        return ""
    leading = doc_type.FORMAT_LEADING.get(format_id, {})
    margins = doc_type.FORMAT_MARGINS.get(format_id)
    variables = " ".join(
        [f"--doc-{name}: {_number(pt)}pt;" for name, pt in sizes.items()]
        + [f"--doc-leading-{name}: {_number(ratio)};" for name, ratio in leading.items()]
    )
    at = f".fmt.f-{format_id}"
    s = {key: _number(value) for key, value in space.items()}
    page = (
        f"@page {{ margin: {_number(margins['top'])}mm {_number(margins['right'])}mm "
        f"{_number(margins['bottom'])}mm {_number(margins['left'])}mm; }}"
        if margins
        else ""
    )
    return (
        f"\n{at} {{ {variables} font-size: var(--doc-body) !important; "
        "line-height: var(--doc-leading-body) !important; }\n"
        f"{at} section {{ margin: 0 !important; }}\n"
        f"{at} h2 {{ margin: {s['headingBefore']}pt 0 {s['headingAfter']}pt !important; "
        "font-size: var(--doc-h1) !important; }\n"
        f"{at} :is(.callout, .tb-core) {{ margin: {s['callout']}pt 0 !important; }}\n"
        f"{at} .tb {{ margin: 0 0 {s['head']}pt !important; }}\n"
        f"{at} ul:not(.steps) > li, {at} ul:not(.steps) ul {{ margin-top: 0 !important; "
        f"margin-bottom: {s['item']}pt !important; }}\n"
        f"{at} ul:not(.steps) {{ margin: 0 !important; }}\n"
        f"{at} table {{ margin: {s['callout']}pt 0 !important; }}\n"
        f"{at} :is(th, td) {{ padding: 0.15em 0.5em !important; height: auto !important; }}\n"
        f"{page}"
    )


def one_page(fmt: doc_formats.DocFormat) -> bool:
    """`docFormats.ts` onePage: the format has its own scale and spacing."""
    return fmt.id in doc_type.FORMAT_SPACE


def fmt_class(fmt: doc_formats.DocFormat) -> str:
    """`docFormats.ts` fmtClass."""
    return (
        "fmt"
        + (" gaejo" if fmt.lists == "gaejo" else "")
        + (f" tb-scaled f-{fmt.id}" if one_page(fmt) else "")
    )


def format_css(fmt: doc_formats.DocFormat) -> str:
    """`docFormats.ts` formatCss, with the placeholder mark the paged preview adds."""
    return (
        f"{TITLE_BLOCK_CSS}\n{numbering_css(fmt.numbering)}"
        + (f"\n{GAEJO_CSS}" if fmt.lists == "gaejo" else "")
        + (f"\n{CORE_CSS}{format_scale_css(fmt.id)}" if one_page(fmt) else "")
        + f"\n{PLACEHOLDER_CSS}"
    )


#: The paged preview's own rules that a head block meets (`pagedSheet.ts`): a cover's height
#: is its own; a cover page ends at its page break, not at a screen height.
PAGED_CSS = ".cover { min-height: 0 !important; }"

# `docFormats.ts` CALLOUT_HTML and CONTENT_HEADING_HTML.
_CALLOUT = re.compile(
    r"(<section\b[^>]{0,200}\b(?:callout|cards)\b[^>]{0,200}>[\s\S]*?</section>)", re.I
)
_HEADING = re.compile(r"<(h[2-6])(\s[^>]*)?>", re.I)
_SECTION_HEADING = re.compile(r"<section\b[^>]*>\s*$", re.I)


def number_headings(html: str, numbering: str) -> str:
    """Each section heading and content heading given its `data-num`, as the paged preview
    numbers them (`sectionNumbers`, `numberHtmlHeadings`); a callout's or card's title is
    not counted."""
    if numbering == "none":
        return html
    a = b = 0
    out: list[str] = []
    for at, part in enumerate(_CALLOUT.split(html)):
        if at % 2:
            out.append(part)
            continue
        cursor = 0
        for found in _HEADING.finditer(part):
            out.append(part[cursor : found.start()])
            cursor = found.end()
            tag, rest = found.group(1), found.group(2) or ""
            if tag.lower() == "h2" and _SECTION_HEADING.search(part[: found.start()]):
                a, b = a + 1, 0
                label = doc_formats.number_heading(numbering, 1, [a, b, 0])
            else:
                b += 1
                label = doc_formats.number_heading(numbering, 2, [a, b, 0])
            out.append(f'<{tag} data-num="{label}"{rest}>')
        out.append(part[cursor:])
    return "".join(out)


_SKIP = re.compile(r"^(?:code|pre|a|script|style|textarea|title)$", re.I)
_OPEN = re.compile(r"^<(/?)([A-Za-z][A-Za-z0-9-]*)")


def mark_placeholders(html: str) -> str:
    """`placeholders.ts` markPlaceholders: each 「[담당자]」 in the text marked as a blank."""
    skipping = 0
    marked = False
    out: list[str] = []
    for part in re.split(r"(<[^>]{0,4000}>)", html):
        tag = _OPEN.match(part)
        if tag:
            if _SKIP.match(tag.group(2)) and not part.endswith("/>"):
                skipping = max(0, skipping + (-1 if tag.group(1) else 1))
            marked = not tag.group(1) and "doc-placeholder" in part
            out.append(part)
            continue
        out.append(
            part
            if skipping or marked
            else placeholders.PLACEHOLDER.sub(r'<span class="doc-placeholder">\g<0></span>', part)
        )
    return "".join(out)


def preview(fmt: doc_formats.DocFormat, sample: str) -> tuple[str, str]:
    """`(css, body)` for a sample written in `fmt`: the body in the formatted document's own
    element, its headings numbered and its blanks marked, and the rules that draw it."""
    body = mark_placeholders(number_headings(sample, fmt.numbering))
    return f"{format_css(fmt)}\n{PAGED_CSS}", f'<div class="{fmt_class(fmt)}">{body}</div>'
