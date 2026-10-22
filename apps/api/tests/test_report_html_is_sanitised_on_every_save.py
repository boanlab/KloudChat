"""Report section HTML reaches storage sanitised however it is saved."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import ArtifactVersion
from app.routers import workspace

EVIL = '<p onclick="steal()">본문</p><img src="x" onerror="steal()"><script>steal()</script>'


def _report(html: str) -> dict:
    return {"kind": "report", "sources": [], "sections": [
        {"id": "s1", "heading": "개요", "level": 1, "status": "done", "format": "html",
         "content": html},
    ]}


def _clean(body: dict) -> bool:
    html = body["data"]["sections"][0]["content"]
    return "본문" in html and not any(
        word in html for word in ("onclick", "onerror", "<script", "steal"))


@pytest.mark.asyncio
async def test_script_in_a_report_section_is_dropped_on_create_update_and_restore():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    user = User(id="xss-user", email="xss@example.test", password_hash="unused", name="X")
    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            made = await client.post(
                "/artifacts", json={"kind": "report", "title": "보고서", "data": _report(EVIL)})
            assert made.status_code == 201, made.text
            artifact_id = made.json()["id"]
            assert _clean(made.json())
            assert _clean((await client.get(f"/artifacts/{artifact_id}")).json())

            saved = await client.patch(f"/artifacts/{artifact_id}", json={
                "data": _report(EVIL), "expectedVersion": 1, "summary": "edit"})
            assert saved.status_code == 200, saved.text
            assert _clean(saved.json())

            # a version stored before sanitising existed comes back sanitised too
            async with AsyncSession(engine, expire_on_commit=False) as db:
                db.add(ArtifactVersion(artifact_id=artifact_id, version=0, data=_report(EVIL),
                                       summary="old"))
                await db.commit()
            restored = await client.post(f"/artifacts/{artifact_id}/restore",
                                         json={"version": 0})
            assert restored.status_code == 200, restored.text
            assert _clean(restored.json())
    finally:
        await engine.dispose()
