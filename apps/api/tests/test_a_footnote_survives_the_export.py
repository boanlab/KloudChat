"""근거를 각주로 다는 서식이 실제로 각주를 내보내는가.

`doc-report` tells the writer to mark a cited sentence with `<sup>*</sup>` and
put the note in a `<small>` beside it, and the seed's CSS gathers those at the
foot of the section. Everything downstream of the screen lost them, and lost
them in a way nobody would notice from the code: `<small>` was not a block, so
two notes ran together into one sentence, and the first — which begins `* ` —
was read back as a bullet. A report whose whole point is that every figure has
a source came out of the exporters with its sources turned into a list item.
"""

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
    # The writer's own mark is gone: `*` and `**` cannot be matched to anything
    # by a reader of the exported file, because Word renumbers its notes.
    assert "* 보안운영팀" not in markdown


def test_a_note_is_not_a_bullet() -> None:
    kinds = [k for k, _, _ in report_export._markdown_to_lines(richtext.to_markdown(_CITED))]
    assert kinds == ["body", "body", "note", "note"], kinds


def test_the_mark_and_the_note_are_paired_by_position() -> None:
    """The pairing the 서식's own checklist asks for, and the only one available.

    `*`/`**`/`***` runs out at three and means nothing to a reader of the file.
    Order is what the writer was told to keep, so order is what is used.
    """
    markdown = richtext.to_markdown(_CITED)
    assert "오탐이 32% 줄었다[^1]" in markdown
    assert "감소가 이어졌다[^2]" in markdown
    lines = [line for line in markdown.splitlines() if line.startswith("[^")]
    assert lines[0].startswith("[^1]:") and lines[1].startswith("[^2]:")


def test_word_gets_real_footnotes() -> None:
    """A part, a relationship, a content type and two reference runs.

    Real footnotes rather than small type after the paragraph, because that is
    the difference the reader can use: Word's own footnote sits on the page its
    mark is on, renumbers itself when a paragraph moves, and survives being
    pasted into somebody else's document.
    """
    data = report_export.to_docx("제목", _sections())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "word/footnotes.xml" in archive.namelist()
        footnotes = archive.read("word/footnotes.xml").decode()
        document = archive.read("word/document.xml").decode()
        types = archive.read("[Content_Types].xml").decode()
        rels = archive.read("word/_rels/document.xml.rels").decode()

    # The separators Word draws above a page's notes. Without them Word offers
    # to repair the file, which is what the reader sees instead of a document.
    assert 'w:type="separator"' in footnotes
    assert 'w:type="continuationSeparator"' in footnotes
    assert footnotes.count("<w:footnote ") == 4  # two separators, two notes
    assert "보안운영팀 2025년 4분기 집계." in footnotes

    # Marks in the prose, pointing at ids 1 and 2 — 0 and -1 are separators.
    assert 'w:footnoteReference w:id="1"' in document
    assert 'w:footnoteReference w:id="2"' in document
    # And the note text is *not* in the prose. That was the old behaviour and
    # it read as a sentence the writer left in the middle of the page.
    assert "보안운영팀" not in document

    assert "footnotes+xml" in types
    assert "footnotes.xml" in rels


def test_a_document_with_no_notes_has_no_footnote_part() -> None:
    # An empty `footnotes.xml` is a part Word has to be told to ignore, and
    # every document that never cited anything would carry one.
    plain = [{"heading": "성과", "content": "각주 없는 문단."}]
    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", plain))) as archive:
        assert "word/footnotes.xml" not in archive.namelist()


def test_the_other_two_formats_put_the_notes_under_the_section() -> None:
    """Neither has a footnote model, so they do what the screen does.

    reportlab composes a flowing story and a real footnote is a page-layout
    construct; OWPML has `<hp:footNote>` and a malformed one is a file Hancom
    refuses to open. Gathered under the section, numbered, in small type — the
    same shape the 서식 draws — is available in both for the cost of a
    paragraph.
    """
    assert report_export.to_pdf("제목", _sections()).startswith(b"%PDF")

    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", _sections()))) as archive:
        body = archive.read("Contents/section0.xml").decode()
        header = archive.read("Contents/header.xml").decode()
    assert "보안운영팀 2025년 4분기 집계." in body
    assert "같은 자료, 2026년 1분기." in body
    # Set smaller than the prose, and the shape it refers to is defined.
    assert 'charPrIDRef="5"' in body
    assert 'id="5"' in header


def test_a_note_with_no_paragraph_before_it_is_still_written() -> None:
    """Mismatched halves are a fault for the checks to report, not to lose text over."""
    orphan = [{"heading": "성과", "content": "[^1]: 출처만 있고 본문이 없다."}]
    data = report_export.to_docx("제목", orphan)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode()
    assert "출처만 있고 본문이 없다." in document


# ── 표시가 페이지에 새지 않는가 ────────────────────────────────────────

def test_the_notation_never_reaches_the_page() -> None:
    """`[^1]` is for the exporters to read, not for anybody to see.

    Missed the first time round, in all three formats at once, because the
    tests checked that the *note* arrived and never that the *mark* had gone.
    A reader opening the file saw `줄었다[^1].` — four characters of
    punctuation in the middle of a sentence, which is worse than the `*` it
    replaced, because at least `*` looked deliberate.
    """
    sections = _sections()

    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", sections))) as archive:
        assert "[^1]" not in archive.read("word/document.xml").decode()

    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", sections))) as archive:
        body = archive.read("Contents/section0.xml").decode()
    assert "[^1]" not in body
    # Raised instead. A single character, so no second character shape and no
    # extra id that has to be right for Hancom to open the file.
    assert "줄었다¹" in body
    # And the note carries the same mark, or the pair cannot be matched up.
    assert "¹ 보안운영팀" in body

    pdf = report_export.to_pdf("제목", sections)
    assert pdf.startswith(b"%PDF")


def test_the_word_mark_sits_where_the_writer_put_it() -> None:
    """Both marks landed at the end of the second paragraph.

    The line was written as one run and the reference hung off the end of it,
    so a note cited in the first sentence pointed from after the full stop of
    whichever paragraph happened to come before the note — and with two notes
    in a section they piled up side by side.
    """
    data = report_export.to_docx("제목", _sections())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode()

    first = document.index("줄었다")
    second = document.index("이어졌다")
    one = document.index('w:footnoteReference w:id="1"')
    two = document.index('w:footnoteReference w:id="2"')
    # Mark 1 is inside the first sentence's paragraph, mark 2 inside the
    # second's — not both after the second.
    assert first < one < second < two


def test_a_mark_with_no_note_is_dropped() -> None:
    """The notation leaking, rather than a footnote."""
    orphan = [{"heading": "성과", "content": "출처가 없는 문장[^7]."}]
    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", orphan))) as archive:
        document = archive.read("word/document.xml").decode()
    assert "[^7]" not in document
    assert "w:footnoteReference" not in document
    assert "출처가 없는 문장" in document
