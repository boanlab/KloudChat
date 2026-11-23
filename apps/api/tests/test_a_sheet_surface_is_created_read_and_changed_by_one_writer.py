"""The sheet surface: a blank workbook is made from a request, the grid's model is read, changed
cells land as the next version, a stale writer gets a 409, and the download is the workbook."""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import SessionKind
from app.models.user import User
from app.routers import sheet as sheet_router
from app.routers import workspace
from app.services import settings_store

USER = User(id="sheet-user", email="sheet@example.test", password_hash="unused", name="S")


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    app = FastAPI()
    app.include_router(sheet_router.router)
    app.include_router(workspace.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: USER
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
            yield c
    finally:
        await engine.dispose()


def _cell(model: dict, r: int, c: int) -> dict | None:
    return next((x for x in model["sheets"][0]["cells"] if x["r"] == r and x["c"] == c), None)


@pytest.mark.asyncio
async def test_a_blank_sheet_is_made_read_changed_and_downloaded(client):
    made = await client.post("/artifacts/xlsx", json={})
    assert made.status_code == 201
    artifact = made.json()
    assert artifact["kind"] == "xlsx" and artifact["version"] == 1
    sheet_id = artifact["id"]

    model = (await client.get(f"/artifacts/{sheet_id}/xlsx/model")).json()
    assert model["version"] == 1 and model["sheets"][0]["name"] == "시트1"

    ops = [{"sheet": "시트1", "r": 0, "c": 0, "v": "합계"}, {"sheet": "시트1", "r": 0, "c": 1,
                                                          "v": 7}]
    changed = await client.patch(
        f"/artifacts/{sheet_id}/xlsx/cells", json={"expectedVersion": 1, "ops": ops})
    assert changed.status_code == 200 and changed.json()["version"] == 2

    model = (await client.get(f"/artifacts/{sheet_id}/xlsx/model")).json()
    assert model["version"] == 2
    assert _cell(model, 0, 0)["v"] == "합계" and _cell(model, 0, 1)["v"] == 7

    download = await client.get(f"/artifacts/{sheet_id}/xlsx")
    assert download.headers["x-artifact-version"] == "2"
    book = openpyxl.load_workbook(io.BytesIO(download.content))
    assert book["시트1"]["A1"].value == "합계"


@pytest.mark.asyncio
async def test_a_writer_that_read_an_older_version_is_told_to_reload(client):
    sheet_id = (await client.post("/artifacts/xlsx", json={})).json()["id"]
    ops = [{"sheet": "시트1", "r": 1, "c": 1, "v": 1}]
    first = await client.patch(
        f"/artifacts/{sheet_id}/xlsx/cells", json={"expectedVersion": 1, "ops": ops})
    assert first.status_code == 200
    stale = await client.patch(
        f"/artifacts/{sheet_id}/xlsx/cells", json={"expectedVersion": 1, "ops": ops})
    assert stale.status_code == 409
    whole = await client.put(f"/artifacts/{sheet_id}/xlsx?expectedVersion=1", content=b"PK")
    assert whole.status_code in (409, 422)


@pytest.mark.asyncio
async def test_a_sheet_cannot_be_made_without_its_file_or_from_a_non_sheet(client):
    refused = await client.post("/artifacts", json={"kind": "xlsx", "title": "x", "data": {}})
    assert refused.status_code == 422
    sheet_id = (await client.post("/artifacts/xlsx", json={})).json()["id"]
    bad = await client.patch(
        f"/artifacts/{sheet_id}/xlsx/cells",
        json={"expectedVersion": 1, "ops": [{"sheet": "없는 시트", "r": 0, "c": 0, "v": 1}]})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_person_who_lost_the_race_can_download_their_edits_without_saving_them(client):
    sheet_id = (await client.post("/artifacts/xlsx", json={})).json()["id"]
    first = [{"sheet": "시트1", "r": 0, "c": 0, "v": "다른 사람"}]
    await client.patch(f"/artifacts/{sheet_id}/xlsx/cells", json={"expectedVersion": 1, "ops": first})
    mine = [{"sheet": "시트1", "r": 1, "c": 0, "v": "내 편집"}]
    got = await client.post(
        f"/artifacts/{sheet_id}/xlsx/edits-file", json={"baseVersion": 1, "ops": mine})
    assert got.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(got.content)).active
    # my edits on the version I read: the other person's cell is not in it
    assert ws["A2"].value == "내 편집" and ws["A1"].value is None
    assert (await client.get(f"/artifacts/{sheet_id}/xlsx/model")).json()["version"] == 2


def test_the_sheet_surface_is_optional_and_off_by_default():
    assert SessionKind.sheet.value in settings_store.OPTIONAL_KINDS
    assert SessionKind.sheet.value not in settings_store.DEFAULT_ENABLED_KINDS
