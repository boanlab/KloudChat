"""An unanswered turn marks the question row; nothing is invented and the mark is served back."""

from __future__ import annotations

import asyncio
import json

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
    """One conversation mid-turn: the question committed, the answer not yet."""

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

    async def store_artifacts(**_kwargs):
        return None

    async def enrich_memory(**_kwargs):
        return None

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: turn)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_store_artifacts", store_artifacts)
    monkeypatch.setattr(sessions_router, "_enrich_memory", enrich_memory)

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
    # No empty assistant row.
    assert turn.answer is None


@pytest.mark.asyncio
async def test_a_stream_that_ends_saying_nothing_is_still_a_turn_without_an_answer(
    monkeypatch,
) -> None:
    """A stream with no exception and no text still marks the question unanswered."""
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
    # Partial text survives, labelled.
    assert answer.content == "보조금은 지자체마다 "
    assert answer.failure is TurnFailure.interrupted
    # Only the answer row is marked.
    assert turn.question.failure is None


@pytest.mark.asyncio
async def test_a_step_is_stored_once_with_the_words_it_finished_on(monkeypatch) -> None:
    """A step's running and done events share an id and are stored once."""
    turn = _Turn()
    await _drain(
        turn,
        monkeypatch,
        [
            {"type": "step", "id": "s1", "label": "웹 검색 중", "status": "running"},
            {"type": "step", "id": "s1", "label": "웹 검색", "status": "done", "detail": "5건"},
            {"type": "delta", "text": "답"},
            {"type": "usage", "inputTokens": 1, "outputTokens": 1},
        ],
    )

    answer = turn.answer
    assert answer is not None
    assert answer.steps == [{"id": "s1", "label": "웹 검색", "status": "done", "detail": "5건"}]


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


@pytest.mark.asyncio
async def test_done_names_the_stored_answer(monkeypatch) -> None:
    """`done` carries the stored answer row's id."""
    turn = _Turn()
    chunks = await _drain(
        turn,
        monkeypatch,
        [
            {"type": "delta", "text": "지자체마다 다릅니다."},
            {"type": "usage", "inputTokens": 12, "outputTokens": 8},
        ],
    )

    (done,) = [chunk for chunk in chunks if '"type": "done"' in chunk]
    assert turn.answer is not None
    assert f'"messageId": "{turn.answer.id}"' in done


@pytest.mark.asyncio
async def test_done_names_nothing_when_nothing_was_stored(monkeypatch) -> None:
    turn = _Turn()
    chunks = await _drain(turn, monkeypatch, [chat_service.ChatStreamError("upstream 502")])

    (done,) = [chunk for chunk in chunks if '"type": "done"' in chunk]
    assert "messageId" not in done


# ── 중단 ───────────────────────────────────────────────────────────────


def _press_stop(session_id: str) -> None:
    """What POST /sessions/{id}/stop does, from inside the turn."""
    for signal in sessions_router._STOPPING.get(session_id, set()):
        signal.set()


@pytest.mark.asyncio
async def test_a_stopped_turn_is_marked_stopped_and_still_charged(monkeypatch) -> None:
    """A stopped turn is stored as `stopped`, not `interrupted`, and usage is estimated."""
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
    # Estimated from the traffic, and flagged as an estimate.
    assert answer.usage["estimated"] is True
    assert answer.usage["inputTokens"] > 0
    assert answer.usage["outputTokens"] > 0
    assert answer.usage["credits"] > 0


@pytest.mark.asyncio
async def test_a_completed_hop_keeps_its_reported_usage_when_a_later_one_is_stopped(
    monkeypatch,
) -> None:
    """An estimate fills a gap; it never overwrites usage the proxy reported."""
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


def _error_events(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: ") :])
                if event.get("type") == "error":
                    out.append(event)
    return out


@pytest.mark.asyncio
async def test_the_error_event_says_why(monkeypatch) -> None:
    """The error event carries the machine code and the upstream sentence, keys blanked."""
    turn = _Turn()
    chunks = await _drain(
        turn,
        monkeypatch,
        [chat_service.ChatStreamError("upstream_502: overloaded; key sk-abc123XYZ rejected")],
    )

    (error,) = _error_events(chunks)
    assert error["code"] == "upstream_502"
    assert "overloaded" in error["reason"]
    assert "sk-abc123XYZ" not in error["reason"]
    assert error["message"] == "모델 응답을 받지 못했습니다."


@pytest.mark.asyncio
async def test_an_unreachable_backend_is_named_as_one(monkeypatch) -> None:
    turn = _Turn()
    chunks = await _drain(
        turn, monkeypatch, [chat_service.ChatStreamError("upstream_unreachable: connect refused")]
    )

    (error,) = _error_events(chunks)
    assert error["code"] == "upstream_unreachable"
    assert error["reason"] == "connect refused"


@pytest.mark.asyncio
async def test_a_crash_still_carries_a_code(monkeypatch) -> None:
    turn = _Turn()
    chunks = await _drain(turn, monkeypatch, [RuntimeError("boom")])

    (error,) = _error_events(chunks)
    assert error["code"] == "internal_error"
    # No upstream sentence, so nothing quoted.
    assert "reason" not in error


def test_the_mark_travels_with_the_transcript() -> None:
    """The stored outcome is returned with the transcript."""
    question = Message(
        session_id="session-1",
        role=Role.user,
        content="질문",
        failure=TurnFailure.no_answer,
    )

    assert MessageOut.of(question).failure is TurnFailure.no_answer
    assert MessageOut.of(question).model_dump(by_alias=True)["failure"] == "no_answer"
    # Rows without an outcome, and every ordinary turn.
    assert MessageOut.of(Message(session_id="s", role=Role.user, content="질문")).failure is None
