"""A file the conversation carried before, and this turn does not.

An attachment belongs to the turn it was sent with: it is read once, into that
turn's prompt, and the next question starts without it. Nothing said so — the
turn simply had no file block and no file report, because there were no files —
so a model asked "같은 csv 에서 cryptoflex 의 5000 지점 값은?" one turn later had
the conversation, no data, and no reason to think anything was missing.

Measured on the same question three times: **24.4, 22.8, 35.8**, against a real
value of 1.10. Bare numbers, no hedging. The first turn had answered correctly
from the file, which is what makes the second answer credible.

This is the file report's own principle applied one turn later — the system
knows exactly what happened and should say it, rather than leave the model to
infer it from a gap.
"""

from __future__ import annotations

from app.services.workspace_context import ContextFile, _file_report


def _msg(role: str, names: list[str] | None = None):
    """Enough of a message row for the reader under test."""

    class Row:
        def __init__(self):
            self.role = type("R", (), {"value": role})()
            self.attachments = [{"name": n} for n in names] if names else None

    return Row()


# ── what the model is told ─────────────────────────────────────────────


def test_a_file_from_an_earlier_turn_is_named_as_absent():
    report = _file_report((), (), earlier=("openloop_p99.csv",))
    assert "openloop_p99.csv" in report
    # The instruction that matters: the number is not there to be recalled.
    assert "지어내" in report


def test_it_does_not_read_as_a_file_that_arrived():
    """`included` says the whole thing was delivered. This is the opposite, and
    the two must not sound alike."""
    report = _file_report((), (), earlier=("shot.csv",))
    assert "전달됨" not in report


def test_nothing_is_said_when_the_conversation_never_carried_one():
    assert _file_report((), (), earlier=()) == ""


def test_a_file_attached_again_this_turn_is_not_also_reported_as_gone():
    """Both lines would be true of different files and contradictory about one."""
    report = _file_report(
        (ContextFile("openloop_p99.csv", "included", 200, 200),),
        (),
        earlier=("openloop_p99.csv",),
    )
    assert report.count("openloop_p99.csv") == 1
    assert "전체 200자 전달됨" in report
