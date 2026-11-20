"""A written sum with a term the reader cannot parse is left as written, never a crash.

11-20: a strict-local report section held 「1만 1000만」-style term inside a sum; `_ko_number`
raised on it inside `fix_written_sums`, the sections stage failed and the whole report was lost
(「보고서를 만들지 못했습니다」)."""

from __future__ import annotations

from app.services import units


def test_the_sum_stays_and_nothing_raises() -> None:
    text = "합계 = 1만 1000만 원 + 500만 원 = 1,600만 원\n"
    assert units.fix_written_sums(text, printed=[]) == text


def test_a_readable_wrong_sum_is_still_corrected() -> None:
    fixed = units.fix_written_sums("합계 = 1,200만 원 + 500만 원 = 1,600만 원\n", printed=[])
    assert "1,700만" in fixed
