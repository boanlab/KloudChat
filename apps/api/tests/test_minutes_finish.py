"""A minutes document's finish (staff sweep STAFF-12/23): an empty 기한 column goes with its
name in the caption, and the turn's line names the document's genre."""

from __future__ import annotations

from app.pipeline import save_stages
from app.services import report


def test_an_empty_due_column_goes_and_its_caption_follows():
    body = ("표 2. 항목·내용·기한\n\n| 항목 | 내용 | 기한 | 비고 |\n|---|---|---|---|\n"
            "| 1 | 시행 |  | 찬성 6 |\n| 2 | 신청제 | | - |")
    out = report.drop_empty_columns(body)
    assert out.startswith("표 2. 항목·내용\n")
    assert "| 항목 | 내용 | 비고 |" in out and "기한" not in out
    # A column left for a pen stays; so does a 기한 column with one date in it.
    check = "| 할 일 | 확인 |\n|---|---|\n| 신청서 | |\n| 서명 | |"
    assert report.drop_empty_columns(check) == check
    dated = "| 항목 | 기한 |\n|---|---|\n| a | 11/4 |\n| b | |"
    assert report.drop_empty_columns(dated) == dated


def test_the_turn_line_names_the_genre():
    minutes = save_stages._written_note(5, {"format": "minutes"})
    assert minutes == "5개 절로 회의록 작성을 마쳤습니다."
    assert save_stages._written_note(4, None) == "4개 섹션으로 보고서를 작성했습니다."
