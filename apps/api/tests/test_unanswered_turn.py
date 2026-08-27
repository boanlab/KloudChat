"""A question with nothing under it, and a record that admits it.

A quarter of the conversations on the test account are one user message and no
reply. The question is written before the model answers, so a turn that breaks,
is refused, or has its connection closed leaves the sentence behind on its own
— and until now nothing anywhere said the answer never came. Opened, such a
conversation was a prompt with silence under it; in the list it looked exactly
like one that worked.

Keeping the question is right. The person did ask, and deleting their words
would be the dishonest repair. What these tests hold is the other half:

1. A turn that produces nothing marks the question, and does not invent an
   assistant message to say so. Nothing spoke, so nothing may read as though it
   had.
2. A turn that writes some of an answer and then breaks keeps what it wrote and
   labels it, because half an answer that looks whole is the same lie in a
   quieter voice.
3. A turn that answers marks nothing, on either row. An ordinary conversation
   must not learn to look failed.
4. The mark comes back out with the transcript, which is the whole point: the
   browser already says this while it is happening, in one tab, until reload.
"""

from __future__ import annotations

import asyncio

import pytest

from app.models.chat import ChatSession, Message, Role, TurnFailure
from app.models.user import User
from app.routers import sessions as sessions_router
from app.schemas.chat import MessageOut
from app.services import chat as chat_service


def _model(model_id: str = "vendor/model") -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "kinds": ["chat"],
        "dataBoundary": "external",
        "strictLocal": False,
        "privacyOnly": False,
        "inputCreditCost": 10,
        "creditCost": 10,
        "contextWindow": 32_000,
        "supportsTools": False,
    }


class _Turn:
    """One conversation mid-turn: the question committed, the answer not yet.

    Mirrors the only state `_run_turn` ever opens its own session onto — the
    route has already written the user's sentence and closed its transaction.
    """

    def __init__(self) -> None:
        self.session = ChatSession(id="session-1", user_id="user-1", model="vendor/model")
        self.user = User(
            id="user-1",
            email="person@example.test",
            password_hash="hash",
            name="Person",
        )
        self.question = Message(
            id="message-1",
            session_id="session-1",
            role=Role.user,
            content="전기차 보조금이 어떻게 되나요?",
        )
        self.added: list[object] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, key):
        if model is ChatSession and key == self.session.id:
            return self.session
        if model is User and key == self.user.id:
            return self.user
        if model is Message and key == self.question.id:
            return self.question
        return None

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    @property
    def answer(self) -> Message | None:
        return next(
            (
                row
                for row in self.added
                if isinstance(row, Message) and row.role is Role.assistant
            ),
            None,
        )


async def _drain(turn: _Turn, monkeypatch, events) -> list[str]:
    async def run_turn(*_args, **_kwargs):
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event

    return await _drain_with(turn, monkeypatch, run_turn)


async def _drain_with(turn: _Turn, monkeypatch, run_turn) -> list[str]:
    async def title(*_args, **_kwargs):
        return "제목", {"inputTokens": 0, "outputTokens": 0}

    async def enrich(**_kwargs):
        return None, None

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: turn)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)

    return [
        chunk
        async for chunk in sessions_router._run_turn(
            user_id=turn.user.id,
            api_key="virtual-key",
            auto_memory=False,
            session_id=turn.session.id,
            model=_model(),
            messages=[{"role": "user", "content": turn.question.content}],
            tools=[],
            first_user_message=turn.question.content,
            user_message_id=turn.question.id,
            is_first_turn=True,
        )
    ]


@pytest.mark.asyncio
async def test_a_turn_that_breaks_before_a_word_marks_the_question(monkeypatch) -> None:
    turn = _Turn()
    await _drain(turn, monkeypatch, [chat_service.ChatStreamError("upstream 502")])

    assert turn.question.failure is TurnFailure.no_answer
    # Nothing spoke, so nothing may be stored as having spoken. An empty
    # assistant row would have been the easy fix and the wrong one.
    assert turn.answer is None


@pytest.mark.asyncio
async def test_a_stream_that_ends_saying_nothing_is_still_a_turn_without_an_answer(
    monkeypatch,
) -> None:
    """The quietest variant: no exception, no text, no reply row, no complaint."""
    turn = _Turn()
    await _drain(turn, monkeypatch, [{"type": "usage", "inputTokens": 12, "outputTokens": 0}])

    assert turn.question.failure is TurnFailure.no_answer
    assert turn.answer is None


@pytest.mark.asyncio
async def test_half_an_answer_is_kept_and_said_to_be_half(monkeypatch) -> None:
    turn = _Turn()
    await _drain(
        turn,
        monkeypatch,
        [
            {"type": "delta", "text": "보조금은 지자체마다 "},
            chat_service.ChatStreamError("connection reset"),
        ],
    )

    answer = turn.answer
    assert answer is not None
    # What it managed to write survives; the label is the difference between a
    # short answer and an answer that stopped.
    assert answer.content == "보조금은 지자체마다 "
    assert answer.failure is TurnFailure.interrupted
    # The question was answered, as far as it goes. Marking it too would put the
    # notice in two places and the way back under the wrong one.
    assert turn.question.failure is None


@pytest.mark.asyncio
async def test_an_ordinary_turn_marks_neither_row(monkeypatch) -> None:
    turn = _Turn()
    await _drain(
        turn,
        monkeypatch,
        [
            {"type": "delta", "text": "지자체마다 다릅니다."},
            {"type": "usage", "inputTokens": 12, "outputTokens": 8},
        ],
    )

    answer = turn.answer
    assert answer is not None
    assert answer.failure is None
    assert turn.question.failure is None


# ── 중단 ───────────────────────────────────────────────────────────────


def _press_stop(session_id: str) -> None:
    """What POST /sessions/{id}/stop does, from inside the turn."""
    for signal in sessions_router._STOPPING.get(session_id, set()):
        signal.set()


@pytest.mark.asyncio
async def test_a_stopped_turn_is_marked_stopped_and_still_charged(monkeypatch) -> None:
    """The person pressed 중단; the row must say so, and the tokens were spent.

    Two things were wrong. A pressed button and a dropped socket were stored as
    the same `interrupted`, so the notice for a stop the reader chose came up
    in the error colour. And the proxy reports usage on its final chunk, which
    a stopped stream never reaches — so the turn settled as 0 in · 0 out under
    a paid model.
    """
    turn = _Turn()

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": "보조금은 지자체마다 다르고, "}
        _press_stop(turn.session.id)
        await asyncio.Event().wait()  # the model keeps going; the reader did not
        yield {"type": "usage", "inputTokens": 500, "outputTokens": 400}  # never reached

    await asyncio.wait_for(_drain_with(turn, monkeypatch, run_turn), timeout=2)

    answer = turn.answer
    assert answer is not None
    assert answer.content == "보조금은 지자체마다 다르고, "
    assert answer.failure is TurnFailure.stopped
    assert turn.question.failure is None
    # Estimated from what went up and what came down, and said to be.
    assert answer.usage["estimated"] is True
    assert answer.usage["inputTokens"] > 0
    assert answer.usage["outputTokens"] > 0
    assert answer.usage["credits"] > 0


@pytest.mark.asyncio
async def test_a_completed_hop_keeps_its_reported_usage_when_a_later_one_is_stopped(
    monkeypatch,
) -> None:
    """An estimate fills a gap; it does not overwrite a figure the proxy gave."""
    turn = _Turn()

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": "첫 번째 hop"}
        yield {"type": "usage", "inputTokens": 120, "outputTokens": 30}
        _press_stop(turn.session.id)
        await asyncio.Event().wait()

    await asyncio.wait_for(_drain_with(turn, monkeypatch, run_turn), timeout=2)

    answer = turn.answer
    assert answer is not None
    assert answer.failure is TurnFailure.stopped
    assert answer.usage["inputTokens"] == 120
    assert answer.usage["outputTokens"] == 30
    assert "estimated" not in answer.usage


@pytest.mark.asyncio
async def test_stopped_before_the_first_token_marks_the_question_stopped(monkeypatch) -> None:
    turn = _Turn()

    async def run_turn(*_args, **_kwargs):
        _press_stop(turn.session.id)
        await asyncio.Event().wait()
        yield {"type": "delta", "text": "never"}

    await asyncio.wait_for(_drain_with(turn, monkeypatch, run_turn), timeout=2)

    assert turn.answer is None
    assert turn.question.failure is TurnFailure.stopped


def test_the_mark_travels_with_the_transcript() -> None:
    """Otherwise it says nothing to the only person who needs it.

    The browser already puts a notice on screen when a stream dies under it.
    That notice lives in one tab's memory and is gone on reload, which is the
    entire complaint — so the stored outcome has to come back down the wire.
    """
    question = Message(
        session_id="session-1",
        role=Role.user,
        content="질문",
        failure=TurnFailure.no_answer,
    )

    assert MessageOut.of(question).failure is TurnFailure.no_answer
    assert MessageOut.of(question).model_dump(by_alias=True)["failure"] == "no_answer"
    # Every row written before any of this existed, and every ordinary turn.
    assert MessageOut.of(Message(session_id="s", role=Role.user, content="질문")).failure is None
