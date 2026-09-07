"""What `search_knowledge` can look through in a plain conversation."""

from __future__ import annotations

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import Agent, StoredFile
from app.routers import sessions as sessions_router


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Db:
    """Returns every file row; the helper is expected to keep only its own."""

    def __init__(self, files: list[StoredFile]):
        self.files = files

    async def exec(self, query):
        assert query.get_final_froms()[0].name == "files"
        return _Result(self.files)


def _user() -> User:
    return User(id="user-1", email="person@example.test", password_hash="hash", name="Person")


def _file(name: str, **fields) -> StoredFile:
    return StoredFile(id=f"file-{name}", user_id="user-1", name=name, text=f"{name} 본문", **fields)


async def test_a_conversation_without_an_agent_searches_its_own_uploads():
    """Uploads into this conversation are on the shelf; a project's and another chat's are not."""
    session = ChatSession(id="session-1", user_id="user-1", kind=SessionKind.chat, index_key="k1")
    db = _Db(
        [
            _file("학칙.pdf", session_id="session-1"),
            _file("메모.txt", session_id="session-1"),
            _file("다른대화.pdf", session_id="session-2"),
            _file("프로젝트.md", session_id="session-1", project_id="project-1"),
        ]
    )
    unreadable = _file("사진.png", session_id="session-1")
    unreadable.text = ""
    db.files.append(unreadable)

    shelf, key = await sessions_router._knowledge_shelf(db, _user(), session, None)

    assert [name for name, _, _ in shelf] == ["학칙.pdf", "메모.txt"]
    assert key == "k1"


async def test_an_agent_conversation_keeps_the_agents_collection():
    """Agent knowledge and this chat's uploads share the shelf; vector search stays the agent's."""
    agent = Agent(id="agent-1", owner_id="user-1", name="비서", index_key="agent-key")
    session = ChatSession(
        id="session-1", user_id="user-1", kind=SessionKind.chat, agent_id="agent-1", index_key="k1"
    )
    db = _Db(
        [
            _file("지침.md", agent_id="agent-1"),
            _file("학칙.pdf", session_id="session-1"),
            _file("남의에이전트.md", agent_id="agent-2"),
        ]
    )

    shelf, key = await sessions_router._knowledge_shelf(db, _user(), session, agent)

    assert [name for name, _, _ in shelf] == ["지침.md", "학칙.pdf"]
    assert key == "agent-key"


async def test_a_conversation_that_never_indexed_anything_has_no_collection():
    session = ChatSession(id="session-1", user_id="user-1", kind=SessionKind.chat)
    db = _Db([_file("학칙.pdf", session_id="session-1")])
    shelf, key = await sessions_router._knowledge_shelf(db, _user(), session, None)
    assert [name for name, _, _ in shelf] == ["학칙.pdf"]
    assert key == ""
