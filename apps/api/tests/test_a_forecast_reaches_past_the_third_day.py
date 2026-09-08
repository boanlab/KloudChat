"""A multi-day forecast keeps naming days past 모레 instead of running out of words."""

from __future__ import annotations

from app.services.tools.builtin import format_weather


def _daily(dates: list[str]) -> dict:
    n = len(dates)
    return {
        "latitude": 33.59,
        "longitude": 130.40,
        "timezone": "Asia/Tokyo",
        "current": {
            "time": "2026-09-08T09:00",
            "temperature_2m": 27,
            "apparent_temperature": 29,
            "weather_code": 1,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 10,
            "precipitation": 0,
        },
        "daily": {
            "time": dates,
            "weather_code": [1] * n,
            "temperature_2m_max": [28] * n,
            "temperature_2m_min": [20] * n,
            "precipitation_probability_max": [10] * n,
        },
    }


def test_the_first_three_days_read_as_relative_names():
    text = format_weather("후쿠오카", _daily(["2026-09-08", "2026-09-09", "2026-09-10"]))

    assert "오늘(2026-09-08)" in text
    assert "내일(2026-09-09)" in text
    assert "모레(2026-09-10)" in text


def test_a_day_past_the_third_gets_a_weekday_not_a_repeated_date():
    dates = [
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
        "2026-09-14",  # a Monday
    ]

    text = format_weather("후쿠오카", _daily(dates))

    assert "9월 14일(월)" in text
    # The fallback this replaced named the day with its own ISO date, which then
    # got the date appended again right after it: "2026-09-14(2026-09-14)".
    assert "2026-09-14(2026-09-14)" not in text
