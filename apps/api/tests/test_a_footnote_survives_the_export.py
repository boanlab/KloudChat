"""`<sup>*</sup>`/`<small>` footnotes survive the docx, PDF and HWPX exports."""

from __future__ import annotations

import io
import zipfile

from app.services import report_export, richtext

_CITED = (
    "<p>도입 첫해 오탐이 32% 줄었다<sup>*</sup>.</p>"
    "<p>이후에도 감소가 이어졌다<sup>**</sup>.</p>"
    "<small>* 보안운영팀 2025년 4분기 집계.</small>"
    "<small>** 같은 자료, 2026년 1분기.</small>"
)


def _sections() -> list[dict]:
    return richtext.normalise([{"heading": "성과", "content": _CITED, "format": "html"}])


def test_two_notes_stay_two_notes() -> None:
    markdown = richtext.to_markdown(_CITED)
    lines = [line for line in markdown.splitlines() if line.strip()]
    notes = [line for line in lines if line.startswith("[^")]
    assert len(notes) == 2, markdown
    assert notes[0].endswith("보안운영팀 2025년 4분기 집계.")
    assert notes[1].endswith("같은 자료, 2026년 1분기.")
    # The writer's `*` mark is gone; Word renumbers its notes.
    assert "* 보안운영팀" not in markdown


def test_a_note_is_not_a_bullet() -> None:
    kinds = [k for k, _, _, _d in report_export._markdown_to_lines(richtext.to_markdown(_CITED))]
    assert kinds == ["body", "body", "note", "note"], kinds


def test_the_mark_and_the_note_are_paired_by_position() -> None:
    """Marks and notes are paired by order, not by the `*` run."""
    markdown = richtext.to_markdown(_CITED)
    assert "오탐이 32% 줄었다[^1]" in markdown
    assert "감소가 이어졌다[^2]" in markdown
    lines = [line for line in markdown.splitlines() if line.startswith("[^")]
    assert lines[0].startswith("[^1]:") and lines[1].startswith("[^2]:")


def test_word_gets_real_footnotes() -> None:
    """The docx carries real footnotes: part, relationship, content type, reference runs."""
    data = report_export.to_docx("제목", _sections())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "word/footnotes.xml" in archive.namelist()
        footnotes = archive.read("word/footnotes.xml").decode()
        document = archive.read("word/document.xml").decode()
        types = archive.read("[Content_Types].xml").decode()
        rels = archive.read("word/_rels/document.xml.rels").decode()

    # Without the separators Word offers to repair the file.
    assert 'w:type="separator"' in footnotes
    assert 'w:type="continuationSeparator"' in footnotes
    assert footnotes.count("<w:footnote ") == 4  # two separators, two notes
    assert "보안운영팀 2025년 4분기 집계." in footnotes

    # Ids 1 and 2 are notes; 0 and -1 are separators.
    assert 'w:footnoteReference w:id="1"' in document
    assert 'w:footnoteReference w:id="2"' in document
    # The note text is not in the prose.
    assert "보안운영팀" not in document

    assert "footnotes+xml" in types
    assert "footnotes.xml" in rels


def test_a_document_with_no_notes_has_no_footnote_part() -> None:
    # No empty `footnotes.xml` part.
    plain = [{"heading": "성과", "content": "각주 없는 문단."}]
    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", plain))) as archive:
        assert "word/footnotes.xml" not in archive.namelist()


def test_the_other_two_formats_put_the_notes_under_the_section() -> None:
    """PDF and HWPX gather numbered notes in small type under the section."""
    assert report_export.to_pdf("제목", _sections()).startswith(b"%PDF")

    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", _sections()))) as archive:
        body = archive.read("Contents/section0.xml").decode()
        header = archive.read("Contents/header.xml").decode()
    assert "보안운영팀 2025년 4분기 집계." in body
    assert "같은 자료, 2026년 1분기." in body
    # Smaller than the prose, and the char shape it references is defined.
    assert 'charPrIDRef="5"' in body
    assert 'id="5"' in header


def test_a_note_with_no_paragraph_before_it_is_still_written() -> None:
    """A note with no preceding paragraph is still written."""
    orphan = [{"heading": "성과", "content": "[^1]: 출처만 있고 본문이 없다."}]
    data = report_export.to_docx("제목", orphan)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode()
    assert "출처만 있고 본문이 없다." in document


# ── 표시가 페이지에 새지 않는가 ────────────────────────────────────────

def test_the_notation_never_reaches_the_page() -> None:
    """`[^1]` never appears in any exported format."""
    sections = _sections()

    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", sections))) as archive:
        assert "[^1]" not in archive.read("word/document.xml").decode()

    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", sections))) as archive:
        body = archive.read("Contents/section0.xml").decode()
    assert "[^1]" not in body
    # A single superscript character: no extra char shape or id.
    assert "줄었다¹" in body
    # The note carries the same mark.
    assert "¹ 보안운영팀" in body

    pdf = report_export.to_pdf("제목", sections)
    assert pdf.startswith(b"%PDF")


def test_the_word_mark_sits_where_the_writer_put_it() -> None:
    """Each docx footnote reference sits in the paragraph that cited it."""
    data = report_export.to_docx("제목", _sections())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode()

    first = document.index("줄었다")
    second = document.index("이어졌다")
    one = document.index('w:footnoteReference w:id="1"')
    two = document.index('w:footnoteReference w:id="2"')
    # Mark 1 in the first paragraph, mark 2 in the second.
    assert first < one < second < two


def test_a_mark_with_no_note_is_dropped() -> None:
    """A mark without a note is dropped rather than leaked."""
    orphan = [{"heading": "성과", "content": "출처가 없는 문장[^7]."}]
    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", orphan))) as archive:
        document = archive.read("word/document.xml").decode()
    assert "[^7]" not in document
    assert "w:footnoteReference" not in document
    assert "출처가 없는 문장" in document
