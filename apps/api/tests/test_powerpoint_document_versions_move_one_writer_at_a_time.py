"""A PowerPoint document's versions: the editor saves from the version it loaded, the agent's
tools read before they edit, and whichever lands second is told to reload. The tools join a
turn only when the chat has a PowerPoint document or the request names one; a plain 「발표
자료」 or 「PPT 만들어 줘」 stays with the slides surface."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_powerpoint_edits_keep_each_runs_formatting import company
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, StoredFile
from app.routers import pptx as pptx_router
from app.routers import workspace
from app.services import files, pptx_doc, pptx_edit
from app.services.tools import docx as docx_tools
from app.services.tools import pptx as pptx_tools
from app.services.tools.base import ToolContext
from app.services.tools.registry import build_tools

USER = User(id="pptx-user", email="pptx@example.test", password_hash="unused", name="P")


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

    monkeypatch.setattr(pptx_tools, "SessionLocal", local)
    app = FastAPI()
    app.include_router(pptx_router.router)
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
    made = await client.post("/artifacts/pptx", json={"title": "보고"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["version"], body["data"]["format"]) == ("pptx", 1, "pptx")
    assert body["data"]["slides"] == 1
    first = await client.get(f"/artifacts/{body['id']}/pptx")
    assert first.status_code == 200 and first.headers["x-artifact-version"] == "1"
    assert first.headers["content-type"].startswith(pptx_doc.PPTX_MIME)
    assert "s1.1 (제목): (비어 있음)" in pptx_edit.outline(first.content)

    edited = pptx_edit.apply(first.content, [{"op": "replace_text", "id": "s1.1",
                                              "text": "손으로 쓴 제목"}]).data
    saved = await client.put(f"/artifacts/{body['id']}/pptx?expectedVersion=1", content=edited)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and "손으로 쓴 제목" in saved.json()["data"]["text"]

    stale = await client.put(f"/artifacts/{body['id']}/pptx?expectedVersion=1", content=edited)
    assert stale.status_code == 409
    junk = await client.put(f"/artifacts/{body['id']}/pptx?expectedVersion=2", content=b"nope")
    assert junk.status_code == 422 and junk.json()["detail"] == "not_a_pptx_file"

    old = await client.get(f"/artifacts/{body['id']}/pptx?version=1")
    assert old.status_code == 200 and old.content == first.content
    restored = await client.post(f"/artifacts/{body['id']}/restore", json={"version": 1})
    assert restored.status_code == 200 and restored.json()["version"] == 3
    # a Word route does not serve it
    assert (await client.get(f"/artifacts/{body['id']}/docx")).status_code == 404


@pytest.mark.asyncio
async def test_an_uploaded_pptx_opens_and_a_legacy_ppt_does_not(stack):
    client, local = stack
    file_id = await _upload(local, "2-business-deck.pptx", company())
    made = await client.post("/artifacts/pptx", json={"fileId": file_id, "sessionId": None})
    assert made.status_code == 201, made.text
    assert made.json()["title"] == "2-business-deck"
    assert "[사업명]" in made.json()["data"]["text"] and made.json()["data"]["slides"] == 4

    legacy = await _upload(local, "옛 발표.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    refused = await client.post("/artifacts/pptx", json={"fileId": legacy})
    assert refused.status_code == 422 and refused.json()["detail"] == "not_a_pptx_file"
    bare = await client.post("/artifacts", json={"kind": "pptx", "title": "x", "data": {}})
    assert bare.status_code == 422


@pytest.mark.asyncio
async def test_the_agent_writes_reads_then_edits_and_a_hand_save_makes_it_read_again(stack):
    client, _ = stack
    ctx = ToolContext(user_id=USER.id, session_id="s-1")
    nothing = await pptx_tools.pptx_read({}, ctx)
    assert nothing.failed and "pptx_write" in nothing.content

    empty = await pptx_tools.pptx_write({"title": "사업 계획 보고", "slides": []}, ctx)
    assert empty.failed and "slides" in empty.content
    written = await pptx_tools.pptx_write(
        {"title": "사업 계획 보고", "subtitle": "[부서명] · [YYYY. MM. DD.]",
         "slides": [{"title": "추진 배경", "bullets": ["매출 [전년 대비]", "고객 [고객 수]명"]},
                    {"title": "일정", "rows": [["단계", "기간"], ["1단계", "[기간]"]]}]}, ctx)
    assert not written.failed, written.content
    assert "[부서명]" in written.content and "[기간]" in written.content
    artifact_id = written.trace["artifactId"]
    assert ctx.pptx["changed"] and written.trace["artifactVersion"] == 1
    assert not ctx.docx and not ctx.hwp

    blind = await pptx_tools.pptx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[기간]", "value": "1~3월"}]},
        ToolContext(user_id=USER.id, session_id="s-1"))
    assert blind.failed and "pptx_read" in blind.content

    read = await pptx_tools.pptx_read({}, ctx)
    assert "v1" in read.content and "s3.2 (표 2×2)" in read.content
    edited = await pptx_tools.pptx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[기간]", "value": "1~3월"},
                 {"op": "fill_blank", "blank": "[없는 칸]", "value": "x"}]}, ctx)
    assert edited.failed and "없습니다" in edited.content and "add_slide" in edited.content
    edited = await pptx_tools.pptx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[기간]", "value": "1~3월"}],
         "summary": "기간 채움"}, ctx)
    assert not edited.failed, edited.content
    assert edited.trace == {"artifactId": artifact_id, "artifactVersion": 2}
    assert "남은 빈칸: [부서명]" in edited.content

    # the person saves by hand: the agent's numbers are stale until it reads again
    current = await client.get(f"/artifacts/{artifact_id}/pptx")
    hand = pptx_edit.apply(current.content, [{"op": "replace_text", "id": "s2.1",
                                              "text": "추진 배경 (손 편집)"}]).data
    saved = await client.put(f"/artifacts/{artifact_id}/pptx?expectedVersion=2", content=hand)
    assert saved.status_code == 200
    stale = await pptx_tools.pptx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[고객 수]", "value": "1,200"}]}, ctx)
    assert stale.failed and "v3" in stale.content
    await pptx_tools.pptx_read({}, ctx)
    on_top = await pptx_tools.pptx_edit(
        {"ops": [{"op": "fill_blank", "blank": "[고객 수]", "value": "1,200"},
                 {"op": "add_slide", "after": "s3", "title": "다음 단계",
                  "body": "[담당자] 지정"}]}, ctx)
    assert not on_top.failed, on_top.content
    # a slide op moved the numbers: the deck as it is now comes back
    assert "번호가 바뀌었습니다" in on_top.content and "s4.1 (제목): 다음 단계" in on_top.content
    text = pptx_edit.plain_text((await client.get(f"/artifacts/{artifact_id}/pptx")).content)
    assert "추진 배경 (손 편집)" in text and "1,200" in text and "1~3월" in text


@pytest.mark.asyncio
async def test_powerpoint_tools_join_the_turn_only_when_asked_for(stack):
    _, local = stack
    async with local() as db:
        plain = {t.name for t in await build_tools(db, USER, web_search=False,
                                                   include_connectors=False)}
        with_doc = {t.name for t in await build_tools(db, USER, web_search=False, pptx=True,
                                                      include_connectors=False)}
        strict = {t.name for t in await build_tools(db, USER, web_search=False, pptx=True,
                                                    strict_local=True)}
    assert not plain & pptx_tools.NAMES
    assert pptx_tools.NAMES <= with_doc and pptx_tools.NAMES <= strict
    assert not with_doc & docx_tools.NAMES


def test_a_request_names_powerpoint_and_a_plain_deck_request_does_not():
    from app.routers.sessions import _ASKS_DOCX, _ASKS_PPTX, _STRICT_LOCAL_TOOL_NAMES

    for text in ("사업 계획을 PowerPoint로 5장 만들어 줘", "파워포인트 파일로", "pptx 파일로 줘",
                 "PPT 파일로 만들어 주세요", "a Power Point deck", "MS PowerPoint"):
        assert _ASKS_PPTX.search(text), text
    for text in ("발표 자료 만들어 줘", "PPT 만들어 줘", "사업 계획 발표 슬라이드 5장",
                 "pptxy", "appt 파일", "Word로 작성해 줘"):
        assert not _ASKS_PPTX.search(text), text
    assert not _ASKS_DOCX.search("사업 계획을 PowerPoint로 5장 만들어 줘")
    assert pptx_tools.NAMES <= _STRICT_LOCAL_TOOL_NAMES


def test_the_newest_written_deck_is_read_by_the_server_first():
    from app.routers.sessions import _document_preset

    tools = {*pptx_tools.NAMES, *docx_tools.NAMES}
    written = Artifact(user_id=USER.id, kind=ArtifactKind.pptx, data={"text": "추진 배경"})
    blank = Artifact(user_id=USER.id, kind=ArtifactKind.pptx, data={"text": ""})
    assert _document_preset(written, tools) == ("pptx_read", {})
    assert _document_preset(blank, tools) is None
    assert _document_preset(written, docx_tools.NAMES) is None


def test_the_note_names_the_powerpoint_tools_and_keeps_a_template():
    from app.services.workspace_context import PPTX_NOTE

    assert "pptx_read first, then pptx_edit" in PPTX_NOTE and "a PowerPoint editor" in PPTX_NOTE
    assert "company template" in PPTX_NOTE and "set_notes" in PPTX_NOTE


def test_the_edit_schema_names_where_before_what():
    """A model that writes the keys in schema order (DeepSeek) ended a set_cell at `col`."""
    from app.services.tools.docx import TOOLS as DOCX

    for tool in (pptx_tools.PPTX_EDIT, DOCX[1]):
        keys = list(tool.parameters["properties"]["ops"]["items"]["properties"])
        assert keys.index("col") < keys.index("text") and keys.index("at") < keys.index("value")
