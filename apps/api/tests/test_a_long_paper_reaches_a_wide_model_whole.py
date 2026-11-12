"""Attached text is budgeted by the model's window, with the configured floor kept."""

from app.core.config import settings
from app.services.workspace_context import file_budget


def test_no_model_keeps_the_floor() -> None:
    assert file_budget(None) == settings.file_context_chars


def test_an_unknown_window_is_budgeted_by_the_default_window_not_the_floor() -> None:
    # A hosted model whose window nobody stated gets the default window's share, the same
    # one its conversation is fitted to — not the 24k-character floor.
    default = file_budget({"contextWindow": settings.default_context_window})
    assert file_budget({"contextWindow": None}) == default
    assert file_budget({"contextWindow": 0}) == default
    assert default > settings.file_context_chars * 3


def test_a_wide_window_carries_a_sixteen_page_paper() -> None:
    # A 126,976-token window; a sixteen-page paper is about 72,000 characters.
    assert file_budget({"contextWindow": 126_976}) > 72_000


def test_a_small_window_never_drops_below_the_floor() -> None:
    assert file_budget({"contextWindow": 8_000}) == settings.file_context_chars


def test_the_share_is_capped() -> None:
    assert file_budget({"contextWindow": 2_000_000}) == 150_000
