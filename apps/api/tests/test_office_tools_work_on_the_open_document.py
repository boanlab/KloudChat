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


@pytest.mark.asyncio
async def test_undo_brings_the_earlier_version_back_as_the_newest(local):
    first, _ = await _two(local)
    ctx = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": first.id})
    await hwp_tools.hwp_read({}, ctx)
    await hwp_tools.hwp_edit({"ops": [{"op": "replace_text", "id": "p2", "text": "바뀐 글"}]},
                             ctx)
    undone = await hwp_tools.hwp_restore({}, ctx)
    assert not undone.failed, undone.content
    assert undone.trace == {"artifactId": first.id, "artifactVersion": 3}
    read = await hwp_tools.hwp_read({}, ctx)
    assert "연수 본문" in read.content and "바뀐 글" not in read.content
    nothing = await hwp_tools.hwp_restore({"version": 9}, ctx)
    assert nothing.failed


@pytest.mark.asyncio
async def test_a_blank_document_open_is_written_into(local):
    async with local() as db:
        blank = await hwp_doc.create(db, user_id=USER, title="빈 문서", session_id=SESSION,
                                     data=hwp_doc.blank())
    ctx = ToolContext(user_id=USER, session_id=SESSION, open_artifact={"id": blank.id})
    made = await hwp_tools.hwp_write({"title": "연수 공문", "format": "plain", "body": "본문"}, ctx)
    assert not made.failed and made.trace == {"artifactId": blank.id, "artifactVersion": 2}


def test_a_blank_document_is_written_first_unless_a_search_was_asked_for():
    from app.models.chat import ChatSession, SessionKind
    from app.models.workspace import Artifact, ArtifactKind
    from app.routers import sessions as sessions_router

    tools = {"pptx_write", "pptx_read", "pptx_edit", "web_search"}
    blank = Artifact(id="d1", user_id=USER, kind=ArtifactKind.pptx, title="PowerPoint 문서",
                     data={"text": "", "slides": 1})
    slides = ChatSession(id=SESSION, user_id=USER, kind=SessionKind.slides, artifact_id="d1")
    ask = ("2026년 사이버 보안 위협 전망을 임원 브리핑용 PowerPoint 6장으로 만들어 주세요. "
           "꼭 들어갈 내용: Agentic AI, 랜섬웨어 피해 증가, 공급망 공격 확대.")
    first = sessions_router._blank_write_first
    assert first(blank, tools, ask, slides) == "pptx_write"
    # the person asked for research first: the search runs
    assert first(blank, tools, "최신 자료를 검색해서 " + ask, slides) is None
    # a written deck is read first, not rewritten
    written = Artifact(id="d1", user_id=USER, kind=ArtifactKind.pptx, title="덱",
                       data={"text": "표지", "slides": 3})
    assert first(written, tools, ask, slides) is None
    # a question about a blank chat document is no write
    chat = ChatSession(id=SESSION, user_id=USER, kind=SessionKind.chat)
    assert first(blank, tools, "이 문서는 몇 장이야?", chat) is None


def test_an_untitled_deck_takes_its_covers_title():
    from types import SimpleNamespace

    from app.services import pptx_doc, pptx_edit
    from app.services.tools import office

    kit = SimpleNamespace(prefix="pptx", edit=pptx_edit)
    deck = pptx_doc.deck("2026년 사이버 보안 위협 전망", "정보보호팀",
                         [{"title": "요약", "bullets": ["가"]}])
    named = office._named_by_cover(kit, SimpleNamespace(title="PowerPoint 문서"), deck)
    assert named == "2026년 사이버 보안 위협 전망"
    assert office._named_by_cover(kit, SimpleNamespace(title="임원 브리핑"), deck) is None
    hwp = SimpleNamespace(prefix="hwp", edit=pptx_edit)
    assert office._named_by_cover(hwp, SimpleNamespace(title="한글 문서"), deck) is None
