"""An agent's @handle is the one typed, and no other agent of the owner's.

The slug field in the form was not sent: `AgentIn` had no such field, so the
server always derived the handle from the name, and nothing — column, route,
or form — checked it against the rows already there. Four agents named
회의록 정리 사본 were four @회의록-정리-사본.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.models.user import User
from app.models.workspace import Agent
from app.routers import workspace as workspace_router
from app.schemas.workspace import AgentIn


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """The owner's agents on the table, and nothing else to find."""

    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents
        self.added: list[object] = []
        self.commits = 0

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        if table == "agents":
            return _Result(self.agents)
        return _Result([])

    async def get(self, model, row_id):
        if model is Agent:
            return next((a for a in self.agents if a.id == row_id), None)
        return None

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _row):
        return None


def _user() -> User:
    return User(id="user-1", email="u@example.test", password_hash="h", name="U")


def _agent(agent_id: str, name: str, slug: str) -> Agent:
    return Agent(id=agent_id, owner_id="user-1", name=name, slug=slug)


@pytest.mark.asyncio
async def test_the_typed_handle_is_the_one_stored() -> None:
    db = _Db([])
    out = await workspace_router.create_agent(
        AgentIn(name="논문 검토 도우미", slug="Paper Reviewer"), _user(), db
    )
    assert out.slug == "paper-reviewer"
    assert db.added[0].slug == "paper-reviewer"


@pytest.mark.asyncio
async def test_a_blank_handle_still_comes_from_the_name() -> None:
    db = _Db([])
    out = await workspace_router.create_agent(AgentIn(name="회의록 정리"), _user(), db)
    assert out.slug == "회의록-정리"


@pytest.mark.asyncio
async def test_a_handle_another_agent_holds_is_refused_before_the_write() -> None:
    db = _Db([_agent("a1", "회의록 정리", "회의록-정리")])
    with pytest.raises(workspace_router.HTTPException) as refused:
        await workspace_router.create_agent(AgentIn(name="회의록 정리"), _user(), db)
    assert refused.value.status_code == 409
    assert refused.value.detail == "slug_taken"
    assert db.added == []


@pytest.mark.asyncio
async def test_an_agent_may_keep_its_own_handle_on_edit() -> None:
    mine = _agent("a1", "회의록 정리", "회의록-정리")
    db = _Db([mine, _agent("a2", "다른 것", "다른-것")])
    out = await workspace_router.patch_agent(
        "a1", AgentIn(name="회의록 정리", slug="회의록-정리", description="바뀜"), _user(), db
    )
    assert out.slug == "회의록-정리"
    assert mine.description == "바뀜"


@pytest.mark.asyncio
async def test_an_edit_cannot_take_a_handle_another_agent_holds() -> None:
    db = _Db([_agent("a1", "하나", "하나"), _agent("a2", "둘", "둘")])
    with pytest.raises(workspace_router.HTTPException) as refused:
        await workspace_router.patch_agent("a1", AgentIn(name="하나", slug="둘"), _user(), db)
    assert refused.value.status_code == 409


# ── the migration's dedupe, without a database ─────────────────────────


def _migration():
    path = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0038_agent_slug_unique.py"
    )
    spec = importlib.util.spec_from_file_location("m0038", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_existing_duplicates_keep_the_oldest_and_suffix_the_rest() -> None:
    resolve = _migration().resolve_slugs
    rows = [
        ("a1", "u1", "회의록 정리 사본", "회의록-정리-사본"),
        ("a2", "u1", "회의록 정리 사본", "회의록-정리-사본"),
        ("a3", "u1", "회의록 정리 사본", "회의록-정리-사본"),
        ("b1", "u2", "회의록 정리 사본", "회의록-정리-사본"),  # another owner: untouched
        ("a4", "u1", "이름만 있음", ""),  # never got a slug: derived, not left empty
    ]
    assert resolve(rows) == [
        ("a2", "회의록-정리-사본-2"),
        ("a3", "회의록-정리-사본-3"),
        ("a4", "이름만-있음"),
    ]


def test_a_suffix_never_pushes_a_handle_past_the_column_limit() -> None:
    resolve = _migration().resolve_slugs
    long = "x" * 60
    changed = resolve([("a1", "u1", long, long), ("a2", "u1", long, long)])
    assert changed == [("a2", "x" * 58 + "-2")]
