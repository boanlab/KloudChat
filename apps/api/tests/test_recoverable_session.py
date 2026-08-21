"""A conversation that has failed once must still work.

The report this fixes: pick a different model, watch the request fail, come
back to the model that had been answering all along, ask again — and it fails
again, and keeps failing, forever. Only a brand-new conversation escapes. From
the outside the whole service looks broken.

What actually happened is one line of transcript. A question is stored before
the model answers, so a turn that fails leaves a user message with nothing
under it. The next question is appended straight after it, and the payload that
goes to the model now holds two user turns in a row. Chat templates are written
for a transcript that alternates; several of the local ones refuse that outright
— so the poison is in the conversation, not in the model that was picked, and
changing models cannot lift it.

These tests hold the repair at the place it belongs: the payload builder. Every
message sent to a model alternates, whatever the stored history looks like.
"""

from __future__ import annotations

from app.models.chat import SessionKind
from app.services.context import build_messages


def _roles(messages: list[dict[str, str]]) -> list[str]:
    return [m["role"] for m in messages]


def test_a_question_nobody_answered_does_not_break_the_next_one():
    history = [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답"},
        # The turn that failed: asked, never answered.
        {"role": "user", "content": "실패한 질문"},
        {"role": "user", "content": "다시 묻는 질문"},
    ]

    messages = build_messages(SessionKind.chat, history)

    assert _roles(messages) == ["system", "user", "assistant", "user"]
    # Merged, not dropped. The unanswered question is still what the person
    # asked, and 다시 물어보기 has to be answerable in its light.
    assert "실패한 질문" in messages[-1]["content"]
    assert "다시 묻는 질문" in messages[-1]["content"]


def test_several_failures_in_a_row_still_produce_one_turn():
    history = [{"role": "user", "content": f"질문 {n}"} for n in range(4)]

    messages = build_messages(SessionKind.chat, history)

    assert _roles(messages) == ["system", "user"]
    assert all(f"질문 {n}" in messages[-1]["content"] for n in range(4))


def test_reference_data_does_not_double_the_user_turn():
    """The other way two user turns appear — and it happens on healthy sessions.

    The reference block is a user message by design, and it lands immediately
    before a history that ordinarily opens with one.
    """
    messages = build_messages(
        SessionKind.chat,
        [{"role": "user", "content": "질문"}],
        untrusted_context=["첨부 문서 내용"],
    )

    assert _roles(messages) == ["system", "user"]
    assert "첨부 문서 내용" in messages[-1]["content"]
    # The instruction that makes the block safe travels with it.
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
