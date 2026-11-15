"""Google Drive with `drive.file`: a picked file comes in as project material or an attachment
(Docs exported to .docx, others downloaded, the upload limit kept), syncs again only when it
changed, the agent reads it, and a document goes to Drive only once the person confirms the
card — every write audited. A turn about Drive is internal material: strict-local where the
administrator keeps internal material there."""

from __future__ import annotations

import io
import json
from datetime import timedelta

import httpx
import pytest
from docx import Document
from integration_fixtures import connected_google, request_stub, world  # noqa: F401
from sqlmodel import select

from app.core.config import settings
from app.models.integrations import IntegrationAction, IntegrationImport
from app.models.user import AuditEvent, utcnow
from app.models.workspace import Artifact, ArtifactKind, Project, StoredFile
from app.routers import integrations as router
from app.services import files as file_service
from app.services.integrations import actions, drive, tools
from app.services.tools.base import ToolContext

DRIVE = "www.googleapis.com/drive/v3/files"
DOC_ID = "1AbCdEfGhIjKlMnOp"


def _docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _doc_meta(modified: str = "2026-11-10T01:00:00Z", **extra) -> dict:
    return {"id": DOC_ID, "name": "회의록 11월", "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": modified, "webViewLink": f"https://docs.google.com/document/d/{DOC_ID}",
            **extra}


def _serve_doc(world, text: str = "11월 운영위원회 회의록. 예산 3건 의결.",  # noqa: F811
               modified: str = "2026-11-10T01:00:00Z") -> None:
    world.provider.on(f"GET {DRIVE}/{DOC_ID}", _doc_meta(modified))
    world.provider.on(f"GET {DRIVE}/{DOC_ID}/export", lambda r: httpx.Response(
        200, content=_docx(text)) if "wordprocessingml" in r.url.params["mimeType"]
        else httpx.Response(400))


async def _project(world) -> Project:  # noqa: F811
    project = Project(user_id=world.member.id, name="운영위")
    world.db.add(project)
    await world.db.commit()
    return project


@pytest.mark.asyncio
async def test_a_picked_google_doc_becomes_project_material_with_its_drive_link(
        world):  # noqa: F811
    await connected_google(world.db, world.member)
    project = await _project(world)
    _serve_doc(world)
    out = await router.drive_import(
        router.DriveImport(file_ids=[f"https://docs.google.com/document/d/{DOC_ID}/edit"],
                           project_id=project.id), world.member, world.db)
    assert out["refused"] == []
    stored = await world.db.get(StoredFile, out["files"][0].id)
    assert stored.name == "회의록 11월.docx" and stored.project_id == project.id
    assert "예산 3건 의결" in stored.text
    assert stored.source_url == f"https://docs.google.com/document/d/{DOC_ID}"
    link = (await world.db.exec(select(IntegrationImport))).one()
    assert link.external_id == DOC_ID and link.modified == "2026-11-10T01:00:00Z"
    audit = (await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == "integration.import"))).one()
    assert audit.event_metadata["files"] == [stored.id]
    listed = await router.drive_imports(world.member, world.db, file_ids=stored.id)
    assert listed[0]["driveId"] == DOC_ID


@pytest.mark.asyncio
async def test_a_file_not_picked_too_large_or_trashed_is_refused_by_name(
        world, monkeypatch):  # noqa: F811
    await connected_google(world.db, world.member)
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    world.provider.on(f"GET {DRIVE}/big000000001", {
        "id": "big000000001", "name": "영상.mp4", "mimeType": "video/mp4",
        "size": str(5 * 1024 * 1024)})
    world.provider.on(f"GET {DRIVE}/trash0000001", {
        "id": "trash0000001", "name": "x.pdf", "mimeType": "application/pdf", "trashed": True})
    # never picked: drive.file cannot see it
    out = await router.drive_import(
        router.DriveImport(file_ids=["big000000001", "trash0000001", "unpicked0001", "../x"]),
        world.member, world.db)
    assert out["files"] == []
    assert [r["error"] for r in out["refused"]] == ["too_large", "not_found", "not_found",
                                                     "bad_id"]
    # the big file's bytes were never fetched
    assert not [c for c in world.provider.calls if c.url.params.get("alt") == "media"]


@pytest.mark.asyncio
async def test_a_brought_in_file_syncs_again_only_when_drive_changed_it(world):  # noqa: F811
    await connected_google(world.db, world.member)
    project = await _project(world)
    _serve_doc(world)
    out = await router.drive_import(router.DriveImport(file_ids=[DOC_ID], project_id=project.id),
                                    world.member, world.db)
    file_id = out["files"][0].id
    exports = len(world.provider.sent(f"GET {DRIVE}/{DOC_ID}/export"))
    same = await router.drive_sync(file_id, world.member, world.db)
    assert same["changed"] is False
    assert len(world.provider.sent(f"GET {DRIVE}/{DOC_ID}/export")) == exports

    _serve_doc(world, "고친 회의록. 예산 4건 의결.", modified="2026-11-12T09:00:00Z")
    moved = await router.drive_sync(file_id, world.member, world.db)
    assert moved["changed"] is True
    stored = await world.db.get(StoredFile, file_id)
    assert "4건" in stored.text and stored.id == file_id
    link = (await world.db.exec(select(IntegrationImport))).one()
    assert link.modified == "2026-11-12T09:00:00Z"


@pytest.mark.asyncio
async def test_the_agent_reads_a_picked_file_and_is_told_what_it_cannot_see(world):  # noqa: F811
    await connected_google(world.db, world.member)
    _serve_doc(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    by_name = {t.name: t for t in drive.tools({"drive", "drive_export"})}
    read = await by_name[drive.READ].run({"file_id": DOC_ID}, ctx)
    assert not read.failed and "예산 3건 의결" in read.content
    world.provider.on(f"GET {DRIVE}", {"files": []})
    found = await by_name[drive.SEARCH].run({"query": "예산'"}, ctx)
    assert found.empty and "드라이브에서 가져오기" in found.content
    sent = world.provider.sent(f"GET {DRIVE}")[0].url.params["q"]
    assert "name contains '예산\\''" in sent
    # an import needs a project conversation
    refused = await by_name[drive.IMPORT].run({"file_ids": [DOC_ID]}, ctx)
    assert refused.failed


@pytest.mark.asyncio
async def test_a_disconnected_person_is_told_to_reconnect_not_shown_an_error(world):  # noqa: F811
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    tool = drive.tools({"drive"})[0]
    result = await tool.run({"query": "x"}, ctx)
    assert result.failed and "설정 › 연결" in result.content
    assert world.provider.calls == []


async def _artifact(world) -> Artifact:  # noqa: F811
    artifact = Artifact(user_id=world.member.id, session_id="s1", kind=ArtifactKind.docx,
                        title="11월 운영위 결과 보고", data={})
    artifact.storage_key = file_service.write_blob(world.member.id, artifact.id, "r.docx",
                                                   _docx("결과 보고"))
    world.db.add(artifact)
    await world.db.commit()
    return artifact


@pytest.mark.asyncio
async def test_saving_to_drive_waits_for_the_card_and_is_audited_once_confirmed(
        world):  # noqa: F811
    await connected_google(world.db, world.member)
    artifact = await _artifact(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1",
                      open_artifact={"id": artifact.id, "version": 1})
    export = {t.name: t for t in drive.tools({"drive_export"})}[drive.EXPORT]
    proposed = await export.run({}, ctx)
    # nothing uploaded; the model is told it waits
    assert not [c for c in world.provider.calls if "upload" in c.url.path]
    assert "Do not say it is done" in proposed.content
    action_id = proposed.trace["confirm"]["actionId"]
    card = await router.get_action(action_id, world.member, world.db)
    assert card["status"] == "pending" and card["payload"]["format"] == "docx"
    assert card["payload"]["title"] == "11월 운영위 결과 보고" and card["editable"] == ["format"]

    def uploaded(request: httpx.Request):
        assert request.url.params["uploadType"] == "multipart"
        assert b"PK" in request.content and "11월 운영위 결과 보고.docx".encode() in request.content
        return {"id": "newfile", "name": "11월 운영위 결과 보고.docx",
                "webViewLink": "https://drive.google.com/file/d/newfile/view"}

    world.provider.on("POST www.googleapis.com/upload/drive/v3/files", uploaded)
    done = await router.confirm_action(action_id, request_stub(), world.member, world.db)
    assert done["status"] == "done"
    assert done["result"]["url"] == "https://drive.google.com/file/d/newfile/view"
    audit = (await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == "integration.write"))).one()
    assert audit.event_metadata["kind"] == "drive.export" and audit.actor_id == world.member.id
    # a second press does not upload twice
    with pytest.raises(Exception) as again:
        await router.confirm_action(action_id, request_stub(), world.member, world.db)
    assert getattr(again.value, "detail", "") == "action_decided"
    assert len(world.provider.sent("POST www.googleapis.com/upload/drive/v3/files")) == 1


@pytest.mark.asyncio
async def test_a_card_can_be_cancelled_expires_and_takes_only_its_own_edits(world):  # noqa: F811
    await connected_google(world.db, world.member)
    artifact = await _artifact(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    export = {t.name: t for t in drive.tools({"drive_export"})}[drive.EXPORT]
    first = (await export.run({"artifact_id": artifact.id}, ctx)).trace["confirm"]["actionId"]
    with pytest.raises(Exception) as refused:
        await router.confirm_action(first, request_stub(), world.member, world.db,
                                    router.ActionDecision(edits={"title": "다른 이름"}))
    assert getattr(refused.value, "detail", "") == "not_editable:title"
    with pytest.raises(Exception) as refused:
        await router.confirm_action(first, request_stub(), world.member, world.db,
                                    router.ActionDecision(edits={"format": "exe"}))
    assert getattr(refused.value, "detail", "") == "bad_format"
    assert (await router.cancel_action(first, world.member, world.db))["status"] == "cancelled"

    second = (await export.run({"artifact_id": artifact.id}, ctx)).trace["confirm"]["actionId"]
    row = await world.db.get(IntegrationAction, second)
    row.created_at = utcnow() - actions.EXPIRES - timedelta(minutes=1)
    world.db.add(row)
    await world.db.commit()
    assert (await router.get_action(second, world.member, world.db))["status"] == "expired"
    with pytest.raises(Exception) as refused:
        await router.confirm_action(second, request_stub(), world.member, world.db)
    assert getattr(refused.value, "detail", "") == "action_expired"
    # someone else's card is not found
    with pytest.raises(Exception) as refused:
        await router.get_action(second, world.admin, world.db)
    assert getattr(refused.value, "detail", "") == "not_found"
    assert not world.provider.sent("POST www.googleapis.com/upload/drive/v3/files")


@pytest.mark.asyncio
async def test_a_card_sends_once_even_when_pressed_twice_at_once_and_not_after_the_feature_goes(
        world):  # noqa: F811
    await connected_google(world.db, world.member)
    artifact = await _artifact(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    export = {t.name: t for t in drive.tools({"drive_export"})}[drive.EXPORT]
    first = (await export.run({"artifact_id": artifact.id}, ctx)).trace["confirm"]["actionId"]
    world.provider.on("POST www.googleapis.com/upload/drive/v3/files", {"id": "f"})
    # another tab read the card while it was still waiting, then this one confirmed it
    async with world.maker() as other:
        stale = await actions.owned(other, world.member, first)
        assert stale.status == "pending"
        done = await router.confirm_action(first, request_stub(), world.member, world.db)
        assert done["status"] == "done"
        with pytest.raises(actions.ActionRefused) as late:
            await actions.confirm(other, world.member, stale)
        assert late.value.code == "action_decided"
    assert len(world.provider.sent("POST www.googleapis.com/upload/drive/v3/files")) == 1
    second = (await export.run({"artifact_id": artifact.id}, ctx)).trace["confirm"]["actionId"]
    world.values["integrations.google.features"] = "drive"
    with pytest.raises(Exception) as refused:
        await router.confirm_action(second, request_stub(), world.member, world.db)
    assert getattr(refused.value, "detail", "") == "feature_off"


@pytest.mark.asyncio
async def test_a_failed_upload_is_said_on_the_card_and_audited(world):  # noqa: F811
    await connected_google(world.db, world.member)
    artifact = await _artifact(world)
    ctx = ToolContext(user_id=world.member.id, session_id="s1")
    export = {t.name: t for t in drive.tools({"drive_export"})}[drive.EXPORT]
    action_id = (await export.run({"artifact_id": artifact.id}, ctx)).trace["confirm"]["actionId"]
    world.provider.on("POST www.googleapis.com/upload/drive/v3/files", (
        403, {"error": {"errors": [{"reason": "storageQuotaExceeded"}]}}))
    failed = await router.confirm_action(action_id, request_stub(), world.member, world.db)
    assert failed["status"] == "failed" and failed["error"] == "forbidden"
    assert (await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == "integration.write_failed"))).one()


@pytest.mark.asyncio
async def test_drive_tools_join_a_turn_only_when_it_is_about_drive(world):  # noqa: F811
    assert await tools.turn_tools(world.db, world.member, "드라이브의 회의록 요약해 줘") == []
    await connected_google(world.db, world.member)
    names = [t.name for t in await tools.turn_tools(world.db, world.member,
                                                     "내 구글 문서 회의록 요약해 줘")]
    assert names == sorted([drive.EXPORT, drive.IMPORT, drive.READ, drive.SEARCH])
    assert await tools.turn_tools(world.db, world.member, "보고서 목차 짜 줘") == []
    # an agent that lists one gets them without the words
    assert await tools.turn_tools(world.db, world.member, "요약", [drive.READ])
    # the administrator turned export off
    world.values["integrations.google.features"] = "drive"
    names = [t.name for t in await tools.turn_tools(world.db, world.member, "드라이브")]
    assert drive.EXPORT not in names and drive.SEARCH in names
    rows = await tools.catalog_rows(world.db, world.member)
    assert {r["group"] for r in rows} == {"Google 드라이브"}
    assert json.dumps(rows, ensure_ascii=False).count("드라이브") >= 3
