"""「지금부터 답은 영어로만」 holds after the next Korean question.

The research lane asked for English answers from now on; the next answer was English, the
one after (a Korean question) Korean again: the chat default answers in the question's
language, and the replayed rule lost to it.
"""

from __future__ import annotations

from app.models.chat import SessionKind
from app.services.context import build_messages


def _system(history: list[dict[str, str]]) -> str:
    return build_messages(SessionKind.chat, history, remind_standing=True)[0]["content"]


def _turns(*users: str) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for text in users[:-1]:
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": "OK."}]
    return history + [{"role": "user", "content": users[-1]}]


def test_a_standing_english_rule_reaches_the_system_turn():
    system = _system(_turns("지금부터 답은 영어로만 해 줘.", "트랜스포머가 뭐야?", "어텐션은?"))
    assert "Write the entire answer in English, even when the question is in Korean" in system


def test_withdrawn_or_overridden_or_never_set_it_does_not():
    assert "in English" not in _system(_turns("트랜스포머가 뭐야?", "어텐션은?"))
    # A later withdrawal clears it.
    assert "even when the question is in Korean" not in _system(
        _turns("지금부터 답은 영어로만 해 줘.", "원래대로 해 줘.", "어텐션은?")
    )
    # Switching back to Korean for the rest of the conversation.
    assert "even when the question is in Korean" not in _system(
        _turns("지금부터 답은 영어로만 해 줘.", "앞으로는 한국어로 답해 줘.", "어텐션은?")
    )
    # The question naming a language itself wins.
    assert "even when the question is in Korean" not in _system(
        _turns("지금부터 답은 영어로만 해 줘.", "이건 한국어로 설명해 줘.")
    )
