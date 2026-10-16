"""파일함 미리보기: 그림·PDF가 아닌 파일은 뽑아 둔 글을 보여 준다.
길면 앞부분만, 남의 파일은 없는 것처럼."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import StoredFile
from app.routers import workspace as ws


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[StoredFile.__table__]))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


async def _file(db, text: str, user_id: str = "me") -> StoredFile:
    row = StoredFile(
        user_id=user_id, name="회의록.docx", size=10, mime="application/msword", text=text
    )
    db.add(row)
    await db.commit()
    return row


async def test_a_files_text_comes_whole_when_short(db):
    row = await _file(db, "1. 일정 확정\n2. 예산 검토")
    out = await ws.file_text(row.id, SimpleNamespace(id="me"), db)
    assert out.text == "1. 일정 확정\n2. 예산 검토"
    assert out.truncated is False


async def test_a_long_text_is_cut_and_says_so(db):
    row = await _file(db, "가" * (ws.PREVIEW_CHARS + 5))
    out = await ws.file_text(row.id, SimpleNamespace(id="me"), db)
    assert len(out.text) == ws.PREVIEW_CHARS
    assert out.truncated is True


async def test_another_persons_file_is_not_found(db):
    row = await _file(db, "남의 글", user_id="someone")
    with pytest.raises(HTTPException) as caught:
        await ws.file_text(row.id, SimpleNamespace(id="me"), db)
    assert caught.value.status_code == 404
