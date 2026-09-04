"""`thinking.starved`: an answer starved by reasoning tokens is re-asked with more room."""

from __future__ import annotations

import inspect

import pytest

from app.services import deck, page, report, thinking


def payload(*, finish: str, content: str, reasoning: int = 0, completion: int = 0) -> dict:
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {
            "prompt_tokens": 70,
            "completion_tokens": completion or reasoning,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def test_thought_that_left_no_room_asks_for_more() -> None:
    """`length` with empty content and reasoning tokens asks for more than thought plus cap."""
    bigger = thinking.starved(payload(finish="length", content="", reasoning=1152), 860)
    assert bigger > 1152 + 860, "다시 물을 예산이 생각한 만큼도 안 됩니다"


def test_an_answer_that_arrived_is_left_alone() -> None:
    assert thinking.starved(payload(finish="stop", content='{"title": "x"}'), 860) == 0


def test_a_truncated_answer_is_left_alone() -> None:
    """`length` with partial content is not re-asked; salvage parsers handle it."""
    assert thinking.starved(
        payload(finish="length", content='{"title": "정보보호", "slid', reasoning=1031), 860
    ) == 0


def test_an_empty_answer_the_model_chose_is_left_alone() -> None:
    """`stop` with empty content is a refusal, not starvation."""
    assert thinking.starved(payload(finish="stop", content=""), 400) == 0


def test_running_out_with_no_reasoning_at_all_still_asks_for_more() -> None:
    """`length` with empty content and no reasoning tokens still asks for more."""
    assert thinking.starved(payload(finish="length", content="", completion=400), 400) > 400


@pytest.mark.parametrize("module", [deck, report, page], ids=["deck", "report", "page"])
def test_all_three_document_paths_check_it(module) -> None:
    """Every document `_complete` checks for starvation and counts both calls' tokens."""
    source = inspect.getsource(module._complete)
    assert "thinking.starved" in source, module.__name__
    assert "completion_tokens" in source
