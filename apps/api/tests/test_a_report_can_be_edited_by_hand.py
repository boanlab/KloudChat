"""A report somebody typed in is still a report everything else can read.

A section used to be Markdown and nothing else, because the only author was the
model. Once a person edits the document, that stops holding: size, face,
alignment and an emphasis colour have no Markdown, and a table typed into a
word processor is not a run of `|` characters. So an edited section is stored
as HTML and marked `format: "html"`.

Two things have to stay true after that change, and neither is obvious:

**The markup is not trusted.** It arrives on a PATCH from a browser, and past
that boundary the same string is rendered into a panel, written into a `.docx`
and served from a share link. `sanitise` runs at the boundary, with the narrow
allowlist a person may actually set — and nothing else. What is missing from
that allowlist is the point of it: no layout, so a template is still a template
after somebody has typed in it.

**Every consumer still sees Markdown.** Three exporters parse it a line at a
time, the reviewer scores prose, and the rewriter reads the neighbouring
sections for continuity. Handed `<p style=…>`, each of those does something
visibly wrong rather than failing — tags drawn as text in a PDF, findings spent
on markup instead of the argument.
"""

from __future__ import annotations

import io
import zipfile

from pypdf import PdfReader

from app.services import report_export, richtext
from app.services.design_templates import sanitise

# ── what a person may set, and what they may not ───────────────────────


def test_the_four_things_a_toolbar_offers_survive():
    kept = sanitise(
        '<p style="font-size: 18pt; font-family: Batang; text-align: center; color: #c00">가</p>',
        editable_styles=True,
    )
    for declaration in ("font-size: 18pt", "font-family: Batang", "text-align: center"):
        assert declaration in kept
    assert "color: #c00" in kept


def test_browser_canonical_rgb_colours_are_kept_as_safe_hex():
    kept = sanitise(
        '<p><span style="color: rgb(204, 0, 0); '
        'background-color: rgb(255, 243, 163)">가</span></p>',
        editable_styles=True,
    )
    assert "color: #cc0000" in kept
    assert "background-color: #fff3a3" in kept
    assert "rgb(" not in kept


def test_layout_is_still_the_template_s():
    # The whole value of a 서식 is that it decides the layout. A person setting
    # `position` or a margin is a person quietly leaving the template behind.
    kept = sanitise(
        '<p style="position: fixed; margin: 0 0 40px; display: grid; font-size: 12pt">가</p>',
        editable_styles=True,
    )
    assert "font-size: 12pt" in kept
    for banned in ("position", "margin", "display"):
        assert banned not in kept


def test_a_value_with_a_function_call_is_dropped_whole():
    # `expression(` is legacy-IE only and `url(` fetches. Neither belongs in a
    # file that is also downloaded and opened outside the sandbox.
    assert "expression" not in sanitise(
        '<p style="font-size: expression(alert(1))">가</p>', editable_styles=True
    )
    assert "url(" not in sanitise(
        '<p style="background: url(http://example.com/x.png)">가</p>', editable_styles=True
    )


def test_a_script_does_not_become_editable_because_a_person_typed_it():
    dirty = '<p onclick="steal()">가</p><script>steal()</script>'
    assert sanitise(dirty, editable_styles=True) == "<p>가</p>"


def test_the_model_still_gets_no_inline_style_at_all():
    # A writer that invents its own type scale produces a document that
    # disagrees with itself on every page. The default is unchanged.
    assert sanitise('<p style="font-size: 18pt">가</p>') == "<p>가</p>"


# ── and everything downstream still reads Markdown ─────────────────────


def test_a_typed_table_survives_as_a_table():
    # Flattened to a bullet, it is a table somebody rebuilds by hand after
    # every export.
    markdown = richtext.to_markdown(
        "<table><thead><tr><th>기준</th><th>값</th></tr></thead>"
        "<tbody><tr><td>비용</td><td>3억</td></tr></tbody></table>"
    )
    assert markdown.splitlines() == ["| 기준 | 값 |", "| --- | --- |", "| 비용 | 3억 |"]


def test_lists_keep_their_kind():
    assert richtext.to_markdown("<ul><li>가</li><li>나</li></ul>") == "- 가\n- 나"
    assert richtext.to_markdown("<ol><li>가</li><li>나</li></ol>") == "1. 가\n2. 나"


def test_a_sub_heading_never_lands_as_a_title():
    # The section's own heading is drawn by the wrapper, so a heading in the
    # body is always below it. `_markdown_to_lines` reads two hashes or more.
    assert richtext.to_markdown("<h1>소제목</h1>").startswith("## ")
    assert richtext.to_markdown("<h3>소제목</h3>").startswith("### ")


def test_formatting_that_has_no_markdown_is_dropped_not_approximated():
    # Half-applied emphasis reads worse than none — the call `_strip_inline`
    # already makes in the exporters.
    assert richtext.to_markdown('<p style="font-size: 18pt">큰 글씨</p>') == "큰 글씨"


def test_emphasis_that_does_have_markdown_is_kept():
    assert richtext.to_markdown("<p><strong>가</strong>와 <em>나</em></p>") == "**가**와 *나*"


def test_prose_around_a_table_keeps_its_order():
    markdown = richtext.to_markdown("<p>앞</p><table><tr><td>셀</td></tr></table><p>뒤</p>")
    assert markdown.index("앞") < markdown.index("셀") < markdown.index("뒤")


def test_a_markdown_section_is_left_exactly_alone():
    # The overwhelming majority of sections, and every one written before the
    # editor shipped. Round-tripping them through the converter would rewrite
    # documents nobody edited.
    section = {"content": "- 가\n- 나", "format": "markdown"}
    assert richtext.as_markdown(section) == "- 가\n- 나"
    assert richtext.as_markdown({"content": "본문"}) == "본문"


def test_normalise_marks_what_it_converted():
    sections = [
        {"heading": "가", "content": "<p>본문</p>", "format": "html"},
        {"heading": "나", "content": "그대로"},
    ]
    out = richtext.normalise(sections)
    assert [s["content"] for s in out] == ["본문", "그대로"]
    # Every body is Markdown now, so every flag says so — a consumer that
    # checks the flag rather than the caller must not see a stale "html".
    assert {s["format"] for s in out} == {"markdown"}
    # The originals are untouched: the exporters take a normalised copy, and
    # the stored document is still the one the person typed.
    assert sections[0]["content"] == "<p>본문</p>"


def test_editable_run_and_paragraph_formatting_reaches_docx_and_pdf():
    html = (
        '<p style="text-align: center; line-height: 1.5">'
        '<strong><em><u><span style="font-size: 18pt; color: #cc0000; '
        'background-color: #fff3a3">강조 문장</span></u></em></strong></p>'
    )
    sections = richtext.normalise([
        {"heading": "서식 검증", "content": html, "format": "html"}
    ])
    block = sections[0]["_formatting"][0]
    assert block["style"]["text-align"] == "center"
    assert block["style"]["line-height"] == "1.5"
    assert block["runs"][0]["style"]["font-size"] == "18pt"

    archive = zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", sections)))
    document = archive.read("word/document.xml").decode()
    for expected in (
        'w:val="center"', 'w:line="360"', '<w:b', '<w:i', '<w:u',
        'w:sz w:val="36"', 'w:color w:val="CC0000"', 'w:fill="FFF3A3"',
    ):
        assert expected in document

    pdf = PdfReader(io.BytesIO(report_export.to_pdf("제목", sections)))
    assert "강조 문장" in (pdf.pages[0].extract_text() or "")
    stream = pdf.pages[0].get_contents().get_data()
    assert b"18 Tf" in stream or b"18.0 Tf" in stream
    assert b".8 0 0 rg" in stream or b"0.8 0 0 rg" in stream

    hwpx = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", sections)))
    header = hwpx.read("Contents/header.xml").decode()
    section = hwpx.read("Contents/section0.xml").decode()
    assert 'height="1800" textColor="#CC0000" shadeColor="#FFF3A3"' in header
    assert "<hh:bold/>" in header
    assert "<hh:italic/>" in header
    assert '<hh:underline type="BOTTOM" shape="SOLID"' in header
    assert '<hh:align horizontal="CENTER"' in header
    assert '<hh:lineSpacing type="PERCENT" value="150"' in header
    # The body must actually reference both dynamic definitions; definitions
    # sitting unused in header.xml would only make the file look correct.
    assert 'paraPrIDRef="10"' in section
    assert 'charPrIDRef="6"' in section
