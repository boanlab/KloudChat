"""The artifacts gallery asks for a family of kinds at once (reports: web, HWP, Word)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind
from app.routers import workspace

_DDL = """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, project_id TEXT, kind TEXT,
        title TEXT, data TEXT, storage_key TEXT, version INTEGER DEFAULT 1,
        created_at DATETIME, updated_at DATETIME,
        search_text TEXT NOT NULL DEFAULT ''
    )
"""

T0 = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_DDL)
    session = AsyncSession(engine, expire_on_commit=False)
    for i, kind in enumerate(["report", "hwp", "docx", "deck", "pptx", "chart"]):
        session.add(Artifact(id=f"a{i}", user_id="u1", kind=ArtifactKind(kind), title=kind,
                             data={}, updated_at=T0 + timedelta(hours=i)))
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


async def _kinds(db, kind):
    user = User(id="u1", email="u@example.test", name="U", password_hash="x")
    rows = await workspace.list_artifacts(user, db, kind=kind, project_id=None, q=None, limit=60,
                                          before_at=None, before_id=None)
    return sorted(r.kind for r in rows)


@pytest.mark.asyncio
async def test_a_family_is_asked_for_as_a_comma_list(db) -> None:
    assert await _kinds(db, "report,hwp,docx") == ["docx", "hwp", "report"]
    assert await _kinds(db, "deck,pptx") == ["deck", "pptx"]
    assert await _kinds(db, "docx") == ["docx"]
    # Unknown kinds are ignored; nothing known left is an empty page, not every artifact.
    assert await _kinds(db, "report,nonsense") == ["report"]
    assert await _kinds(db, "nonsense") == []
