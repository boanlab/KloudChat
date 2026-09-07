"""Deleting a conversation deletes what it produced.

A conversation and its artifacts are one record: a report or deck does not outlive the
request that made it, and a share link to it stops working. Before, the artifacts were
detached and lingered on the artifacts screen with no conversation behind them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Delete, Select

from app.models.workspace import Artifact, ArtifactVersion
from app.routers import sessions


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Db:
    """Records the statements a route runs; the select answers with the given artifact ids."""

    def __init__(self, made: list[str]):
        self.made = made
        self.statements = []

    async def exec(self, statement):
        self.statements.append(statement)
        return _Rows(self.made) if isinstance(statement, Select) else _Rows([])


def _kind(statement) -> str:
    return "delete" if isinstance(statement, Delete) else "select"


def _table(statement) -> str:
    return statement.table.name if isinstance(statement, Delete) else statement.froms[0].name


@pytest.mark.asyncio
async def test_the_artifacts_of_the_conversations_go_versions_first():
    db = _Db(["a1", "a2"])
    assert await sessions._delete_artifacts_of(db, ["s1", "s2"]) == 2
    kinds = [(_kind(s), _table(s)) for s in db.statements]
    assert kinds == [
        ("select", Artifact.__tablename__),
        ("delete", ArtifactVersion.__tablename__),
        ("delete", Artifact.__tablename__),
    ]
    # The select is scoped to these conversations; the deletes to the artifacts it found.
    where = str(db.statements[0].whereclause)
    assert "session_id" in where
    assert "artifact_id" in str(db.statements[1].whereclause)
    assert "artifacts.id" in str(db.statements[2].whereclause)


@pytest.mark.asyncio
async def test_a_conversation_that_made_nothing_deletes_nothing():
    db = _Db([])
    assert await sessions._delete_artifacts_of(db, ["s1"]) == 0
    assert [_kind(s) for s in db.statements] == ["select"]
