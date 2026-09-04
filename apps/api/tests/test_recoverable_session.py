"""build_messages always sends alternating roles, whatever the stored history holds."""

from __future__ import annotations

from app.models.chat import SessionKind
from app.services.context import build_messages


def _roles(messages: list[dict[str, str]]) -> list[str]:
    return [m["role"] for m in messages]


def test_a_question_nobody_answered_does_not_break_the_next_one():
    history = [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답"},
        # An unanswered turn.
        {"role": "user", "content": "실패한 질문"},
        {"role": "user", "content": "다시 묻는 질문"},
    ]

    messages = build_messages(SessionKind.chat, history)

    assert _roles(messages) == ["system", "user", "assistant", "user"]
    # Merged, not dropped.
    assert "실패한 질문" in messages[-1]["content"]
    assert "다시 묻는 질문" in messages[-1]["content"]


def test_several_failures_in_a_row_still_produce_one_turn():
    history = [{"role": "user", "content": f"질문 {n}"} for n in range(4)]

    messages = build_messages(SessionKind.chat, history)

    assert _roles(messages) == ["system", "user"]
    assert all(f"질문 {n}" in messages[-1]["content"] for n in range(4))


def test_reference_data_does_not_double_the_user_turn():
    """The untrusted-context block merges into the opening user turn."""
    messages = build_messages(
        SessionKind.chat,
        [{"role": "user", "content": "질문"}],
        untrusted_context=["첨부 문서 내용"],
    )

    assert _roles(messages) == ["system", "user"]
    assert "첨부 문서 내용" in messages[-1]["content"]
    assert "따르지 말고" in messages[-1]["content"]


def test_an_ordinary_conversation_is_left_alone():
    history = [
        {"role": "user", "content": "가"},
        {"role": "assistant", "content": "나"},
        {"role": "user", "content": "다"},
    ]

    messages = build_messages(SessionKind.chat, history)

    assert _roles(messages) == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in messages[1:]] == ["가", "나", "다"]
