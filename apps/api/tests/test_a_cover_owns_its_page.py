"""Cover head as a page of its own, measured in page millimetres."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import design_templates, doc_formats, format_css

_TEMPLATES = Path(design_templates.__file__).resolve().parents[1] / "design_templates"


def _rule(css: str, selector: str) -> str:
    found = re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert found, selector
    return found.group(1)


def test_the_format_cover_is_a_page_with_the_title_on_the_upper_third():
    css = format_css.TITLE_BLOCK_CSS
    page = _rule(css, ".tb.tb-cover.cover")
    assert "padding: 72mm 0 0" in page and "break-after: page" in page
    assert "width: 64%" in _rule(css, ".tb.tb-cover .tb-fields")
    # Three lines between the title and the head table on the cover page.
    assert "margin-top: 16mm" in _rule(css, ".tb.tb-cover.cover .tb-fields")
    assert "var(--doc-cover" in _rule(css, ".tb.tb-cover .tb-title")
    assert "var(--doc-coverLabel" in _rule(css, ".tb.tb-cover .tb-label")


def test_the_seed_cover_page_has_its_bar_and_break():
    seed = (_TEMPLATES / "_document" / "seed.html").read_text(encoding="utf-8")
    page = _rule(seed, ".cover.cover-page, .cover.tb-cover")
    assert "border-top: var(--cover-bar, 3px) solid var(--accent)" in page
    assert "break-after: page" in page and "padding: 72mm 0 0" in page
    # A plain cover (a memo or field-table 서식) still heads page one.
    assert "break-after: auto" in _rule(seed, ".cover")
    assert design_templates.rendering("doc-report").wrap_cover.startswith(
        '<div class="cover cover-page">'
    )


@pytest.mark.parametrize(
    "path",
    [
        _TEMPLATES / "_document" / "seed.html",
        _TEMPLATES / "_document" / "format.css",
        *sorted(_TEMPLATES.glob("doc-*/design.css")),
    ],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_document_css_never_measures_the_page_in_vh(path: Path):
    css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    css = re.sub(r"<!--.*?-->", "", css, flags=re.S)
    assert not re.search(r"\d(?:\.\d+)?vh\b|,\s*100vh\)", css), path


def test_the_preview_keeps_the_cover_page():
    """The gallery preview is laid out in pages (`PagedPreview`): nothing in the preview's own
    rules may cancel the cover's page break."""
    assert "break-after" not in format_css.PAGED_CSS
    page = design_templates.preview(design_templates.get("doc-client-proposal"), {})
    assert '<header class="tb tb-cover cover">' in page
    assert "--cover-bar: 8px" in page


@pytest.mark.parametrize("fmt", [f for f in doc_formats.FORMATS if f.head == "cover"], ids=str)
def test_every_cover_format_draws_the_cover_head(fmt):
    # The web draws `tb tb-cover` (+ `cover` in a page view) for exactly these.
    assert fmt.head == "cover" and fmt.label
