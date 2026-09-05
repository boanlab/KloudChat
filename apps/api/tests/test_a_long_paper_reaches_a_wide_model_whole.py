"""Attached text is budgeted by the model's window, with the configured floor kept."""

from app.core.config import settings
from app.services.workspace_context import file_budget


def test_an_unknown_window_keeps_the_floor() -> None:
    assert file_budget(None) == settings.file_context_chars
    assert file_budget({"contextWindow": None}) == settings.file_context_chars
    assert file_budget({"contextWindow": 0}) == settings.file_context_chars


def test_a_wide_window_carries_a_sixteen_page_paper() -> None:
    # Qwen3.5-122B reports 126,976 tokens; the Spectre paper is about 72,000 characters.
    assert file_budget({"contextWindow": 126_976}) > 72_000


def test_a_small_window_never_drops_below_the_floor() -> None:
    assert file_budget({"contextWindow": 8_000}) == settings.file_context_chars


def test_the_share_is_capped() -> None:
    assert file_budget({"contextWindow": 2_000_000}) == 150_000
