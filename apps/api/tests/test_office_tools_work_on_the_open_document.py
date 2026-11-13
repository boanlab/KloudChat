"""The office tools work on the document open beside the chat, never on "the newest of its
kind": with several and none open they ask which; a write makes a new document unless a
rewrite of the open one was asked and read first; the person's standing rules come with
every read."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.services import hwp_doc
from app.services.tools import hwp as hwp_tools
from app.services.tools.base import ToolContext

USER, OTHER, SESSION = "office-user", "someone-else", "s-office"


@pytest.fixture
async def local(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)

    def make():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(hwp_tools, "SessionLocal", make)
    try:
        yield make
    finally:
        await engine.dispose()


async def _two(local):
    async with local() as db:
        first = await hwp_doc.create(db, user_id=USER, title="연수 공문", session_id=SESSION,
                                     data=hwp_doc.plain("연수 공문", "연수 본문"))
        second = await hwp_doc.create(db, user_id=USER, title="회의 공문", session_id=SESSION,
                                      data=hwp_doc.plain("회의 공문", "회의 본문"))
    return first, second


@pytest.mark.asyncio
async def test_the_open_document_is_read_not_the_newest(local):
    first, _ = await _two(local)
    ctx = ToolContext(user_id=USER, session_id=SESSION,
                      open_artifact={"id": first.id, "version": 1})
    read = await hwp_tools.hwp_read({}, ctx)
    assert not read.failed and "연수 공문" in read.content and "연수 본문" in read.content
    edited = await hwp_tools.hwp_edit(
        {"ops": [{"op": "replace_text", "id": "p2", "text": "연수 본문 고침"}]}, ctx)
    assert not edited.failed, edited.content
    assert edited.trace["artifactId"] == first.id


@pytest.mark.asyncio
async def test_with_several_and_none_open_the_tool_asks_which(local):
    await _two(local)
    ctx = ToolContext(user_id=USER, session_id=SESSION)
    read = await hwp_tools.hwp_read({}, ctx)
    assert read.failed and "연수 공문" in read.content and "회의 공문" in read.content
    assert "물어보세요" in read.content
    picked = await hwp_tools.hwp_read({"document": "회의 공문"}, ctx)
    assert not picked.failed and "회의 본문" in picked.content


@pytest.mark.asyncio
async def test_an_open_document_of_someone_else_or_another_chat_is_not_used(local):
    first, _ = await _two(local)
    async with local() as db:
        foreign = await hwp_doc.create(db, user_id=OTHER, title="남의 문서", session_id=SESSION,
                                       data=hwp_doc.plain("남의 문서", "비밀"))
        elsewhere = await hwp_doc.create(db, user_id=USER, title="다른 대화", session_id="s-2",
                                         data=hwp_doc.plain("다른 대화", "딴 내용"))
    for opened in (foreign.id, elsewhere.id):
        ctx = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": opened})
        read = await hwp_tools.hwp_read({}, ctx)
        assert "비밀" not in read.content and "딴 내용" not in read.content


@pytest.mark.asyncio
async def test_a_write_makes_a_new_document_unless_a_read_rewrite_was_asked(local):
    first, _ = await _two(local)
    ctx = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": first.id})
    made = await hwp_tools.hwp_write({"title": "새 공문", "format": "plain", "body": "새 글"}, ctx)
    assert not made.failed and made.trace["artifactId"] != first.id
    assert "그대로" in made.content
    async with local() as db:
        kept = await db.get(type(first), first.id)
    assert kept.version == 1

    fresh = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": first.id})
    blind = await hwp_tools.hwp_write(
        {"title": "연수 공문", "format": "plain", "body": "다시 씀", "replace": True}, fresh)
    assert blind.failed and "hwp_read" in blind.content
    await hwp_tools.hwp_read({}, fresh)
    rewritten = await hwp_tools.hwp_write(
        {"title": "연수 공문", "format": "plain", "body": "다시 씀", "replace": True}, fresh)
    assert not rewritten.failed, rewritten.content
    assert rewritten.trace == {"artifactId": first.id, "artifactVersion": 2}


@pytest.mark.asyncio
async def test_a_read_carries_the_rules_set_for_the_conversation(local):
    first, _ = await _two(local)
    ctx = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": first.id},
                      standing=["앞으로 기관명은 익명으로 써 줘"])
    read = await hwp_tools.hwp_read({}, ctx)
    assert "앞으로 기관명은 익명으로 써 줘" in read.content


def test_the_write_tool_says_it_makes_a_new_document():
    write = hwp_tools.HWP_WRITE
    assert "replace" in write.parameters["properties"]
    assert "replace:true only when" in write.description
    assert "document" in hwp_tools.HWP_READ.parameters["properties"]
