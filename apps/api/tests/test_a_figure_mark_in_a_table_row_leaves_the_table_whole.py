"""A figure mark the writer put in a table row stands after the table, so the table keeps its
rows and no stray 「|」 lines or leading space are left when the mark goes.

Round-3 r3r02 (11-21, KC-SA, three tables): 「| 면제 확대 | … |\n| [[그림: compare | … ]] |  |」 came
out as 「|\n\n| |\n 제도 변화는 …」 after the figure stage took the mark out."""

from __future__ import annotations

from app.services import report

BODY = ("제도는 단계적으로 바뀌었습니다.\n\n"
        "| 구분 | 내용 | 시기 |\n|---|---|---|\n| 공시 | 11개 항목 게시 | 2024년 |\n| 면제 | 100여 종 | 2025년 |\n"
        "| [[그림: flow | 제도 변화 흐름 | 공시 -> 면제]] |  |\n\n"
        "〔그림〕과 같이 제도 변화는 단계적으로 진행되고 있습니다.")


def test_the_mark_leaves_the_table_for_its_own_line():
    out = report.figure_marks_out_of_tables(BODY)
    assert "| 면제 | 100여 종 | 2025년 |\n\n[[그림: flow | 제도 변화 흐름 | 공시 -> 면제]]\n\n〔그림〕과" in out


def test_an_undrawn_mark_leaves_no_stray_pipes_or_space():
    out = report.caption_figures_and_tables([{"heading": "제도 변화", "content": BODY}])[0]["content"]
    assert "|\n\n|" not in out and "\n| |" not in out and "\n|\n" not in out
    assert "\n제도 변화는 단계적으로" in out
