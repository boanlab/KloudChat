"""What the code tells the minutes writer asks for nothing it would print as a note: the date
block gives its base date without asking the minutes to state it, and a resolved due the date
alone (no 「원문」 for 비고); and a value the material changes (「420만원 → 355만원」, 「80만원을
요청했으나 … 50만원으로 줄이자고 했고 합의」) is no conflict the writer must explain. Shapes from
the round 16 audit (tc, tc2, H6s)."""

from __future__ import annotations

import datetime as dt
import json
import time

from test_minutes_say_only_what_the_memo_says import TUTOR

from app.services import key_figures, report_checks


async def _no_model(*args, **kwargs):
    raise AssertionError("no model call here")


async def test_the_date_block_asks_for_no_basis_and_no_original_wording_for_a_resolved_due():
    pre = await report_checks.prepare("minutes", "회의록으로 정리해 줘.", TUTOR,
                                      complete=_no_model, model="m", api_key="k",
                                      today=dt.date(2026, 10, 22))
    block = next(b for b in pre.blocks if b.startswith("# 확정 날짜"))
    assert "기준일 " in block and "state this basis" not in block
    assert "원문" not in block and "original wording" not in block and "(기준 " not in block
    assert "「다음 주 금요일」 → 2026-10-23(금)" in block


def _listed(name: str, *values: str) -> str:
    return json.dumps([{"name": name, "values": [{"value": v} for v in values]}],
                      ensure_ascii=False)


def test_a_value_the_material_changes_is_no_conflict():
    arrow = "사무실 전기료 월 420만원 → 355만원."
    listed = _listed("사무실 전기료", "월 420만원", "월 355만원")
    assert key_figures.parse_all(listed, arrow)[1] == []
    agreed = "박민호가 간식비 80만원을 요청했으나 팀장이 50만원으로 줄이자고 했고 합의."
    assert key_figures.parse_all(_listed("간식비", "80만원", "50만원"), agreed)[1] == []
    assert key_figures.changes("예산을 80만원에서 50만원으로") == [
        ("원", 800000.0, 500000.0, "80만원", "50만원")]
    # two sources giving two values still are one
    mails = "3월 메일: 단가 2만원.\n4월 메일: 단가 2.5만원."
    assert len(key_figures.parse_all(_listed("단가", "2만원", "2.5만원"), mails)[1]) == 1


def test_the_change_reading_finishes_on_a_long_run_of_spaces():
    started = time.perf_counter()
    key_figures.changes("420만원" + " " * 50_000 + "→" + " " * 50_000 + "355만원")
    assert time.perf_counter() - started < 0.5
