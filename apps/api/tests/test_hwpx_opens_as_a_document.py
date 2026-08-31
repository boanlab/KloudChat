"""올린 한글 문서를 편집할 수 있는 문서로 여는 길.

The half that was missing. This server could already *write* `.hwpx` — the
report exporter builds OWPML — and *read* one as flat text for a model to refer
to. What it could not do was the thing somebody actually wants with a file they
uploaded: change it. The flat reader is right for its job and wrong for this
one, because it drops exactly what a person edits by: the headings that say
where to look and the tables that hold the numbers.

These check the shape survives, which is the only claim worth making. Whether
the words came through was never in doubt.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services import hwpx_import

_NS = (
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
)


def _archive(header: str, section: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Contents/header.xml", f"<hh:head {_NS}>{header}</hh:head>")
        archive.writestr("Contents/section0.xml", f"<hp:sec {_NS}>{section}</hp:sec>")
    return buffer.getvalue()


def _para(text: str, *, style: str = "0", char: str = "1", para: str = "1") -> str:
    return (
        f'<hp:p styleIDRef="{style}" paraPrIDRef="{para}">'
        f'<hp:run charPrIDRef="{char}"><hp:t>{text}</hp:t></hp:run></hp:p>'
    )


#: 9pt body, 14pt heading — the sizes the real 계획서 this was built against uses.
_HEADER = (
    '<hh:charProperties><hh:charPr id="1" height="900"/><hh:charPr id="2" height="1400"/>'
    "</hh:charProperties>"
    '<hh:paraProperties><hh:paraPr id="1"><hh:heading type="NONE" level="0"/></hh:paraPr>'
    '<hh:paraPr id="2"><hh:heading type="BULLET" level="0"/></hh:paraPr></hh:paraProperties>'
    '<hh:styles><hh:style id="0" name="바탕글"/><hh:style id="1" name="+제목"/></hh:styles>'
)


def test_a_heading_starts_a_section_and_the_prose_goes_under_it() -> None:
    data = _archive(
        _HEADER,
        _para("2-3. 전교생 AI기초 교육 의무화", style="1", char="2")
        + _para("이수체계를 개편한다.")
        + _para("가. 개편방향", style="1", char="2")
        + _para("두 과목으로 나눈다."),
    )

    parts = hwpx_import.sections(data)

    assert [p.heading for p in parts] == ["2-3. 전교생 AI기초 교육 의무화", "가. 개편방향"]
    assert "이수체계를 개편한다." in parts[0].html
    assert "두 과목으로 나눈다." in parts[1].html


def test_a_table_stays_a_table() -> None:
    """Flattened to lines it is a comparison the reader has to rebuild — and
    cannot edit back, which is the difference between reading and opening."""
    rows = (
        "<hp:tbl>"
        "<hp:tr><hp:tc>" + _para("구분") + "</hp:tc><hp:tc>" + _para("기존") + "</hp:tc></hp:tr>"
        "<hp:tr><hp:tc>" + _para("학점") + "</hp:tc><hp:tc>" + _para("6학점") + "</hp:tc></hp:tr>"
        "</hp:tbl>"
    )
    parts = hwpx_import.sections(_archive(_HEADER, _para("표", style="1", char="2") + rows))

    html = parts[0].html
    assert "<table>" in html and "<th>구분</th>" in html
    assert "<td>6학점</td>" in html
    # And the cells are not also loose paragraphs beside it: `iter()` walks
    # into a table, so 구분 and 기존 arrived as two more headings once.
    assert html.count("<p>") == 0
    assert len(parts) == 1


def test_a_cell_holding_two_lines_keeps_them_apart() -> None:
    """Run together they made 교육AI이론 — a word that does not exist."""
    cell = "<hp:tc>" + _para("SW 중심 교육") + _para("AI이론 실습") + "</hp:tc>"
    rows = f"<hp:tbl><hp:tr>{cell}</hp:tr></hp:tbl>"
    parts = hwpx_import.sections(_archive(_HEADER, _para("표", style="1", char="2") + rows))

    assert "교육AI이론" not in parts[0].html
    # Two lines, kept as two. Joined with a space they read as one sentence
    # the writer never wrote; `<br>` is what a cell holds a break in.
    assert "SW 중심 교육<br>AI이론 실습" in parts[0].html


def test_the_document_says_which_paragraphs_are_list_items() -> None:
    """The bullet is drawn by the word processor and is not in the text, so
    nothing about the words can tell you. `hh:heading type="BULLET"` can."""
    data = _archive(
        _HEADER,
        _para("항목", style="1", char="2")
        + _para("전교생 6학점 필수", para="2")
        + _para("계열별 교과목 개편", para="2"),
    )

    html = hwpx_import.sections(data)[0].html

    assert html.count("<li>") == 2
    assert "<ul>" in html


def test_a_list_item_is_never_promoted_to_a_heading() -> None:
    """Even set large. A document that calls it a list item has answered."""
    data = _archive(
        _HEADER,
        _para("제목", style="1", char="2") + _para("크게 쓴 항목", char="2", para="2"),
    )

    parts = hwpx_import.sections(data)

    assert [p.heading for p in parts] == ["제목"]
    assert "<li>크게 쓴 항목</li>" in parts[0].html


def test_a_document_with_no_headings_is_still_a_document() -> None:
    """Refusing it would refuse a file over a convention its author never used."""
    parts = hwpx_import.sections(_archive(_HEADER, _para("한 문단뿐이다.")))

    assert len(parts) == 1
    assert parts[0].heading == "개요"
    assert "한 문단뿐이다." in parts[0].html


def test_a_file_that_is_not_a_zip_says_so() -> None:
    with pytest.raises(RuntimeError, match="열지 못했습니다"):
        hwpx_import.sections(b"not a zip at all")


def _tc(text: str, *, col: int, row: int, across: int = 1, down: int = 1) -> str:
    """One cell with the address and span OWPML actually carries."""
    return (
        "<hp:tc>"
        + _para(text)
        + f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
        + f'<hp:cellSpan colSpan="{across}" rowSpan="{down}"/>'
        + "</hp:tc>"
    )


def test_a_row_under_a_vertical_merge_is_not_shifted_left():
    """The bug that was hard to see, because the table still looked like one.

    A 계열 cell merged down three rows means the two rows under it hold one
    cell fewer. Read in document order, 법과대학 lands in the 계열 column and
    every value on that row sits one column left of where it belongs.
    """
    rows = (
        "<hp:tbl>"
        "<hp:tr>"
        + _tc("인문계열", col=0, row=0, down=3)
        + _tc("문과대학", col=1, row=0)
        + _tc("인공지능과인문학", col=2, row=0)
        + "</hp:tr>"
        "<hp:tr>" + _tc("법과대학", col=1, row=1) + _tc("빅데이터와법", col=2, row=1) + "</hp:tr>"
        "</hp:tbl>"
    )
    parts = hwpx_import.sections(_archive(_HEADER, _para("표", style="1", char="2") + rows))

    html = parts[0].html
    assert "<td>법과대학</td>" in html
    # The 계열 column of that row is empty — the value belongs to the row above.
    # The merge itself, rather than the hole it used to leave: 인문계열
    # reaches down three rows and the rows under it hold what is theirs.
    assert '<th rowspan="3">인문계열</th>' in html
    assert "<tr><td>법과대학</td><td>빅데이터와법</td></tr>" in html


def test_a_heading_spanning_the_table_keeps_its_column_count():
    """A cell across all five columns is one value and four empty squares, not
    a row of one that shifts everything after it."""
    rows = (
        "<hp:tbl>"
        "<hp:tr>" + _tc("1. 수요 조사", col=0, row=0, across=3) + "</hp:tr>"
        "<hp:tr>"
        + _tc("과목명", col=0, row=1)
        + _tc("가", col=1, row=1)
        + _tc("나", col=2, row=1)
        + "</hp:tr>"
        "</hp:tbl>"
    )
    parts = hwpx_import.sections(_archive(_HEADER, _para("표", style="1", char="2") + rows))

    html = parts[0].html
    assert '<th colspan="3">1. 수요 조사</th>' in html
    assert "<td>과목명</td><td>가</td><td>나</td>" in html


def test_a_table_with_no_addresses_is_still_read():
    """A producer is not obliged to write them, and dropping every cell of such
    a table is worse than reading it in document order."""
    rows = (
        "<hp:tbl><hp:tr><hp:tc>"
        + _para("가")
        + "</hp:tc><hp:tc>"
        + _para("나")
        + "</hp:tc></hp:tr></hp:tbl>"
    )
    parts = hwpx_import.sections(_archive(_HEADER, _para("표", style="1", char="2") + rows))

    assert "<th>가</th><th>나</th>" in parts[0].html
