"""`DOCUMENT_SECTION_CONCURRENCY`: how many sections of a long report are in flight at once."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.pipeline import report_stages


async def _peak(count: int) -> int:
    gate = report_stages.section_gate()
    running = peak = 0

    async def one() -> None:
        nonlocal running, peak
        async with gate:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

    await asyncio.gather(*(one() for _ in range(count)))
    return peak


@pytest.mark.asyncio
async def test_the_default_is_four_at_a_time() -> None:
    assert settings.document_section_concurrency == 4
    assert await _peak(10) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 3, 7])
async def test_the_gate_follows_the_setting(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    monkeypatch.setattr(settings, "document_section_concurrency", limit)
    assert await _peak(10) == limit


def test_the_setting_must_be_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_SECTION_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("DOCUMENT_SECTION_CONCURRENCY", "2")
    assert Settings().document_section_concurrency == 2
