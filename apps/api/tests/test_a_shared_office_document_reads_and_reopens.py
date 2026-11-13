"""A shared office document (HWP, Word, PowerPoint) reaches its reader as its file, through the
share only, without the owner's file-box id; a document with no conversation joins a new chat
to be edited beside, and one in a conversation stays there."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import (
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Share,
    ShareScope,
    ShareView,
    StoredFile,
)
from app.routers import docx as docx_router
from app.routers import shares as shares_router
from app.services import docx_doc, office_doc

#: one blank document, its bytes fixed (a new one differs in its zip times)
BLANK = docx_doc.blank()

USER = User(id="doc-owner", email="owner@example.test", password_hash="unused", name="주인")


def _metadata() -> MetaData:
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    return metadata


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    metadata = _metadata()
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (
                User, ChatSession, Artifact, ArtifactVersion, StoredFile, Share, ShareView)])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    async with local() as db:
        db.add(User(id=USER.id, email=USER.email, password_hash="x", name=USER.name))
        await db.commit()

    app = FastAPI()
    app.include_router(docx_router.router)

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


def _request() -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/shared/x", "headers": [],
        "client": ("127.0.0.1", 1234), "query_string": b"",
    })


async def _document(local, *, session_id: str | None = None, source: str | None = None):
    async with local() as db:
        doc = await office_doc.create(
            db, docx_doc.KIND, user_id=USER.id, title="직원 연수 공문",
            data=BLANK, session_id=session_id, source_file_id=source,
        )
        return doc


async def _share(local, *, artifact_id: str, scope=ShareScope.link, token="tok-doc"):
    async with local() as db:
        db.add(Share(token=token, owner_id=USER.id, artifact_id=artifact_id, scope=scope))
        await db.commit()


async def test_the_reader_gets_the_documents_file_and_not_the_owners_file_id(stack):
    _, local = stack
    doc = await _document(local, source="f" * 32)
    await _share(local, artifact_id=doc.id)
    async with local() as db:
        payload = await shares_router.read_shared("tok-doc", _request(), db)
        assert payload["artifactKind"] == "docx"
        assert office_doc.SOURCE_KEY not in payload["data"]
        assert payload["data"]["format"] == "docx"
        reply = await shares_router.read_shared_document("tok-doc", _request(), db)
    assert reply.body == BLANK
    assert reply.media_type == docx_doc.DOCX_MIME
    assert "attachment" in reply.headers["content-disposition"]
    assert reply.headers["cache-control"] == "no-store"
    # the stored body keeps its origin
    async with local() as db:
        assert office_doc.source_of((await db.get(Artifact, doc.id)).data) == "f" * 32


async def test_a_revoked_or_member_only_share_gives_no_file(stack):
    _, local = stack
    doc = await _document(local)
    await _share(local, artifact_id=doc.id, scope=ShareScope.workspace, token="tok-members")
    async with local() as db:
        with pytest.raises(HTTPException) as signed_out:
            await shares_router.read_shared_document("tok-members", _request(), db)
        assert signed_out.value.status_code == 401
        with pytest.raises(HTTPException) as unknown:
            await shares_router.read_shared_document("tok-nothing", _request(), db)
        assert unknown.value.status_code == 404


async def test_a_shared_picture_has_no_document_file(stack):
    _, local = stack
    async with local() as db:
        picture = Artifact(user_id=USER.id, kind=ArtifactKind.image, title="그림", data={})
        db.add(picture)
        await db.commit()
        picture_id = picture.id
    await _share(local, artifact_id=picture_id, token="tok-picture")
    async with local() as db:
        with pytest.raises(HTTPException) as missing:
            await shares_router.read_shared_document("tok-picture", _request(), db)
    assert missing.value.status_code == 404


async def test_a_document_with_no_conversation_joins_a_new_chat(stack):
    client, local = stack
    doc = await _document(local)
    async with local() as db:
        chat = ChatSession(id="c" * 32, user_id=USER.id, title="새 대화", kind=SessionKind.chat)
        db.add(chat)
        await db.commit()
    moved = await client.post(f"/artifacts/{doc.id}/docx/session", json={"sessionId": "c" * 32})
    assert moved.status_code == 200
    assert moved.json()["sessionId"] == "c" * 32
    assert moved.json()["version"] == doc.version


async def test_a_document_in_a_conversation_stays_there(stack):
    client, local = stack
    async with local() as db:
        db.add_all([
            ChatSession(id="a" * 32, user_id=USER.id, title="원래 대화", kind=SessionKind.chat),
            ChatSession(id="b" * 32, user_id=USER.id, title="새 대화", kind=SessionKind.chat),
        ])
        await db.commit()
    doc = await _document(local, session_id="a" * 32)
    kept = await client.post(f"/artifacts/{doc.id}/docx/session", json={"sessionId": "b" * 32})
    assert kept.status_code == 409
    async with local() as db:
        assert (await db.get(Artifact, doc.id)).session_id == "a" * 32
    # another account's conversation is not one to join
    async with local() as db:
        db.add(ChatSession(id="d" * 32, user_id="someone-else", title="남의 대화",
                           kind=SessionKind.chat))
        await db.commit()
    lonely = await _document(local)
    refused = await client.post(
        f"/artifacts/{lonely.id}/docx/session", json={"sessionId": "d" * 32}
    )
    assert refused.status_code == 404


async def test_a_save_keeps_the_summary_the_editor_sent(stack):
    client, local = stack
    doc = await _document(local)
    saved = await client.put(
        f"/artifacts/{doc.id}/docx?expectedVersion=1&summary=Saved%20in%20the%20editor",
        content=BLANK,
    )
    assert saved.status_code == 200
    async with local() as db:
        row = (
            await db.exec(select(ArtifactVersion).where(ArtifactVersion.artifact_id == doc.id))
        ).one()
    assert row.summary == "Saved in the editor"
