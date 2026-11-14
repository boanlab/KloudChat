"""Report sections reach storage as Markdown however they are saved; HTML (an older client, an
older version, a stored report from before the change) is sanitised and converted."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
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

_PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0082_report_sections_markdown.py"
_SPEC = importlib.util.spec_from_file_location("m0082", _PATH)
m0082 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m0082)


def _report(html: str, **extra) -> dict:
    return {"kind": "report", "sources": [], "sections": [
        {"id": "s1", "heading": "개요", "level": 1, "status": "done", "format": "html",
         "content": html, **extra},
    ]}


def _markdown(body: dict) -> bool:
    section = body["data"]["sections"][0]
    return section["format"] == "markdown" and "본문" in section["content"] and not any(
        word in section["content"] for word in ("onclick", "onerror", "<script", "steal", "<p"))


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    user = User(id="md-user", email="md@example.test", password_hash="unused", name="M")
    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
            yield http, engine
    finally:
        await engine.dispose()


async def test_html_is_stored_as_markdown_on_create_update_and_restore(client):
    http, engine = client
    made = await http.post("/artifacts", json={"kind": "report", "title": "보고서",
                                               "data": _report(EVIL)})
    assert made.status_code == 201, made.text
    artifact_id = made.json()["id"]
    assert _markdown(made.json())
    assert _markdown((await http.get(f"/artifacts/{artifact_id}")).json())

    saved = await http.patch(f"/artifacts/{artifact_id}", json={
        "data": _report(EVIL), "expectedVersion": 1, "summary": "edit"})
    assert saved.status_code == 200, saved.text
    assert _markdown(saved.json())

    # a version stored while sections were HTML comes back as Markdown
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(ArtifactVersion(artifact_id=artifact_id, version=0, data=_report(EVIL),
                               summary="old"))
        await db.commit()
    restored = await http.post(f"/artifacts/{artifact_id}/restore", json={"version": 0})
    assert restored.status_code == 200, restored.text
    assert _markdown(restored.json())


async def test_a_changed_section_loses_its_fact_check_and_an_unchanged_one_keeps_it(client):
    http, _ = client
    check = {"status": "done", "claims": []}
    data = {"kind": "report", "sources": [], "sections": [
        {"id": "a", "heading": "가", "level": 1, "status": "done", "content": "그대로",
         "factCheck": check},
        {"id": "b", "heading": "나", "level": 1, "status": "done", "content": "원래",
         "factCheck": check},
    ]}
    made = await http.post("/artifacts", json={"kind": "report", "title": "t", "data": data})
    artifact_id = made.json()["id"]
    data["sections"][1]["content"] = "고침"
    saved = await http.patch(f"/artifacts/{artifact_id}", json={"data": data,
                                                               "expectedVersion": 1})
    sections = saved.json()["data"]["sections"]
    assert sections[0]["factCheck"] == check
    assert "factCheck" not in sections[1]


async def test_a_picture_added_to_an_old_html_section_leaves_it_markdown(client, monkeypatch):
    http, engine = client
    made = await http.post("/artifacts", json={"kind": "report", "title": "t",
                                               "data": _report("<p>본문</p>")})
    artifact_id = made.json()["id"]
    # an HTML section stored before the change
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("UPDATE artifacts SET data = :data WHERE id = :id"),
            {"data": json.dumps(_report("<p><strong>본문</strong></p>")), "id": artifact_id},
        )

    async def picture(*_args):
        return "image/png", b"\x89PNG"

    monkeypatch.setattr(workspace, "_picture_bytes", picture)
    added = await http.post(f"/artifacts/{artifact_id}/sections/image",
                            json={"sectionId": "s1", "artifactId": "pic", "caption": "그림"})
    assert added.status_code == 200, added.text
    section = added.json()["data"]["sections"][0]
    assert section["format"] == "markdown"
    assert section["content"].startswith("**본문**\n\n![그림](data:image/png;base64,")


_MIGRATION_DDL = (
    """CREATE TABLE artifacts (id TEXT PRIMARY KEY, kind TEXT, data TEXT, version INTEGER,
        storage_key TEXT)""",
    """CREATE TABLE artifact_versions (id TEXT PRIMARY KEY, artifact_id TEXT, version INTEGER,
        data TEXT, storage_key TEXT, summary TEXT, created_at DATETIME)""",
)


def test_the_migration_converts_stored_html_reports_and_keeps_the_old_copy():
    engine = sa.create_engine("sqlite://")
    html = _report("<h3>소제목</h3><p><strong>굵게</strong></p><ul><li>가</li></ul>")
    plain = {"sections": [{"id": "m", "heading": "가", "content": "그대로"}]}
    deck = {"slides": [], "sections": [{"format": "html", "content": "<p>x</p>"}]}
    with engine.begin() as connection:
        for statement in _MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        for row_id, kind, data in (("r1", "report", html), ("r2", "report", plain),
                                   ("d1", "deck", deck)):
            connection.execute(
                sa.text("INSERT INTO artifacts VALUES (:id, :kind, :data, 3, NULL)"),
                {"id": row_id, "kind": kind, "data": json.dumps(data)},
            )
        with Operations.context(MigrationContext.configure(connection)):
            m0082.upgrade()
        rows = {
            row_id: (json.loads(data), version)
            for row_id, data, version in connection.execute(
                sa.text("SELECT id, data, version FROM artifacts"))
        }
        versions = connection.execute(
            sa.text("SELECT artifact_id, version, data, summary FROM artifact_versions")).all()

    converted, version = rows["r1"]
    assert version == 4
    assert converted["sections"][0]["format"] == "markdown"
    assert converted["sections"][0]["content"] == "### 소제목\n\n**굵게**\n\n- 가"
    assert rows["r2"] == (plain, 3)
    assert rows["d1"] == (deck, 3)
    assert [(a, v, json.loads(d), s) for a, v, d, s in versions] == [
        ("r1", 3, html, m0082.SUMMARY)
    ]
