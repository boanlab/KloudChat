"""An HWP document's versions: the editor saves from the version it loaded, the agent's tools
read before they edit, and whichever lands second is told to reload."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import ArtifactVersion
from app.routers import hwp as hwp_router
from app.routers import workspace
from app.services import hwp_doc, hwpx_edit
from app.services.tools import hwp as hwp_tools
from app.services.tools.base import ToolContext
from app.services.tools.registry import build_tools

USER = User(id="hwp-user", email="hwp@example.test", password_hash="unused", name="H")


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(hwp_tools, "SessionLocal", local)
    app = FastAPI()
    app.include_router(hwp_router.router)
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


@pytest.mark.asyncio
async def test_the_editor_saves_a_new_version_and_a_stale_save_is_refused(stack):
    client, local = stack
    made = await client.post("/artifacts/hwp", json={"title": "공문"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["version"], body["data"]["format"]) == ("hwp", 1, "hwpx")
    first = await client.get(f"/artifacts/{body['id']}/hwp")
    assert first.status_code == 200 and first.headers["x-artifact-version"] == "1"

    edited = hwpx_edit.apply(first.content, [{"op": "replace_text", "id": "p1",
                                              "text": "손으로 쓴 줄"}]).data
    saved = await client.put(f"/artifacts/{body['id']}/hwp?expectedVersion=1", content=edited)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert "손으로 쓴 줄" in saved.json()["data"]["text"]

    stale = await client.put(f"/artifacts/{body['id']}/hwp?expectedVersion=1", content=edited)
    assert stale.status_code == 409
    junk = await client.put(f"/artifacts/{body['id']}/hwp?expectedVersion=2", content=b"nope")
    assert junk.status_code == 422

    # the first version is still there to load and restore
    old = await client.get(f"/artifacts/{body['id']}/hwp?version=1")
    assert old.status_code == 200 and old.content == first.content
    async with local() as db:
        rows = (await db.exec(select(ArtifactVersion))).all()
    assert [r.version for r in rows] == [1]
    restored = await client.post(f"/artifacts/{body['id']}/restore", json={"version": 1})
    assert restored.status_code == 200 and restored.json()["version"] == 3
    again = await client.get(f"/artifacts/{body['id']}/hwp")
    assert again.content == first.content


@pytest.mark.asyncio
async def test_a_listing_card_carries_only_the_top_of_the_text(stack):
    client, _ = stack
    data = hwp_doc.official("긴 공문", "\n".join(f"{n}. 항목 {'가' * 60}" for n in range(1, 40)))
    async with stack[1]() as db:
        await hwp_doc.create(db, user_id=USER.id, title="긴 공문", data=data)
    cards = (await client.get("/artifacts")).json()
    assert cards[0]["kind"] == "hwp" and cards[0]["partial"] is True
    assert 0 < len(cards[0]["data"]["text"]) <= 400


@pytest.mark.asyncio
async def test_an_hwp_artifact_cannot_be_made_without_its_file(stack):
    client, _ = stack
    refused = await client.post("/artifacts", json={"kind": "hwp", "title": "x", "data": {}})
    assert refused.status_code == 422


@pytest.mark.asyncio
async def test_the_agent_writes_reads_then_edits_and_a_hand_save_makes_it_read_again(stack):
    client, _ = stack
    ctx = ToolContext(user_id=USER.id, session_id="s-1")
    nothing = await hwp_tools.hwp_read({}, ctx)
    assert nothing.failed

    written = await hwp_tools.hwp_write(
        {"title": "직원 연수 참석 요청", "format": "official",
         "body": "1. 연수명: [연수명]\n2. 일시: [YYYY. MM. DD.]"}, ctx)
    assert not written.failed, written.content
    assert "[수신]" in written.content and "[연수명]" in written.content
    artifact_id = written.trace["artifactId"]
    assert ctx.hwp["changed"] and written.trace["artifactVersion"] == 1

    unread = ToolContext(user_id=USER.id, session_id="s-1")
    blind = await hwp_tools.hwp_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"}]}, unread)
    assert blind.failed and "hwp_read" in blind.content

    read = await hwp_tools.hwp_read({}, ctx)
    assert "v1" in read.content and "[연수명]" in read.content
    edited = await hwp_tools.hwp_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"},
                 {"op": "fill_blank", "blank": "[없는 칸]", "value": "x"}]}, ctx)
    assert edited.failed and "없습니다" in edited.content
    edited = await hwp_tools.hwp_edit(
        {"ops": [{"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"}],
         "summary": "연수명 채움"}, ctx)
    assert not edited.failed, edited.content
    assert edited.trace == {"artifactId": artifact_id, "artifactVersion": 2}
    assert "남은 빈칸: [수신]" in edited.content

    # the person saves by hand: the agent's numbers are stale until it reads again
    current = await client.get(f"/artifacts/{artifact_id}/hwp")
    hand = hwpx_edit.apply(current.content, [{"op": "fill_blank", "blank": "[수신]",
                                              "value": "각 부서장"}]).data
    saved = await client.put(f"/artifacts/{artifact_id}/hwp?expectedVersion=2", content=hand)
    assert saved.status_code == 200
    stale = await hwp_tools.hwp_edit(
        {"ops": [{"op": "fill_blank", "blank": "[YYYY. MM. DD.]", "value": "2027. 2. 11."}]},
        ctx)
    assert stale.failed and "v3" in stale.content
    await hwp_tools.hwp_read({}, ctx)
    on_top = await hwp_tools.hwp_edit(
        {"ops": [{"op": "fill_blank", "blank": "[YYYY. MM. DD.]", "value": "2027. 2. 11."}]},
        ctx)
    assert not on_top.failed
    final = (await client.get(f"/artifacts/{artifact_id}/hwp")).content
    text = hwpx_edit.plain_text(final)
    assert "각 부서장" in text and "2027. 2. 11." in text and "교원연수" in text


@pytest.mark.asyncio
async def test_hwp_tools_join_the_turn_only_when_asked_for(stack):
    _, local = stack
    async with local() as db:
        plain = {t.name for t in await build_tools(db, USER, web_search=False,
                                                   include_connectors=False)}
        with_doc = {t.name for t in await build_tools(db, USER, web_search=False, hwp=True,
                                                      include_connectors=False)}
        strict = {t.name for t in await build_tools(db, USER, web_search=False, hwp=True,
                                                    strict_local=True)}
        held = {t.name for t in await build_tools(db, USER, web_search=False, hwp=True,
                                                  allowed=["calculate"],
                                                  include_connectors=False)}
    assert not plain & hwp_tools.NAMES
    assert hwp_tools.NAMES <= with_doc and hwp_tools.NAMES <= strict
    assert not held & hwp_tools.NAMES


def test_a_request_names_hwp_in_its_usual_spellings():
    from app.routers.sessions import _ASKS_HWP, _STRICT_LOCAL_TOOL_NAMES

    for text in ("공문을 hwp로 만들어 줘", "한글 문서로 써 줘", "HWPX 파일로", "아래아한글 파일로"):
        assert _ASKS_HWP.search(text), text
    for text in ("한글로 번역해 줘", "whwpx", "한글날 행사"):
        assert not _ASKS_HWP.search(text), text
    assert hwp_tools.NAMES <= _STRICT_LOCAL_TOOL_NAMES


def test_a_document_format_head_shows_blanks_for_fields_not_given():
    data = hwp_doc.official("제목", "1. 본문", {"수신": "각 부서장"})
    outline = hwpx_edit.outline(data)
    assert "수신 | 각 부서장" in outline and "[발신]" in outline and "[시행일]" in outline


def test_a_written_document_is_read_by_the_server_before_the_model_answers():
    from app.models.workspace import Artifact, ArtifactKind
    from app.routers.sessions import _document_preset

    tools = {"hwp_read", "hwp_edit", "hwp_write"}
    written = Artifact(user_id=USER.id, kind=ArtifactKind.hwp, data={"text": "제목  공문"})
    blank = Artifact(user_id=USER.id, kind=ArtifactKind.hwp, data={"text": ""})
    assert _document_preset(written, tools) == ("hwp_read", {})
    # nothing to read yet: the model writes with hwp_write
    assert _document_preset(blank, tools) is None
    assert _document_preset(None, tools) is None
    assert _document_preset(written, {"calculate"}) is None
