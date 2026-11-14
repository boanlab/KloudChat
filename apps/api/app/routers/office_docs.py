"""Office document artifacts (HWP, Word, PowerPoint) over HTTP: create one (blank, or from an
uploaded file), fetch a version's file for the editor, save the editor's file as a new
version, and give a document with no conversation one to be edited beside. `hwp.py`,
`docx.py` and `pptx.py` each mount one set."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession
from app.models.workspace import Artifact, Project, StoredFile
from app.routers.workspace import _own
from app.schemas.auth import Wire
from app.schemas.workspace import ArtifactOut
from app.services import files as file_service
from app.services import office_doc, office_look


class DocumentCreate(Wire):
    title: str = ""
    session_id: str | None = None
    project_id: str | None = None
    #: an uploaded file to open; none = a blank page
    file_id: str | None = None


class DocumentSession(Wire):
    session_id: str


@dataclass(frozen=True)
class Routes:
    #: `/artifacts/{slug}` and `/artifacts/{id}/{slug}`
    slug: str
    kind: office_doc.Kind
    #: an empty document; the hwp and docx ones take a `office_look.Look`
    blank: Callable[..., bytes]
    blank_title: str
    #: an upload's name ending, dropped from the title
    suffix: re.Pattern[str]
    #: a stored format's media type
    mime: Callable[[str], str]


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="이 문서는 다른 곳에서 이미 저장되었습니다. 최신 판을 불러온 뒤 다시 저장하세요.",
    )


def _too_large() -> HTTPException:
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"file_too_large_{settings.max_upload_mb}mb",
    )


def router_for(spec: Routes) -> APIRouter:
    router = APIRouter(tags=[spec.slug])
    kind = spec.kind

    async def document(db: DbSession, user: CurrentUser, artifact_id: str) -> Artifact:
        artifact = await _own(db, Artifact, "user_id", user, artifact_id)
        if artifact.kind is not kind.kind:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return artifact

    @router.post(
        f"/artifacts/{spec.slug}", response_model=ArtifactOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_document(payload: DocumentCreate, user: CurrentUser, db: DbSession):
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
            title = title or spec.suffix.sub("", stored.name)
        else:
            # a blank document in a project takes its theme and default report template
            look = None
            if project_id and spec.slug in ("hwp", "docx"):
                look = await office_look.for_project(db, user, await db.get(Project, project_id))
            data = spec.blank(look) if look is not None else spec.blank()
            title = title or spec.blank_title
        try:
            artifact = await office_doc.create(
                db, kind, user_id=user.id, title=title, data=data,
                session_id=payload.session_id, project_id=project_id,
                source_file_id=payload.file_id,
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"not_a_{spec.slug}_file",
            ) from None
        return ArtifactOut.of(artifact)

    @router.get(f"/artifacts/{{artifact_id}}/{spec.slug}")
    async def document_file(
        artifact_id: str, user: CurrentUser, db: DbSession, version: int | None = Query(None)
    ):
        """A version's file (the current one by default), for the editor to load."""
        artifact = await document(db, user, artifact_id)
        try:
            if version is None or version == artifact.version:
                data = office_doc.read(artifact)
                version = artifact.version
            else:
                data = await office_doc.read_version(db, artifact, version)
        except (OSError, FileNotFoundError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
        fmt = kind.file_format(data) or kind.editable
        name = office_doc.download_name(artifact.title, fmt)
        return Response(
            content=data,
            media_type=spec.mime(fmt),
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                "Cache-Control": "no-store",
                "X-Artifact-Version": str(version),
            },
        )

    @router.put(f"/artifacts/{{artifact_id}}/{spec.slug}", response_model=ArtifactOut)
    async def save_document(
        artifact_id: str,
        request: Request,
        user: CurrentUser,
        db: DbSession,
        expected_version: int = Query(..., alias="expectedVersion"),
        summary: str = Query("", max_length=200),
    ):
        """The editor's file as the next version; 409 when the agent or another tab saved
        since `expectedVersion`."""
        artifact = await document(db, user, artifact_id)
        limit = settings.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise _too_large()
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                raise _too_large()
        try:
            # the editor names its saves in the person's language; this only when it sent none
            artifact = await office_doc.save(
                db, kind, artifact, bytes(body), expected=expected_version,
                summary=summary or office_doc.EDITOR_SAVE,
            )
        except office_doc.Conflict:
            raise _conflict() from None
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"not_a_{kind.editable}_file",
            ) from None
        return ArtifactOut.of(artifact)

    @router.post(f"/artifacts/{{artifact_id}}/{spec.slug}/session", response_model=ArtifactOut)
    async def attach_session(
        artifact_id: str, payload: DocumentSession, user: CurrentUser, db: DbSession
    ):
        """A document with no conversation (made outside one, or its conversation is gone)
        joins `sessionId`, a new chat to edit it beside; one already in a conversation stays
        there (409), as its agent's turns and history belong to it."""
        artifact = await document(db, user, artifact_id)
        session = await _own(db, ChatSession, "user_id", user, payload.session_id)
        if artifact.session_id and artifact.session_id != session.id:
            current = await db.get(ChatSession, artifact.session_id)
            if current is not None and current.user_id == user.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="already_in_a_conversation"
                )
        artifact.session_id = session.id
        artifact.project_id = artifact.project_id or session.project_id
        db.add(artifact)
        await db.commit()
        await db.refresh(artifact)
        return ArtifactOut.of(artifact)

    return router
