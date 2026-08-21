"""A picture and a clip as turns in the conversation that asked for them.

These surfaces used to write nothing at all: the prompt became the session's
name and the artifact was hung on the session, and the conversation itself
stayed empty. Opened, it was a blank screen with a panel beside it — the one
place in the product where what somebody typed did not appear where they typed
it, and where a result they had paid for was not in the record of asking for it.

The reply is still not a sentence. What these hold is the shape that lets both
halves be stored anyway:

1. The prompt is an ordinary user message.
2. The answer is an assistant message with no words in it, carrying the ids of
   what was made. Nothing may be written there *about* the picture — that would
   be the model quoted saying something no model said.
3. A request that came back with nothing marks the prompt and leaves no reply,
   which is what a chat turn does when it dies before its first word.
4. A batch that broke halfway keeps what arrived and says it is less than what
   was asked for.
"""

from __future__ import annotations

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions as sessions_router
from app.schemas.chat import MessageOut


class _Db:
    """Just enough of a session to catch what the recorder writes."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, row: object) -> None:
        self.added.append(row)

    def rows(self, role: Role) -> list[Message]:
        return [r for r in self.added if isinstance(r, Message) and r.role is role]


def _session(**over) -> ChatSession:
    fields = {"id": "session-1", "user_id": "user-1", "kind": SessionKind.image, **over}
    return ChatSession(**fields)


def _picture(index: int) -> Artifact:
    return Artifact(
        id=f"artifact-{index}",
        user_id="user-1",
        session_id="session-1",
        kind=ArtifactKind.image,
        title="포스터",
        data={"kind": "image", "aspect": "1:1"},
    )


def _record(db: _Db, session: ChatSession, prompt: str, made: list[Artifact], **kwargs) -> None:
    sessions_router._record_media(db, session, prompt, made, **kwargs)


def test_the_prompt_is_stored_as_the_person_s_own_message() -> None:
    db, session = _Db(), _session()
    _record(db, session, "학과 홍보 포스터", [_picture(1)], model="vendor/image", credits=4400)

    asked = db.rows(Role.user)
    assert len(asked) == 1
    # Word for word. The title is a truncation of the same sentence and the
    # transcript is where the whole of it belongs.
    assert asked[0].content == "학과 홍보 포스터"
    assert asked[0].failure is None


def test_the_reply_is_the_picture_and_says_nothing_else() -> None:
    db, session = _Db(), _session()
    _record(db, session, "학과 홍보 포스터", [_picture(1)], model="vendor/image", credits=4400)

    answered = db.rows(Role.assistant)
    assert len(answered) == 1
    assert answered[0].artifact_ids == ["artifact-1"]
    # An empty body is the point of the whole exercise. A sentence here would
    # be the product speaking on behalf of a model that produced a file and no
    # words at all.
    assert answered[0].content == ""


def test_a_batch_of_four_is_one_turn() -> None:
    """Four pictures answer one request. Four turns would be four requests, and
    the person made one."""
    db, session = _Db(), _session()
    made = [_picture(i) for i in range(1, 5)]
    _record(db, session, "네 가지 시안", made, model="vendor/image", credits=17_600)

    assert len(db.rows(Role.user)) == 1
    answered = db.rows(Role.assistant)
    assert len(answered) == 1
    assert answered[0].artifact_ids == ["artifact-1", "artifact-2", "artifact-3", "artifact-4"]
    # And the session still points at one thing: the panel and 원본 작업 열기
    # open the newest, while the transcript keeps every batch under its prompt.
    assert session.artifact_id == "artifact-4"


def test_what_the_turn_cost_travels_with_it() -> None:
    """The job card carried this figure and then disappeared. On a shared
    allowance where a clip is twelve thousand credits, what it cost is not a
    detail somebody goes looking for afterwards."""
    db, session = _Db(), _session()
    _record(db, session, "포스터", [_picture(1)], model="vendor/image", credits=4400)

    answered = db.rows(Role.assistant)[0]
    assert answered.usage == {"credits": 4400}
    assert answered.model == "vendor/image"


def test_a_request_that_produced_nothing_marks_the_prompt() -> None:
    db, session = _Db(), _session()
    _record(db, session, "만들어지지 않을 그림", [], failed=True)

    assert db.rows(Role.user)[0].failure is TurnFailure.no_answer
    # Nothing spoke, so nothing may be stored as having spoken — and an empty
    # assistant row with no artifacts under it would render as a turn still
    # waiting for a picture that is never coming.
    assert db.rows(Role.assistant) == []
    # The name still stands: an attempt is a record of what was asked for.
    assert session.title == "만들어지지 않을 그림"
    # But the session must not point at anything, because nothing exists.
    assert session.artifact_id is None


def test_a_batch_that_broke_halfway_keeps_what_arrived_and_says_so() -> None:
    """Two of four. Each picture is a separate call and a separate charge, so
    the two that came back are real and paid for; presenting them as the answer
    to a request for four would be the quieter version of the same lie a
    half-written chat answer tells when it looks whole."""
    db, session = _Db(), _session()
    _record(db, session, "네 가지 시안", [_picture(1), _picture(2)], credits=8800, failed=True)

    asked, answered = db.rows(Role.user)[0], db.rows(Role.assistant)[0]
    assert answered.artifact_ids == ["artifact-1", "artifact-2"]
    assert answered.failure is TurnFailure.interrupted
    # The question was answered, as far as it goes. Marking it too would put
    # the notice in two places, under the wrong one of them.
    assert asked.failure is None


def test_the_name_is_still_the_prompt_and_still_only_the_first_one() -> None:
    db, session = _Db(), _session()
    _record(db, session, "  포스터\n  가로로  ", [_picture(1)])
    assert session.title == "포스터 가로로"

    _record(db, session, "이번엔 세로로", [_picture(2)])
    # A second batch in the same session is more of the same work, not a new
    # subject. Both prompts are in the transcript, which is where the second
    # one is now legible instead.
    assert session.title == "포스터 가로로"
    assert len(db.rows(Role.user)) == 2


def test_a_title_somebody_chose_is_never_overwritten() -> None:
    db, session = _Db(), _session(title="졸업 전시 자료")
    _record(db, session, "포스터", [_picture(1)])
    assert session.title == "졸업 전시 자료"


def test_the_ids_come_back_out_with_the_transcript() -> None:
    """Otherwise the reader gets the prompt and an empty bubble. The browser
    renders the artifacts where the answer goes, and it can only do that for
    ids the transcript hands it."""
    out = MessageOut.of(
        Message(
            session_id="session-1",
            role=Role.assistant,
            content="",
            artifact_ids=["artifact-1", "artifact-2"],
        )
    )
    assert out.artifact_ids == ["artifact-1", "artifact-2"]
    assert out.model_dump(by_alias=True)["artifactIds"] == ["artifact-1", "artifact-2"]
