"""발표에서 가장 흔한 장이 여섯 줄 글머리로 나오던 문제.

The `.pptx` writer has drawn a real PowerPoint table from `slides[].rows` for
as long as it has existed, and the `.pdf` writer draws the same thing. Neither
was ever reached: `table` was not a layout the model could pick, and the
frontend `Slide` type had nowhere to put the rows. So a comparison — 대안 A vs
대안 B, the slide a working deck is mostly made of — came out as a list the
audience had to rebuild the table from in their heads.

A layout is offered only when all three renderers can draw it. This one could
be drawn by two of them and was offered by none.
"""

from __future__ import annotations

import io
import zipfile

from app.services import deck, deck_export

_ROWS = [
    ["기준", "대안 A", "대안 B"],
    ["초기 비용", "0원", "약 3억"],
    ["도입 기간", "2주", "4개월"],
]


def test_the_model_may_choose_a_table() -> None:
    assert "table" in deck._LAYOUTS
    # And it is asked for one in the terms the exporters read back.
    assert "rows" in deck._TABLE_PROMPT
    assert "머리글" in deck._TABLE_PROMPT


def test_rows_are_cleaned_into_a_rectangle() -> None:
    ragged = deck._clean_rows([["기준", "A", "B"], ["비용", "0원"], ["기간", "2주", "4개월"]])
    # Padded rather than dropped: a model that gives three headings and a row
    # of two has made one mistake, and throwing the table away over it costs
    # the reader the other seven cells.
    assert ragged == [["기준", "A", "B"], ["비용", "0원", ""], ["기간", "2주", "4개월"]]


def test_a_table_of_one_row_is_not_a_table() -> None:
    # A heading with nothing under it. The caller falls back to a list.
    assert deck._clean_rows([["기준", "대안 A"]]) == []
    assert deck._clean_rows(None) == []
    assert deck._clean_rows([[], [""]]) == []


def test_a_slide_table_is_cut_to_what_the_back_row_can_read() -> None:
    wide = deck._clean_rows([[f"열{i}" for i in range(9)] for _ in range(9)])
    assert len(wide) == deck._MAX_ROWS
    assert all(len(row) == deck._MAX_COLUMNS for row in wide)
    # A sentence in a cell is a paragraph nobody reads from eight metres away.
    long = deck._clean_rows([["기준", "A"], ["비용", "가" * 80]])
    assert len(long[1][1]) == deck._MAX_CELL


def test_powerpoint_gets_a_real_table_not_a_list() -> None:
    slides = [
        {"id": "s1", "layout": "title", "title": "제목"},
        {"id": "s2", "layout": "table", "title": "대안 비교", "rows": _ROWS},
    ]
    data = deck_export.to_pptx("제목", slides)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slide = archive.read("ppt/slides/slide2.xml").decode()
    # `<a:tbl>` is PowerPoint's own table. A picture of one would print the
    # same and be uneditable, unsearchable and unfixable by whoever presents it.
    assert "<a:tbl>" in slide
    for text in ("기준", "대안 A", "약 3억", "4개월"):
        assert f"<a:t>{text}</a:t>" in slide


def test_the_pdf_draws_the_same_table() -> None:
    slides = [{"id": "s1", "layout": "table", "title": "대안 비교", "rows": _ROWS}]
    assert deck_export.to_pdf("제목", slides).startswith(b"%PDF")


def test_the_table_is_drawn_in_the_decks_own_accent() -> None:
    """PowerPoint's default table style owes nothing to the deck.

    Every new table gets a theme style — a banded blue — so the `.pptx` was the
    odd one out: the preview and the `.pdf` both draw accent-coloured headings
    over plain rows with a rule under the head, and the projected deck showed a
    blue nobody chose. A preview that differs from the `.pptx` is discovered in
    the room.
    """
    slides = [{"id": "s1", "layout": "table", "title": "비교", "accent": "#2b4c7e", "rows": _ROWS}]
    with zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("제목", slides))) as archive:
        slide = archive.read("ppt/slides/slide1.xml").decode()

    # The style's own banding, off. Left on, it paints every other row whatever
    # the theme says and the explicit fills below are painted over.
    assert 'firstRow="0"' in slide or "firstRow" not in slide
    assert 'bandRow="0"' in slide or "bandRow" not in slide
    # Cells carry no fill of their own, and the head row a rule in the accent.
    assert "<a:noFill/>" in slide
    assert '<a:srgbClr val="2B4C7E"/>' in slide
    assert "<a:lnB " in slide


def test_the_head_rule_sits_where_the_schema_wants_it() -> None:
    """`a:tcPr` is ordered, and a line after the fill is a repair prompt."""
    import re

    slides = [{"id": "s1", "layout": "table", "title": "비교", "rows": _ROWS}]
    with zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("제목", slides))) as archive:
        slide = archive.read("ppt/slides/slide1.xml").decode()
    for properties in re.findall(r"<a:tcPr\b[^>]*>(.*?)</a:tcPr>", slide, re.S):
        if "<a:lnB" in properties and "<a:noFill/>" in properties:
            assert properties.index("<a:lnB") < properties.index("<a:noFill/>")


# ── 강조 수치 ──────────────────────────────────────────────────────────

_METRICS = [["32%", "오탐 감소"], ["1.4초", "평균 응답"], ["99.2%", "가용성"]]


def test_the_model_may_choose_metrics() -> None:
    assert "metrics" in deck._LAYOUTS
    # And is told the one thing that matters about a figure on a screen: an
    # invented number set at 44pt is more convincing than anything else on the
    # slide, so the rule is to drop the layout rather than fill it.
    assert "지어낸 수치" in deck._METRICS_PROMPT
    assert "bullets 로 답하라" in deck._METRICS_PROMPT


def test_a_figure_with_no_label_is_dropped() -> None:
    # A number with nothing saying what it counts is a number nobody can use,
    # and a label with no number is a bullet in the wrong slide.
    assert deck._clean_metrics([["32%"], ["1.4초", "평균 응답"], ["", "가용성"]]) == [
        ["1.4초", "평균 응답"]
    ]
    assert deck._clean_metrics(None) == []


def test_metrics_are_cut_to_what_fits_across_a_slide() -> None:
    many = deck._clean_metrics([[f"{n}%", f"이름{n}"] for n in range(9)])
    assert len(many) == deck._MAX_METRICS
    long = deck._clean_metrics([["가" * 40, "나" * 40]])
    assert len(long[0][0]) == deck._MAX_VALUE
    assert len(long[0][1]) == deck._MAX_LABEL


def test_powerpoint_sets_the_figures_large() -> None:
    """The size is the layout. Drawn at body size this is a bullet list."""
    import re

    slides = [{"id": "s1", "layout": "metrics", "title": "성과", "metrics": _METRICS}]
    with zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("제목", slides))) as archive:
        slide = archive.read("ppt/slides/slide1.xml").decode()
    for text in ("32%", "오탐 감소", "99.2%"):
        assert f"<a:t>{text}</a:t>" in slide
    sizes = {int(size) for size in re.findall(r'sz="(\d+)"', slide)}
    # 44pt for the figures, and the labels well under it.
    assert 4400 in sizes
    assert min(sizes) < 2000


def test_the_pdf_draws_the_same_figures() -> None:
    slides = [{"id": "s1", "layout": "metrics", "title": "성과", "metrics": _METRICS}]
    assert deck_export.to_pdf("제목", slides).startswith(b"%PDF")


# ── 차트 ───────────────────────────────────────────────────────────────

_CHART = {
    "kind": "bar",
    "unit": "건",
    "categories": ["1분기", "2분기", "3분기", "4분기"],
    "series": [{"name": "처리 건수", "values": [120, 210, 380, 460]}],
}


def test_the_model_may_choose_a_chart() -> None:
    assert "chart" in deck._LAYOUTS
    # And is told to abandon the layout rather than fill it with invented
    # numbers: a chart reads as more factual than the numbers it is drawn from.
    assert "지어낸 수치" in deck._CHART_PROMPT
    assert "bullets 로 답하라" in deck._CHART_PROMPT


def test_a_series_shorter_than_its_categories_is_trimmed_not_padded() -> None:
    """The failure that would be invisible and wrong.

    A chart with four labels and three values is not a chart with a gap in it.
    Drawn as-is, every bar stands under the wrong label and the audience takes
    away a fact that was never in the data. Both are cut to the length they
    agree on, so what is shown is the part that is actually paired.
    """
    trimmed = deck._clean_chart(
        {
            "kind": "bar",
            "categories": ["1분기", "2분기", "3분기", "4분기"],
            "series": [{"name": "건수", "values": [120, 210, 380]}],
        }
    )
    assert trimmed is not None
    assert trimmed["categories"] == ["1분기", "2분기", "3분기"]
    assert trimmed["series"][0]["values"] == [120, 210, 380]


def test_a_chart_that_cannot_be_drawn_is_refused() -> None:
    assert deck._clean_chart(None) is None
    assert deck._clean_chart({"categories": ["가", "나"], "series": []}) is None
    # One point is not a shape. The slide falls back to a list.
    assert deck._clean_chart({"categories": ["가"], "series": [{"values": [1]}]}) is None
    # A value that is not a number stops that series where it went wrong,
    # rather than skipping it — everything after a skipped value would stand
    # under the wrong label.
    partial = deck._clean_chart(
        {"categories": ["가", "나", "다", "라"], "series": [{"values": [1, 2, "미정", 4]}]}
    )
    assert partial["categories"] == ["가", "나"]
    assert partial["series"][0]["values"] == [1.0, 2.0]
    # And when that leaves one point, there is no shape left to draw.
    assert (
        deck._clean_chart(
            {"categories": ["가", "나", "다"], "series": [{"values": [1, "미정", 3]}]}
        )
        is None
    )


def test_powerpoint_gets_a_chart_it_can_edit() -> None:
    """A native chart, not a picture of one.

    A native chart carries its own worksheet, so a number that turns out to be
    wrong the morning of the talk is fixed in the file the presenter already
    has. A raster is a picture they have to come back to us to change.
    """
    slides = [{"id": "s1", "layout": "chart", "title": "처리 추이", "chart": _CHART}]
    with zipfile.ZipFile(io.BytesIO(deck_export.to_pptx("제목", slides))) as archive:
        parts = archive.namelist()
        # The chart part, and the workbook behind it.
        assert any(n.startswith("ppt/charts/chart") for n in parts), parts
        assert any("embeddings" in n for n in parts), parts
        chart = next(archive.read(n).decode() for n in parts if n.startswith("ppt/charts/chart"))

    for label in ("1분기", "4분기", "처리 건수"):
        assert label in chart
    # The floor is zero. A bar chart with its bottom cut off exaggerates every
    # difference on the slide.
    assert "<c:min val=\"0\"/>" in chart or "<c:min val=\"0.0\"/>" in chart


def test_the_pdf_draws_the_same_chart() -> None:
    slides = [{"id": "s1", "layout": "chart", "title": "처리 추이", "chart": _CHART}]
    assert deck_export.to_pdf("제목", slides).startswith(b"%PDF")
    line = [
        {"id": "s1", "layout": "chart", "title": "추이", "chart": dict(_CHART, kind="line")}
    ]
    assert deck_export.to_pdf("제목", line).startswith(b"%PDF")


def test_an_unusable_chart_does_not_break_the_export() -> None:
    # Written before `_clean_chart` existed, or edited by hand since.
    broken = [
        {"id": "s1", "layout": "chart", "title": "x", "chart": {"categories": [], "series": []}}
    ]
    assert deck_export.to_pptx("제목", broken)
    assert deck_export.to_pdf("제목", broken).startswith(b"%PDF")


def test_the_printed_scale_uses_numbers_a_reader_recognises() -> None:
    """Gridlines at 132, 264, 397 are arithmetic, not a scale.

    `highest * 1.15` quartered gives whatever it gives. PowerPoint rounds the
    top of its own charts, so the `.pdf` has to agree with it or the same deck
    is read off two different scales depending on which file somebody opened.
    """
    from app.services.deck_export import _nice_ceiling

    # Quarters of the result are what a reader actually sees.
    assert _nice_ceiling(460) == 500
    assert _nice_ceiling(8.1) == 10
    assert _nice_ceiling(0.9) == 1
    assert _nice_ceiling(1200) == 2000

    from app.services.deck_export import _tick_label

    for highest in (3, 47, 460, 8.1, 12345):
        top = _nice_ceiling(highest)
        # The bars have to fit, and they have to fill the chart rather than
        # sitting in the bottom half of it.
        assert highest <= top <= highest * 2.5
        # And every gridline can be written short enough to sit beside an axis.
        assert all(len(_tick_label(top * n / 4)) <= 7 for n in range(5))


def test_the_printed_chart_names_its_lines() -> None:
    """Two lines and no legend is two lines nobody can tell apart."""
    from app.services import deck_export as export

    drawn: list[str] = []

    class _Spy:
        def __getattr__(self, name):
            def record(*args, **kwargs):
                if name == "drawString":
                    drawn.append(str(args[-1]))
                return _Spy()

            return record

    export._pdf_chart(
        _Spy(),
        {
            "kind": "line",
            "unit": "초",
            "categories": ["1월", "2월", "3월"],
            "series": [("평균", [3.0, 2.0, 1.0]), ("95분위", [8.0, 6.0, 4.0])],
        },
        accent=(0.1, 0.3, 0.5),
        muted=(0.4, 0.4, 0.4),
        top=400,
        width=600,
        font="Helvetica",
    )
    assert "평균" in drawn and "95분위" in drawn


# ── 완성 판정 ──────────────────────────────────────────────────────────

def test_a_slide_of_the_new_kinds_counts_as_written() -> None:
    """`bullets` 와 `body` 만 보던 판정이 새 세 레이아웃을 못 봤다.

    A table, a strip of figures and a chart carry their content in fields that
    did not exist when this test was written, so a finished one looked exactly
    like a slide the model had failed to write. `filled` dropped it from the
    stored deck, and the panel's own copy of the same test left 내보내기, 발표
    and 텍스트 수정 disabled forever on a deck that was complete — which is how
    this was found, six minutes into a browser test waiting for a button.
    """
    for slide in (
        {"layout": "table", "title": "비교", "rows": _ROWS},
        {"layout": "metrics", "title": "성과", "metrics": _METRICS},
        {"layout": "chart", "title": "추이", "chart": _CHART},
    ):
        assert deck.has_content(slide), slide["layout"]
        assert deck.filled([slide]) == [slide], slide["layout"]


def test_an_empty_slide_still_counts_as_unwritten() -> None:
    # The check the old one was right about, which the fix must not lose.
    assert not deck.has_content({"layout": "bullets", "title": "빈 장"})
    assert not deck.has_content({"layout": "bullets", "title": "빈 장", "body": "   "})
    assert deck.filled([{"layout": "bullets", "title": "빈 장"}]) == []
    # A cover counts on its title alone.
    assert deck.has_content({"layout": "title", "title": "표지"})


def test_a_slide_that_came_back_wrong_still_gets_something() -> None:
    """빈 장 하나가 덱 전체를 잠갔다.

    Every layout branch falls back to bullets when its own shape does not
    parse, and until now that fallback had none of its own: a model that
    answered with prose, or with its list under a key nobody specified, left
    the slide empty. A blank rectangle in the middle of a talk is the visible
    half of that; the deck whose 내보내기 never enables is the worse half, and
    it is how this was found — a browser test waiting six minutes for a button.
    """
    # The list under a name the prompt never used.
    assert deck._salvaged_bullets({"points": ["가상환경 격리", "의존성 고정"]}) == [
        "가상환경 격리",
        "의존성 고정",
    ]
    # One paragraph, split where the sentences end rather than shown as a wall.
    assert len(deck._salvaged_bullets({"content": "첫 문장이다. 둘째 문장이다."})) == 2
    # Speaker notes are not slide content, and neither is the layout name.
    assert deck._salvaged_bullets({"notes": "말로 할 이야기", "layout": "bullets"}) == []
    # Structure the model invented is not put on a slide.
    assert deck._salvaged_bullets({"data": {"a": 1}, "count": 3}) == []


def test_the_salvage_only_runs_when_there_is_nothing_else() -> None:
    """It must not add bullets to a slide that already said what it meant."""
    import inspect

    source = inspect.getsource(deck._write_slides)
    assert "if not has_content(slide):" in source
    assert "_salvaged_bullets(data)" in source


def test_a_slide_never_comes_out_blank() -> None:
    """빈 장은 남기지 않는다.

    A slide the model answered unusably used to stay empty, and an empty slide
    is not merely a blank rectangle in the middle of a talk: it is a deck whose
    every control is waiting for it. Saying so is what keeps the panel usable,
    and 텍스트 수정 is then the way out.
    """
    import inspect

    source = inspect.getsource(deck._write_slides)
    marker = 'slide["body"] = UNWRITTEN'
    # Said in two places: when the call threw, and when it returned something
    # nothing could be made of. The reader cannot tell those apart and should
    # not have to.
    assert source.count(marker) == 2
    # The salvage runs first; the marker is only for when it found nothing.
    assert source.index("_salvaged_bullets(data)") < source.rindex(marker)
    # And the marker is content, so nothing downstream treats it as a gap.
    assert deck.has_content({"layout": "bullets", "body": deck.UNWRITTEN})


def test_the_sentence_says_so_on_screen_and_not_in_the_file() -> None:
    """패널은 작업대이고 파일은 방이다.

    The same sentence has two audiences. On the panel it is an instruction —
    텍스트 수정 is right there, and the lint has already filed it P0. In an
    exported file it is projected in front of an audience, and a live run put
    "이 장을 쓰지 못했습니다." on slide three of a deck somebody was about to
    present.

    Dropped rather than blanked. An empty slide in a deck is a pause nobody
    planned; the numbering is by position, so what follows moves up.
    """
    import io

    from pptx import Presentation

    from app.services import deck_export

    slides = [
        {"layout": "title", "title": "덱", "body": "부제"},
        {"layout": "bullets", "title": "쓴 장", "bullets": ["내용"]},
        {"layout": "bullets", "title": "못 쓴 장", "body": deck.UNWRITTEN},
    ]
    written = Presentation(io.BytesIO(deck_export.to_pptx("덱", slides)))
    assert len(written.slides._sldIdLst) == 2
    words = " ".join(
        shape.text_frame.text
        for slide in written.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert deck.UNWRITTEN not in words
    assert "쓴 장" in words


def test_a_slide_that_only_lost_its_prose_is_kept() -> None:
    """The marker plus a table is a slide with a table on it. Dropping that
    would lose the work the writer did do."""
    import io

    from pptx import Presentation

    from app.services import deck_export

    slides = [
        {"layout": "title", "title": "덱"},
        {
            "layout": "table",
            "title": "표는 나왔다",
            "body": deck.UNWRITTEN,
            "rows": [["가", "나"], ["1", "2"]],
        },
    ]
    written = Presentation(io.BytesIO(deck_export.to_pptx("덱", slides)))
    assert len(written.slides._sldIdLst) == 2
