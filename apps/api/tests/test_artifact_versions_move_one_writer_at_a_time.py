"""Report/deck/page versions: a save, a rewrite or a restore moves the version in one
conditional UPDATE, so of two writers that read the same version (two API replicas) exactly
one lands and the other is told to reload (409) instead of overwriting it.

Runs on SQLite; set KC_TEST_POSTGRES_URL (postgresql+asyncpg://…, a throwaway database that
`alembic upgrade head` has migrated) to run the same cases against Postgres as well."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import Artifact, ArtifactVersion
from app.routers import workspace

USER = User(id="race-user", email="race@example.test", password_hash="unused", name="R")
_PG = os.environ.get("KC_TEST_POSTGRES_URL")
BACKENDS = ["sqlite", *(["postgres"] if _PG else [])]


def _report(text: str) -> dict:
    return {
        "kind": "report",
        "sources": [],
        "sections": [{"id": "s1", "heading": "개요", "html": f"<p>{text}</p>"}],
    }


@pytest.fixture(params=BACKENDS)
async def stack(request, tmp_path):
    if request.param == "postgres":
        engine = create_async_engine(_PG)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            if await db.get(User, USER.id) is None:
                db.add(User(id=USER.id, email=USER.email, password_hash="unused", name="R"))
                await db.commit()
    else:
        # a file, not :memory:, so two sessions are two connections that really contend
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
        async with engine.begin() as connection:
            for statement in _DDL:
                await connection.exec_driver_sql(statement)

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: USER
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            made = await client.post(
                "/artifacts", json={"kind": "report", "title": "보고서", "data": _report("하나")}
            )
            assert made.status_code == 201, made.text
            yield client, local, made.json()["id"]
    finally:
        if request.param == "postgres":
            async with engine.begin() as connection:
                await connection.execute(delete(ArtifactVersion))
                await connection.execute(delete(Artifact))
        await engine.dispose()


async def _row(local, artifact_id: str) -> Artifact:
    async with local() as db:
        return await db.get(Artifact, artifact_id)


async def _versions(local, artifact_id: str) -> list[int]:
    async with local() as db:
        rows = await db.exec(
            select(ArtifactVersion.version).where(ArtifactVersion.artifact_id == artifact_id)
        )
        return sorted(rows.all())


@pytest.mark.asyncio
async def test_a_stale_save_and_a_stale_restore_are_refused(stack):
    client, local, artifact_id = stack
    saved = await client.patch(
        f"/artifacts/{artifact_id}", json={"data": _report("둘"), "expectedVersion": 1}
    )
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = await client.patch(
        f"/artifacts/{artifact_id}", json={"data": _report("셋"), "expectedVersion": 1}
    )
    assert stale.status_code == 409
    refused = await client.post(
        f"/artifacts/{artifact_id}/restore", json={"version": 1, "expectedVersion": 1}
    )
    assert refused.status_code == 409
    restored = await client.post(
        f"/artifacts/{artifact_id}/restore", json={"version": 1, "expectedVersion": 2}
    )
    assert restored.status_code == 200 and restored.json()["version"] == 3
    assert "하나" in restored.json()["data"]["sections"][0]["html"]
    assert await _versions(local, artifact_id) == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["patch", "restore"])
async def test_a_write_that_lands_after_the_read_wins_and_the_other_gets_409(
    stack, monkeypatch, route
):
    """Another replica saves between this request's read and its write: the Python check
    still sees the version it read, so only the conditional UPDATE can catch it."""
    client, local, artifact_id = stack
    await client.patch(f"/artifacts/{artifact_id}", json={"data": _report("둘")})  # v2
    own = workspace._own

    async def read_then_lose(db, model, field, user, item_id):
        row = await own(db, model, field, user, item_id)
        if model is Artifact:
            async with local() as other:
                await workspace._next_version(other, await other.get(Artifact, item_id), 2, "")
                (await other.get(Artifact, item_id)).data = _report("다른 곳")
                await other.commit()
        return row

    monkeypatch.setattr(workspace, "_own", read_then_lose)
    if route == "patch":
        lost = await client.patch(
            f"/artifacts/{artifact_id}", json={"data": _report("늦음"), "expectedVersion": 2}
        )
    else:
        lost = await client.post(
            f"/artifacts/{artifact_id}/restore", json={"version": 1, "expectedVersion": 2}
        )
    assert lost.status_code == 409, lost.text
    row = await _row(local, artifact_id)
    assert row.version == 3 and "다른 곳" in row.data["sections"][0]["html"]
    assert await _versions(local, artifact_id) == [1, 2]


@pytest.mark.asyncio
async def test_two_concurrent_writers_from_the_same_version_one_lands(stack):
    """Both transactions are open at once; the second UPDATE waits on the first's row lock,
    then matches nothing."""
    _, local, artifact_id = stack
    first, second = local(), local()
    try:
        a = await first.get(Artifact, artifact_id)
        b = await second.get(Artifact, artifact_id)
        assert a.version == b.version == 1
        await workspace._next_version(first, a, 1, "A")
        a.data = _report("A")
        first.add(a)
        racing = asyncio.create_task(workspace._next_version(second, b, 1, "B"))
        await asyncio.sleep(0.3)
        await first.commit()
        with pytest.raises(HTTPException) as lost:
            await racing
        assert lost.value.status_code == 409
    finally:
        await first.close()
        await second.close()
    row = await _row(local, artifact_id)
    assert row.version == 2 and "A" in row.data["sections"][0]["html"]
    assert await _versions(local, artifact_id) == [1]
