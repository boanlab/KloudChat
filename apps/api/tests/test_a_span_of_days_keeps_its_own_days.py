"""A span of days the person wrote (「12/14~12/18」, 「3월 9~12일」) is kept day by day."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services import check_skills, skill_scripts

TODAY = dt.date(2026, 11, 20)


def _ground(person: str, answer: str, computed: list[str] | None = None) -> check_skills.Outcome:
    options = {**check_skills.options_for(TODAY, {}, surface="chat"), "person": person,
               "requirements": True}
    return check_skills.ground(("grounding-check",), answer, material=person,
                               computed=computed or [], options=options, evidence=[person])


def test_a_short_span_in_a_dated_memo_stays_as_the_answer_writes_it():
    memo = "[물류팀 메모 — 2026-12-02 작성]\n- 재고 실사(12/14~12/18), 창고 B동 출고 중지."
    answer = "재고 실사는 12월 14일부터 12월 18일까지 진행되며, 이 기간 B동 출고를 멈춥니다."
    out = _ground(memo, answer)
    assert out.text == answer
    assert "2026-12-02" not in out.text


def test_a_spans_first_day_is_no_deadline_to_put_back():
    request = ("- 원래 출고일 3월 26일\n- 서버 점검으로 3월 9~12일 출고 중단\n"
               "- 4월 2일까지 회신 요청")
    answer = "3월 9일부터 12일까지 서버 점검으로 출고가 멈췄습니다. 3월 26일 출고는 그대로입니다."
    trace = skill_scripts.module("grounding-check", "trace")
    found = trace.check(answer, request, ["3월 9일(월)~3월 12일(목)"], {"person": request})
    assert not [f for f in found if f.get("placeholder") == "3월 26일"]
    assert _ground(request, answer, ["3월 9일(월)~3월 12일(목)"]).text == answer


@pytest.mark.parametrize(("written", "inside"), [
    ("11월 17~20일", [(None, 11, 17), (None, 11, 18), (None, 11, 20)]),
    ("11/10~11/14", [(None, 11, 10), (None, 11, 12), (None, 11, 14)]),
    ("2026. 12. 29. ~ 1. 2.", [(2026, 12, 30), (2027, 1, 1), (2027, 1, 2)]),
    ("11월 17일부터 20일까지", [(None, 11, 17), (None, 11, 19), (None, 11, 20)]),
])
def test_every_day_of_a_written_span_counts_as_the_persons(written: str, inside):
    trace = skill_scripts.module("grounding-check", "trace")
    own = trace.person_days(f"- 일정: {written}")
    for key in inside:
        assert key in own


def test_hours_after_a_day_are_no_span_of_days():
    trace = skill_scripts.module("grounding-check", "trace")
    assert (None, 11, 18) not in trace.range_days("11월 17일부터 3시간 점검")


def test_a_garbled_copy_of_the_one_deadline_still_reads_as_that_deadline():
    trace = skill_scripts.module("grounding-check", "trace")
    request = "- 제출 기한: 11월 6일까지\n- 담당: 기획팀"
    found = trace.check("서류는 11월 16일까지 제출해 주세요.", request, ["11월 16일"],
                        {"person": request})
    assert [f.get("placeholder") for f in found if f["kind"] == "date"] == ["11월 6일"]
