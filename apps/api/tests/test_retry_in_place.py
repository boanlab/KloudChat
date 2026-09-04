"""`SendMessage.retry_of` reruns the latest failed question in place, reusing its row."""

from __future__ import annotations

import pytest
from test_starting_points import _Db, _patch_route_services, _request, _Result, _user

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage


class _Transcript(_Db):
    """A `_Db` that serves message rows and records deletes."""

    def __init__(self, session: ChatSession, messages: list[Message]):
        super().__init__(session)
        self.messages = messages
        self.deleted: list[object] = []

    async def get(self, model, row_id):
        if model is Message:
            return next((m for m in self.messages if m.id == row_id), None)
        return await super().get(model, row_id)

    async def exec(self, query):
        if query.get_final_froms()[0].name == "messages":
            return _Result(self.messages)
        return await super().exec(query)

    async def delete(self, row):
        self.deleted.append(row)


def _chat() -> ChatSession:
    return ChatSession(id="session-1", user_id="user-1", kind=SessionKind.chat)


def _question(msg_id: str, text: str, failure: TurnFailure | None = None) -> Message:
    return Message(id=msg_id, session_id="session-1", role=Role.user, content=text, failure=failure)


def _reply(msg_id: str, text: str, failure: TurnFailure | None = None) -> Message:
    return Message(
        id=msg_id, session_id="session-1", role=Role.assistant, content=text, failure=failure
    )


def _capture_chat_turn(monkeypatch) -> dict:
    captured: dict = {}
    _patch_route_services(monkeypatch, {"calls": 0})

    async def run_turn(**kwargs):
        captured.update(kwargs)
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(sessions_router, "_run_turn", run_turn)
    return captured


async def _send(db: _Transcript, payload: SendMessage) -> None:
    response = await sessions_router.send_message(
        db.session.id, payload, _request(f"/sessions/{db.session.id}/messages"), _user(), db
    )
    async for _chunk in response.body_iterator:
        pass


@pytest.mark.asyncio
async def test_a_retry_reuses_the_question_and_replaces_what_failed_under_it(monkeypatch):
    """A retry reuses the question row, deletes the failed reply, and sends the question once."""
    earlier_q, earlier_a = _question("q0", "첫 질문"), _reply("a0", "첫 답")
    failed_q = _question("q1", "로마 제국의 역사를 3000단어로")
    failed_a = _reply("a1", "", failure=TurnFailure.interrupted)
    db = _Transcript(_chat(), [earlier_q, earlier_a, failed_q, failed_a])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="로마 제국의 역사를 3000단어로", retry_of="q1"))

    written = [r for r in db.added if isinstance(r, Message) and r.role is Role.user]
    assert written == [failed_q]
    assert failed_q.failure is None
    assert captured["user_message_id"] == "q1"
    assert db.deleted == [failed_a]
    wire = [m for m in captured["messages"] if m["role"] in ("user", "assistant")]
    assert [m["content"] for m in wire] == ["첫 질문", "첫 답", "로마 제국의 역사를 3000단어로"]


@pytest.mark.asyncio
async def test_a_retry_uses_the_stored_words_not_the_echo(monkeypatch):
    """A retry runs the stored question text, not the client's echo."""
    failed_q = _question("q1", "저장된 질문", failure=TurnFailure.no_answer)
    db = _Transcript(_chat(), [failed_q])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="다른 문장", retry_of="q1"))

    assert captured["first_user_message"] == "저장된 질문"
    assert failed_q.content == "저장된 질문"
    assert failed_q.failure is None


@pytest.mark.asyncio
async def test_only_the_latest_question_can_be_rerun(monkeypatch):
    """A retry of anything but the latest question is refused with 409."""
    db = _Transcript(
        _chat(),
        [_question("q1", "먼저", failure=TurnFailure.no_answer), _question("q2", "나중에")],
    )
    _capture_chat_turn(monkeypatch)

    with pytest.raises(sessions_router.HTTPException) as refused:
        await _send(db, SendMessage(content="먼저", retry_of="q1"))
    assert refused.value.status_code == 409
    assert refused.value.detail == "retry_not_latest"
    assert db.deleted == []


@pytest.mark.asyncio
async def test_a_question_from_another_conversation_cannot_be_rerun_here(monkeypatch):
    other = Message(id="q9", session_id="session-9", role=Role.user, content="남의 질문")
    db = _Transcript(_chat(), [_question("q1", "내 질문")])
    db.messages.append(other)
    _capture_chat_turn(monkeypatch)

    with pytest.raises(sessions_router.HTTPException) as refused:
        await _send(db, SendMessage(content="남의 질문", retry_of="q9"))
    assert refused.value.status_code == 404


@pytest.mark.asyncio
async def test_a_plain_send_still_writes_a_new_question(monkeypatch):
    """A send without `retry_of` writes a new question row."""
    db = _Transcript(_chat(), [_question("q1", "먼저"), _reply("a1", "답")])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="다음 질문"))

    written = [r for r in db.added if isinstance(r, Message) and r.role is Role.user]
    assert [r.content for r in written] == ["다음 질문"]
    assert db.deleted == []
    assert captured["user_message_id"] == written[0].id
