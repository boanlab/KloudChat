"""File box: uploaded and generated files, dropped with a conversation on request."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import StoredFile
from app.routers import sessions
from app.routers import workspace as ws
from app.schemas.chat import SessionBulkDelete
from app.schemas.workspace import FileOut
from app.services import files as file_service
from app.services import index_client, sandbox


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[StoredFile.__table__]))

    def session():
        return AsyncSession(engine, expire_on_commit=False)

    forgotten: list[tuple[str, str]] = []

    async def forget(*, collection: str, doc_id: str) -> bool:
        forgotten.append((collection, doc_id))
        return True

    monkeypatch.setattr(index_client, "forget_document", forget)
    monkeypatch.setattr(sandbox, "SessionLocal", session)
    session.forgotten = forgotten
    yield session


async def _file(session, name: str, **extra) -> StoredFile:
    async with session() as s:
        row = StoredFile(user_id="me", name=name, size=3, mime="text/plain", **extra)
        row.storage_key = file_service.write_blob("me", row.id, name, b"abc")
        s.add(row)
        await s.commit()
        return row


async def test_a_code_runs_output_is_marked_made_and_an_upload_is_not(db):
    made = await sandbox.store_output("me", "s1", "lru_results.csv", b"frame,fault\n3,12\n")
    async with db() as s:
        row = await s.get(StoredFile, made["id"])
    assert row.origin == "generated" and FileOut.of(row).origin == "generated"
    upload = await _file(db, "회의녹취.txt", session_id="s1")
    assert upload.origin == "upload" and FileOut.of(upload).origin == "upload"


async def test_deleting_a_conversations_files_leaves_a_projects_and_an_agents(db):
    mine = await _file(db, "transcript.txt", session_id="s1", indexed_at=None)
    made = await _file(db, "status.csv", session_id="s1", origin="generated")
    held = await _file(db, "spec.pdf", session_id="s1", project_id="p1")
    agents = await _file(db, "faq.md", session_id="s1", agent_id="a1")
    other = await _file(db, "other.txt", session_id="s2")
    async with db() as s:
        assert await ws.delete_session_files(s, ["s1"]) == 2
        await s.commit()
        left = {f.name for f in (await s.exec(select(StoredFile))).all()}
    assert left == {"spec.pdf", "faq.md", "other.txt"}
    assert not (file_service.storage_root() / mine.storage_key).exists()
    assert not (file_service.storage_root() / made.storage_key).exists()
    assert (file_service.storage_root() / held.storage_key).exists()
    assert (file_service.storage_root() / agents.storage_key).exists()
    assert (file_service.storage_root() / other.storage_key).exists()
    async with db() as s:
        assert await ws.delete_session_files(s, []) == 0


def test_the_audit_note_says_whether_the_files_stayed():
    assert sessions._files_note(0, 0) == ""
    assert sessions._files_note(3, 0) == "첨부 파일은 지우지 않음"
    assert sessions._files_note(3, 3) == "파일 3개도 지움"
    # kept by default: the person is asked first
    assert SessionBulkDelete().files is False


def test_the_migration_adds_and_drops_the_origin_on_sqlite():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0075_file_origin.py"
    spec = importlib.util.spec_from_file_location("m0075", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0075", "0074")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE files (id VARCHAR PRIMARY KEY, name VARCHAR)"))
        conn.execute(sa.text("INSERT INTO files VALUES ('f1', 'old.txt')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert conn.execute(sa.text("SELECT origin FROM files")).scalar() == "upload"
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "origin" not in {c["name"] for c in sa.inspect(conn).get_columns("files")}
