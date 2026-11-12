"""HWP document artifacts: create one (blank, or from an uploaded `.hwp`/`.hwpx`), fetch a
version's file for the editor, and save the editor's file as a new version."""

from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession
from app.models.workspace import Artifact, ArtifactKind, Project, StoredFile
from app.routers.workspace import _own
from app.schemas.auth import Wire
from app.schemas.workspace import ArtifactOut
from app.services import files as file_service
from app.services import hwp_doc

router = APIRouter(tags=["hwp"])


class HwpCreate(Wire):
    title: str = ""
    session_id: str | None = None
    project_id: str | None = None
    #: an uploaded `.hwp`/`.hwpx` to open; none = a blank page
    file_id: str | None = None


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="이 문서는 다른 곳에서 이미 저장되었습니다. 최신 판을 불러온 뒤 다시 저장하세요.",
    )


async def _document(db: DbSession, user: CurrentUser, artifact_id: str) -> Artifact:
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.hwp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return artifact


@router.post("/artifacts/hwp", response_model=ArtifactOut, status_code=status.HTTP_201_CREATED)
async def create_hwp(payload: HwpCreate, user: CurrentUser, db: DbSession):
    project_id = payload.project_id
    if payload.session_id:
        session = await _own(db, ChatSession, "user_id", user, payload.session_id)
        project_id = project_id or session.project_id
    if project_id:
        await _own(db, Project, "user_id", user, project_id)
    title = payload.title
    if payload.file_id:
        stored = await _own(db, StoredFile, "user_id", user, payload.file_id)
        try:
            data = file_service.read_blob(stored.storage_key)
        except OSError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
        title = title or re.sub(r"\.hwpx?$", "", stored.name, flags=re.I)
    else:
        data = hwp_doc.blank()
        title = title or "새 한글 문서"
    try:
        artifact = await hwp_doc.create(
            db, user_id=user.id, title=title, data=data,
            session_id=payload.session_id, project_id=project_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_hwp_file"
        ) from None
    return ArtifactOut.of(artifact)


@router.get("/artifacts/{artifact_id}/hwp")
async def hwp_file(
    artifact_id: str, user: CurrentUser, db: DbSession, version: int | None = Query(None)
):
    """A version's file (the current one by default), for the editor to load."""
    artifact = await _document(db, user, artifact_id)
    try:
        if version is None or version == artifact.version:
            data = hwp_doc.read(artifact)
            version = artifact.version
        else:
            data = await hwp_doc.read_version(db, artifact, version)
    except (OSError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    fmt = hwp_doc.file_format(data) or "hwpx"
    name = hwp_doc.download_name(artifact.title, fmt)
    return Response(
        content=data,
        media_type=hwp_doc.HWPX_MIME if fmt == "hwpx" else "application/x-hwp",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
            "X-Artifact-Version": str(version),
        },
    )


@router.put("/artifacts/{artifact_id}/hwp", response_model=ArtifactOut)
async def save_hwp(
    artifact_id: str,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    expected_version: int = Query(..., alias="expectedVersion"),
    summary: str = Query("", max_length=200),
):
    """The editor's HWPX as the next version; 409 when the agent or another tab saved
    since `expectedVersion`."""
    artifact = await _document(db, user, artifact_id)
    limit = settings.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file_too_large_{settings.max_upload_mb}mb",
        )
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"file_too_large_{settings.max_upload_mb}mb",
            )
    try:
        artifact = await hwp_doc.save(
            db, artifact, bytes(body), expected=expected_version,
            summary=summary or "편집기에서 저장",
        )
    except hwp_doc.Conflict:
        raise _conflict() from None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_hwpx_file"
        ) from None
    return ArtifactOut.of(artifact)
