"""다음 리필: the next 1st at 00:00 in the service zone, across month and year ends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.credits import next_cycle_reset


def _kst_first(year: int, month: int) -> datetime:
    """00:00 KST on the 1st, which is 15:00 UTC the day before."""
    return datetime(year, month, 1, tzinfo=UTC) - timedelta(hours=9)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # mid-month: the walk's 11-14 reads 12월 1일, not 11월 1일
        (datetime(2026, 11, 14, 8, 0, tzinfo=UTC), _kst_first(2026, 12)),
        # the last UTC evening of November is already 12-01 in Korea: January next
        (datetime(2026, 11, 30, 15, 30, tzinfo=UTC), _kst_first(2027, 1)),
        # one minute before the turn in Korea: still December's 1st
        (datetime(2026, 11, 30, 14, 59, tzinfo=UTC), _kst_first(2026, 12)),
        # year end
        (datetime(2026, 12, 31, 14, 0, tzinfo=UTC), _kst_first(2027, 1)),
        (datetime(2026, 12, 31, 15, 0, tzinfo=UTC), _kst_first(2027, 2)),
        # short months
        (datetime(2027, 1, 31, 12, 0, tzinfo=UTC), _kst_first(2027, 2)),
        (datetime(2027, 2, 27, 15, 0, tzinfo=UTC), _kst_first(2027, 3)),
        (datetime(2028, 2, 29, 3, 0, tzinfo=UTC), _kst_first(2028, 3)),
    ],
)
def test_the_next_refill_is_the_next_first_in_the_service_zone(
    now: datetime, expected: datetime
) -> None:
    assert next_cycle_reset(now) == expected
    assert next_cycle_reset(now) > now
