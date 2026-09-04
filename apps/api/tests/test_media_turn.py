"""How a picture or clip request is recorded as a turn: user prompt, wordless assistant reply."""

from __future__ import annotations

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions as sessions_router
from app.schemas.chat import MessageOut


class _Db:
    """Captures the rows the recorder adds."""

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
    # Verbatim; the title is only a truncation.
    assert asked[0].content == "학과 홍보 포스터"
    assert asked[0].failure is None


def test_the_reply_is_the_picture_and_says_nothing_else() -> None:
    db, session = _Db(), _session()
    _record(db, session, "학과 홍보 포스터", [_picture(1)], model="vendor/image", credits=4400)

    answered = db.rows(Role.assistant)
    assert len(answered) == 1
    assert answered[0].artifact_ids == ["artifact-1"]
    # No words may be attributed to a model that produced only a file.
    assert answered[0].content == ""


def test_a_batch_of_four_is_one_turn() -> None:
    """A batch of pictures is one user turn and one assistant turn."""
    db, session = _Db(), _session()
    made = [_picture(i) for i in range(1, 5)]
    _record(db, session, "네 가지 시안", made, model="vendor/image", credits=17_600)

    assert len(db.rows(Role.user)) == 1
    answered = db.rows(Role.assistant)
    assert len(answered) == 1
    assert answered[0].artifact_ids == ["artifact-1", "artifact-2", "artifact-3", "artifact-4"]
    # The session points at the newest artifact.
    assert session.artifact_id == "artifact-4"


def test_what_the_turn_cost_travels_with_it() -> None:
    """Credits and model are stored on the assistant turn."""
    db, session = _Db(), _session()
    _record(db, session, "포스터", [_picture(1)], model="vendor/image", credits=4400)

    answered = db.rows(Role.assistant)[0]
    assert answered.usage == {"credits": 4400}
    assert answered.model == "vendor/image"


def test_a_request_that_produced_nothing_marks_the_prompt() -> None:
    db, session = _Db(), _session()
    _record(db, session, "만들어지지 않을 그림", [], failed=True)

    assert db.rows(Role.user)[0].failure is TurnFailure.no_answer
    # No assistant row: an empty one would render as a turn still waiting.
    assert db.rows(Role.assistant) == []
    assert session.title == "만들어지지 않을 그림"
    assert session.artifact_id is None


def test_a_batch_that_broke_halfway_keeps_what_arrived_and_says_so() -> None:
    """A partial batch keeps its artifacts and is marked interrupted on the reply only."""
    db, session = _Db(), _session()
    _record(db, session, "네 가지 시안", [_picture(1), _picture(2)], credits=8800, failed=True)

    asked, answered = db.rows(Role.user)[0], db.rows(Role.assistant)[0]
    assert answered.artifact_ids == ["artifact-1", "artifact-2"]
    assert answered.failure is TurnFailure.interrupted
    assert asked.failure is None


def test_the_name_is_still_the_prompt_and_still_only_the_first_one() -> None:
    db, session = _Db(), _session()
    _record(db, session, "  포스터\n  가로로  ", [_picture(1)])
    assert session.title == "포스터 가로로"

    _record(db, session, "이번엔 세로로", [_picture(2)])
    assert session.title == "포스터 가로로"
    assert len(db.rows(Role.user)) == 2


def test_a_title_somebody_chose_is_never_overwritten() -> None:
    db, session = _Db(), _session(title="졸업 전시 자료")
    _record(db, session, "포스터", [_picture(1)])
    assert session.title == "졸업 전시 자료"


def test_the_ids_come_back_out_with_the_transcript() -> None:
    """MessageOut carries artifact ids under the `artifactIds` alias."""
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
