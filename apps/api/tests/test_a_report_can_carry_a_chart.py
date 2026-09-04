"""Report chart fences become native charts in .docx/.pdf and tables or pictures in .hwpx."""

from __future__ import annotations

import io
import zipfile

from app.services import report_export

_FENCE = (
    "```chart\n"
    "bar | 건\n"
    "분기 | 처리 건수 | 반려 건수\n"
    "1분기 | 120 | 8\n"
    "2분기 | 210 | 11\n"
    "3분기 | 380 | 9\n"
    "```\n"
)
_SECTIONS = [{"id": "s1", "heading": "처리 추이", "content": f"앞 문장.\n\n{_FENCE}\n뒤 문장.\n"}]


def test_the_fence_is_read_as_numbers_and_not_as_prose() -> None:
    lines = report_export._markdown_to_lines(_SECTIONS[0]["content"])
    charts = [line for line in lines if line[0] == "chart"]
    assert len(charts) == 1
    chart = charts[0][1]
    assert chart["kind"] == "bar"
    assert chart["unit"] == "건"
    assert chart["categories"] == ["1분기", "2분기", "3분기"]
    assert chart["series"] == [
        ("처리 건수", [120.0, 210.0, 380.0]),
        ("반려 건수", [8.0, 11.0, 9.0]),
    ]
    assert not any("```" in str(line[1]) for line in lines)


def test_a_row_missing_a_value_is_dropped_not_padded() -> None:
    """A row with a missing value is dropped rather than padded with zero."""
    chart = report_export._chart_block(
        "bar | 건\n분기 | A | B\n1분기 | 120 | 8\n2분기 | 210\n3분기 | 380 | 9\n"
    )
    assert chart["categories"] == ["1분기", "3분기"]
    assert chart["series"][0][1] == [120.0, 380.0]


def test_a_chart_of_one_point_is_not_a_chart() -> None:
    assert report_export._chart_block("bar | 건\n분기 | 값\n1분기 | 120\n") is None
    assert report_export._chart_block("bar | 건\n분기 | 값\n") is None
    assert report_export._chart_block("") is None


def test_word_gets_a_chart_it_can_edit() -> None:
    """The .docx carries chart part, embedded workbook, content type and relationship."""
    data = report_export.to_docx("제목", _SECTIONS)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = archive.namelist()
        types = archive.read("[Content_Types].xml").decode()
        rels = archive.read("word/_rels/document.xml.rels").decode()
        document = archive.read("word/document.xml").decode()

    assert "word/charts/chart1.xml" in parts, parts
    # Without the embedded workbook Word shows the chart with no editable data.
    assert any(n.startswith("word/embeddings/") for n in parts), parts
    chart_rels = archive_rels(data, "word/charts/_rels/chart1.xml.rels")
    assert "embeddings" in chart_rels

    assert "chart+xml" in types
    assert "charts/chart1.xml" in rels
    assert "<c:chart " in document or "<c:chart" in document
    assert "graphicData" in document

    chart = zipfile.ZipFile(io.BytesIO(data)).read("word/charts/chart1.xml").decode()
    for label in ("1분기", "처리 건수", "반려 건수"):
        assert label in chart


def archive_rels(data: bytes, path: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(path).decode()


def test_the_numbers_survive_where_a_chart_cannot_be_drawn() -> None:
    """.hwpx renders a chart fence as a table carrying every value."""
    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", _SECTIONS))) as archive:
        body = archive.read("Contents/section0.xml").decode()
    assert "<hp:tbl" in body
    for text in ("1분기", "처리 건수", "380", "반려 건수"):
        assert text in body


def test_the_pdf_draws_both_kinds() -> None:
    assert report_export.to_pdf("제목", _SECTIONS).startswith(b"%PDF")
    line = [{"id": "s1", "heading": "추이", "content": _FENCE.replace("bar |", "line |")}]
    assert report_export.to_pdf("제목", line).startswith(b"%PDF")


def test_an_edit_elsewhere_does_not_delete_the_chart() -> None:
    """An edited section's chart figure round-trips back to a chart fence."""
    from app.services import richtext

    edited = (
        "<p>앞 문장.</p>"
        '<figure class="chart" data-source="bar | 건&#10;분기 | 처리 건수&#10;'
        '1분기 | 120&#10;2분기 | 210"></figure>'
        "<p>뒤 문장.</p>"
    )
    markdown = richtext.to_markdown(edited)
    assert "```chart" in markdown
    assert "1분기 | 120" in markdown

    chart = [line for line in report_export._markdown_to_lines(markdown) if line[0] == "chart"]
    assert len(chart) == 1
    assert chart[0][1]["categories"] == ["1분기", "2분기"]


def test_the_chart_survives_the_sanitiser() -> None:
    from app.services import design_templates

    kept = design_templates.sanitise(
        '<figure class="chart" data-source="bar | 건"></figure>'
    )
    assert 'class="chart"' in kept
    assert "data-source" in kept


def test_the_word_chart_is_drawn_in_the_documents_accent() -> None:
    """The Word chart uses the template's accent colour, a zero floor and a Hangul face."""
    data = report_export.to_docx(
        "제목", _SECTIONS, tokens={"accent": "#2b4c7e", "muted": "#666666"}
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        chart = archive.read("word/charts/chart1.xml").decode()

    assert "2B4C7E" in chart, "서식의 accent 가 차트에 없다"
    assert 'val="0"' in chart
    assert 'typeface="맑은 고딕"' in chart


def test_both_surfaces_style_a_chart_the_same_way() -> None:
    """Deck and report charts are styled by the one implementation in services.charts."""
    import inspect

    from app.services import charts, deck_export

    assert "charts.apply(" in inspect.getsource(deck_export._pptx_chart)
    assert "chartkit.part(" in inspect.getsource(report_export._docx_chart)
    assert "apply(" in inspect.getsource(charts.part)


def test_the_chart_has_no_frame_round_it() -> None:
    """The Word chart has square corners and no fill, like every other report figure."""
    data = report_export.to_docx("제목", _SECTIONS)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        chart = archive.read("word/charts/chart1.xml").decode()
    assert '<c:roundedCorners val="0"/>' in chart
    assert "<a:noFill/>" in chart


def test_both_line_charts_show_where_the_readings_are() -> None:
    """Line charts draw point markers in both the PowerPoint and PDF writers."""
    import inspect

    from app.services import charts

    assert "XL_MARKER_STYLE.CIRCLE" in inspect.getsource(charts.apply)
    assert "makeMarker" in inspect.getsource(report_export._pdf_chart)


def test_hangul_embeds_a_chart_somebody_has_seen() -> None:
    """.hwpx embeds a chart's rendered PNG when one exists, instead of the table."""
    import base64

    from app.services import pictures

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="
    )
    source = "bar | 건\n분기 | 처리 건수\n1분기 | 120\n2분기 | 210"
    section = {
        "id": "s1",
        "heading": "추이",
        "content": f"```chart\n{source}\n```\n",
        "diagrams": {report_export.diagram_key(source): pictures.encode("image/png", png)},
    }
    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", [section]))) as archive:
        assert [n for n in archive.namelist() if n.startswith("BinData/")]
        body = archive.read("Contents/section0.xml").decode()
    assert "binaryItemIDRef=" in body
    assert "<hp:tbl" not in body


def test_hangul_falls_back_to_the_numbers_when_nobody_has_looked() -> None:
    with zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", _SECTIONS))) as archive:
        body = archive.read("Contents/section0.xml").decode()
        assert not [n for n in archive.namelist() if n.startswith("BinData/")]
    assert "<hp:tbl" in body
    assert "380" in body
