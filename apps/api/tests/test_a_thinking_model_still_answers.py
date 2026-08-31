"""A model that thinks must not be reported as a person who asked badly.

A reasoning model returns its chain of thought as `completion_tokens`, and an
OpenAI-compatible `max_tokens` caps thought and answer together. The report
outline asked for 400 tokens and an eight-slide deck outline for 860; three of
five models measured on this deployment's own gateway spend more than that
thinking. The call returns 200, `finish_reason: "length"`, `content: ""` — and
the product said "요청을 조금 더 구체적으로 적어 주세요" to somebody whose request
was fine and whose rewrite could not have helped.

What has to hold is that the three cases stay apart: an answer that arrived, an
answer the model declined to give, and an answer there was no room for.
"""

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
    """The measured shape: gpt-5-nano, 1,152 reasoning tokens, nothing said."""
    bigger = thinking.starved(payload(finish="length", content="", reasoning=1152), 860)
    assert bigger > 1152 + 860, "다시 물을 예산이 생각한 만큼도 안 됩니다"


def test_an_answer_that_arrived_is_left_alone() -> None:
    assert thinking.starved(payload(finish="stop", content='{"title": "x"}'), 860) == 0


def test_a_truncated_answer_is_left_alone() -> None:
    """Partial JSON is what the callers' salvage parsers are for.

    Re-asking would throw away what did arrive and charge for the privilege.
    """
    assert thinking.starved(
        payload(finish="length", content='{"title": "정보보호", "slid', reasoning=1031), 860
    ) == 0


def test_an_empty_answer_the_model_chose_is_left_alone() -> None:
    """`finish_reason: "stop"` with nothing in it is a refusal, not a squeeze.

    Asking again with more room would buy nothing and cost a call.
    """
    assert thinking.starved(payload(finish="stop", content=""), 400) == 0


def test_running_out_with_no_reasoning_at_all_still_asks_for_more() -> None:
    """A ceiling too small for the answer itself is the same bug without the
    reasoning — a long outline truncated to nothing."""
    assert thinking.starved(payload(finish="length", content="", completion=400), 400) > 400


@pytest.mark.parametrize("module", [deck, report, page], ids=["deck", "report", "page"])
def test_all_three_document_paths_check_it(module) -> None:
    """One of the three having the guard is the same bug in the other two.

    They are three copies of the same `_complete`, which is why this is
    parametrised rather than written once against whichever was fixed first.
    """
    source = inspect.getsource(module._complete)
    assert "thinking.starved" in source, module.__name__
    # And both calls are billed, so both are counted.
    assert "completion_tokens" in source
