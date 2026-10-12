"""Moving a result into a project checks the project is the caller's own."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion, Project
from app.routers import workspace


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    """SQLite has no JSONB; the schema only needs to be creatable in memory."""
    return "JSON"


@pytest.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [m.__table__ for m in (Project, Artifact, ArtifactVersion)]
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))
    me = User(id="me", email="me@example.test", password_hash="unused", name="Me")
    other = User(id="other", email="other@example.test", password_hash="unused", name="Other")
    async with AsyncSession(engine, expire_on_commit=False) as db:
        mine = Project(user_id=me.id, name="내 프로젝트")
        theirs = Project(user_id=other.id, name="남의 프로젝트")
        artifact = Artifact(
            user_id=me.id, kind=ArtifactKind.report, title="보고서", data={"sections": []}
        )
        db.add_all([mine, theirs, artifact])
        await db.commit()
        ids = {"mine": mine.id, "theirs": theirs.id, "artifact": artifact.id}
    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: me
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client, ids, engine


async def test_someone_elses_project_is_refused_and_nothing_moves(setup):
    client, ids, engine = setup
    refused = await client.patch(f"/artifacts/{ids['artifact']}", json={"projectId": ids["theirs"]})
    assert refused.status_code == 404
    async with AsyncSession(engine) as db:
        assert (await db.get(Artifact, ids["artifact"])).project_id is None


async def test_my_own_project_is_accepted(setup):
    client, ids, _ = setup
    moved = await client.patch(f"/artifacts/{ids['artifact']}", json={"projectId": ids["mine"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["projectId"] == ids["mine"]
