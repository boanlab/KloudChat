"""A shared conversation names its agent, project and 서식, and never sends their bodies."""

from __future__ import annotations

import json

import pytest
from fastapi import Request
from fastapi.encoders import jsonable_encoder

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.workspace import Agent, Artifact, Project, Share, ShareScope
from app.routers import shares as shares_router
from app.services import design_templates

#: A shape that ships in the image.
_TEMPLATE_ID = "doc-brief"


@pytest.mark.asyncio
async def test_a_shared_conversation_names_what_shaped_it():
    """The payload names agent, project and 서식."""
    db = _Db(
        session=_session(),
        agent=_agent(),
        project=_project(),
        messages=[_asked(), _answered()],
    )

    payload = await shares_router.read_shared("token-1", _request(), db)

    shape = design_templates.get(_TEMPLATE_ID)
    assert payload["startedWith"] == {
        "agent": "감사 담당",
        "project": "📁 2분기 감사",
        "format": {"name": shape.name, "nameEn": shape.name_en},
    }


@pytest.mark.asyncio
async def test_the_public_page_never_sees_a_system_prompt_or_a_projects_instructions():
    """No system prompt or project instructions anywhere in the payload."""
    db = _Db(session=_session(), agent=_agent(), project=_project(), messages=[_asked()])

    payload = await shares_router.read_shared("token-1", _request(), db)
    wire = json.dumps(jsonable_encoder(payload), ensure_ascii=False)

    assert "감사 담당" in wire and "2분기 감사" in wire
    assert "내부 감사 절차대로" not in wire
    assert "회계 팀에만 공유" not in wire


@pytest.mark.asyncio
async def test_the_turn_carries_the_starting_point_it_began_from():
    """The turn carries the starting point it began from."""
    db = _Db(session=_session(), messages=[_asked()])

    payload = await shares_router.read_shared("token-1", _request(), db)

    assert payload["messages"][0].started_from == {"templateId": "t_incident", "title": "장애 보고"}


@pytest.mark.asyncio
async def test_a_conversation_that_started_with_nothing_claims_nothing():
    """A conversation with no agent, project or 서식 reports none."""
    session = _session()
    session.agent_id = None
    session.project_id = None
    session.render_template_id = None
    db = _Db(session=session, messages=[_asked()])

    payload = await shares_router.read_shared("token-1", _request(), db)

    assert payload["startedWith"] == {"agent": None, "project": None, "format": None}


@pytest.mark.asyncio
async def test_a_project_belonging_to_somebody_else_is_not_named():
    """A `project_id` outside the owner's workspace is not named."""
    stranger = _project()
    stranger.user_id = "user-2"
    db = _Db(session=_session(), project=stranger, messages=[_asked()])

    payload = await shares_router.read_shared("token-1", _request(), db)

    assert payload["startedWith"]["project"] is None


@pytest.mark.asyncio
async def test_a_shape_an_upgrade_removed_degrades_to_no_format():
    """A `render_template_id` no longer in the catalogue degrades to no format."""
    session = _session()
    session.render_template_id = "doc-withdrawn"
    db = _Db(session=session, messages=[_asked()])

    payload = await shares_router.read_shared("token-1", _request(), db)

    assert payload["startedWith"]["format"] is None


# ── fakes ──────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Enough of the session to serve one public read."""

    def __init__(
        self,
        *,
        session: ChatSession,
        messages: list[Message],
        agent: Agent | None = None,
        project: Project | None = None,
        artifact: Artifact | None = None,
    ):
        self.session = session
        self.messages = messages
        self.agent = agent
        self.project = project
        self.artifact = artifact
        self.share = Share(
            id="share-1",
            token="token-1",
            owner_id=session.user_id,
            session_id=session.id,
            scope=ShareScope.link,
        )
        self.commits = 0

    async def get(self, model, row_id):
        rows = {
            ChatSession: self.session,
            Agent: self.agent,
            Project: self.project,
            Artifact: self.artifact,
        }
        row = rows.get(model)
        return row if row is not None and row.id == row_id else None

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        if table == "shares":
            return _Result([self.share])
        if table == "messages":
            return _Result(self.messages)
        if table == "share_views":
            # Every read is a first visit; the visit log has its own tests.
            return _Result([])
        raise AssertionError(f"unexpected query: {query}")

    def add(self, _row):
        pass

    async def commit(self):
        self.commits += 1


def _session() -> ChatSession:
    return ChatSession(
        id="session-1",
        user_id="user-1",
        kind=SessionKind.report,
        title="2분기 감사 요약",
        agent_id="agent-1",
        project_id="project-1",
        render_template_id=_TEMPLATE_ID,
    )


def _agent() -> Agent:
    return Agent(
        id="agent-1",
        owner_id="user-1",
        name="감사 담당",
        description="감사 보고서를 쓴다",
        system_prompt="내부 감사 절차대로 쓰고, 확인되지 않은 수치는 쓰지 않는다.",
    )


def _project() -> Project:
    return Project(
        id="project-1",
        user_id="user-1",
        name="2분기 감사",
        emoji="📁",
        instructions="회계 팀에만 공유한다. 미확정 수치는 괄호로 표시한다.",
    )


def _asked() -> Message:
    return Message(
        id="message-1",
        session_id="session-1",
        role=Role.user,
        content="어제 새벽 장애 정리해 줘",
        started_from={"templateId": "t_incident", "title": "장애 보고"},
    )


def _answered() -> Message:
    return Message(id="message-2", session_id="session-1", role=Role.assistant, content="정리했다.")


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/shared/token-1",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )
