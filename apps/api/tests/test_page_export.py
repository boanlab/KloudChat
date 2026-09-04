"""`page_export`: an HTML artifact reads back into slides or sections without losing content."""

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

#: Cover definition list, a footnote, and a code line.
_HELD_BLOCKS = [
    {
        "layout": "cover",
        "title": "서버 교체 검토",
        "html": (
            '<p class="lead">2분기 기술 검토</p>'
            "<dl><dt>목적</dt><dd>교체 여부 판단</dd>"
            "<dt>독자</dt><dd>인프라팀</dd></dl>"
        ),
    },
    {
        "layout": "section",
        "title": "배경",
        "html": (
            "<p>보증이 2월에 끝났다.</p>"
            "<small>구매 계약서 3항, 2026-01-04 확인.</small>"
            "<p>재기동은 <code>systemctl restart kc-api</code> 로 한다.</p>"
        ),
    },
]


def _held_html(template_id: str = "doc-report") -> str:
    template = dt.get(template_id)
    blocks = [{**b, "html": dt.sanitise(b["html"])} for b in _HELD_BLOCKS]
    return dt.render(
        template, title="서버 교체 검토", tokens=_TOKENS, body=dt.assemble(template, blocks)
    )


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
    """A split slide keeps its explicit columns."""
    split = next(s for s in page_export.to_slides(_deck_html()) if s["layout"] == "two-column")

    assert split["columns"] == [
        ["유지", "비용 없음", "복구 경로 없음"],
        ["교체", "예산 필요"],
    ]
    # Also flattened for renderers that only know bullets.
    assert split["bullets"] == ["유지", "비용 없음", "복구 경로 없음", "교체", "예산 필요"]


def test_the_slide_number_is_not_read_as_content():
    """The seed's slide number is not read as content."""
    for slide in page_export.to_slides(_deck_html()):
        assert "1" not in (slide.get("bullets") or [])
        assert slide.get("body") != "1"


def test_a_document_becomes_sections_the_report_exporters_understand():
    sections = page_export.to_sections(_doc_html())

    assert [s["heading"] for s in sections] == ["서버 교체 검토", "배경"]
    assert sections[0]["content"] == "2분기 기술 검토\n\n<!-- pagebreak -->"
    # Content is Markdown, as `report_export._markdown_to_lines` reads.
    assert "- 장애 3회" in sections[1]["content"]


def test_the_one_pager_grid_does_not_swallow_its_cards():
    """Sections inside a `div.grid` are still read as sections."""
    sections = page_export.to_sections(_doc_html("doc-brief"))
    assert [s["heading"] for s in sections] == ["서버 교체 검토", "배경"]


def test_every_tag_the_catalogue_admits_is_accounted_for():
    """Every tag `sanitise` admits is read as text, carried as structure, or dropped on purpose."""
    known = (
        page_export._TEXT_TAGS | page_export._CARRIED_TAGS | page_export._DROPPED_TAGS
    )
    assert not dt._ALLOWED_TAGS - known
    # `h2` is written by the block wrapper, so `sanitise` strips it but the reader needs it.
    assert known - dt._ALLOWED_TAGS == {"h2"}
    assert not page_export._TEXT_TAGS & page_export._CARRIED_TAGS
    assert not page_export._DROPPED_TAGS & (
        page_export._TEXT_TAGS | page_export._CARRIED_TAGS
    )


def test_a_footnote_is_a_note_and_not_the_next_claim():
    """A footnote stays after the paragraph it sources and before the next."""
    sections = page_export.to_sections(_held_html())
    lines = sections[1]["content"].split("\n\n")

    assert lines == [
        "보증이 2월에 끝났다.",
        "* 구매 계약서 3항, 2026-01-04 확인.",
        "재기동은 systemctl restart kc-api 로 한다.",
    ]


def test_footnote_markers_count_up_and_restart_each_section():
    """Footnote markers are written as text (the seed uses CSS counters) and restart per section."""
    html = (
        "<div class='page'>"
        "<section><h2>가</h2><p>첫째<sup>*</sup></p><small>하나</small>"
        "<p>둘째<sup>**</sup></p><small>둘</small></section>"
        "<section><h2>나</h2><p>셋째<sup>*</sup></p><small>셋</small></section>"
        "</div>"
    )
    first, second = page_export.to_sections(html)

    assert "* 하나" in first["content"]
    assert "** 둘" in first["content"]
    assert "첫째*" in first["content"]
    assert "* 셋" in second["content"]
    assert "** " not in second["content"]


def test_a_definition_list_is_a_list_of_labelled_items_rather_than_a_table():
    """A `dl` becomes `- term: definition` bullets, not a table."""
    cover = page_export.to_sections(_held_html())[0]

    assert "- 목적: 교체 여부 판단" in cover["content"]
    assert "- 독자: 인프라팀" in cover["content"]
    assert page_export.read(_held_html())[0]["rows"] == []


def test_a_dangling_term_is_still_carried():
    read = page_export.read("<section><h2>ㄱ</h2><dl><dt>기간</dt></dl></section>")
    assert read[0]["bullets"] == ["기간"]


def test_a_hard_line_break_does_not_join_the_two_halves_of_a_line():
    read = page_export.read("<section><h2>ㄱ</h2><p>앞줄<br />뒷줄</p></section>")
    assert read[0]["paragraphs"] == ["앞줄 뒷줄"]


def test_a_note_on_a_slide_goes_where_a_presentation_keeps_one():
    """A `small` note on a slide goes to the notes pane."""
    blocks = [
        {
            "layout": "quote",
            "title": "한 줄",
            "html": (
                "<blockquote>점검하지 않은 장비는 없는 장비다</blockquote>"
                "<small>3팀 토의 정리, 5주차</small>"
            ),
        }
    ]
    template = dt.get("deck-editorial")
    html = dt.render(
        template, title="t", tokens=_TOKENS, body=dt.assemble(template, blocks)
    )
    slide = page_export.to_slides(html)[0]

    assert slide["body"] == "점검하지 않은 장비는 없는 장비다"
    assert slide["notes"] == "3팀 토의 정리, 5주차"


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
    """A table slide exports as a PowerPoint table."""
    slides = page_export.to_slides(_deck_html())
    xml = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS), 5)

    assert "<a:tbl>" in xml
    assert "가동 연령" in xml and "7년" in xml


def test_a_two_column_slide_draws_the_columns_it_was_given():
    slides = page_export.to_slides(_deck_html())
    xml = _slide_xml(deck_export.to_pptx("t", slides, tokens=_TOKENS), 4)
    assert "유지" in xml and "교체" in xml


def test_a_json_deck_still_halves_its_own_list():
    """A JSON deck without explicit columns still halves its list."""
    # `_split_columns` keeps a list shorter than six in one column.
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


def _docx_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.read("word/document.xml").decode()


def _hwpx_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.read("Contents/section0.xml").decode()


@pytest.mark.parametrize("template_id", ["doc-report", "doc-lab", "doc-brief"])
def test_nothing_a_document_may_hold_is_dropped_on_the_way_to_a_file(template_id):
    """Cover facts, footnotes and code reach the .docx, .hwpx and .pdf."""
    sections = page_export.to_sections(_held_html(template_id))
    for text in (
        _docx_text(report_export.to_docx("t", sections, tokens=_TOKENS)),
        _hwpx_text(report_export.to_hwpx("t", sections, tokens=_TOKENS)),
    ):
        assert "교체 여부 판단" in text
        assert "구매 계약서 3항" in text
        assert "systemctl restart kc-api" in text
    # The PDF is compressed; only its header is checked.
    assert report_export.to_pdf("t", sections, tokens=_TOKENS)[:4] == b"%PDF"


def test_a_dark_template_presents_on_a_dark_ground():
    """A dark template exports slides on a dark ground."""
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
