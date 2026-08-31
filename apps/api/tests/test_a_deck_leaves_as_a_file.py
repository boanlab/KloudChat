"""덱 서식의 파워포인트 절반.

The Word half of this reasoning lives in `test_a_report_leaves_as_a_document`,
and it is the same one: a 서식 that shapes the screen and then hands over a
file in the library's defaults has not shaped the thing that actually gets
presented.

What Word calls styles, PowerPoint calls a master and its layouts. A deck built
by dropping text boxes onto blank slides carries its design in every box —
changing the title face means walking forty shapes, the outline pane is empty
because none of that text sits in a placeholder, and 새 슬라이드 offers nothing
because no layout describes what a slide here looks like.

Read back out of the built files rather than asserted against the generator.
The first version of that generator set the type scale on `a:lvl1pPr`, where
it parses, saves and means nothing: every deck kept PowerPoint's own 44pt title
and 32pt body while the spec said 40 and 20. Nothing caught it, because nothing
opened the file.
"""

from __future__ import annotations

import pathlib

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

from app.services import design_templates

_THEME = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"


def _decks() -> list:
    return [row for row in design_templates.all_templates() if row.kind == "deck"]


def test_every_deck_template_ships_a_form() -> None:
    for row in _decks():
        assert row.form_file, f"{row.id} 에 form.pptx 가 없습니다"
        assert pathlib.Path(row.form_file).is_file()
        assert row.form_file.endswith(".pptx"), f"{row.id}: 덱의 양식은 .pptx 여야 합니다"


def test_the_form_is_made_of_layouts_and_not_of_drawings() -> None:
    """모든 글자가 자리표시자 안에 있다.

    A line drawn as a text box is a line the outline pane cannot see, the
    master cannot restyle and 새 슬라이드 cannot reproduce.
    """
    for row in _decks():
        deck = Presentation(row.form_file)
        assert len(deck.slides) > 0, f"{row.id}: 양식에 슬라이드가 없습니다"
        for index, slide in enumerate(deck.slides, start=1):
            drawn = [shape.name for shape in slide.shapes if not shape.is_placeholder]
            assert not drawn, f"{row.id} {index}장: 직접 그린 도형 {drawn}"
            assert slide.slide_layout.name, f"{row.id} {index}장: 레이아웃이 없습니다"


def test_the_design_is_on_the_master_rather_than_on_the_runs() -> None:
    """글꼴도 색도 크기도 마스터가 들고 있다."""
    #: First-level sizes seen across the catalogue, per style list.
    scales: dict[str, set[int]] = {}

    for row in _decks():
        deck = Presentation(row.form_file)

        for slide in deck.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        assert run.font.size is None, f"{row.id}: 글자 크기를 직접 지정했습니다"
                        assert run.font.name is None, f"{row.id}: 글꼴을 직접 지정했습니다"
                        assert run.font.color.type is None, f"{row.id}: 색을 직접 지정했습니다"

        master = deck.slide_masters[0]
        theme = etree.fromstring(master.part.part_related_by(_THEME).blob)
        elements = theme.find(qn("a:themeElements"))

        fonts = elements.find(qn("a:fontScheme"))
        for tag in ("a:majorFont", "a:minorFont"):
            block = fonts.find(qn(tag))
            latin = block.find(qn("a:latin")).get("typeface")
            east = block.find(qn("a:ea")).get("typeface")
            assert latin and latin != "Calibri Light", f"{row.id}: {tag} 가 기본값입니다"
            # A Hangul run reads `ea` and falls back to PowerPoint's default
            # without it — the deck looks right in the theme pane and comes out
            # in the wrong face on the screen.
            assert east == latin, f"{row.id}: {tag} 의 한글 글꼴이 라틴과 다릅니다"

        styles = master.element.find(qn("p:txStyles"))
        assert styles is not None, f"{row.id}: 마스터에 텍스트 스타일이 없습니다"
        for tag in ("p:titleStyle", "p:bodyStyle"):
            block = styles.find(qn(tag))
            levels = []
            for level in list(block):
                run = level.find(qn("a:defRPr"))
                assert run is not None, f"{row.id}: {tag} 에 defRPr 없는 수준이 있습니다"
                size = run.get("sz")
                assert size, f"{row.id}: {tag} 에 크기 없는 수준이 있습니다"
                levels.append(int(size))
            # An outline steps down. A list that does not is one nobody set.
            assert levels == sorted(levels, reverse=True), f"{row.id}: {tag} 크기가 내려가지 않습니다"
            scales.setdefault(tag, set()).add(levels[0])


    # The regression this exists for. Four 서식 declare four scales — 40, 36,
    # 36 and 44 — so a catalogue where every deck reports the same number is a
    # catalogue where none of them took, which is exactly what happened when
    # the size went onto `a:lvl1pPr`: every file read back PowerPoint's own
    # 44pt and 32pt, and the specs applied to nothing.
    for tag, seen in scales.items():
        assert len(seen) > 1, f"{tag}: 모든 덱이 같은 크기 {seen} 입니다 — 서식이 반영되지 않았습니다"


def test_the_template_ships_without_slides() -> None:
    """`template.pptx` 는 마스터와 레이아웃뿐이다.

    The writer builds on it, so a slide left inside would arrive at the front
    of every deck written from it — the same reason the Word template is empty.
    """
    for row in _decks():
        base = pathlib.Path(row.form_file).with_name("template.pptx")
        assert base.is_file(), f"{row.id} 에 template.pptx 가 없습니다"
        assert len(Presentation(str(base)).slides) == 0, f"{row.id}: 템플릿에 슬라이드가 있습니다"


def test_the_contents_use_the_wide_canvas() -> None:
    """16:9 로 만들었으면 안에 든 것도 16:9 여야 한다.

    Setting `slide_width` changes the canvas and nothing else. The template
    `python-pptx` starts from is 4:3 — the same height, 9,144,000 EMU wide
    against 12,192,000 — so every placeholder in the master and in the eleven
    layouts keeps its 4:3 geometry unless something moves it. The deck is then
    16:9 with 4:3 contents: everything stops at 71% of the slide and the last
    29% is a band of nothing down the right of every page.

    Read off the built slides, because the failure is invisible in the code
    that sets the size — the line looks right and the file is wrong.
    """
    for row in _decks():
        deck = Presentation(row.form_file)
        assert deck.slide_width / deck.slide_height > 1.7, f"{row.id}: 16:9 가 아닙니다"

        edges = [
            (shape.left + shape.width) / deck.slide_width
            for slide in deck.slides
            for shape in slide.placeholders
            if shape.left is not None and shape.width is not None
        ]
        assert edges, f"{row.id}: 잴 자리표시자가 없습니다"
        # 4:3 geometry left behind on a 16:9 canvas stops at 8,686,800 of
        # 12,192,000 — a hair over 71%.
        assert max(edges) > 0.8, (
            f"{row.id}: 가장 오른쪽 자리표시자가 {max(edges):.0%} 에서 멈춥니다 — "
            "마스터와 레이아웃이 4:3 좌표에 남아 있습니다"
        )
        # And scaled once, not twice: reading a layout placeholder that
        # inherits from the master *after* the master has been widened returns
        # the widened number, so a second pass multiplies it again.
        assert max(edges) <= 1.0, (
            f"{row.id}: 자리표시자가 슬라이드 밖 {max(edges):.0%} 까지 나갑니다 — "
            "물려받은 값을 두 번 키웠을 때 이렇게 됩니다"
        )

        # The vertical half, which this test did not measure and so did not
        # catch. A placeholder that inherits carries no `a:xfrm` at all, and
        # setting one value makes the element with the other half of each pair
        # at zero — `left` alone gives `y="0"`, `width` alone gives `cy="0"`.
        # Every inheriting layout came out pinned to the top edge with no
        # height, the title sitting on the first bullet, and the numbers this
        # test did look at were all correct.
        for slide in deck.slides:
            for shape in slide.placeholders:
                assert shape.height, f"{row.id}: '{shape.name}' 높이가 0 입니다"
                assert shape.top + shape.height <= deck.slide_height, (
                    f"{row.id}: '{shape.name}' 이 슬라이드 아래로 넘칩니다"
                )


def test_the_export_is_built_on_the_template_it_was_written_in() -> None:
    """덱도 제 서식의 파일 위에 지어진다.

    The Word half of this has been true for a while and the PowerPoint half was
    not: `to_pptx` opened a blank `Presentation()`, so the 서식 shaped the
    screen and then the file — the thing that is actually presented — came out
    in `python-pptx`'s Calibri with its own 4:3 master. Somebody who chose
    강의형 덱 and downloaded a `.pptx` got a deck with none of it in.
    """
    import io

    from app.services import deck_export

    slides = [
        {"layout": "title", "title": "제목", "body": "부제"},
        {"layout": "bullets", "title": "본문", "bullets": ["가", "나"]},
    ]

    def major(raw: bytes) -> str:
        deck = Presentation(io.BytesIO(raw))
        theme = etree.fromstring(deck.slide_masters[0].part.part_related_by(_THEME).blob)
        fonts = theme.find(qn("a:themeElements")).find(qn("a:fontScheme"))
        return fonts.find(qn("a:majorFont")).find(qn("a:latin")).get("typeface")

    # Without one, PowerPoint's own — which is the behaviour to keep for a deck
    # written under no 서식 at all.
    assert major(deck_export.to_pptx("제목", slides)) == "Calibri"

    for row in _decks():
        assert row.pptx_template, f"{row.id} 에 template.pptx 가 없습니다"
        built = deck_export.to_pptx("제목", slides, template=row.pptx_template)
        assert major(built) != "Calibri", f"{row.id}: 서식이 파일에 실리지 않았습니다"
