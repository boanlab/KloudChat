"""A shared sheet shows its cells to the reader as a table and hands out the workbook, only through
the share link; nothing of the owner's other sheets or formulas' sources leaves by another route."""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, Share, ShareScope, ShareView, StoredFile
from app.routers import shares as shares_router
from app.services import sheet_build, sheet_doc


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all, tables=[
                metadata.tables[m.__tablename__] for m in (
                    User, ChatSession, Artifact, StoredFile, Share, ShareView)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/shared/x", "headers": [],
                    "client": ("127.0.0.1", 1234), "query_string": b""})


async def _shared(db, *, live=True):
    owner = User(email="owner@example.com", name="주인", password_hash="x")
    db.add(owner)
    await db.commit()
    title, data = sheet_build.build({"title": "월별 매출", "sheets": [{
        "name": "매출", "rows": [["월", "매출"], ["1월", 1200], ["2월", 1500],
                               ["합계", "=SUM(B2:B3)"]],
        "styles": [{"range": "B2:B4", "numFmt": "#,##0"}], "merges": []}]})
    artifact = await sheet_doc.create(db, user_id=owner.id, title=title, data=data)
    share = Share(token="tok-sheet", owner_id=owner.id, artifact_id=artifact.id,
                  scope=ShareScope.link)
    db.add(share)
    await db.commit()
    return artifact


async def test_a_shared_sheet_is_a_table_with_the_values_the_formulas_give(db):
    await _shared(db)
    payload = await shares_router.read_shared("tok-sheet", _request(), db)
    assert payload["artifactKind"] == "xlsx" and payload["data"]["sheets"] == ["매출"]
    sheet = await shares_router.read_shared_sheet("tok-sheet", _request(), db)
    cells = {(c["r"], c["c"]): c for c in sheet["sheets"][0]["cells"]}
    assert cells[(3, 1)]["v"] == 2700 and cells[(3, 1)]["s"]["numFmt"] == "#,##0"
    # the page draws values; no formula text rides along
    assert all("f" not in c for c in cells.values()) and sheet["truncated"] is False


async def test_the_shared_sheet_downloads_as_the_workbook_itself(db):
    await _shared(db)
    got = await shares_router.read_shared_document("tok-sheet", _request(), db)
    assert got.media_type == sheet_doc.XLSX_MIME
    assert "xlsx" in got.headers["content-disposition"]
    book = openpyxl.load_workbook(io.BytesIO(got.body))
    assert book["매출"]["B4"].value == "=SUM(B2:B3)"


async def test_a_revoked_share_shows_nothing_and_a_non_sheet_has_no_sheet_route(db):
    artifact = await _shared(db)
    share = (await db.exec(select(Share))).first()
    other = Artifact(user_id=artifact.user_id, kind=ArtifactKind.html, title="x",
                     data={"content": "<p>x</p>"})
    db.add(other)
    await db.commit()
    db.add(Share(token="tok-html", owner_id=artifact.user_id, artifact_id=other.id,
                 scope=ShareScope.link))
    await db.commit()
    with pytest.raises(HTTPException) as refused:
        await shares_router.read_shared_sheet("tok-html", _request(), db)
    assert refused.value.status_code == 404
    from app.models.user import utcnow

    share.revoked_at = utcnow()
    db.add(share)
    await db.commit()
    with pytest.raises(HTTPException) as gone:
        await shares_router.read_shared_sheet("tok-sheet", _request(), db)
    assert gone.value.status_code == 404
