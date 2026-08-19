"""Reading an HTML artifact back into files.

There is no rendering engine in this image, so the conversion is structural
rather than pixel-faithful. What has to hold is that nothing is lost on the
way: every block becomes a slide or a section, in order, with its words, its
columns and its tables intact — and that a deck stays a deck and a document
stays a document.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services import deck_export, page_export, report_export
from app.services import design_templates as dt

_TOKENS = {"accent": "#7a1f3d", "ink": "#111827", "muted": "#6b7280", "font": "gothic"}

_DECK_BLOCKS = [
    {"layout": "cover", "title": "연구실 장비 점검", "html": '<p class="lead">2026 상반기</p>'},
    {
        "layout": "bullets",
        "title": "현황",
        "html": "<ul><li>보유 42대</li><li>점검 필요 5대</li></ul>",
    },
    {
        "layout": "quote",
        "title": "한 줄",
        "html": "<blockquote>점검하지 않은 장비는 없는 장비다</blockquote>",
    },
    {
        "layout": "split",
        "title": "비교",
        "html": (
            '<div class="cols">'
            "<div><h3>유지</h3><ul><li>비용 없음</li><li>복구 경로 없음</li></ul></div>"
            "<div><h3>교체</h3><ul><li>예산 필요</li></ul></div>"
            "</div>"
        ),
    },
    {
        "layout": "table",
        "title": "수치",
        "html": (
            "<table><thead><tr><th>항목</th><th>값</th></tr></thead>"
            "<tbody><tr><td>가동 연령</td><td>7년</td></tr></tbody></table>"
        ),
    },
]

_DOC_BLOCKS = [
    {"layout": "cover", "title": "서버 교체 검토", "html": '<p class="lead">2분기 기술 검토</p>'},
    {
        "layout": "section",
        "title": "배경",
        "html": "<p>현재 서버는 보증이 끝났다.</p><ul><li>장애 3회</li></ul>",
    },
]


def _deck_html(template_id: str = "deck-editorial") -> str:
    template = dt.get(template_id)
    body = dt.assemble(template, _DECK_BLOCKS)
    return dt.render(template, title="연구실 장비 점검", tokens=_TOKENS, body=body)


def _doc_html(template_id: str = "doc-report") -> str:
    template = dt.get(template_id)
    body = dt.assemble(template, _DOC_BLOCKS)
    return dt.render(template, title="서버 교체 검토", tokens=_TOKENS, body=body)


# ── reading it back ────────────────────────────────────────────────────


def test_every_slide_survives_the_round_trip_in_order():
    slides = page_export.to_slides(_deck_html(), accent="#7a1f3d")

    assert [s["title"] for s in slides] == ["연구실 장비 점검", "현황", "한 줄", "비교", "수치"]
    assert [s["layout"] for s in slides] == [
        "title",
        "bullets",
        "quote",
        "two-column",
        "table",
    ]
    assert all(s["accent"] == "#7a1f3d" for s in slides)


def test_each_kind_of_content_lands_where_its_renderer_looks_for_it():
    by_title = {s["title"]: s for s in page_export.to_slides(_deck_html())}

    assert by_title["연구실 장비 점검"]["body"] == "2026 상반기"
    assert by_title["현황"]["bullets"] == ["보유 42대", "점검 필요 5대"]
    assert by_title["한 줄"]["body"] == "점검하지 않은 장비는 없는 장비다"
    assert by_title["수치"]["rows"] == [["항목", "값"], ["가동 연령", "7년"]]


def test_a_split_slide_keeps_which_column_each_line_was_in():
    """Halving a merged list would put the wrong items on the wrong side."""
    split = next(s for s in page_export.to_slides(_deck_html()) if s["layout"] == "two-column")

    assert split["columns"] == [
        ["유지", "비용 없음", "복구 경로 없음"],
        ["교체", "예산 필요"],
    ]
    # Flattened too, so a renderer that only knows bullets still draws them.
    assert split["bullets"] == ["유지", "비용 없음", "복구 경로 없음", "교체", "예산 필요"]


def test_the_slide_number_is_not_read_as_content():
    """The seed prints it; it is furniture, and the exporters draw their own."""
    for slide in page_export.to_slides(_deck_html()):
        assert "1" not in (slide.get("bullets") or [])
        assert slide.get("body") != "1"


def test_a_document_becomes_sections_the_report_exporters_understand():
    sections = page_export.to_sections(_doc_html())

    assert [s["heading"] for s in sections] == ["서버 교체 검토", "배경"]
    assert sections[0]["content"] == "2분기 기술 검토"
    # Markdown, because that is what `report_export._markdown_to_lines` reads.
    assert "- 장애 3회" in sections[1]["content"]


def test_the_one_pager_grid_does_not_swallow_its_cards():
    """Its sections sit inside a `div.grid`; each must still be a section."""
    sections = page_export.to_sections(_doc_html("doc-brief"))
    assert [s["heading"] for s in sections] == ["서버 교체 검토", "배경"]


def test_markup_that_is_not_ours_yields_nothing_rather_than_raising():
    assert page_export.to_slides("<p>그냥 문단</p>") == []
    assert page_export.read("") == []


# ── and out to files ───────────────────────────────────────────────────


def _parts(blob: bytes, prefix: str) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return [n for n in archive.namelist() if n.startswith(prefix)]


def _slide_xml(blob: bytes, index: int) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.read(f"ppt/slides/slide{index}.xml").decode()


def test_an_html_deck_becomes_one_powerpoint_slide_per_section():
    slides = page_export.to_slides(_deck_html(), accent="#7a1f3d")
    blob = deck_export.to_pptx("연구실 장비 점검", slides, tokens=_TOKENS)

    assert len(_parts(blob, "ppt/slides/slide")) == len(_DECK_BLOCKS)
    assert "연구실 장비 점검" in _slide_xml(blob, 1)
    assert "7A1F3D" in _slide_xml(blob, 1)  # the design system's accent


def test_a_table_slide_becomes_a_real_powerpoint_table():
    """Flattened to bullets it would be a list somebody has to reassemble."""
    slides = page_export.to_slides(_deck_html())
    xml = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS), 5)

    assert "<a:tbl>" in xml
    assert "가동 연령" in xml and "7년" in xml


def test_a_two_column_slide_draws_the_columns_it_was_given():
    slides = page_export.to_slides(_deck_html())
    xml = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS), 4)
    # Both headings survive as the first line of their own column.
    assert "유지" in xml and "교체" in xml


def test_a_json_deck_still_halves_its_own_list():
    """The explicit columns are an HTML deck's; nothing else gained a field."""
    # Six, because `_split_columns` keeps a short list in one column — two of
    # two is a gap down the middle rather than a layout.
    columns = deck_export._columns_of({}, ["가", "나", "다", "라", "마", "바"], "two-column")
    assert columns == [["가", "나", "다"], ["라", "마", "바"]]


@pytest.mark.parametrize("template_id", [t.id for t in dt.all_templates() if t.kind == "deck"])
def test_every_deck_template_converts(template_id):
    slides = page_export.to_slides(_deck_html(template_id))
    assert len(slides) == len(_DECK_BLOCKS)
    assert deck_export.to_pptx("t", slides, tokens=_TOKENS)[:2] == b"PK"
    assert deck_export.to_pdf("t", slides, tokens=_TOKENS)[:4] == b"%PDF"


@pytest.mark.parametrize("template_id", ["doc-report", "doc-brief", "doc-notice", "doc-minutes"])
def test_both_document_templates_reach_every_report_format(template_id):
    sections = page_export.to_sections(_doc_html(template_id))
    assert report_export.to_docx("t", sections, tokens=_TOKENS)[:2] == b"PK"
    assert report_export.to_pdf("t", sections, tokens=_TOKENS)[:4] == b"%PDF"
    assert report_export.to_hwpx("t", sections, tokens=_TOKENS)[:2] == b"PK"


def test_a_dark_template_presents_on_a_dark_ground():
    """The design system picks colours for a document; a projector inverts them."""
    slides = page_export.to_slides(_deck_html("deck-signal"))
    light = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS), 2)
    dark = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS, dark=True), 2)

    assert "0E1116" in dark and "0E1116" not in light
    assert "F5F6F7" in dark  # the light ink that ground needs
    assert _TOKENS["ink"].lstrip("#").upper() in light


def test_only_the_template_that_says_so_is_dark():
    assert dt.get("deck-signal").dark is True
    assert dt.get("deck-editorial").dark is False
    assert dt.get("doc-report").dark is False
