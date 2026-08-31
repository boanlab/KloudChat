"""한글에서 고치고 돌아와도 문서가 그대로인가.

Opening a `.hwpx` and exporting it back is not one feature, it is a loop, and
the person who uses it uses it more than once: open the 계획서 the 팀장 sent,
fix a number here, export, mail it, get it back, open it again. Every check
before this one looked at a single leg of that loop. None of them could see
what the loop does to a document over time, and what it did was rot it.

Two failures came out of running it, both invisible to a reader of either leg
alone:

  1. Every trip grew another copy of the title. The exporter writes the
     document's name at the top; the reader saw a large centred paragraph and
     called it the first heading. Three trips, three titles.
  2. `28.03 — a Korean date, an apostrophe standing where 20 would be — came
     back as 28.03. Markdown had read the apostrophe as an unclosed code
     marker and the export stripped it. The number is still a number, so
     nothing looked wrong; it is simply a different year.

So this compares the document to itself across three trips, structurally:
headings and their levels, tables by row and column, lists by item. The
comparison is `hwpx_import.differences`, which names what moved rather than
asserting two blobs are equal — a diff nobody can read is a test nobody fixes.
"""

from __future__ import annotations

import io
import zipfile

from app.services import hwpx_import, report_export, richtext

_NS = (
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"'
)

#: 9pt body, 14pt heading, and paraPr 3 centred — the shape of the real 계획서.
_HEADER = (
    '<hh:charProperties><hh:charPr id="1" height="900"/><hh:charPr id="2" height="1400"/>'
    "</hh:charProperties>"
    '<hh:paraProperties><hh:paraPr id="1"><hh:heading type="NONE" level="0"/></hh:paraPr>'
    '<hh:paraPr id="3"><hh:align horizontal="CENTER"/>'
    '<hh:heading type="NONE" level="0"/></hh:paraPr></hh:paraProperties>'
    '<hh:styles><hh:style id="0" name="바탕글"/><hh:style id="1" name="+제목"/></hh:styles>'
)


def _para(text: str, *, style: str = "0", char: str = "1", para: str = "1") -> str:
    return (
        f'<hp:p styleIDRef="{style}" paraPrIDRef="{para}">'
        f'<hp:run charPrIDRef="{char}"><hp:t>{text}</hp:t></hp:run></hp:p>'
    )


def _cell(text: str, *, across: int = 1, down: int = 1) -> str:
    """One cell. `text` may hold newlines; each becomes its own `hp:p`."""
    body = "".join(_para(line) for line in text.split("\n"))
    return (
        f"<hp:tc><hp:subList>{body}</hp:subList>"
        f'<hp:cellSpan colSpan="{across}" rowSpan="{down}"/></hp:tc>'
    )


def _archive(section: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Contents/header.xml", f"<hh:head {_NS}>{_HEADER}</hh:head>")
        archive.writestr("Contents/section0.xml", f"<hp:sec {_NS}>{section}</hp:sec>")
    return buffer.getvalue()


#: A title, two levels of heading, a table whose cells hold Korean dates, and a
#: list. Everything a 계획서 has that a round trip can lose.
_DOCUMENT = _archive(
    _para("전교생 AI기초 교육 의무화", style="1", char="2", para="3")
    + _para("2-3. 이수체계 개편", style="1", char="2")
    + _para("전교생이 두 과목을 듣는다.")
    + "<hp:tbl>"
    + "<hp:tr>" + _cell("구분") + _cell("기존") + _cell("개편") + _cell("적용") + "</hp:tr>"
    + "<hp:tr>" + _cell("학점") + _cell("6학점") + _cell("9학점") + _cell("`28.03") + "</hp:tr>"
    + "<hp:tr>" + _cell("대상") + _cell("일부") + _cell("전교생") + _cell("`24.03") + "</hp:tr>"
    + "</hp:tbl>"
    + "<hp:tbl>"
    + "<hp:tr>" + _cell("1. 단과대학별 AI-PD", across=3) + _cell("비고") + "</hp:tr>"
    + "<hp:tr>"
    + _cell("죽전", down=2)
    + _cell("SW융합대학")
    + _cell("3명")
    + _cell("1학기 위촉\n2학기 재위촉")
    + "</hp:tr>"
    + "<hp:tr>" + _cell("공과대학") + _cell("2명") + _cell("-") + "</hp:tr>"
    + "</hp:tbl>"
    + _para("가. 세부 추진전략", style="1", char="2")
    + _para("- 학과별 AI-PD를 위촉한다.")
    + _para("- `26년 현재 17명을 위촉했다.")
)


def _trip(title: str, parts: list[hwpx_import.Section]) -> hwpx_import.Document:
    """Sections out to a file and back, the way the export and the import do it."""
    sections = [
        {
            "id": str(index),
            "heading": part.heading,
            "level": part.level,
            "format": "html",
            "content": part.html,
        }
        for index, part in enumerate(parts)
    ]
    return hwpx_import.read(report_export.to_hwpx(title, richtext.normalise(sections)))


def test_three_trips_change_nothing() -> None:
    """Once is a feature; three times is the loop somebody actually lives in."""
    first = hwpx_import.read(_DOCUMENT)
    before = hwpx_import.shape(first.sections)

    document = first
    for _ in range(3):
        document = _trip(document.title, document.sections)

    assert hwpx_import.differences(before, hwpx_import.shape(document.sections)) == []


def test_the_title_is_the_title_and_not_a_section() -> None:
    """The centred line at the top is the document's name. Read as a heading it
    became a section, and the next export wrote a title above it — so the file
    grew one more copy of its own name per trip."""
    document = hwpx_import.read(_DOCUMENT)

    assert document.title == "전교생 AI기초 교육 의무화"
    assert [p.heading for p in document.sections] == ["2-3. 이수체계 개편", "가. 세부 추진전략"]
    assert [p.level for p in document.sections] == [1, 2]

    once = _trip(document.title, document.sections)
    assert once.title == document.title
    assert [p.heading for p in once.sections] == [p.heading for p in document.sections]


def test_a_korean_date_keeps_its_apostrophe() -> None:
    """`28.03 is 2028년 3월. Stripped of the mark it is 28.03, which is a
    different thing said the same way, and no error is raised anywhere."""
    opened = hwpx_import.read(_DOCUMENT)
    shape = hwpx_import.shape(_trip(opened.title, opened.sections).sections)

    cells = [cell[0] for table in shape["tables"] for row in table for cell in row]
    assert "`28.03" in cells
    items = [item for group in shape["lists"] for item in group]
    assert any("`26년" in item for item in items)


def test_a_merged_cell_is_still_merged_on_the_other_side() -> None:
    """The header that spans four columns, and the 비고 that runs to two lines.

    Markdown has neither, and every export used to end at GFM — so a merge came
    back as one filled cell followed by empty ones, and a two-line cell as one
    sentence. A real 재난 상황 보고서 opened here carries 123 merged cells.
    """
    opened = hwpx_import.read(_DOCUMENT)
    trip = _trip(opened.title, opened.sections)

    tables = hwpx_import.shape(trip.sections)["tables"]
    merged = next(t for t in tables if any(c[0].startswith("1. 단과대학") for r in t for c in r))
    assert merged[0][0] == ("1. 단과대학별 AI-PD", 3, 1)
    assert merged[1][0] == ("죽전", 1, 2)
    # The row under a vertical merge holds its own three cells, not four.
    assert [c[0] for c in merged[2]] == ["공과대학", "2명", "-"]
    # And the break the writer put in the cell is still a break.
    assert merged[1][3][0] == "1학기 위촉\n2학기 재위촉"


def test_two_tables_written_back_to_back_stay_two() -> None:
    """A GFM table has one rule row, under its head. A second rule begins a new
    table — without that the exporters read consecutive tables as one, and a
    상황 보고서 built out of 107 single-row boxes came out with 64."""
    pairs = report_export._markdown_to_lines(
        "| 가 | 1 |\n| --- | --- |\n\n| 나 | 2 |\n| --- | --- |"
    )
    drawn = [t.flat() for kind, t, _, _d in pairs if kind == "table"]
    assert drawn == [[["가", "1"]], [["나", "2"]]]

    # A blank line *inside* one table still does not end it — models write them.
    loose = report_export._markdown_to_lines("| a | b |\n| --- | --- |\n\n| 1 | 2 |")
    assert [t.flat() for kind, t, _, _d in loose if kind == "table"] == [[["a", "b"], ["1", "2"]]]


def test_code_is_still_stripped_of_its_markers() -> None:
    """The narrowed rule has to still do the job it was written for."""
    assert report_export._strip_inline("실행은 `python -m pytest` 로 한다") == (
        "실행은 python -m pytest 로 한다"
    )
    assert report_export._strip_inline("적용 `28.03 `28.03") == "적용 `28.03 `28.03"
