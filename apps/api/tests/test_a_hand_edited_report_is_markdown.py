"""Hand-edited reports are Markdown: HTML from an older client is converted, styling dropped,
and what Markdown carries (emphasis, lists, quotes, task boxes) reaches the exports."""

from __future__ import annotations

import io
import zipfile

from pypdf import PdfReader

from app.services import report_export, richtext
from app.services.design_templates import sanitise

# ── styling is not kept: precise formatting is the HWP/Word outputs' job ──


def test_no_inline_style_survives_the_sanitiser():
    kept = sanitise('<p style="font-size: 18pt; color: #c00; text-align: center">가</p>')
    assert kept == "<p>가</p>"


def test_a_script_is_dropped():
    assert sanitise('<p onclick="steal()">가</p><script>steal()</script>') == "<p>가</p>"


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
    # No stale "html" flag after conversion, and no styling riding along.
    assert {s["format"] for s in out} == {"markdown"}
    assert not any("_formatting" in s for s in out)
    # The originals are untouched.
    assert sections[0]["content"] == "<p>본문</p>"


def test_an_html_section_is_stored_as_markdown_with_its_styling_dropped():
    section = richtext.markdown_section({
        "id": "s1",
        "format": "html",
        "content": '<h3>소제목</h3><p style="color: #c00"><strong>굵게</strong> <s>취소</s></p>'
                   "<ul><li>가</li><li>나</li></ul><table><tr><th>기준</th><th>값</th></tr>"
                   "<tr><td colspan='2'>합침</td></tr></table>",
    })
    assert section["format"] == "markdown"
    assert section["content"].splitlines() == [
        "### 소제목", "", "**굵게** ~~취소~~", "", "- 가", "- 나", "",
        "| 기준 | 값 |", "| --- | --- |", "| 합침 |  |",
    ]
    markdown = {"format": "markdown", "content": "그대로"}
    assert richtext.markdown_section(markdown) is markdown


def test_markdown_emphasis_reaches_docx_pdf_and_hwpx():
    sections = [{"heading": "서식", "content": "**굵게** *기울임* ~~취소~~ 그리고 \\*별표\\*"}]

    archive = zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", sections)))
    document = archive.read("word/document.xml").decode()
    for expected in ("<w:b/>", "<w:i/>", "<w:strike/>"):
        assert expected in document
    # escapes the editor writes are not printed
    assert "*별표*" in document and "\\*" not in document

    pdf = PdfReader(io.BytesIO(report_export.to_pdf("제목", sections)))
    text = pdf.pages[0].extract_text() or ""
    assert "굵게" in text and "~~" not in text and "**" not in text

    hwpx = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", sections)))
    header = hwpx.read("Contents/header.xml").decode()
    assert "<hh:bold/>" in header and "<hh:italic/>" in header


def test_quotes_rules_and_task_boxes_are_written_as_text():
    lines = report_export._markdown_to_lines("> 인용\n\n---\n\n- [ ] 할 일\n- [x] 한 일")
    assert [(kind, text) for kind, text, *_ in lines] == [
        ("body", "인용"), ("bullet", "☐ 할 일"), ("bullet", "☑ 한 일"),
    ]


def test_a_nested_list_under_numbers_keeps_its_level():
    lines = report_export._markdown_to_lines("1. 하나\n   - 안쪽\n      - 더 안쪽\n2. 둘")
    assert [depth for *_, depth in lines] == [0, 1, 2, 0]
