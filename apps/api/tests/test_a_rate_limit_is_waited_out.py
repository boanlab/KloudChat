"""A 429 that names its reset moment is waited out, not retried into."""

from datetime import UTC, datetime

from app.services import ratelimit

_BODY = (
    '{"error":{"message":"Rate limit exceeded for REDACTED Limit type: tokens. '
    'Current limit: 100000, Remaining: 0. Limit resets at: 2026-09-06 05:25:06 UTC"}}'
)


def test_the_wait_runs_to_the_named_reset() -> None:
    now = datetime(2026, 9, 6, 5, 24, 30, tzinfo=UTC)
    assert ratelimit.retry_delay(_BODY, {}, 2.0, now=now) == 37.0


def test_a_reset_already_past_falls_back_to_the_step() -> None:
    now = datetime(2026, 9, 6, 5, 26, 0, tzinfo=UTC)
    assert ratelimit.retry_delay(_BODY, {}, 6.0, now=now) == 6.0


def test_a_far_reset_is_capped() -> None:
    now = datetime(2026, 9, 6, 5, 0, 0, tzinfo=UTC)
    assert ratelimit.retry_delay(_BODY, {}, 2.0, now=now) == ratelimit.MAX_WAIT_SEC


def test_retry_after_is_honoured_when_no_reset_is_named() -> None:
    assert ratelimit.retry_delay("busy", {"retry-after": "15"}, 2.0) == 15.0
    assert ratelimit.retry_delay("busy", {}, 2.0) == 2.0


def test_the_document_writers_wait_this_way() -> None:
    from app.services import deck, page, report

    for module in (deck, report, page):
        assert module.ratelimit is ratelimit
    assert sum(deck._BACKOFF) >= 60 and sum(report._BACKOFF) >= 60
