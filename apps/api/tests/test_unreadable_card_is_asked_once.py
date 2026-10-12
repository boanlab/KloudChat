"""A reply to the 「읽지 못한 파일」 card goes on with what was read; it is not asked again."""

from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import grounding
from app.services.workspace_context import ContextFile, WorkspaceContext

_REQUEST = "연구실 주간 미팅 녹음 파일이야. 회의록으로 정리해 줘."
_FILES = (ContextFile("녹음.mp3", "unreadable", 0, 0),)


class _Reached(Exception):
    """The turn got past the attachment check to the writer's route."""


def _card() -> dict:
    return {
        "stage": "clarify",
        "request": _REQUEST,
        "attachments": ["f1"],
        "questions": [q.wire() for q in grounding.questions_for(list(_FILES))],
        "answers": {},
    }


async def _send(monkeypatch, *, pending, content, answers=None, history=()):
    user = User(email="card@example.test", name="Learner", password_hash="hash")
    model = {**_external_model("synthetic/writer"), "kinds": ["chat", "report"]}
    session = ChatSession(
        user_id=user.id, kind=SessionKind.report, model=model["id"], pending=pending
    )
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    workspace = WorkspaceContext(blocks=(), applied_skills=(), attachments=_FILES)
    seen: dict = {}

    async def assemble(*_args, **_kwargs):
        return workspace

    async def owned(*_args, **_kwargs):
        return [], None

    async def stored_history(*_args, **_kwargs):
        return list(history)

    async def route(**kwargs):
        seen["request"] = kwargs["request"]
        raise _Reached

    class Db(_NoWriteDb):
        async def get(self, *_args):
            return None

    monkeypatch.setattr(sessions, "assemble", assemble)
    monkeypatch.setattr(sessions, "_owned_attachments", owned)
    monkeypatch.setattr(sessions, "_history", stored_history)
    monkeypatch.setattr(sessions, "_document_route", route)
    db = Db()
    payload = SendMessage(content=content, answers=answers)
    try:
        response = await sessions.send_message(session.id, payload, _request(), user, db)
    except _Reached:
        return None, seen, session, db
    return response, seen, session, db


async def test_the_first_request_puts_the_card_up(monkeypatch):
    response, _seen, session, db = await _send(monkeypatch, pending=None, content=_REQUEST)

    assert response is not None and session.pending["stage"] == "clarify"
    asked = [m for m in db.added if isinstance(m, Message) and m.role is Role.assistant]
    assert asked[0].routing["askedCard"]["ids"] == ["unreadable"]


async def test_a_typed_reply_goes_on_with_what_was_read(monkeypatch):
    response, seen, _session, _db = await _send(
        monkeypatch, pending=_card(), content="더 있는 정보는 없어. 자료대로 진행해 줘."
    )

    assert response is None, "같은 질문을 다시 물었습니다"
    request = seen["request"]
    assert request.count("덧붙인 조건:") == 1
    assert request.count("자료대로 진행해 줘") == 1
    # The writer is told which file did not make it, by name.
    assert "녹음.mp3" in request


async def test_pressing_proceed_goes_on_too(monkeypatch):
    response, seen, _session, _db = await _send(
        monkeypatch, pending=_card(), content="있는 자료로 진행해 주세요", answers={}
    )

    assert response is None
    assert "녹음.mp3" in seen["request"]


async def test_the_same_card_is_not_put_up_twice_in_a_row(monkeypatch):
    """Even with the pending turn gone, the card that was just shown is not shown again."""
    asked = Message(
        session_id="s",
        role=Role.assistant,
        content="시작하기 전에 확인할 것이 있습니다.",
        routing={"askedCard": {"ids": ["unreadable"], "attachments": []}},
    )
    response, _seen, _session, _db = await _send(
        monkeypatch, pending=None, content=_REQUEST, history=[asked]
    )

    assert response is None
