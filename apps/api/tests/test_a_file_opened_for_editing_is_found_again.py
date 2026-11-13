"""A file-box file opened in an office editor becomes a copy (the file stays as it was); the
copy remembers its file through every version, so the file box can offer to reopen it."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import StoredFile
from app.routers import hwp as hwp_router
from app.routers import workspace
from app.services import files as file_service
from app.services import hwpx_edit

USER = User(id="box-user", email="box@example.test", password_hash="unused", name="B")


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[StoredFile.__table__]))

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(hwp_router.router)
    app.include_router(workspace.router)

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: USER
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client, local
    finally:
        await engine.dispose()


async def _upload(client, local, user_id: str = USER.id) -> tuple[str, bytes]:
    """A `.hwpx` in the file box, as an upload leaves it."""
    blank = await client.post("/artifacts/hwp", json={"title": "빈 문서"})
    data = (await client.get(f"/artifacts/{blank.json()['id']}/hwp")).content
    async with local() as db:
        row = StoredFile(user_id=user_id, name="연수 공문.hwpx", size=len(data),
                         mime="application/hwp+zip")
        row.storage_key = file_service.write_blob(user_id, row.id, row.name, data)
        db.add(row)
        await db.commit()
        return row.id, data


@pytest.mark.asyncio
async def test_the_copy_keeps_its_file_through_saves_and_restores(stack):
    client, local = stack
    file_id, original = await _upload(client, local)
    assert (await client.get(f"/files/{file_id}/documents")).json() == []

    made = await client.post("/artifacts/hwp", json={"fileId": file_id})
    assert made.status_code == 201, made.text
    copy = made.json()
    assert copy["title"] == "연수 공문"
    assert copy["data"]["sourceFileId"] == file_id

    edited = hwpx_edit.apply(original, [{"op": "replace_text", "id": "p1",
                                         "text": "고친 줄"}]).data
    saved = await client.put(f"/artifacts/{copy['id']}/hwp?expectedVersion=1", content=edited)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["sourceFileId"] == file_id
    restored = await client.post(f"/artifacts/{copy['id']}/restore", json={"version": 1})
    assert restored.json()["data"]["sourceFileId"] == file_id

    found = (await client.get(f"/files/{file_id}/documents")).json()
    assert [row["id"] for row in found] == [copy["id"]]
    # the file itself was never written
    async with local() as db:
        row = await db.get(StoredFile, file_id)
        assert file_service.read_blob(row.storage_key) == original


@pytest.mark.asyncio
async def test_documents_of_other_files_and_people_stay_out(stack):
    client, local = stack
    file_id, _ = await _upload(client, local)
    other_id, _ = await _upload(client, local)
    await client.post("/artifacts/hwp", json={"fileId": other_id})
    await client.post("/artifacts/hwp", json={"title": "빈 문서"})
    assert (await client.get(f"/files/{file_id}/documents")).json() == []

    theirs, _ = await _upload(client, local, user_id="someone-else")
    assert (await client.get(f"/files/{theirs}/documents")).status_code == 404
