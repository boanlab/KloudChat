"""HWP, Word and PowerPoint documents share one set of routes (`office_docs.router_for`): the
editor saves from the version it loaded, a save from an older one is refused, a file that is
not the format is refused, and the first version stays to load and restore. Each format's own
behaviour (uploads, the agent's tools) is in its own `*_document_versions_*` file."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import ArtifactVersion, StoredFile
from app.routers import docx as docx_router
from app.routers import hwp as hwp_router
from app.routers import pptx as pptx_router
from app.routers import workspace
from app.services import docx_doc, docx_edit, hwp_doc, hwpx_edit, pptx_doc, pptx_edit

USER = User(id="office-user", email="office@example.test", password_hash="unused", name="O")


@dataclass(frozen=True)
class Format:
    slug: str
    stored: str  # `data.format` of the artifact
    mime: str
    edit: ModuleType
    block: str  # the first block a blank document has
    blank: str  # how the outline names that block when it is empty
    other: str  # another format's route, which must not serve this one


FORMATS = [
    Format("hwp", "hwpx", hwp_doc.HWPX_MIME, hwpx_edit, "p1", "p1:", "docx"),
    Format("docx", "docx", docx_doc.DOCX_MIME, docx_edit, "p1", "p1: (빈 줄)", "hwp"),
    Format(
        "pptx", "pptx", pptx_doc.PPTX_MIME, pptx_edit, "s1.1", "s1.1 (제목): (비어 있음)", "docx",
    ),
]


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(lambda sync: StoredFile.__table__.create(sync))

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    # the deck's project look has its own tests; these tables hold no people or projects
    monkeypatch.delattr(pptx_doc, "project_look")
    app = FastAPI()
    for router in (hwp_router, docx_router, pptx_router, workspace):
        app.include_router(router.router)

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


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", FORMATS, ids=lambda fmt: fmt.slug)
async def test_the_editor_saves_a_new_version_and_a_stale_save_is_refused(stack, fmt):
    client, local = stack
    made = await client.post(f"/artifacts/{fmt.slug}", json={"title": "공문"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["version"], body["data"]["format"]) == (fmt.slug, 1, fmt.stored)
    url = f"/artifacts/{body['id']}/{fmt.slug}"
    first = await client.get(url)
    assert first.status_code == 200 and first.headers["x-artifact-version"] == "1"
    assert first.headers["content-type"].startswith(fmt.mime)
    assert fmt.blank in fmt.edit.outline(first.content)

    edited = fmt.edit.apply(first.content, [{"op": "replace_text", "id": fmt.block,
                                             "text": "손으로 쓴 줄"}]).data
    saved = await client.put(f"{url}?expectedVersion=1", content=edited)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and "손으로 쓴 줄" in saved.json()["data"]["text"]

    stale = await client.put(f"{url}?expectedVersion=1", content=edited)
    assert stale.status_code == 409
    junk = await client.put(f"{url}?expectedVersion=2", content=b"nope")
    assert junk.status_code == 422 and junk.json()["detail"] == f"not_a_{fmt.stored}_file"

    # the first version is still there to load and restore
    old = await client.get(f"{url}?version=1")
    assert old.status_code == 200 and old.content == first.content
    async with local() as db:
        rows = (await db.exec(select(ArtifactVersion))).all()
    assert [r.version for r in rows] == [1]
    restored = await client.post(f"/artifacts/{body['id']}/restore", json={"version": 1})
    assert restored.status_code == 200 and restored.json()["version"] == 3
    again = await client.get(url)
    assert again.content == first.content
    # another format's route does not serve it
    assert (await client.get(f"/artifacts/{body['id']}/{fmt.other}")).status_code == 404
