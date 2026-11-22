"""A change the material gives in points (「+4.2 points open rate」) backs 「4.2 percentage
points」."""

from __future__ import annotations

from app.services import value_guard

BRIEF = ("- Open rate: Oct 31.5% (Sep 28.9%)\n"
         "- Subject-line A/B test on Oct 14: question-style subject +4.2 points open rate vs. "
         "statement-style")


def test_the_given_points_back_the_same_percentage_points():
    basis = value_guard.percent_basis([BRIEF])
    text = "Question-style subject lines won by 4.2 percentage points in open rate."
    assert value_guard.mark_unbacked_percents(text, basis) == (text, 0)


def test_other_percentage_points_are_still_checked():
    basis = value_guard.percent_basis([BRIEF])
    marked, n = value_guard.mark_unbacked_percents(
        "Question-style subject lines won by 7.9 percentage points in open rate.", basis)
    assert n == 1
