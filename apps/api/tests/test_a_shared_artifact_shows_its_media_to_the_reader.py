"""A shared picture or clip reaches its reader through the share, and nothing else does."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import create_access_token
from app.models.chat import ChatSession, SessionKind
from app.models.user import User, UserStatus, utcnow
from app.models.workspace import (
    Artifact,
    ArtifactKind,
    Share,
    ShareScope,
    ShareView,
    StoredFile,
)
from app.routers import shares as shares_router
from app.services import files as file_service

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
async def db():
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


@pytest.fixture(autouse=True)
def _blobs(monkeypatch):
    monkeypatch.setattr(file_service, "read_blob", lambda key: _PNG)


def _request(query: str = "") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/shared/x", "headers": [],
        "client": ("127.0.0.1", 1234), "query_string": query.encode(),
    })


async def _picture(db, *, scope=ShareScope.link):
    owner = User(email="owner@example.com", name="주인", password_hash="x")
    db.add(owner)
    await db.commit()
    picture = StoredFile(user_id=owner.id, name="cat.png", size=len(_PNG), mime="image/png",
                         storage_key=f"{owner.id}/cat.png", text="", tokens=0)
    other = StoredFile(user_id=owner.id, name="secret.png", size=len(_PNG), mime="image/png",
                       storage_key=f"{owner.id}/secret.png", text="", tokens=0)
    db.add_all([picture, other])
    await db.commit()
    artifact = Artifact(user_id=owner.id, kind=ArtifactKind.image, title="고양이",
                        data={"kind": "image", "prompt": "고양이",
                              "src": f"/api/files/{picture.id}/content"})
    db.add(artifact)
    await db.commit()
    share = Share(token="tok-1", owner_id=owner.id, artifact_id=artifact.id, scope=scope)
    db.add(share)
    await db.commit()
    return owner, picture, other, share


async def test_the_shared_body_points_its_picture_at_the_shares_own_route(db):
    _, picture, _, share = await _picture(db)
    payload = await shares_router.read_shared("tok-1", _request(), db)
    assert payload["data"]["src"] == f"/api/shared/tok-1/files/{picture.id}"
    # The stored body itself is unchanged.
    stored = await db.get(Artifact, share.artifact_id)
    assert stored.data["src"] == f"/api/files/{picture.id}/content"


async def test_an_anonymous_reader_gets_the_picture_without_counting_a_view(db):
    _, picture, _, share = await _picture(db)
    response = await shares_router.read_shared_file("tok-1", picture.id, _request(), db)
    assert response.body == _PNG and response.media_type == "image/png"
    assert response.headers["Content-Security-Policy"] == "sandbox"
    assert (await db.get(Share, share.id)).views == 0


async def test_a_file_the_body_does_not_name_or_a_revoked_link_is_not_found(db):
    _, picture, other, share = await _picture(db)
    with pytest.raises(HTTPException) as caught:
        await shares_router.read_shared_file("tok-1", other.id, _request(), db)
    assert caught.value.status_code == 404
    share.revoked_at = utcnow()
    db.add(share)
    await db.commit()
    with pytest.raises(HTTPException) as caught:
        await shares_router.read_shared_file("tok-1", picture.id, _request(), db)
    assert caught.value.status_code == 404


async def test_a_members_only_link_needs_a_signed_in_reader_for_its_media(db):
    owner, picture, _, _ = await _picture(db, scope=ShareScope.workspace)
    with pytest.raises(HTTPException) as caught:
        await shares_router.read_shared_file("tok-1", picture.id, _request(), db)
    assert caught.value.status_code == 401
    reader = User(email="reader@example.com", name="독자", password_hash="x",
                  status=UserStatus.active)
    db.add(reader)
    await db.commit()
    token, _ = create_access_token(reader.id, reader.role.value)
    response = await shares_router.read_shared_file(
        "tok-1", picture.id, _request(f"t={token}"), db
    )
    assert response.body == _PNG


async def test_a_conversation_share_reaches_only_its_own_artifact_media(db):
    owner, picture, _, share = await _picture(db)
    session = ChatSession(user_id=owner.id, kind=SessionKind.chat, title="그림",
                          artifact_id=share.artifact_id)
    db.add(session)
    await db.commit()
    db.add(Share(token="tok-2", owner_id=owner.id, session_id=session.id))
    await db.commit()
    response = await shares_router.read_shared_file("tok-2", picture.id, _request(), db)
    assert response.body == _PNG
