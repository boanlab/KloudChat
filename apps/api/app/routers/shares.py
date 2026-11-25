"""Read-only share links; public read returns names only, no owner or walkable ids."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections import OrderedDict
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession, client_ip, current_user, current_viewer
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactKind,
    Project,
    Share,
    ShareScope,
    ShareView,
    StoredFile,
)
from app.schemas.workspace import ShareIn, ShareOut, ShareViewOut
from app.services import (
    design_templates,
    docx_doc,
    hwp_doc,
    message_links,
    office_doc,
    pptx_doc,
    sheet_doc,
    sheet_model,
)
from app.services import files as file_service

router = APIRouter(tags=["shares"])

#: token = entire authorisation
_TOKEN_BYTES = 32

#: repeat opens within window = one visit
_VISIT_WINDOW = timedelta(hours=1)

#: a stored file an artifact body points at (`src`, `posterSrc`, pictures in sections)
_FILE_REF = re.compile(r"/files/([0-9a-f]{32})/content")


def _file_ids(artifact: Artifact) -> set[str]:
    return set(_FILE_REF.findall(json.dumps(artifact.data or {})))


#: office documents: the file itself is the body, served by `/shared/{token}/document`
_OFFICE = {
    ArtifactKind.hwp: hwp_doc.KIND,
    ArtifactKind.docx: docx_doc.KIND,
    ArtifactKind.pptx: pptx_doc.KIND,
    ArtifactKind.xlsx: sheet_doc.KIND,
}


def _office_mime(kind: ArtifactKind, fmt: str) -> str:
    if kind is ArtifactKind.hwp:
        return hwp_doc.HWPX_MIME if fmt == "hwpx" else "application/x-hwp"
    if kind is ArtifactKind.xlsx:
        return sheet_doc.XLSX_MIME
    return docx_doc.DOCX_MIME if kind is ArtifactKind.docx else pptx_doc.PPTX_MIME


def _shared_data(token: str, artifact: Artifact):
    """Body with its stored-file links on the share's own media route (no account needed)."""
    if not artifact.data:
        return artifact.data
    data = artifact.data
    if artifact.kind in _OFFICE:
        # the owner's file-box file it was copied from: an id of theirs, not the reader's
        data = {k: v for k, v in data.items() if k != office_doc.SOURCE_KEY}
        if artifact.kind is ArtifactKind.xlsx:
            # `text` is the search text (formulas, hidden sheets) and `form` names the owner's
            # saved form file: neither is for a reader of the share
            data = {k: v for k, v in data.items() if k not in ("text", "form")}
    wire = json.dumps(data, ensure_ascii=False)
    return json.loads(_FILE_REF.sub(lambda m: f"/shared/{token}/files/{m.group(1)}", wire))


async def _record_view(db: DbSession, share: Share, request: Request, viewer) -> None:
    """View record: signed-in by name/email, anonymous by address."""
    ip = client_ip(request)
    since = utcnow() - _VISIT_WINDOW
    same = (
        (ShareView.viewer_id == viewer.id)
        if viewer is not None
        else (col(ShareView.viewer_id).is_(None), ShareView.ip == ip)
    )
    conditions = (same,) if not isinstance(same, tuple) else same
    open_visit = (
        await db.exec(
            select(ShareView)
            .where(ShareView.share_id == share.id, ShareView.last_at >= since, *conditions)
            .order_by(col(ShareView.last_at).desc())
        )
    ).first()
    if open_visit is not None:
        open_visit.last_at = utcnow()
        open_visit.opens += 1
        db.add(open_visit)
        return

    db.add(
        ShareView(
            share_id=share.id,
            viewer_id=viewer.id if viewer is not None else None,
            viewer_name=(getattr(viewer, "name", "") or "") if viewer is not None else "",
            viewer_email=(getattr(viewer, "email", "") or "") if viewer is not None else "",
            ip=ip,
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )


async def _owned_target(db: DbSession, user, payload: ShareIn):
    if payload.artifact_id:
        row = await db.get(Artifact, payload.artifact_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return row, None
    if payload.session_id:
        row = await db.get(ChatSession, payload.session_id)
        # published bot's conversation not shareable by owner
        if row is None or row.user_id != user.id or row.publication_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return None, row
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nothing_to_share")


@router.post("/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
async def create_share(payload: ShareIn, user: CurrentUser, db: DbSession):
    """New link, or the live one for same (thing, scope)."""
    await _owned_target(db, user, payload)

    existing = (
        await db.exec(
            select(Share).where(
                Share.owner_id == user.id,
                Share.scope == payload.scope,
                col(Share.revoked_at).is_(None),
                Share.artifact_id == payload.artifact_id,
                Share.session_id == payload.session_id,
            )
        )
    ).first()
    if existing is not None:
        return ShareOut.of(existing)

    share = Share(
        token=secrets.token_urlsafe(_TOKEN_BYTES),
        owner_id=user.id,
        artifact_id=payload.artifact_id,
        session_id=payload.session_id,
        scope=payload.scope,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)
    return ShareOut.of(share)


@router.get("/shares", response_model=list[ShareOut])
async def list_shares(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Share)
            .where(Share.owner_id == user.id, col(Share.revoked_at).is_(None))
            .order_by(col(Share.created_at).desc())
        )
    ).all()
    return [ShareOut.of(s) for s in rows]


@router.get("/shares/{share_id}/views", response_model=list[ShareViewOut])
async def list_share_views(share_id: str, user: CurrentUser, db: DbSession):
    """Link visits; owner only, newest first."""
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    rows = (
        await db.exec(
            select(ShareView)
            .where(ShareView.share_id == share_id)
            .order_by(col(ShareView.last_at).desc())
            .limit(200)
        )
    ).all()
    return [ShareViewOut.of(v) for v in rows]


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(share_id: str, user: CurrentUser, db: DbSession):
    """Revoke; row kept."""
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    share.revoked_at = utcnow()
    db.add(share)
    await db.commit()


@router.get("/shared/{token}")
async def read_shared(token: str, request: Request, db: DbSession):
    """Public read; revoked and unknown tokens both 404."""
    share = (await db.exec(select(Share).where(Share.token == token))).first()
    if share is None or share.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    # optional auth by hand: a dependency would 401 `link` readers
    try:
        viewer = await current_user(await _identity(request, db))
    except HTTPException:
        viewer = None

    if share.scope is ShareScope.workspace and viewer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign_in_required")

    share.views += 1
    db.add(share)
    await _record_view(db, share, request, viewer)

    if share.artifact_id:
        artifact = await db.get(Artifact, share.artifact_id)
        if artifact is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        await db.commit()
        return {
            "kind": "artifact",
            "title": artifact.title,
            "artifactKind": artifact.kind.value,
            "data": _shared_data(token, artifact),
            "updatedAt": artifact.updated_at,
        }

    session = await db.get(ChatSession, share.session_id) if share.session_id else None
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    messages = (
        await db.exec(
            select(Message)
            .where(Message.session_id == session.id)
            .order_by(col(Message.created_at), col(Message.id))
        )
    ).all()
    # via session's own artifact_id only, never listing
    artifact = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if artifact is not None and artifact.user_id != session.user_id:
        artifact = None
    # names only: system_prompt/instructions never leave
    agent = await db.get(Agent, session.agent_id) if session.agent_id else None
    project = await db.get(Project, session.project_id) if session.project_id else None
    if project is not None and project.user_id != session.user_id:
        project = None
    shape = design_templates.get(session.render_template_id)
    links = await message_links.run_links(db, session.id)
    await db.commit()
    return {
        "kind": "session",
        "title": session.title,
        "sessionKind": session.kind.value,
        "startedWith": {
            "agent": agent.name if agent is not None else None,
            "project": f"{project.emoji} {project.name}".strip() if project is not None else None,
            # both languages; page picks by UI language
            "format": (
                {"name": shape.name, "nameEn": shape.name_en} if shape is not None else None
            ),
        },
        "messages": [message_links.linked(m, links) for m in messages],
        "artifact": (
            {
                "title": artifact.title,
                "artifactKind": artifact.kind.value,
                "data": _shared_data(token, artifact),
                "updatedAt": artifact.updated_at,
            }
            if artifact is not None
            else None
        ),
        "updatedAt": session.updated_at,
    }


async def _shared_artifact(token: str, request: Request, db: DbSession) -> Artifact | None:
    """The artifact a live share shows (its own, or its conversation's), for the share's
    readers only; not counted as a visit."""
    share = (await db.exec(select(Share).where(Share.token == token))).first()
    if share is None or share.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if share.scope is ShareScope.workspace:
        # `<img>`/`<video>` send no header: the page adds `?t=` as for the owner's own media
        try:
            await current_viewer(request, db)
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="sign_in_required"
            ) from None

    artifact = None
    if share.artifact_id:
        artifact = await db.get(Artifact, share.artifact_id)
    elif share.session_id:
        session = await db.get(ChatSession, share.session_id)
        if session is not None and session.artifact_id:
            artifact = await db.get(Artifact, session.artifact_id)
            if artifact is not None and artifact.user_id != session.user_id:
                artifact = None
    return artifact


@router.get("/shared/{token}/document")
async def read_shared_document(token: str, request: Request, db: DbSession):
    """A shared office document's current file (HWP, Word, PowerPoint), to draw read only or
    download; same reach as the share, not counted. This is the owner's file as it is (formulas,
    hidden sheets and comments included): a shared document is one a reader may keep. The sheet
    page (`/sheet`) shows values only."""
    artifact = await _shared_artifact(token, request, db)
    if artifact is None or artifact.kind not in _OFFICE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        data = office_doc.read(artifact)
    except (OSError, FileNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    kind = _OFFICE[artifact.kind]
    fmt = kind.file_format(data) or kind.editable
    name = office_doc.download_name(artifact.title, fmt)
    return Response(
        content=data,
        media_type=_office_mime(artifact.kind, fmt),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


#: what a shared sheet shows at most: the page draws a plain table, not an editor
SHARED_SHEET_CELLS = 30_000


#: shaped sheets by (artifact, version): a public link must not re-read the workbook on every hit
_SHARED_SHEETS: OrderedDict[tuple[str, int], dict] = OrderedDict()
_SHARED_SHEETS_KEPT = 16


def _shaped_sheet(data: bytes) -> dict:
    model = sheet_model.to_model(data)
    left = SHARED_SHEET_CELLS
    sheets = []
    for item in model["sheets"]:
        if item.get("hidden"):
            continue
        # no formulas leave: a reader sees what the sheet shows (`v`), its formats and layout
        cells = [
            {k: v for k, v in cell.items() if k != "f"}
            | ({"v": "(계산 안 됨)"} if "f" in cell and "v" not in cell else {})
            for cell in item["cells"][:left]
        ]
        left -= len(cells)
        sheets.append({**item, "cells": cells})
    return {"sheets": sheets, "truncated": bool(model["truncated"]) or left <= 0}


@router.get("/shared/{token}/sheet")
async def read_shared_sheet(token: str, request: Request, db: DbSession):
    """A shared sheet's cells (values, number formats, merges, widths), to draw read only as a
    table; same reach as the share, not counted. The file itself is `/document`."""
    artifact = await _shared_artifact(token, request, db)
    if artifact is None or artifact.kind is not ArtifactKind.xlsx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    key = (artifact.id, artifact.version)
    shaped = _SHARED_SHEETS.get(key)
    if shaped is None:
        try:
            shaped = await asyncio.to_thread(_shaped_sheet, office_doc.read(artifact))
        except (OSError, FileNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing"
            ) from None
        except Exception:  # noqa: BLE001 - a package the reader cannot walk is not shown as a sheet
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_xlsx_file"
            ) from None
        _SHARED_SHEETS[key] = shaped
        while len(_SHARED_SHEETS) > _SHARED_SHEETS_KEPT:
            _SHARED_SHEETS.popitem(last=False)
    return {"title": artifact.title, **shaped, "version": artifact.version}


@router.get("/shared/{token}/files/{file_id}")
async def read_shared_file(token: str, file_id: str, request: Request, db: DbSession):
    """A picture or clip the shared artifact shows; same reach as the share, not counted."""
    artifact = await _shared_artifact(token, request, db)
    # only files the shared body names, and only its owner's
    if artifact is None or file_id not in _file_ids(artifact):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != artifact.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        data = file_service.read_blob(stored.storage_key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    media, inline = file_service.served_as(stored.name, stored.mime, data)
    return Response(
        content=data,
        media_type=media,
        headers=file_service.download_headers(media, inline, stored.name),
    )


async def _identity(request: Request, db: DbSession):
    from app.core.deps import current_identity

    return await current_identity(request, db)
