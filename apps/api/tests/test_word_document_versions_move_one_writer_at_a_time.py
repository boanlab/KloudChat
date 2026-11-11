"""A Word document's versions: the editor saves from the version it loaded, the agent's tools
read before they edit, and whichever lands second is told to reload. The tools join a turn
only when the chat has a Word document or the request names one."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL
from test_word_edits_keep_each_runs_formatting import gongmun

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, StoredFile
from app.routers import docx as docx_router
from app.routers import workspace
from app.services import docx_doc, docx_edit, files
from app.services.tools import docx as docx_tools
from app.services.tools import hwp as hwp_tools
from app.services.tools.base import ToolContext
from app.services.tools.registry import build_tools

USER = User(id="docx-user", email="docx@example.test", password_hash="unused", name="D")


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(lambda sync: StoredFile.__table__.create(sync))

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(docx_tools, "SessionLocal", local)
    app = FastAPI()
    app.include_router(docx_router.router)
    app.include_router(workspace.router)

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


async def _upload(local, name: str, data: bytes) -> str:
    key = files.write_blob(USER.id, "upload", name, data)
    async with local() as db:
        stored = StoredFile(user_id=USER.id, name=name, size=len(data), storage_key=key)
        db.add(stored)
        await db.commit()
        return stored.id


@pytest.mark.asyncio
async def test_the_editor_saves_a_new_version_and_a_stale_save_is_refused(stack):
    client, _ = stack
    made = await client.post("/artifacts/docx", json={"title": "공문"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["version"], body["data"]["format"]) == ("docx", 1, "docx")
    first = await client.get(f"/artifacts/{body['id']}/docx")
    assert first.status_code == 200 and first.headers["x-artifact-version"] == "1"
    assert first.headers["content-type"].startswith(docx_doc.DOCX_MIME)
    # a blank page is typed in body text
    assert docx_edit.outline(first.content).startswith("p1: (빈 줄)")

    edited = docx_edit.apply(first.content, [{"op": "replace_text", "id": "p1",
                                              "text": "손으로 쓴 줄"}]).data
    saved = await client.put(f"/artifacts/{body['id']}/docx?expectedVersion=1", content=edited)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and "손으로 쓴 줄" in saved.json()["data"]["text"]

    stale = await client.put(f"/artifacts/{body['id']}/docx?expectedVersion=1", content=edited)
    assert stale.status_code == 409
    junk = await client.put(f"/artifacts/{body['id']}/docx?expectedVersion=2", content=b"nope")
    assert junk.status_code == 422 and junk.json()["detail"] == "not_a_docx_file"

    old = await client.get(f"/artifacts/{body['id']}/docx?version=1")
    assert old.status_code == 200 and old.content == first.content
    restored = await client.post(f"/artifacts/{body['id']}/restore", json={"version": 1})
    assert restored.status_code == 200 and restored.json()["version"] == 3
    # an HWP route does not serve it
    assert (await client.get(f"/artifacts/{body['id']}/hwp")).status_code == 404


@pytest.mark.asyncio
async def test_an_uploaded_docx_opens_and_a_legacy_doc_does_not(stack):
    client, local = stack
    file_id = await _upload(local, "2-gongmun.docx", gongmun())
    made = await client.post("/artifacts/docx", json={"fileId": file_id, "sessionId": None})
    assert made.status_code == 201, made.text
    assert made.json()["title"] == "2-gongmun"
    assert "[수신처]" in made.json()["data"]["text"]

    legacy = await _upload(local, "옛 문서.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    refused = await client.post("/artifacts/docx", json={"fileId": legacy})
    assert refused.status_code == 422 and refused.json()["detail"] == "not_a_docx_file"
    bare = await client.post("/artifacts", json={"kind": "docx", "title": "x", "data": {}})
    assert bare.status_code == 422


@pytest.mark.asyncio
async def test_the_agent_writes_reads_then_edits_and_a_hand_save_makes_it_read_again(stack):
    client, _ = stack
    ctx = ToolContext(user_id=USER.id, session_id="s-1")
    nothing = await docx_tools.docx_read({}, ctx)
    assert nothing.failed and "docx_write" in nothing.content

    written = await docx_tools.docx_write(
        {"title": "직원 연수 참석 요청", "format": "official",
         "body": "1. 연수명: [연수명]\n2. 일시: [YYYY. MM. DD.]"}, ctx)
    assert not written.failed, written.content
    assert "[수신]" in written.content and "[연수명]" in written.content
    artifact_id = written.trace["artifactId"]
    assert ctx.docx["changed"] and written.trace["artifactVersion"] == 1
    assert not ctx.hwp

    blind = await docx_tools.docx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"}]},
        ToolContext(user_id=USER.id, session_id="s-1"))
    assert blind.failed and "docx_read" in blind.content

    read = await docx_tools.docx_read({}, ctx)
    assert "v1" in read.content and "[연수명]" in read.content
    edited = await docx_tools.docx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"},
                 {"op": "fill_blank", "blank": "[없는 칸]", "value": "x"}]}, ctx)
    assert edited.failed and "없습니다" in edited.content
    edited = await docx_tools.docx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"}],
         "summary": "연수명 채움"}, ctx)
    assert not edited.failed, edited.content
    assert edited.trace == {"artifactId": artifact_id, "artifactVersion": 2}
    assert "남은 빈칸: [수신]" in edited.content

    # the person saves by hand: the agent's numbers are stale until it reads again
    current = await client.get(f"/artifacts/{artifact_id}/docx")
    hand = docx_edit.apply(current.content, [{"op": "fill_blank", "blank": "[수신]",
                                              "value": "각 부서장"}]).data
    saved = await client.put(f"/artifacts/{artifact_id}/docx?expectedVersion=2", content=hand)
    assert saved.status_code == 200
    stale = await docx_tools.docx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[YYYY. MM. DD.]", "value": "2027. 2. 11."}]},
        ctx)
    assert stale.failed and "v3" in stale.content
    await docx_tools.docx_read({}, ctx)
    on_top = await docx_tools.docx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[YYYY. MM. DD.]", "value": "2027. 2. 11."}]},
        ctx)
    assert not on_top.failed
    text = docx_edit.plain_text((await client.get(f"/artifacts/{artifact_id}/docx")).content)
    assert "각 부서장" in text and "2027. 2. 11." in text and "교원연수" in text


@pytest.mark.asyncio
async def test_word_tools_join_the_turn_only_when_asked_for(stack):
    _, local = stack
    async with local() as db:
        plain = {t.name for t in await build_tools(db, USER, web_search=False,
                                                   include_connectors=False)}
        with_doc = {t.name for t in await build_tools(db, USER, web_search=False, docx=True,
                                                      include_connectors=False)}
        strict = {t.name for t in await build_tools(db, USER, web_search=False, docx=True,
                                                    strict_local=True)}
        held = {t.name for t in await build_tools(db, USER, web_search=False, docx=True,
                                                  allowed=["calculate"],
                                                  include_connectors=False)}
    assert not plain & docx_tools.NAMES
    assert docx_tools.NAMES <= with_doc and docx_tools.NAMES <= strict
    assert not with_doc & hwp_tools.NAMES
    assert not held & docx_tools.NAMES


def test_a_request_names_word_in_its_usual_spellings():
    from app.routers.sessions import _ASKS_DOCX, _ASKS_HWP, _STRICT_LOCAL_TOOL_NAMES

    for text in ("공문을 Word로 작성해 주세요", "워드 문서로 써 줘", "docx 파일로", "MS Word 파일",
                 "a Word document please", "워드로 만들어 줘"):
        assert _ASKS_DOCX.search(text), text
    for text in ("키워드로 검색해 줘", "패스워드 파일", "in a word, no", "한글 문서로", "docxy"):
        assert not _ASKS_DOCX.search(text), text
    assert not _ASKS_HWP.search("공문을 Word로 작성해 주세요")
    assert docx_tools.NAMES <= _STRICT_LOCAL_TOOL_NAMES


def test_the_newest_written_document_is_read_by_the_server_first():
    from app.routers.sessions import _document_preset

    tools = {*docx_tools.NAMES, *hwp_tools.NAMES}
    written = Artifact(user_id=USER.id, kind=ArtifactKind.docx, data={"text": "제목  공문"})
    blank = Artifact(user_id=USER.id, kind=ArtifactKind.docx, data={"text": ""})
    assert _document_preset(written, tools) == ("docx_read", {})
    assert _document_preset(blank, tools) is None
    assert _document_preset(written, hwp_tools.NAMES) is None


def test_the_written_documents_note_names_the_word_tools():
    from app.services.workspace_context import DOCX_NOTE, HWP_NOTE

    assert "docx_read first, then docx_edit" in DOCX_NOTE and "a Word editor" in DOCX_NOTE
    assert "hwp_read first, then hwp_edit" in HWP_NOTE and "an HWP editor" in HWP_NOTE
