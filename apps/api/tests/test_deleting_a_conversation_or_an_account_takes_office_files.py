"""An office document (HWP, Word, PowerPoint) keeps a file per version. Deleting the conversation
that holds it, singly or in bulk, removes those files after the commit, and nothing else: the
conversation's uploads (kept unless asked), its other documents' rows, other conversations'
documents. Deleting the account with its files takes them with the account's directory; an
administrator who keeps the files (`purgeFiles=false`) leaves them to the storage sweep."""

from __future__ import annotations

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
from app.models.user import User, UserRole
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion, StoredFile
from app.routers import admin, sessions
from app.services import files as file_service


def _me() -> User:
    return User(id="me", email="me@example.test", name="나", password_hash="x")


def _boss() -> User:
    return User(id="boss", email="boss@example.test", name="관리자", password_hash="x",
                role=UserRole.admin)


class World:
    def __init__(self, client, local, blobs):
        self.client = client
        self.local = local
        self.blobs = blobs

    def exists(self, name: str) -> bool:
        return file_service.blob_path(self.blobs[name]).exists()


@pytest.fixture
async def world(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        # SQLite keeps index names in one namespace; the tests need none
        copy.indexes.clear()
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    blob = file_service.write_blob
    blobs = {
        "v1": blob("me", "doc-v1", "document.hwpx", b"one"),
        "v2": blob("me", "doc-v2", "document.hwpx", b"two"),
        "deck": blob("me", "deck-v1", "document.pptx", b"deck"),
        "upload": blob("me", "f1", "원본.hwpx", b"upload"),
        "theirs": blob("other", "doc-v1", "document.docx", b"theirs"),
    }
    async with local() as db:
        db.add_all([_me(), _boss(), User(id="other", email="o@example.test", name="남",
                                   password_hash="x")])
        db.add_all([ChatSession(id="s1", user_id="me", title="공문"),
                    ChatSession(id="s2", user_id="me", title="발표"),
                    ChatSession(id="s3", user_id="other", title="남의 대화")])
        # in s1: an HWP document at v2 (v1 kept as a version) and a web report
        db.add(Artifact(id="doc", user_id="me", session_id="s1", kind=ArtifactKind.hwp,
                        title="공문", version=2, storage_key=blobs["v2"]))
        db.add(ArtifactVersion(artifact_id="doc", version=1, storage_key=blobs["v1"]))
        db.add(Artifact(id="report", user_id="me", session_id="s1", kind=ArtifactKind.report,
                        title="보고서", data={"sections": []}))
        db.add(StoredFile(id="f1", user_id="me", session_id="s1", name="원본.hwpx", size=6,
                          mime="application/octet-stream", storage_key=blobs["upload"]))
        # elsewhere: my deck in s2, someone else's document
        db.add(Artifact(id="deck", user_id="me", session_id="s2", kind=ArtifactKind.pptx,
                        title="발표", storage_key=blobs["deck"]))
        db.add(Artifact(id="theirs", user_id="other", session_id="s3", kind=ArtifactKind.docx,
                        title="남의 문서", storage_key=blobs["theirs"]))
        await db.commit()

    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(admin.router)
    caller = {"user": _me()}

    async def session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: caller["user"]
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            world = World(client, local, blobs)
            world.caller = caller
            yield world
    finally:
        await engine.dispose()


async def _artifact_ids(world) -> set[str]:
    async with world.local() as db:
        return {a.id for a in (await db.exec(select(Artifact))).all()}


async def _left_alone(world) -> None:
    assert world.exists("deck") and world.exists("theirs")
    # the conversation's upload stays in the file box unless asked
    assert world.exists("upload")
    async with world.local() as db:
        assert await db.get(StoredFile, "f1") is not None


async def test_deleting_a_conversation_removes_its_documents_version_files(world):
    assert (await world.client.delete("/sessions/s1")).status_code == 204
    assert not world.exists("v1") and not world.exists("v2")
    assert await _artifact_ids(world) == {"deck", "theirs"}
    await _left_alone(world)


async def test_deleting_conversations_in_bulk_does_the_same(world):
    response = await world.client.post("/sessions/delete", json={"ids": ["s1"]})
    assert response.json()["artifactsDeleted"] == 2
    assert not world.exists("v1") and not world.exists("v2")
    assert await _artifact_ids(world) == {"deck", "theirs"}
    await _left_alone(world)


async def test_deleting_the_account_with_its_files_takes_them(world):
    world.caller["user"] = _boss()
    gone = await world.client.delete("/admin/users/me")
    assert gone.status_code < 300, gone.text
    assert not any(world.exists(name) for name in ("v1", "v2", "deck", "upload"))
    assert world.exists("theirs")
    assert await _artifact_ids(world) == {"theirs"}


async def test_an_administrator_who_keeps_the_files_leaves_them_to_the_sweep(world):
    world.caller["user"] = _boss()
    response = await world.client.delete("/admin/users/me", params={"purgeFiles": "false"})
    assert response.status_code < 300
    assert all(world.exists(name) for name in ("v1", "v2", "deck", "upload", "theirs"))
    assert await _artifact_ids(world) == {"theirs"}
