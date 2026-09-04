"""HWPX table column widths follow content, and cells are left-aligned."""

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
    """A very long cell leaves the other column readable and the total within the page."""
    rows = [["가", "나"], ["짧다", "길" * 400]]

    short, long = rx._column_weights(rows, 2)

    assert short >= 2
    assert long > short
    assert short + long <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_a_column_of_dates_keeps_a_floor() -> None:
    rows = [["시기"], ["`28.03"]]

    assert rx._column_weights(rows, 1)[0] >= len("`28.03")


def test_cells_read_from_the_left_rather_than_justified() -> None:
    """Paragraph shape 7 is LEFT and tables never use JUSTIFY."""
    shapes = {pid: align for pid, align, *_ in rx._HWPX_PARA_SHAPES}

    assert shapes[7] == "LEFT"
    assert "JUSTIFY" not in rx._hwpx_table([["가", "나"], ["다", "라"]])


def test_the_widths_a_caller_names_still_win() -> None:
    """Explicit `widths` override the computed weights."""
    rows = [["1", "이름", "설명"], ["2", "다른 이름", "다른 설명"]]

    table = rx._hwpx_table(rows, widths=[1, 5, 12])
    sizes = [int(m) for m in re.findall(r'<hp:cellSz width="(\d+)"', table)]

    assert sizes[0] < sizes[1] < sizes[2]


def test_a_column_is_at_least_as_wide_as_its_longest_word() -> None:
    """A column is at least as wide as its longest word."""
    rows = [["계열", "비고"], ["스포츠과학대학", "짧다"]]

    first, second = rx._column_weights(rows, 2)

    assert first >= len("스포츠과학대학")
    assert first > second


def test_the_widths_add_up_to_the_page() -> None:
    """Column widths sum to at most the text width."""
    rows = [["가", "나", "다"], ["하나", "둘", "셋"]]

    assert sum(rx._column_weights(rows, 3)) <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_a_real_five_column_table_holds_its_longest_word() -> None:
    """In a five-column table the short columns still hold their longest word."""
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
    """When the table is too wide, short columns keep their word and wide ones absorb the cut."""
    rows = [["가" * 7, "나" * 16, "다" * 12, "라" * 13, "마" * 4]]

    widths = rx._column_weights(rows, 5)

    assert widths[0] == 7
    assert widths[4] == 4
    assert sum(widths) <= rx._HWPX_TEXT_WIDTH // rx._HWPX_BODY_CHAR


def test_when_nothing_fits_the_shortfall_is_shared() -> None:
    """When no column fits, the shortfall is shared evenly."""
    rows = [["가" * 14, "나" * 11, "다" * 16, "라" * 10, "마" * 17]]

    widths = rx._column_weights(rows, 5)

    assert max(widths) - min(widths) <= 1
