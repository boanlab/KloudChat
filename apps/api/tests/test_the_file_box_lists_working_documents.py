"""파일함 「작업 문서」: the person's HWP, Word and PowerPoint files and web reports and web slides,
one row per document (its versions counted), a page at a time in any of its orders; only their
own, named by the conversation and project they belong to; the tab's badge on the files page;
and deleting one takes its version files with it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Project,
    StoredFile,
)
from app.routers import workspace
from app.services import files as file_service

ME = User(id="me", email="me@example.test", name="나", password_hash="x")
KINDS = [ArtifactKind.hwp, ArtifactKind.docx, ArtifactKind.pptx, ArtifactKind.report,
         ArtifactKind.deck]


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (
                User, ChatSession, Project, Artifact, ArtifactVersion, StoredFile, Agent,
                AuditEvent)])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    start = utcnow() - timedelta(days=10)
    async with local() as db:
        db.add_all([ME, User(id="other", email="o@example.test", name="남", password_hash="x")])
        db.add(Project(id="p1", user_id="me", name="연수 준비", emoji="📚"))
        db.add(Project(id="p2", user_id="other", name="남의 프로젝트"))
        db.add(ChatSession(id="s1", user_id="me", title="공문 쓰기", project_id="p1"))
        db.add(ChatSession(id="s2", user_id="other", title="남의 대화"))
        for n in range(130):
            kind = KINDS[n % 5]
            db.add(Artifact(
                id=f"a{n:031d}", user_id="me", kind=kind, title=f"문서 {n:03d}",
                version=1 + n % 4,
                session_id="s1" if n % 3 == 0 else ("gone" if n % 3 == 1 else None),
                project_id="p1" if n % 2 == 0 else None,
                data={"sections": []} if kind is ArtifactKind.report else None,
                updated_at=start + timedelta(minutes=n), created_at=start,
            ))
        # not working documents, or not mine
        db.add(Artifact(id="i" * 32, user_id="me", kind=ArtifactKind.image, title="문서 그림"))
        db.add(Artifact(id="o" * 32, user_id="other", kind=ArtifactKind.hwp, title="문서 남의 것",
                        session_id="s2", project_id="p2"))
        db.add(StoredFile(id="f" * 32, user_id="me", name="문서 원본.hwpx", size=10,
                          mime="application/octet-stream", storage_key="me/f"))
        await db.commit()

    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: ME
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client, local
    finally:
        await engine.dispose()


async def _walk(client, **params) -> list[dict]:
    items, cursor = [], None
    while True:
        response = await client.get("/artifacts/working", params={
            "limit": 40, **params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next"]
        if cursor is None:
            return items


async def test_every_working_document_once_newest_edit_first(stack):
    client, _ = stack
    first = (await client.get("/artifacts/working", params={"limit": 40})).json()
    assert first["total"] == 130
    assert first["counts"] == {"hwp": 26, "docx": 26, "pptx": 26, "report": 26, "deck": 26}
    items = await _walk(client)
    assert len(items) == 130 and len({row["id"] for row in items}) == 130
    assert [row["title"] for row in items[:2]] == ["문서 129", "문서 128"]
    stamps = [row["updatedAt"] for row in items]
    assert stamps == sorted(stamps, reverse=True)
    # neither the picture nor someone else's document
    assert not {"i" * 32, "o" * 32} & {row["id"] for row in items}


async def test_each_row_says_its_versions_and_where_it_belongs(stack):
    client, _ = stack
    rows = {row["title"]: row for row in await _walk(client)}
    zero = rows["문서 000"]
    assert zero["kind"] == "hwp" and zero["version"] == 1
    assert (zero["sessionId"], zero["sessionTitle"]) == ("s1", "공문 쓰기")
    project = (zero["projectId"], zero["projectName"], zero["projectEmoji"])
    assert project == ("p1", "연수 준비", "📚")
    # a conversation since deleted is none; so is no project
    one = rows["문서 001"]
    assert one["sessionId"] is None and one["sessionTitle"] is None
    assert one["projectId"] is None and one["version"] == 2


async def test_other_orders_page_through_everything(stack):
    client, _ = stack
    by_title = await _walk(client, sort="title")
    assert [row["title"] for row in by_title] == sorted(row["title"] for row in by_title)
    assert len(by_title) == 130
    oldest = await _walk(client, sort="oldest")
    assert [row["title"] for row in oldest[:2]] == ["문서 000", "문서 001"]
    assert len({row["id"] for row in oldest}) == 130
    # a cursor belongs to its order
    cursor = (await client.get("/artifacts/working", params={"limit": 5})).json()["next"]
    wrong = await client.get("/artifacts/working", params={"sort": "title", "cursor": cursor})
    assert wrong.status_code == 422


async def test_kind_and_title_filters(stack):
    client, _ = stack
    decks = await _walk(client, kind="deck")
    assert len(decks) == 26 and {row["kind"] for row in decks} == {"deck"}
    office = await _walk(client, kind="hwp,docx,pptx")
    assert len(office) == 78
    body = (await client.get("/artifacts/working", params={"q": "문서 12", "kind": "hwp"})).json()
    # counts follow the search, not the kind
    assert body["counts"]["docx"] == 2 and body["total"] == 2
    assert [row["title"] for row in await _walk(client, q="12")] == [
        "문서 129", "문서 128", "문서 127", "문서 126", "문서 125", "문서 124", "문서 123",
        "문서 122", "문서 121", "문서 120", "문서 112", "문서 012"]
    # only the kinds the tab holds; a wildcard is a letter
    assert (await client.get("/artifacts/working", params={"kind": "image"})).json()["total"] == 0
    assert (await client.get("/artifacts/working", params={"q": "%"})).json()["total"] == 0


async def test_the_files_page_counts_the_tab(stack):
    client, _ = stack
    body = (await client.get("/files/page", params={"box": "true"})).json()
    assert body["counts"] == {"upload": 1, "generated": 0, "working": 130}
    searched = (await client.get("/files/page", params={"box": "true", "q": "원본"})).json()
    assert searched["counts"]["working"] == 0 and searched["counts"]["upload"] == 1
    # inside a project the tab is not there
    inside = (await client.get("/files/page", params={"box": "true", "project_id": "p1"})).json()
    assert "working" not in inside["counts"]


async def test_deleting_one_takes_its_version_files(stack):
    client, local = stack
    keys = [file_service.write_blob("me", f"doc-v{v}", "document.hwpx", b"PK" * v)
            for v in (1, 2)]
    async with local() as db:
        doc = await db.get(Artifact, f"a{0:031d}")
        doc.storage_key = keys[1]
        doc.version = 2
        db.add(doc)
        db.add(ArtifactVersion(artifact_id=doc.id, version=1, storage_key=keys[0]))
        await db.commit()
    assert all(file_service.blob_path(key).exists() for key in keys)
    assert (await client.delete(f"/artifacts/{'a' + '0' * 31}")).status_code == 204
    assert not any(file_service.blob_path(key).exists() for key in keys)
    async with local() as db:
        assert (await db.exec(select(ArtifactVersion))).all() == []
        audit = (await db.exec(select(AuditEvent))).all()
        assert [row.action for row in audit] == ["artifact.delete"]
    # someone else's is not found, and stays
    assert (await client.delete(f"/artifacts/{'o' * 32}")).status_code == 404
    gone = await client.post("/artifacts/delete", json={"ids": ["o" * 32, f"a{5:031d}"]})
    assert gone.json() == {"deleted": 1}
    assert (await client.get("/artifacts/working")).json()["total"] == 128
