"""Google Drive: files picked in Google Picker become project material or attachments, and
an app-made document is saved to Drive once the card is confirmed.

With `drive.file` the app sees only files the person picked or that it made."""

from __future__ import annotations

import re
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.integrations import IntegrationAccount, IntegrationImport
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactKind, Project, StoredFile
from app.services.integrations import accounts, actions, common, config
from app.services.integrations.google import Google
from app.services.integrations.http import ProviderError
from app.services.tools.base import Tool, ToolContext, ToolResult

SEARCH = "drive_search"
READ = "drive_read_file"
IMPORT = "drive_import_to_project"
EXPORT = "drive_export"

#: files one import brings at most
IMPORT_MAX = 20
#: characters of a file the agent reads in one call
READ_CHARS = 30_000

_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_LINK = re.compile(r"/(?:d|folders)/([A-Za-z0-9_-]{10,200})|[?&]id=([A-Za-z0-9_-]{10,200})")


def file_id(raw: Any) -> str:
    """A Drive id from an id or a Drive/Docs link; `ValueError("bad_id")` otherwise."""
    text = str(raw or "").strip()
    if _ID.match(text):
        return text
    found = _LINK.search(text)
    if found:
        return found.group(1) or found.group(2)
    raise ValueError("bad_id")


def limit_bytes() -> int:
    return settings.max_upload_mb * 1024 * 1024


async def bring_in(db: AsyncSession, user: User, row: IntegrationAccount, ids: list[str], *,
                   project_id: str | None = None, session_id: str | None = None
                   ) -> tuple[list[StoredFile], list[dict[str, str]]]:
    """Picked files stored as uploads are, each remembered for syncing; `(stored, refused)`
    where `refused` says which file failed and why."""
    from app.routers.workspace import store_bytes

    if project_id:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise ProviderError("project_not_found", 404)
    google = Google(db, row)
    stored: list[StoredFile] = []
    refused: list[dict[str, str]] = []
    for raw in list(dict.fromkeys(ids))[:IMPORT_MAX]:
        try:
            drive_id = file_id(raw)
            meta = await google.file(drive_id)
            if meta.get("trashed"):
                raise ProviderError("not_found", 404)
            data, name = await google.content(meta, limit_bytes())
        except ValueError:
            refused.append({"id": str(raw)[:200], "error": "bad_id"})
            continue
        except ProviderError as exc:
            if exc.code == "reconnect_required":
                raise
            refused.append({"id": str(raw)[:200], "error": exc.code})
            continue
        link = str(meta.get("webViewLink") or "")[:500]
        made = await store_bytes(db, user.id, name, None, data, project_id=project_id,
                                 session_id=None if project_id else session_id,
                                 source_url=link or None)
        made.origin = "upload"
        db.add(IntegrationImport(user_id=user.id, file_id=made.id, project_id=project_id,
                                 external_id=drive_id, mime=str(meta.get("mimeType") or "")[:200],
                                 modified=str(meta.get("modifiedTime") or "")[:40],
                                 source_url=link))
        stored.append(made)
    if stored:
        accounts.audit(db, user.id, "integration.import", config.GOOGLE,
                       detail=f"{len(stored)}",
                       metadata={"files": [f.id for f in stored], "project": project_id or "",
                                 "refused": len(refused)})
    await db.commit()
    return stored, refused


async def resync(db: AsyncSession, user: User, row: IntegrationAccount, stored: StoredFile
                 ) -> tuple[StoredFile, bool]:
    """A brought-in file read again from Drive when it changed there; `(file, changed)`."""
    from app.routers.workspace import store_bytes

    link = (await db.exec(select(IntegrationImport).where(
        IntegrationImport.user_id == user.id, IntegrationImport.file_id == stored.id))).first()
    if link is None:
        raise ProviderError("not_imported", 404)
    google = Google(db, row)
    meta = await google.file(link.external_id)
    if meta.get("trashed"):
        raise ProviderError("not_found", 404)
    modified = str(meta.get("modifiedTime") or "")
    if modified and modified == link.modified:
        link.synced_at = utcnow()
        db.add(link)
        await db.commit()
        return stored, False
    data, name = await google.content(meta, limit_bytes())
    stored = await store_bytes(db, user.id, name, None, data, stored=stored)
    link.modified = modified[:40]
    link.synced_at = utcnow()
    db.add(link)
    await db.commit()
    return stored, True


async def imports_of(db: AsyncSession, user: User, file_ids: list[str]) -> list[dict[str, Any]]:
    if not file_ids:
        return []
    rows = (await db.exec(select(IntegrationImport).where(
        IntegrationImport.user_id == user.id,
        col(IntegrationImport.file_id).in_(file_ids)))).all()
    return [{"fileId": r.file_id, "driveId": r.external_id, "url": r.source_url,
             "modified": r.modified, "syncedAt": r.synced_at} for r in rows]


def _line(meta: dict[str, Any]) -> str:
    kind = str(meta.get("mimeType") or "").rsplit(".", 1)[-1].rsplit("/", 1)[-1]
    when = str(meta.get("modifiedTime") or "")[:10]
    return f"- {common.clip(meta.get('name'), 160)} ({kind}, {when}) [id: {meta.get('id', '')}]"


# ── agent tools ──────────────────────────────────────────────────────────


async def _search(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = common.clip(arguments.get("query"), 100)

    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        found = await Google(db, row).search(query)
        if not found:
            return ToolResult(
                content=("찾은 파일이 없습니다. 이 앱은 사람이 Google Picker로 고른 파일만 볼 수 "
                         "있습니다. 필요한 파일을 「+ › 드라이브에서 가져오기」로 골라 달라고 "
                         "안내하세요."), detail="파일 없음", empty=True)
        return ToolResult(content="\n".join([f"드라이브 파일 {len(found)}개",
                                             *(_line(f) for f in found)]),
                          detail=f"파일 {len(found)}개")

    return await common.with_account(ctx, config.GOOGLE, "drive", work)


async def _read(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    try:
        drive_id = file_id(arguments.get("file_id"))
    except ValueError:
        return ToolResult(content="오류: file_id 는 드라이브 파일 ID나 링크여야 합니다.",
                          failed=True)

    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        from app.services import files as file_service

        google = Google(db, row)
        meta = await google.file(drive_id)
        data, name = await google.content(meta, limit_bytes())
        text = await file_service.text_of(name, file_service.detected_mime(name, None, data),
                                          data)
        if not text.strip():
            return ToolResult(content=f"「{name}」에서 글을 읽지 못했습니다.", detail="글 없음",
                              empty=True)
        cut = len(text) > READ_CHARS
        return ToolResult(
            content=f"「{name}」 ({meta.get('webViewLink', '')})\n\n{text[:READ_CHARS]}"
            + ("\n\n(뒷부분 생략)" if cut else ""),
            detail=f"{len(text):,}자" + (" (일부)" if cut else ""))

    return await common.with_account(ctx, config.GOOGLE, "drive", work)


async def _import(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if not ctx.project_id:
        return ToolResult(content="오류: 이 대화는 프로젝트에 속하지 않아 프로젝트 자료로 넣을 수 "
                          "없습니다.", failed=True)
    ids = arguments.get("file_ids")
    ids = ids if isinstance(ids, list) else [ids]

    async def work(db: AsyncSession, row: IntegrationAccount) -> ToolResult:
        user = await db.get(User, ctx.user_id)
        if user is None:
            return common.failure("not_connected")
        stored, refused = await bring_in(db, user, row, [str(i) for i in ids if i],
                                         project_id=ctx.project_id)
        lines = [f"프로젝트 자료로 넣은 파일 {len(stored)}개: "
                 + ", ".join(f"「{f.name}」" for f in stored)]
        if refused:
            lines.append("넣지 못한 파일: " + ", ".join(f"{r['id']} ({r['error']})"
                                                  for r in refused))
        return ToolResult(content="\n".join(lines), detail=f"{len(stored)}개 넣음",
                          failed=not stored)

    return await common.with_account(ctx, config.GOOGLE, "drive", work)


#: what an artifact may be saved to Drive as
_FORMATS = {
    ArtifactKind.report: ("docx", "pdf", "hwpx", "md"),
    ArtifactKind.html: ("docx", "pdf", "pptx", "md"),
    ArtifactKind.deck: ("pptx", "pdf", "md"),
    ArtifactKind.docx: ("docx",),
    ArtifactKind.pptx: ("pptx",),
    ArtifactKind.hwp: ("hwpx",),
}
_MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "hwpx": "application/hwp+zip",
    "md": "text/markdown",
}


async def _artifact_for(db: AsyncSession, ctx: ToolContext, wanted: str) -> Artifact | None:
    ids = [wanted] if wanted else []
    if ctx.open_artifact.get("id"):
        ids.append(str(ctx.open_artifact["id"]))
    for artifact_id in ids:
        found = await db.get(Artifact, artifact_id)
        if found is not None and found.user_id == ctx.user_id:
            return found
    if not ctx.session_id:
        return None
    return (await db.exec(select(Artifact).where(
        Artifact.user_id == ctx.user_id, Artifact.session_id == ctx.session_id,
        col(Artifact.kind).in_(list(_FORMATS))).order_by(
        col(Artifact.updated_at).desc()))).first()


async def _export(arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    async def work(db: AsyncSession, _row: IntegrationAccount) -> ToolResult:
        artifact = await _artifact_for(db, ctx, common.clip(arguments.get("artifact_id"), 40))
        if artifact is None or artifact.kind not in _FORMATS:
            return ToolResult(content="오류: 드라이브에 저장할 문서를 찾지 못했습니다. 먼저 문서를 "
                              "만들거나 여세요.", failed=True)
        formats = _FORMATS[artifact.kind]
        wanted = str(arguments.get("format") or "").lower().lstrip(".")
        fmt = wanted if wanted in formats else formats[0]
        payload = {"artifactId": artifact.id, "title": common.clip(artifact.title, 200)
                   or "문서", "format": fmt, "formats": list(formats)}
        return await actions.propose(
            ctx, config.GOOGLE, "drive.export", payload,
            f"드라이브 저장 제안: 「{payload['title']}」을 .{fmt} 파일로 내 드라이브에 올립니다.")

    return await common.with_account(ctx, config.GOOGLE, "drive_export", work)


async def _run_export(db: AsyncSession, _user: User, row: IntegrationAccount,
                      payload: dict[str, Any]) -> dict[str, Any]:
    from app.routers.workspace import artifact_file
    from app.services import office_doc

    artifact = await db.get(Artifact, str(payload.get("artifactId") or ""))
    if artifact is None or artifact.user_id != row.user_id:
        raise ValueError("artifact_missing")
    fmt = str(payload.get("format") or "")
    if artifact.kind in (ArtifactKind.docx, ArtifactKind.pptx, ArtifactKind.hwp):
        data = office_doc.read(artifact)
        name = office_doc.download_name(artifact.title or "문서", fmt)
    else:
        data, name = await artifact_file(artifact, fmt)
    if len(data) > limit_bytes():
        raise ProviderError("too_large", 413)
    made = await Google(db, row).upload(name, _MIMES.get(fmt, "application/octet-stream"),
                                        data)
    return {"id": made.get("id"), "url": made.get("webViewLink"), "label": made.get("name")}


def _edit_format(payload: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    fmt = str(edits.get("format") or payload.get("format") or "")
    if fmt not in (payload.get("formats") or []):
        raise ValueError("bad_format")
    return {**payload, "format": fmt}


actions.register("drive.export", actions.Executor(
    provider=config.GOOGLE, feature="drive_export", editable=frozenset({"format"}),
    run=_run_export, edit=_edit_format))


def tools(features: set[str]) -> list[Tool]:
    """The Drive tools this person's grant and the administrator allow."""
    out: list[Tool] = []
    if "drive" in features:
        out += [
            Tool(
                name=SEARCH,
                description=(
                    "Find Google Drive files the person has given this app (picked in Google "
                    "Picker, or saved by the app). The app cannot see the rest of their Drive; "
                    "if nothing is found, ask them to pick the file with + › 드라이브에서 "
                    "가져오기."),
                parameters={"type": "object", "properties": {
                    "query": {"type": "string", "description": "Words in the file name"}}},
                run=_search, label="드라이브 파일 찾는 중", title="드라이브 파일 찾기",
                source=config.GOOGLE, wants_context=True,
            ),
            Tool(
                name=READ,
                description=(
                    "Read the text of a Drive file the app can open (id or link): Google Docs, "
                    "Sheets and Slides are exported, PDF and office files downloaded."),
                parameters={"type": "object", "properties": {
                    "file_id": {"type": "string", "description": "Drive file id or link"}},
                    "required": ["file_id"]},
                run=_read, label="드라이브 파일 읽는 중", title="드라이브 파일 읽기",
                source=config.GOOGLE, wants_context=True,
            ),
            Tool(
                name=IMPORT,
                description=(
                    "Add Drive files (ids or links the app can open) to this conversation's "
                    "project as material, with their Drive link; only in a project "
                    "conversation."),
                parameters={"type": "object", "properties": {
                    "file_ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["file_ids"]},
                run=_import, label="프로젝트 자료로 넣는 중", title="프로젝트 자료로 넣기",
                read_only=False, source=config.GOOGLE, wants_context=True,
            ),
        ]
    if "drive_export" in features:
        out.append(Tool(
            name=EXPORT,
            description=(
                "Propose saving a document made in this conversation (the open one by default) "
                "to the person's Google Drive as a file. Nothing is uploaded until they confirm "
                "the card this shows."),
            parameters={"type": "object", "properties": {
                "artifact_id": {"type": "string", "description": "Optional; default the open "
                                "or newest document"},
                "format": {"type": "string", "enum": ["docx", "pptx", "pdf", "hwpx", "md"]}}},
            run=_export, label="드라이브 저장 준비 중", title="드라이브 저장 제안",
            read_only=False, source=config.GOOGLE, wants_context=True,
        ))
    return out
