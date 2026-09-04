"""Hand-edited sections: sanitised at the boundary, stored as HTML, read as Markdown downstream."""

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
    # Layout stays the template's: no position, margin or display.
    kept = sanitise(
        '<p style="position: fixed; margin: 0 0 40px; display: grid; font-size: 12pt">가</p>',
        editable_styles=True,
    )
    assert "font-size: 12pt" in kept
    for banned in ("position", "margin", "display"):
        assert banned not in kept


def test_a_value_with_a_function_call_is_dropped_whole():
    # `expression(` and `url(` are function calls; the file is opened outside the sandbox.
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
    # Model output gets no inline style by default.
    assert sanitise('<p style="font-size: 18pt">가</p>') == "<p>가</p>"


# ── and everything downstream still reads Markdown ─────────────────────


def test_a_typed_table_survives_as_a_table():
    markdown = richtext.to_markdown(
        "<table><thead><tr><th>기준</th><th>값</th></tr></thead>"
        "<tbody><tr><td>비용</td><td>3억</td></tr></tbody></table>"
    )
    assert markdown.splitlines() == ["| 기준 | 값 |", "| --- | --- |", "| 비용 | 3억 |"]


def test_lists_keep_their_kind():
    assert richtext.to_markdown("<ul><li>가</li><li>나</li></ul>") == "- 가\n- 나"
    assert richtext.to_markdown("<ol><li>가</li><li>나</li></ol>") == "1. 가\n2. 나"


def test_a_sub_heading_never_lands_as_a_title():
    # The wrapper draws the section heading; body headings start at `##`.
    assert richtext.to_markdown("<h1>소제목</h1>").startswith("## ")
    assert richtext.to_markdown("<h3>소제목</h3>").startswith("### ")


def test_formatting_that_has_no_markdown_is_dropped_not_approximated():
    assert richtext.to_markdown('<p style="font-size: 18pt">큰 글씨</p>') == "큰 글씨"


def test_emphasis_that_does_have_markdown_is_kept():
    assert richtext.to_markdown("<p><strong>가</strong>와 <em>나</em></p>") == "**가**와 *나*"


def test_prose_around_a_table_keeps_its_order():
    markdown = richtext.to_markdown("<p>앞</p><table><tr><td>셀</td></tr></table><p>뒤</p>")
    assert markdown.index("앞") < markdown.index("셀") < markdown.index("뒤")


def test_a_markdown_section_is_left_exactly_alone():
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
    # No stale "html" flag after conversion.
    assert {s["format"] for s in out} == {"markdown"}
    # The originals are untouched.
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
    # The body must reference both definitions, not only declare them.
    assert 'paraPrIDRef="10"' in section
    assert 'charPrIDRef="6"' in section
