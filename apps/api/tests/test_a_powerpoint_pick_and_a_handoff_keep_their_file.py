"""The file picked on the screen holds on the server: a slides conversation asked for a
PowerPoint file gets a blank deck its PowerPoint tools write into (one flow, no client
branch), a hand-off carries the pick, and a retry after a reload makes the same file."""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import ChatSession, SessionKind
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions, user_pipelines
from app.schemas.chat import HandoffIn, SendMessage


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


class _User:
    id = "u1"


@pytest.fixture
async def maker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[ChatSession.__table__, Artifact.__table__]))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def test_a_slides_conversation_asked_for_powerpoint_works_on_a_blank_deck(maker):
    async with maker() as db:
        session = ChatSession(user_id="u1", kind=SessionKind.slides)
        db.add(session)
        await db.commit()
        await sessions._pptx_surface(db, _User(), session)
        deck = await sessions._office_report(db, session)
        assert deck is not None and deck.kind is ArtifactKind.pptx
        assert session.artifact_id == deck.id
        # once it has its document, nothing more is made
        await sessions._pptx_surface(db, _User(), session)
        assert session.artifact_id == deck.id


def test_the_pick_is_part_of_the_contract_and_kept_for_a_retry():
    assert SendMessage(content="x", outputFormat="pptx").output_format == "pptx"
    assert HandoffIn(kind="slides", outputFormat="pptx").output_format == "pptx"
    source = inspect.getsource(sessions._send_message)
    assert '"outputFormat": payload.output_format' in source
    assert 'turn_options.get("outputFormat")' in source
    # a report made by the person's own pipeline is made as the file picked too
    assert "office: str" in inspect.getsource(user_pipelines.turn_response)
    assert "office=(" in source
