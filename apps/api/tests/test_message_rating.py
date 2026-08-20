"""좋아요 / 싫어요: the verdict is kept, and it is the rater's own.

The two buttons under every answer used to write nothing but React state. The
complaint that produced this file is not "no analytics" — it is that a person
marks an answer wrong and the product has forgotten by the time they scroll
back to it. So what these tests hold are the three properties that make the
thumb worth pressing:

1. The verdict lands on the row, and comes back out with the transcript.
2. Withdrawing it is expressible. `null` is a value on this endpoint, not a
   field somebody forgot to send, because pressing a lit thumb again means
   "I take it back" and that has to survive the round trip too.
3. It is written where its owner reads it. A message id in the URL must not
   become a way to rate — or read — somebody else's conversation.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, Message, MessageRating, Role, SessionKind
from app.models.user import User, UserStatus
from app.routers import workspace as workspace_router
from app.schemas.chat import MessageOut


def _user(user_id: str = "user-1") -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="hash",
        name="User",
        status=UserStatus.active,
    )


def _session(owner: str = "user-1") -> ChatSession:
    return ChatSession(id="session-1", user_id=owner, kind=SessionKind.chat)


def _answer(**changes) -> Message:
    values = {
        "id": "message-1",
        "session_id": "session-1",
        "role": Role.assistant,
        "content": "답변입니다",
    }
    values.update(changes)
    return Message(**values)


class _RatingDb:
    """Just enough session to serve the rating route, and to be watched."""

    def __init__(self, session: ChatSession | None, *messages: Message):
        self.session = session
        self.messages = {m.id: m for m in messages}
        self.commits = 0

    async def get(self, model, row_id):
        if model is Message:
            return self.messages.get(row_id)
        if model is ChatSession and self.session is not None and row_id == self.session.id:
            return self.session
        return None

    def add(self, _row):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        pass


def _client(db: _RatingDb, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router.router)

    async def override_user():
        return user

    async def override_db():
        yield db

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_session] = override_db
    return TestClient(app)


def test_a_verdict_is_written_to_the_row_and_read_back_with_it():
    """The whole of the fix, in one round trip.

    The response is the message itself rather than an acknowledgement, so the
    client that drew the thumb optimistically has the server's version of the
    same turn in hand and never has to guess whether the write took.
    """
    message = _answer()
    db = _RatingDb(_session(), message)

    with _client(db, _user()) as client:
        response = client.patch("/messages/message-1/rating", json={"rating": "down"})

    assert response.status_code == 200
    assert message.rating is MessageRating.down
    assert response.json()["rating"] == "down"
    assert db.commits == 1


def test_taking_a_rating_back_is_a_value_and_not_a_missing_field():
    """Pressing the lit thumb again.

    `null` has to reach the column. A schema that treated an absent rating as
    "leave it alone" would make the second press a no-op, and the thumb would
    stay lit over a conversation nobody stands behind any more.
    """
    message = _answer(rating=MessageRating.up)
    db = _RatingDb(_session(), message)

    with _client(db, _user()) as client:
        response = client.patch("/messages/message-1/rating", json={"rating": None})

    assert response.status_code == 200
    assert message.rating is None
    assert response.json()["rating"] is None


@pytest.mark.parametrize(
    ("session", "message_id", "expected"),
    [
        # Somebody else's transcript. The message exists; the caller is not its
        # reader, and an id guessed into this URL must not write on it.
        (_session(owner="user-2"), "message-1", 404),
        # A message whose session has been deleted out from under it.
        (None, "message-1", 404),
        # No such message at all.
        (_session(), "message-404", 404),
    ],
)
def test_a_rating_reaches_only_the_transcript_its_author_owns(session, message_id, expected):
    message = _answer()
    db = _RatingDb(session, message)

    with _client(db, _user()) as client:
        response = client.patch(f"/messages/{message_id}/rating", json={"rating": "up"})

    assert response.status_code == expected
    assert message.rating is None
    assert db.commits == 0


def test_a_question_cannot_be_rated():
    """The buttons only exist under an answer, and the server says the same.

    A rating on one's own question is a number with nothing behind it — nobody
    could act on it, and a column that collects them is worth less than one
    that does not.
    """
    question = _answer(role=Role.user, content="질문입니다")
    db = _RatingDb(_session(), question)

    with _client(db, _user()) as client:
        response = client.patch("/messages/message-1/rating", json={"rating": "down"})

    assert response.status_code == 422
    assert question.rating is None
    assert db.commits == 0


def test_an_unrated_turn_says_nobody_said_rather_than_neither():
    """Null is the third state, and every message that exists today has it.

    `MessageOut` carries the field either way: a transcript that omitted it
    when unset would leave the client unable to tell "not rated" from "this
    build does not send ratings", and the thumb would draw itself from stale
    local state again.
    """
    fresh = MessageOut.of(_answer())
    rated = MessageOut.of(_answer(rating=MessageRating.up))

    assert fresh.rating is None
    assert "rating" in fresh.model_dump()
    assert rated.rating is MessageRating.up
