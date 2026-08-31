"""내보낸 한글 표가 읽히는가.

The file opens — that much was checked by somebody opening it in 한글, which is
the only instrument this format has here. What it opened to was a table whose
every column was the same width whatever it held, so a 구분 column of two-word
labels sat as wide as an 개선 column of two-sentence descriptions: the narrow
one wrapped every label onto three lines while the wide one ran half empty.

Inside those narrow cells the text was justified, which is the body's shape and
the wrong one here. Hangul has no hyphenation to fall back on, so Hancom pulls
the words apart to reach both walls — 랭  체  인  (LangChain)  기반으로, one word
per line with the gaps stretched.
"""

from __future__ import annotations

import re

from app.services import report_export as rx


def test_a_column_of_labels_is_narrower_than_a_column_of_sentences() -> None:
    rows = [
        ["구분", "개선"],
        ["학점", "계열별 대학AI융합입문 3학점과 단과대학별 특화AI융합교육 3학점으로 개편한다"],
    ]

    labels, sentences = rx._column_weights(rows, 2)

    assert labels < sentences


def test_one_long_cell_does_not_take_the_whole_table() -> None:
    """A paragraph in one cell would otherwise close every other column.

    Widths are characters now, so the claim is the one that matters: the other
    column is still wide enough to read, and the pair still fits the page.
    """
    rows = [["가", "나"], ["짧다", "길" * 400]]

    short, long = rx._column_weights(rows, 2)

    assert short >= 2
    assert long > short
    assert short + long <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_a_column_of_dates_keeps_a_floor() -> None:
    rows = [["시기"], ["`28.03"]]

    assert rx._column_weights(rows, 1)[0] >= len("`28.03")


def test_cells_read_from_the_left_rather_than_justified() -> None:
    """Shape 7 is the one that does that, and it has to be defined."""
    shapes = {pid: align for pid, align, *_ in rx._HWPX_PARA_SHAPES}

    assert shapes[7] == "LEFT"
    assert "JUSTIFY" not in rx._hwpx_table([["가", "나"], ["다", "라"]])


def test_the_widths_a_caller_names_still_win() -> None:
    """A procedure's number column is a rail, not a column of data."""
    rows = [["1", "이름", "설명"], ["2", "다른 이름", "다른 설명"]]

    table = rx._hwpx_table(rows, widths=[1, 5, 12])
    sizes = [int(m) for m in re.findall(r'<hp:cellSz width="(\d+)"', table)]

    # Three columns, two rows: the first of each row is the narrowest.
    assert sizes[0] < sizes[1] < sizes[2]


def test_a_column_is_at_least_as_wide_as_its_longest_word() -> None:
    """Hancom splits Hangul between characters when a column is too narrow, so
    스포츠과학대학 comes out 스포츠과 / 학대학 and reads as two words."""
    rows = [["계열", "비고"], ["스포츠과학대학", "짧다"]]

    first, second = rx._column_weights(rows, 2)

    assert first >= len("스포츠과학대학")
    assert first > second


def test_the_widths_add_up_to_the_page() -> None:
    """Hancom lays a table out from its cell sizes, so a remainder left over
    from the division shows up as a column that stops short of the margin."""
    rows = [["가", "나", "다"], ["하나", "둘", "셋"]]

    assert sum(rx._column_weights(rows, 3)) <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_a_real_five_column_table_holds_its_longest_word() -> None:
    """The one this was written against: 스포츠과학대학 in a five-column table
    beside a column of sentences. As a bare weight its floor of seven came out
    three and a half characters wide and the word split anyway."""
    rows = [
        ["계열", "단과대학", "계열별 AI융합입문", "단과대학별 특화AI융합", "이수학점"],
        ["스포츠과학대학", "AI를활용한스포츠코칭", "", "", ""],
        [
            "인문계열",
            "문과대학",
            "인문계열 AI기초교육「대학 AI융합입문(인문)」",
            "인공지능과인문학",
            "6학점",
        ],
    ]

    widths = rx._column_weights(rows, 5)

    assert widths[0] >= len("스포츠과학대학")
    assert widths[4] >= len("이수학점")


def test_a_short_column_keeps_its_word_when_the_table_is_too_wide() -> None:
    """The case the third and fourth attempts both got wrong.

    Five columns of long compound nouns do not fit A4, and the obvious answer —
    scale every column by the same factor — takes 스포츠과학대학 from the seven
    characters it needs down to five and splits it, to buy a column of sentences
    half a character it did not need. Sentences have spaces to wrap at; compound
    nouns do not. So the short needs are met and the shortfall is shared among
    the columns that can absorb it.
    """
    rows = [["가" * 7, "나" * 16, "다" * 12, "라" * 13, "마" * 4]]

    widths = rx._column_weights(rows, 5)

    assert widths[0] == 7
    assert widths[4] == 4
    assert sum(widths) <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_when_nothing_fits_the_shortfall_is_shared() -> None:
    """No column can be saved, so none is sacrificed to another either."""
    rows = [["가" * 14, "나" * 11, "다" * 16, "라" * 10, "마" * 17]]

    widths = rx._column_weights(rows, 5)

    assert max(widths) - min(widths) <= 1
