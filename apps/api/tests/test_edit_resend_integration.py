"""Real HTTP/SQLite fork and resend boundaries; generation and authentication are synthetic."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_privacy import _external_model, _patch_guard_dependencies

from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, Message, Role
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Agent, Artifact, Job, StoredFile
from app.routers import sessions
from app.services import files


@pytest.fixture
async def scenario(monkeypatch, tmp_path):
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copied = table.to_metadata(metadata)
        for column in copied.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(
            metadata.create_all,
            tables=[
                metadata.tables[model.__tablename__]
                for model in (
                    User,
                    Agent,
                    ChatSession,
                    Message,
                    StoredFile,
                    Artifact,
                    Job,
                    AuditEvent,
                )
            ],
        )
    monkeypatch.setattr(files.settings, "file_storage_dir", str(tmp_path))
    monkeypatch.setattr(sessions, "_STOPPING", {})
    user = User(id="owner", email="owner@example.test", name="Fixture", password_hash="unused")
    other = User(id="other", email="other@example.test", name="Other", password_hash="unused")
    source = ChatSession(id="source", user_id=user.id, title="Source", model="fixture/model")
    now = utcnow() - timedelta(minutes=1)
    messages = [
        Message(
            id=f"message-{index}",
            session_id=source.id,
            role=Role.user if index % 2 == 0 else Role.assistant,
            content=content,
            created_at=now + timedelta(seconds=index),
        )
        for index, content in enumerate(
            [
                "Prefix question.",
                "Prefix answer.",
                "Original selected question.",
                "Old answer that must not be reused.",
                "Later question excluded.",
                "Later answer excluded.",
            ]
        )
    ]
    upload = StoredFile(
        id="original-file",
        user_id=user.id,
        session_id=source.id,
        name="notes.txt",
        mime="text/plain",
        size=12,
        text="Fixture note",
    )
    upload.storage_key = files.write_blob(user.id, upload.id, upload.name, b"Fixture note")
    messages[2].attachments = [{"id": upload.id, "name": upload.name, "size": upload.size}]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all([user, other, source, *messages, upload])
        await db.commit()
    real_owned, real_history = sessions._owned, sessions._history
    await _patch_guard_dependencies(
        monkeypatch,
        session=source,
        models=[_external_model("fixture/model")],
        blocks=[],
    )
    monkeypatch.setattr(sessions, "_owned", real_owned)
    monkeypatch.setattr(sessions, "_history", real_history)

    async def enabled():
        return ["chat"]

    async def key(*args, **kwargs):
        return "fixture-only"

    async def credentials(*args, **kwargs):
        return "http://fixture.invalid", "fixture-only"

    async def no_tools(*args, **kwargs):
        return []

    calls = []

    async def run(**kwargs):
        calls.append(kwargs)
        yield sessions.chat_service.sse({"type": "delta", "text": "Synthetic answer"})
        yield sessions.chat_service.sse({"type": "usage", "credits": 0})
        yield sessions.chat_service.sse({"type": "done"})

    monkeypatch.setattr(sessions.settings_store, "enabled_kinds", enabled)
    monkeypatch.setattr(sessions, "has_headroom", lambda *args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions, "build_tools", no_tools)
    monkeypatch.setattr(sessions, "_run_turn", run)

    async def db_session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app = FastAPI()
    app.include_router(sessions.router)
    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            yield client, engine, source, messages, upload, calls, app, other
    finally:
        await engine.dispose()


async def _snapshot(engine, session_id):
    async with AsyncSession(engine) as db:
        messages = (
            await db.exec(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        return [row.model_dump(mode="json") for row in messages]


async def test_http_fork_wire_shape_and_database_preserve_original(scenario):
    client, engine, source, messages, upload, calls, _, _ = scenario
    before = await _snapshot(engine, source.id)
    response = await client.post(f"/sessions/{source.id}/messages/{messages[2].id}/fork")
    assert response.status_code == 201
    body = response.json()
    branch = body["session"]
    clone_id = body["attachmentIdMap"][upload.id]
    assert clone_id != upload.id
    assert body["attachments"][0]["id"] == clone_id
    assert body["attachments"][0]["sessionId"] is None
    assert [row["content"] for row in branch["messages"]] == [row.content for row in messages[:2]]
    assert await _snapshot(engine, source.id) == before
    assert calls == []
    async with AsyncSession(engine) as db:
        stored = await db.get(ChatSession, branch["id"])
        clone = await db.get(StoredFile, clone_id)
        original = await db.get(StoredFile, upload.id)
        assert stored.user_id == source.user_id
        assert stored.pending is None and stored.artifact_id is None and stored.index_key is None
        assert original.session_id == source.id
        assert clone.storage_key != original.storage_key
        assert files.read_blob(clone.storage_key) == files.read_blob(original.storage_key)


async def test_resend_uses_edited_content_prefix_and_cloned_attachment(scenario):
    client, engine, source, messages, upload, calls, _, _ = scenario
    before = await _snapshot(engine, source.id)
    fork = (await client.post(f"/sessions/{source.id}/messages/{messages[2].id}/fork")).json()
    branch_id = fork["session"]["id"]
    clone_id = fork["attachmentIdMap"][upload.id]
    response = await client.post(
        f"/sessions/{branch_id}/messages",
        json={
            "content": "Explain the edited subject briefly.",
            "attachments": [clone_id],
            "webSearch": False,
        },
    )
    assert response.status_code == 200
    assert len(calls) == 1
    outbound = str(calls[0]["messages"])
    assert "Prefix question." in outbound and "Prefix answer." in outbound
    assert "Explain the edited subject briefly." in outbound
    assert all(row.content not in outbound for row in messages[2:])
    assert await _snapshot(engine, source.id) == before
    saved = await _snapshot(engine, branch_id)
    assert saved[-1]["content"] == "Explain the edited subject briefly."
    assert saved[-1]["attachments"][0]["id"] == clone_id
    async with AsyncSession(engine) as db:
        assert (await db.get(StoredFile, clone_id)).session_id == branch_id
        assert (await db.get(StoredFile, upload.id)).session_id == source.id


async def test_fork_requires_new_privacy_decision_and_masks_the_new_turn(scenario):
    client, engine, source, messages, _, calls, _, _ = scenario
    text = "Please rewrite this address politely: person@example.com"
    original_decision = await client.post(f"/sessions/{source.id}/messages", json={"content": text})
    assert original_decision.status_code == 409
    old_token = original_decision.json()["decisionToken"]
    fork = (await client.post(f"/sessions/{source.id}/messages/{messages[2].id}/fork")).json()
    branch_id = fork["session"]["id"]
    before = await _snapshot(engine, branch_id)
    replay = await client.post(
        f"/sessions/{branch_id}/messages",
        json={
            "content": text,
            "privacyAction": "mask_external",
            "privacyDecisionToken": old_token,
        },
    )
    assert 400 <= replay.status_code < 500
    assert await _snapshot(engine, branch_id) == before and calls == []
    decision = await client.post(f"/sessions/{branch_id}/messages", json={"content": text})
    assert decision.status_code == 409
    assert await _snapshot(engine, branch_id) == before
    allowed = await client.post(
        f"/sessions/{branch_id}/messages",
        json={
            "content": text,
            "privacyAction": "mask_external",
            "privacyDecisionToken": decision.json()["decisionToken"],
        },
    )
    assert allowed.status_code == 200 and len(calls) == 1
    assert "person@example.com" not in str(calls[0]["messages"])
    assert "person@example.com" not in str(await _snapshot(engine, branch_id))


async def test_http_foreign_user_cannot_fork_or_create_files(scenario):
    client, engine, source, messages, _, calls, app, other = scenario
    app.dependency_overrides[current_user] = lambda: other
    response = await client.post(f"/sessions/{source.id}/messages/{messages[2].id}/fork")
    assert response.status_code == 404 and calls == []
    async with AsyncSession(engine) as db:
        assert len((await db.exec(select(ChatSession))).all()) == 1
        assert len((await db.exec(select(StoredFile))).all()) == 1
