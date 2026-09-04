"""Projects, files, artifacts, skills, memories, agents.

All six are the same shape: owned by one user, CRUD, no sharing. Connectors are
separate because MCP gives them behaviour of their own. A message's rating is
here for the same reason — it is one owned row edited in place, and none of the
streaming machinery next door has anything to do with it.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, tuple_
from sqlmodel import col, delete, select, update

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentViewer, DbSession
from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User, UserRole, utcnow
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    DesignSystem,
    Memory,
    Project,
    Skill,
    SkillSource,
    StoredFile,
    Template,
    Visibility,
)
from app.schemas.chat import MessageOut, MessageRatingIn
from app.schemas.workspace import (
    AgentIn,
    AgentOut,
    ArtifactIn,
    ArtifactOut,
    ArtifactPatch,
    ArtifactRestore,
    ArtifactVersionDetailOut,
    ArtifactVersionOut,
    BlockImage,
    BlockRewrite,
    BulkDelete,
    DesignExtractIn,
    DesignExtractOut,
    DesignSystemIn,
    DesignSystemOut,
    DesignTemplateOut,
    DesignTemplateUsageOut,
    DiagramPicture,
    FileOut,
    KnowledgeUrl,
    MemoryIn,
    MemoryOut,
    OpenedDocument,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    PromptTemplateOut,
    SectionFactCheck,
    SectionImage,
    SectionRewrite,
    SkillIn,
    SkillOut,
    SlideFactCheck,
    SlideImage,
    SlideRewrite,
    StoreSkillOut,
    TemplateIn,
    TemplateOut,
    ToolCatalogOut,
)
from app.services import (
    critique,
    deck_export,
    design_extract,
    design_templates,
    factcheck,
    hangul,
    hwpx_import,
    index_client,
    lint,
    page_export,
    pictures,
    printing,
    prompt_templates,
    report_export,
    richtext,
    settings_store,
    starter,
)
from app.services import deck as deck_service
from app.services import design as design_service
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import page as page_service
from app.services import report as report_service
from app.services.credits import charge_for_tokens, has_headroom, record_units, settle
from app.services.tools import builtin as builtin_tools
from app.services.tools import registry as tool_registry

log = logging.getLogger(__name__)
router = APIRouter(tags=["workspace"])


def _slug(name: str) -> str:
    """ASCII slug where possible; Korean names keep their characters rather
    than collapsing to an empty string.
    """
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


async def _claim_slug(
    db: DbSession, owner_id: str, wanted: str, *, except_id: str | None = None
) -> str:
    """The slug an agent may have: slugified, and no other agent of this owner's.

    A handle that four agents share is not a handle. Nothing checked it — not
    the column, not the route, not the form — and the form's value was not even
    sent, so 회의록 정리 typed four times was @회의록-정리 four times. The
    database now refuses the duplicate too (0038); this is the check that turns
    that refusal into a sentence before the write.
    """
    slug = _slug(wanted)
    rows = (await db.exec(select(Agent).where(Agent.owner_id == owner_id))).all()
    if any(row.slug == slug and row.id != except_id for row in rows):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_taken")
    return slug


async def _own(db: DbSession, model, owner_field: str, user: User, item_id: str):
    row = await db.get(model, item_id)
    if row is None or getattr(row, owner_field) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


async def _owned_many(db: DbSession, model, owner_field: str, user: User, ids: list[str]):
    """The rows among `ids` this account actually owns.

    Silent about the rest rather than 404: a selection made a minute ago can
    name something already deleted in another tab, and refusing the whole
    request over it would make a list that is nearly right unusable. The count
    that comes back says what really went.
    """
    if not ids:
        return []
    rows = (
        await db.exec(
            select(model).where(col(model.id).in_(ids), getattr(model, owner_field) == user.id)
        )
    ).all()
    return list(rows)


async def _validate_skill_ids(
    db: DbSession,
    user: User,
    skill_ids: list[str] | None,
    *,
    grandfathered: set[str] | None = None,
) -> None:
    """Every reference is installed, owned, and unique.

    Project links are suggestions and agent links are permissions, but neither
    may become a cross-account object reference.
    """
    if skill_ids is None:
        return
    if len(skill_ids) != len(set(skill_ids)):
        raise HTTPException(status_code=422, detail="duplicate_skill_ids")
    if not skill_ids:
        return
    rows = (
        await db.exec(
            select(Skill).where(
                Skill.owner_id == user.id,
                col(Skill.id).in_(skill_ids),
                Skill.enabled == True,  # noqa: E712
            )
        )
    ).all()
    valid = {row.id for row in rows}
    invalid = set(skill_ids) - valid - (grandfathered or set())
    if invalid:
        raise HTTPException(status_code=422, detail="invalid_skill_ids")


async def _validate_tool_names(
    db: DbSession,
    user: User,
    names: list[str] | None,
    *,
    grandfathered: set[str] | None = None,
) -> None:
    if names is None:
        return
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="duplicate_tool_names")
    known = {str(row["name"]) for row in await tool_registry.tool_catalog(db, user)}
    unknown = sorted(set(names) - known - (grandfathered or set()))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown_tools:{','.join(unknown)}")


# ══ projects ═══════════════════════════════════════════════════════════


async def _validate_design_system_id(db: DbSession, user: User, design_id: str | None) -> None:
    """The look must be one the caller can actually see.

    An id from another account would otherwise attach that account's design to
    this project — readable through every artifact it produces afterwards.
    """
    if design_id is None:
        return
    row = await db.get(DesignSystem, design_id)
    if row is None or (row.owner_id != user.id and not row.shared):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design_system_not_found")


def _validated_render_templates(raw: dict[str, str] | None) -> dict[str, str] | None:
    """The formats this project starts its work in, refused rather than trimmed.

    The rule `sessions._resolved_template_id` applies to the composer's pick,
    applied a surface at a time: an unplaceable id is an error, not a key quietly
    dropped.

    An empty value is that surface leaving the map — the built-in track.
    """
    if raw is None:
        return None
    chosen: dict[str, str] = {}
    for surface, template_id in raw.items():
        if not template_id:
            continue
        template = design_templates.get(template_id)
        if template is None or template.kind not in design_templates.HTML_KINDS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
            )
        if template.surface.value != surface:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="design_template_surface_mismatch",
            )
        chosen[template.surface.value] = template.id
    return chosen


def _validated_starting_format(template_id: str, kind: str) -> str:
    """The 서식 a starting point carries, refused rather than stored and ignored.

    Same rule as `_validated_render_templates`, for one id: a shape that does
    not exist, or one this surface cannot wear, is an error. Stored anyway it
    would be dropped silently at render time, and the person who attached it
    would find their 시작점 producing the default shape with nothing saying why.

    Empty is allowed and is the common answer — a job with no fixed shape lets
    the surface choose one from the subject.
    """
    if not template_id:
        return ""
    template = design_templates.get(template_id)
    if template is None or template.kind not in design_templates.HTML_KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    if template.surface.value != kind:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="design_template_surface_mismatch",
        )
    return template.id


async def _project_out(db: DbSession, project: Project) -> ProjectOut:
    files = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.project_id == project.id)
            .order_by(col(StoredFile.created_at).desc())
        )
    ).all()
    session_ids = (
        await db.exec(select(ChatSession.id).where(ChatSession.project_id == project.id))
    ).all()
    return ProjectOut.of(project, list(files), list(session_ids))


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(col(Project.updated_at).desc())
        )
    ).all()
    return [await _project_out(db, p) for p in rows]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectIn, user: CurrentUser, db: DbSession):
    await _validate_skill_ids(db, user, payload.skill_ids)
    await _validate_design_system_id(db, user, payload.design_system_id)
    fields = payload.model_dump()
    fields["render_templates"] = _validated_render_templates(fields["render_templates"])
    project = Project(user_id=user.id, **fields)
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return await _project_out(db, project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: CurrentUser, db: DbSession):
    return await _project_out(db, await _own(db, Project, "user_id", user, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: str, payload: ProjectPatch, user: CurrentUser, db: DbSession):
    project = await _own(db, Project, "user_id", user, project_id)
    changes = payload.model_dump(exclude_unset=True)
    if "skill_ids" in changes:
        await _validate_skill_ids(
            db,
            user,
            changes["skill_ids"],
            grandfathered=set(project.skill_ids or []),
        )
    if "design_system_id" in changes:
        await _validate_design_system_id(db, user, changes["design_system_id"])
    if "render_templates" in changes:
        changes["render_templates"] = _validated_render_templates(changes["render_templates"])
    for field, value in changes.items():
        setattr(project, field, value)
    project.updated_at = utcnow()
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return await _project_out(db, project)


async def _remove_project(db: DbSession, project: Project) -> None:
    """Everything one project takes with it. Uncommitted.

    Shared by the single and bulk routes so the two cannot answer differently
    about what a delete means.
    """
    # Knowledge files go with the project; sessions are detached, not deleted.
    for stored in (
        await db.exec(select(StoredFile).where(StoredFile.project_id == project.id))
    ).all():
        file_service.delete_blob(stored.storage_key)
        await db.delete(stored)
    for session in (
        await db.exec(select(ChatSession).where(ChatSession.project_id == project.id))
    ).all():
        session.project_id = None
        db.add(session)
    await db.delete(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser, db: DbSession):
    await _remove_project(db, await _own(db, Project, "user_id", user, project_id))
    await db.commit()


@router.post("/projects/delete")
async def delete_projects(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Several at once. Returns how many actually went."""
    rows = await _owned_many(db, Project, "user_id", user, payload.ids)
    for row in rows:
        await _remove_project(db, row)
    await db.commit()
    return {"deleted": len(rows)}


# ══ files ══════════════════════════════════════════════════════════════


@router.get("/files", response_model=list[FileOut])
async def list_files(
    user: CurrentUser, db: DbSession, project_id: str | None = None, session_id: str | None = None
):
    query = select(StoredFile).where(StoredFile.user_id == user.id)
    if project_id:
        query = query.where(StoredFile.project_id == project_id)
    if session_id:
        query = query.where(StoredFile.session_id == session_id)
    rows = (await db.exec(query.order_by(col(StoredFile.created_at).desc()))).all()
    return [FileOut.of(f) for f in rows]


@router.post("/files", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    session_id: str | None = Form(None),
):
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file_too_large_{settings.max_upload_mb}mb",
        )
    if project_id:
        await _own(db, Project, "user_id", user, project_id)

    stored = StoredFile(
        user_id=user.id,
        project_id=project_id,
        session_id=session_id,
        name=file_service.safe_name(file.filename or "file"),
        size=len(data),
        mime=file.content_type or "",
    )
    stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name, data)

    # Extraction failure is recorded, not raised — the upload itself succeeded.
    try:
        stored.text = await file_service.text_of(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001
        log.info("extraction failed for %s: %s", stored.name, exc)
        stored.error = str(exc)

    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return FileOut.of(stored)


@router.post(
    "/projects/{project_id}/knowledge/url",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_url(project_id: str, payload: KnowledgeUrl, user: CurrentUser, db: DbSession):
    """Read a web page now and retain that snapshot as project knowledge."""
    await _own(db, Project, "user_id", user, project_id)
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_url")
    backends = await settings_store.tools_config()
    if not backends.fetch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="fetch_unavailable"
        )
    text = await builtin_tools.scrape(backends.fetch, url)
    if not text.strip():
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="page_unreadable")
    stored = StoredFile(
        user_id=user.id,
        project_id=project_id,
        name=_page_name(url),
        size=len(text.encode()),
        mime="text/markdown",
        source_url=url,
        text=text,
        tokens=file_service.estimate_tokens(text),
    )
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return FileOut.of(stored)


@router.get("/files/{file_id}/content")
async def download_file(file_id: str, user: CurrentViewer, db: DbSession):
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    try:
        data = file_service.read_blob(stored.storage_key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    # `inline` for anything with a player or viewer: `attachment` makes an
    # `<audio>` or `<img>` a download prompt in some browsers and a blank frame
    # in others. Every generated clip is served from here.
    mime = stored.mime or "application/octet-stream"
    inline = mime.startswith(("image/", "audio/", "video/")) or mime == "application/pdf"
    # RFC 5987: a raw non-ASCII filename in this header is a UnicodeEncodeError
    # inside starlette, i.e. a 500 on download.
    filename = quote(stored.name)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": (
                f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{filename}"
            )
        },
    )


@router.post("/files/{file_id}/open-as-document", response_model=OpenedDocument)
async def open_file_as_document(file_id: str, user: CurrentUser, db: DbSession):
    """올린 `.hwpx` 를 편집할 수 있는 문서로 연다.

    The half that was missing. This server has been able to *write* `.hwpx`
    since the report exporter learnt OWPML, and to *read* one as flat text
    since attachments could be reference material — but a file somebody
    uploaded and wanted to change had nowhere to go: the text came out as one
    block with the headings and the tables gone, which is not a document any
    more, it is a transcript of one.

    What comes back is an ordinary report: the same artifact the writer
    produces, so it opens in the same editor, prints through the same printer,
    and exports back to `.hwpx` through the exporter that was already there.
    Nothing is generated and nothing is charged — this is a file format being
    read, not a model being asked.
    """
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    if not stored.name.lower().endswith(".hwpx"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_hwpx_file"
        )
    try:
        data = file_service.read_blob(stored.storage_key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    try:
        document = hwpx_import.read(data)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    parts = document.sections
    if not parts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="no_document_body"
        )

    # What the document calls itself, and failing that the file's own name minus
    # the extension. A document opened from a file is that file until somebody
    # renames it — but a file that carries a title on its first page is telling
    # us the name its author chose, which beats `최종_보고서_v3(수정)`.
    title = document.title[:200] or re.sub(r"\.hwpx$", "", stored.name, flags=re.I)[:200]
    title = title or "한글 문서"
    session = ChatSession(
        user_id=user.id,
        project_id=stored.project_id,
        kind=SessionKind.report,
        title=title,
    )
    db.add(session)
    await db.flush()
    artifact = Artifact(
        user_id=user.id,
        session_id=session.id,
        project_id=stored.project_id,
        kind=ArtifactKind.report,
        title=title,
        data={
            "sections": [
                {
                    "id": uuid4().hex,
                    "heading": part.heading,
                    "level": part.level,
                    "status": "done",
                    # Read out of a file rather than written by a model, so it
                    # is markup from the start — the editor's own format, which
                    # is what keeps the tables tables through a save.
                    "format": "html",
                    "content": part.html,
                }
                for part in parts
            ],
            "sources": [],
            "citationStyle": "APA",
        },
    )
    db.add(artifact)
    await db.flush()
    session.artifact_id = artifact.id
    db.add(session)
    await db.commit()
    return OpenedDocument(id=session.id)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: str, user: CurrentUser, db: DbSession):
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    file_service.delete_blob(stored.storage_key)
    await db.delete(stored)
    await db.commit()


# ══ artifacts ══════════════════════════════════════════════════════════


#: One screenful and then some. The gallery asks for the next page when the
#: person asks for it, so this is about what arrives before anything is on
#: screen rather than about how much they can ever see.
_ARTIFACT_PAGE = 60


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    user: CurrentUser,
    db: DbSession,
    kind: str | None = None,
    project_id: str | None = None,
    q: str | None = None,
    limit: int = _ARTIFACT_PAGE,
    before_at: datetime | None = None,
    before_id: str | None = None,
):
    """A page of this account's artifacts, newest first, with bodies cut down.

    A listing draws thumbnails, so it carries cards rather than documents, and
    `q` finds one among hundreds without scrolling.

    Keyset rather than offset: the list is ordered by a timestamp that changes on
    edit. `(before_at, before_id)` is the last row the client has.
    """
    query = select(Artifact).where(Artifact.user_id == user.id)
    if kind:
        query = query.where(Artifact.kind == kind)
    if project_id:
        query = query.where(Artifact.project_id == project_id)
    if q and q.strip():
        # Title only. The bodies are what this endpoint is trying not to read.
        query = query.where(col(Artifact.title).ilike(f"%{q.strip()[:80]}%"))
    if before_at is not None:
        query = query.where(
            tuple_(col(Artifact.updated_at), col(Artifact.id)) < tuple_(before_at, before_id or "")
        )
    rows = (
        await db.exec(
            query.order_by(col(Artifact.updated_at).desc(), col(Artifact.id).desc()).limit(
                max(1, min(limit, 200))
            )
        )
    ).all()
    return [ArtifactOut.card(a) for a in rows]


@router.get("/artifacts/counts")
async def artifact_counts(user: CurrentUser, db: DbSession, q: str | None = None):
    """How many of each kind there are, for the filter row above the grid.

    Counted here because the grid holds one page and the tabs claim to be
    about everything. A count from a page would say "3 slides" to somebody who
    has ninety.
    """
    query = (
        select(Artifact.kind, func.count())
        .where(Artifact.user_id == user.id)
        .group_by(col(Artifact.kind))
    )
    if q and q.strip():
        query = query.where(col(Artifact.title).ilike(f"%{q.strip()[:80]}%"))
    rows = (await db.exec(query)).all()
    counts = {str(kind): int(total) for kind, total in rows}
    return {"counts": counts, "total": sum(counts.values())}


@router.post("/artifacts", response_model=ArtifactOut, status_code=status.HTTP_201_CREATED)
async def create_artifact(payload: ArtifactIn, user: CurrentUser, db: DbSession):
    artifact = Artifact(user_id=user.id, **payload.model_dump())
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(artifact_id: str, user: CurrentUser, db: DbSession):
    return ArtifactOut.of(await _own(db, Artifact, "user_id", user, artifact_id))


def _clean_report_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Report sections with every hand-written body sanitised.

    Only `format: "html"` bodies are touched. A Markdown section is text and is
    escaped by whatever renders it; an HTML one is markup that reaches a panel,
    an export and a share link, and the only place to stop a `<script>` in it is
    before it is stored.
    """
    if not data or not isinstance(data.get("sections"), list):
        return data
    cleaned = []
    for row in data["sections"]:
        section = dict(row) if isinstance(row, dict) else {}
        if section.get("format") == "html":
            section["content"] = design_templates.sanitise(
                str(section.get("content") or ""), editable_styles=True
            )
        cleaned.append(section)
    return {**data, "sections": cleaned}


def _relint(kind: ArtifactKind, data: dict) -> dict:
    """The findings, recomputed from the document as it now is.

    검사 결과는 만들 때 한 번 계산되고 그 뒤로 갱신되지 않았다. So a report
    edited by hand carried findings that named sections it no longer had —
    「추진 계획」 in a document with no such section — and 모두 고치기, which
    finds each finding's section by name, found nothing to rewrite and said
    「모두 고쳤습니다」. Findings are about the text; when the text changes,
    they are recomputed from it, on every path that changes it.
    """
    try:
        if kind is ArtifactKind.report:
            parts = lint.from_sections(list(data.get("sections") or []))
            findings = lint.check(parts)
        elif kind is ArtifactKind.deck:
            parts = lint.from_slides(list(data.get("slides") or []))
            findings = lint.check(parts, slides=True)
        elif data.get("blocks"):
            parts = lint.from_blocks(list(data.get("blocks") or []))
            findings = lint.check(parts, slides=bool(data.get("kind") == "deck"))
        else:
            return data
    except Exception:  # noqa: BLE001 — a checker that cannot read the data must not block a save
        log.warning("relint failed for %s", kind, exc_info=True)
        return data
    return {**data, "lint": lint.wire(findings)}


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def patch_artifact(
    artifact_id: str, payload: ArtifactPatch, user: CurrentUser, db: DbSession
):
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    changes = payload.model_dump(exclude_unset=True)
    summary = changes.pop("summary", "")
    expected = changes.pop("expected_version", None)

    # Checked here rather than in the browser. The client can compare before it
    # writes, but a save landing between its read and its write still wins; the
    # only place the two can be made one step is the transaction that performs
    # the update.
    if expected is not None and expected != artifact.version:
        raise HTTPException(
            status_code=409,
            detail=(
                "이 결과물은 다른 곳에서 이미 수정되었습니다. 최신 내용을 받은 뒤 다시 저장하세요."
            ),
        )

    if "data" in changes and artifact.kind is ArtifactKind.report:
        # A PATCH carries whatever the browser sends, and since the document
        # editor shipped what it sends is markup. Cleaned here rather than
        # trusted: this is the boundary the HTML crosses, and past it the same
        # string is rendered into a panel, written into an export and served
        # from a share link. `editable_styles` keeps the four things a person
        # can actually set — see `design_templates._EDITABLE_STYLE`.
        changes["data"] = _clean_report_data(changes["data"])

    if "data" in changes and isinstance(changes["data"], dict):
        # 글이 바뀌면 검사 결과도 바뀐다.
        changes["data"] = _relint(artifact.kind, changes["data"])

    if "data" in changes:
        # Snapshot before overwriting; otherwise a bad edit is unrecoverable.
        db.add(
            ArtifactVersion(
                artifact_id=artifact.id,
                version=artifact.version,
                data=artifact.data,
                storage_key=artifact.storage_key,
                summary=summary,
            )
        )
        artifact.version += 1

    for field, value in changes.items():
        setattr(artifact, field, value)
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/slides/factcheck", response_model=ArtifactOut)
async def factcheck_slide(
    artifact_id: str, payload: SlideFactCheck, user: CurrentUser, db: DbSession
):
    """Checks one slide's claims against the web and stores the verdicts.

    Per slide, not per deck: a deck-wide run is a dozen unasked-for searches.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")
    if not await factcheck.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="search_unavailable"
        )

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]), key=model_service.fallback_order
    )
    if not usable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    model = usable[0]
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    verdicts, usage = await factcheck.check_slide(slide=target, model=model["id"], api_key=api_key)
    target["factCheck"] = verdicts
    data["slides"] = slides
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    # No version snapshot: a verdict annotates the deck rather than editing it.

    # Charged like the critique beside it, and for the same reason: this is
    # asked for by name and spends up to five calls answering. Billed at the
    # model that ran them, which here is the cheapest one the account may use
    # rather than whatever the deck was written with.
    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="deck.factcheck",
        session_id=artifact.session_id,
        model=model["id"],
        surface="slides",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/sections/diagram", response_model=ArtifactOut)
async def store_diagram(
    artifact_id: str, payload: DiagramPicture, user: CurrentUser, db: DbSession
):
    """Keeps the picture a browser made of one mermaid diagram.

    Mermaid is a JavaScript renderer and this image has no headless browser —
    `report_export` chose reportlab over an HTML engine on purpose. So a
    diagram is drawn by whoever opens the document, and what they drew is
    posted back here so the `.docx`, the `.pdf` and the `.hwpx` have a real
    figure in that place rather than a line of source.

    **No version is taken.** Opening a document is not editing it, and a
    version per reader would bury the edits somebody actually made. The stored
    body is untouched: this only fills a cache beside it, keyed by the
    diagram's own source, and a body that changes simply stops matching the
    key and gets drawn again.

    Free, and deliberately so — the work happened in the reader's browser.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")
    if not pictures.decode(payload.src):
        # A remote address is not a picture this document can carry, and
        # fetching one here is a request nobody asked for.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_embedded")

    data = dict(artifact.data or {})
    sections = [dict(row) for row in (data.get("sections") or [])]
    target = next((row for row in sections if row.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    store = dict(target.get("diagrams") or {})
    if store.get(payload.key) == payload.src:
        # Already have it. Every reader of the document would otherwise write
        # the same bytes back on every open.
        return ArtifactOut.of(artifact)
    store[payload.key] = payload.src
    target["diagrams"] = store
    data["sections"] = sections
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/sections/factcheck", response_model=ArtifactOut)
async def factcheck_section(
    artifact_id: str, payload: SectionFactCheck, user: CurrentUser, db: DbSession
):
    """Checks one report section's claims against the web and stores them.

    The deck has had this since it shipped and the report has not, which is
    backwards: a slide is argued with in the room it is shown in, and a report
    is exported, attached to a mail, and read by people who were not there. The
    figure nobody checked does the most damage on this surface.

    Per section, for the reason the deck runs per slide — a document-wide run is
    a hundred unasked-for searches, and a hundred verdicts at once is not
    something a reader can act on.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")
    if not await factcheck.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="search_unavailable"
        )

    data = dict(artifact.data or {})
    sections = [dict(row) for row in (data.get("sections") or [])]
    target = next((row for row in sections if row.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]), key=model_service.fallback_order
    )
    if not usable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    model = usable[0]
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    verdicts, usage = await factcheck.check_text(
        title=str(target.get("heading") or ""),
        body=str(target.get("content") or ""),
        model=model["id"],
        api_key=api_key,
    )
    target["factCheck"] = verdicts
    data["sections"] = sections
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    # No version snapshot: a verdict annotates the report rather than editing it.

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="report.factcheck",
        session_id=artifact.session_id,
        model=model["id"],
        surface="report",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


#: How a document reaches the reviewer, per kind. The linter reads the same
#: three shapes; this turns them into headings and prose instead of parts.
def _reviewable(artifact: Artifact) -> tuple[str, str]:
    """`(body, rubric)` for one artifact, or `("", "")` when there is nothing."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.report:
        # `as_markdown` rather than the raw body: a section edited in the
        # document editor is markup, and a reviewer handed `<p style=…>` spends
        # its findings on the tags instead of the argument.
        parts = [
            {"heading": s.get("heading") or "", "text": richtext.as_markdown(s)}
            for s in (data.get("sections") or [])
        ]
        return critique.document(parts), ""
    if artifact.kind is ArtifactKind.deck:
        # Every field a slide can carry, through the linter's own reader.
        # Bullets and body alone were handed over, and the reviewer filed a
        # filled `table` and a filled `bands` slide as 「제목만 존재」 P0s —
        # it had been shown two headings and nothing under them.
        parts = [
            {"heading": part.title, "text": " · ".join([*part.labels, *part.lines]).strip(" ·")}
            for part in lint.from_slides(data.get("slides") or [])
        ]
        return critique.document(parts), ""
    if artifact.kind is ArtifactKind.html:
        parts = [
            {"heading": b.get("title") or "", "text": b.get("html") or ""}
            for b in (data.get("blocks") or [])
        ]
        template = design_templates.get(str(data.get("templateId") or ""))
        return critique.document(parts), (template.checklist if template else "")
    return "", ""


@router.post("/artifacts/{artifact_id}/critique", response_model=ArtifactOut)
async def critique_artifact(artifact_id: str, user: CurrentUser, db: DbSession):
    """One reading of a finished document by somebody who did not write it.

    Asked for explicitly and charged, unlike the linter beside it — that one is
    free and certain, this one costs a call and is an opinion. A reading, not a
    gate: nothing is blocked by the score.

    One reviewer, one pass.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    body, rubric = _reviewable(artifact)
    if not body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_reviewable")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]),
        key=model_service.fallback_order,
    )
    session = await db.get(ChatSession, artifact.session_id) if artifact.session_id else None
    model = model_service.find(catalogue["models"], (session.model if session else "") or "") or (
        usable[0] if usable else None
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        result, usage = await critique.review(
            title=artifact.title or "",
            body=body,
            rubric=rubric,
            model=model["id"],
            api_key=api_key,
        )
    except critique.CritiqueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("critique failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="critique_failed"
        ) from exc

    data = dict(artifact.data or {})
    # No version snapshot and no version bump: a review annotates a document
    # rather than editing it, which is the rule the fact-check already follows.
    data["critique"] = {**result, "model": model["id"], "at": utcnow().isoformat()}
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="artifact.critique",
        session_id=artifact.session_id,
        model=model["id"],
        # Read off the artifact, because a critique is asked for on both.
        surface="slides" if artifact.kind == ArtifactKind.deck else "report",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


#: Inlined base64 runs a third larger than the file and lands inside the
#: artifact's JSON, which the list endpoint returns in full. Past this a
#: document stops being something a panel opens quickly.
_MAX_EMBED_BYTES = 3 * 1024 * 1024


async def _picture_bytes(db: DbSession, user: User, artifact_id: str) -> tuple[str, bytes]:
    """The bytes behind an `image` artifact, or an HTTPException saying why not.

    Shared by the two ways a picture gets into a document — a block of an HTML
    page and a slide of a JSON deck — because the checks are the same ones:
    the caller owns it, it is a picture, it is a format that can be drawn, and
    it is small enough to live inside a document.
    """
    picture = await _own(db, Artifact, "user_id", user, artifact_id)
    if picture.kind is not ArtifactKind.image:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_an_image")
    # `src` is this server's own download URL for the blob: /api/files/{id}/content
    match = re.search(r"/files/([0-9a-f]{32})/content", str((picture.data or {}).get("src") or ""))
    stored = await db.get(StoredFile, match.group(1)) if match else None
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image_file_missing")
    mime = (stored.mime or "").lower() or "image/png"
    if mime not in pictures.EMBEDDABLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image_not_embeddable"
        )
    try:
        blob = file_service.read_blob(stored.storage_key)
    except OSError as exc:
        log.warning("image blob unreadable for %s: %s", stored.id, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="image_file_missing"
        ) from exc
    if len(blob) > _MAX_EMBED_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image_too_large"
        )
    return mime, blob


@router.post("/artifacts/{artifact_id}/slides/image", response_model=ArtifactOut)
async def add_slide_image(artifact_id: str, payload: SlideImage, user: CurrentUser, db: DbSession):
    """Puts a picture this workspace already made on one slide of a JSON deck.

    The same path the HTML track has, on the track that has no HTML: a deck's
    slides are JSON, so the picture is stored as the `data:` URI itself rather
    than as bytes, and the preview, the `.pptx` and the `.pdf` all draw it
    from there. No model call, and snapshotted, so it is one click from undone.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")

    mime, blob = await _picture_bytes(db, user, payload.artifact_id)
    target["image"] = {
        "src": pictures.encode(mime, blob),
        "caption": payload.caption.strip(),
    }

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('title') or ''} 에 그림 넣음",
        )
    )
    data["slides"] = slides
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/blocks/image", response_model=ArtifactOut)
async def add_block_image(artifact_id: str, payload: BlockImage, user: CurrentUser, db: DbSession):
    """Puts a picture this workspace already made into one block of a page.

    The writing model cannot produce a picture and `sanitise` drops every `src`
    not already inside the file, so the path runs the other way: a person picks an
    image they made, and the server inlines its bytes as a `data:` URI. The
    artifact stays one file that prints, downloads and shares with the picture in
    it.

    Free, and snapshotted like a rewrite.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.html:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_page")

    data = dict(artifact.data or {})
    blocks = [dict(b) for b in (data.get("blocks") or [])]
    if payload.index >= len(blocks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="block_not_found")
    template = design_templates.get(str(data.get("templateId") or ""))
    if template is None or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="design_template_missing"
        )
    if "html" not in blocks[payload.index]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blocks_not_editable")

    mime, blob = await _picture_bytes(db, user, payload.artifact_id)

    caption = payload.caption.strip()
    markup = design_templates.figure(
        mime=mime,
        data_b64=base64.b64encode(blob).decode("ascii"),
        # A caption when there is one; otherwise something a screen reader can
        # say, because the picture's own title is not in this document.
        alt=caption or "그림",
        caption=caption,
    )

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{blocks[payload.index].get('title')} 에 그림 넣음",
        )
    )
    # Through the same sanitiser as model output: the picture is trusted, the
    # markup around it is built here, and neither gets to skip the door.
    blocks[payload.index]["html"] = design_templates.sanitise(
        f"{blocks[payload.index].get('html') or ''}{markup}"
    )
    data["blocks"] = blocks
    data["content"] = design_templates.render(
        template,
        title=artifact.title or template.name,
        tokens=data.get("design") or {},
        body=design_templates.assemble(template, blocks),
    )
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/sections/image", response_model=ArtifactOut)
async def add_section_image(
    artifact_id: str, payload: SectionImage, user: CurrentUser, db: DbSession
):
    """Puts a picture this workspace made into one section of a report.

    The page track has had this since it shipped and the report track has not,
    which meant the surface most people write on was the one with no way to put
    a picture in a document. Not for want of machinery: a Markdown picture line
    is already read by `richtext` on the way in and by all three exporters on
    the way out — `_IMAGE` in `report_export` decodes it and hands the bytes to
    the same code that draws a figure the writer proposed. So this appends a
    line rather than adding a field, and the `.docx`, `.pdf` and `.hwpx` need no
    changes at all to carry it.

    A section somebody has formatted by hand is HTML, and a Markdown line
    dropped into HTML prints as literal text. So the shape follows the body it
    is joining.

    Free, and snapshotted like a rewrite.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")

    data = dict(artifact.data or {})
    sections = [dict(row) for row in (data.get("sections") or [])]
    target = next((row for row in sections if row.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    mime, blob = await _picture_bytes(db, user, payload.artifact_id)
    src = f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"
    caption = payload.caption.strip()
    body = str(target.get("content") or "").rstrip()

    if target.get("format") == "html":
        label = design_templates.escape(caption or "그림")
        figure = f'<figure><img src="{src}" alt="{label}" />'
        if caption:
            figure += f"<figcaption>{design_templates.escape(caption)}</figcaption>"
        figure += "</figure>"
        # The same door a hand-edited body goes through on PATCH — see
        # `_clean_report_data`. The picture is trusted and the markup around it
        # is built here, and neither gets to skip it.
        target["content"] = design_templates.sanitise(f"{body}{figure}", editable_styles=True)
    else:
        # The alt text is what the exporters print as the caption, so it holds
        # the caption rather than a description of it.
        target["content"] = f"{body}\n\n![{caption}]({src})\n"

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('heading') or '절'} 에 그림 넣음",
        )
    )
    data["sections"] = sections
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/blocks/rewrite", response_model=ArtifactOut)
async def rewrite_block(artifact_id: str, payload: BlockRewrite, user: CurrentUser, db: DbSession):
    """Rewrites one block of an HTML artifact and re-renders the file.

    Blocks are the source and `content` is what they render to, so this replaces
    one block and assembles the document again from the same seed rather than
    splicing markup into a finished file.

    Charged and snapshotted, so a worse rewrite is one click from undone.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.html:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_page")

    data = dict(artifact.data or {})
    blocks = [dict(b) for b in (data.get("blocks") or [])]
    if payload.index >= len(blocks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="block_not_found")
    template = design_templates.get(str(data.get("templateId") or ""))
    if template is None or not template.seed:
        # An artifact written under a template this image no longer ships can
        # still be read and exported; it cannot be re-rendered, and saying so
        # is better than assembling it into some other template's shape.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="design_template_missing"
        )
    if "html" not in blocks[payload.index]:
        # Written before the blocks kept their markup. Rewriting one would
        # rebuild the document out of the pieces that were kept, silently
        # dropping the rest.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blocks_not_editable")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if template.surface.value in m["kinds"]),
        key=model_service.fallback_order,
    )
    session = await db.get(ChatSession, artifact.session_id) if artifact.session_id else None
    model = model_service.find(catalogue["models"], (session.model if session else "") or "") or (
        usable[0] if usable else None
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        fragment, usage = await page_service.rewrite_block(
            request=artifact.title or "",
            blocks=blocks,
            index=payload.index,
            template=template,
            model=model["id"],
            api_key=api_key,
            note=payload.note,
        )
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("block rewrite failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_failed"
        ) from exc

    if not fragment.strip():
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_empty")

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{blocks[payload.index].get('title')} 다시 씀",
        )
    )
    # A rewrite is about the words. The model cannot write a picture, so
    # anything embedded in this block would disappear on the way through —
    # which is a silent deletion of somebody else's work, not a rewrite.
    blocks[payload.index]["html"] = fragment + design_templates.pictures_in(
        blocks[payload.index].get("html") or ""
    )
    data["blocks"] = blocks
    data["content"] = design_templates.render(
        template,
        title=artifact.title or template.name,
        tokens=data.get("design") or {},
        body=design_templates.assemble(template, blocks),
    )
    data["lint"] = lint.wire(
        lint.check(
            lint.from_blocks(blocks),
            slides=template.kind == "deck",
            limits=template.limits,
        )
    )
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="page.rewrite",
        session_id=artifact.session_id,
        model=model["id"],
        surface="report",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/sections/rewrite", response_model=ArtifactOut)
async def rewrite_section(
    artifact_id: str, payload: SectionRewrite, user: CurrentUser, db: DbSession
):
    """Rewrites one section of a report and keeps the previous version.

    Charged like any other model call, and snapshotted like any other edit, so
    a worse rewrite is one click from undone.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")

    data = dict(artifact.data or {})
    sections = [dict(s) for s in (data.get("sections") or [])]
    target = next((s for s in sections if s.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "report" in m["kinds"]),
        key=model_service.fallback_order,
    )
    # An artifact carries no model: the session's if it still has one, else the
    # cheapest that can write a report.
    session = await db.get(ChatSession, artifact.session_id) if artifact.session_id else None
    model = model_service.find(catalogue["models"], (session.model if session else "") or "") or (
        usable[0] if usable else None
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        body, usage = await report_service.rewrite_section(
            request=str(data.get("request") or artifact.title or ""),
            heading=target.get("heading") or "",
            # The surrounding sections as prose. A rewrite reads them for
            # continuity, and markup in that window is noise it may copy.
            sections=richtext.normalise(sections),
            target_id=payload.section_id,
            model=model["id"],
            api_key=api_key,
            note=payload.note,
            sources=list(data.get("sources") or []),
        )
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("section rewrite failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_failed"
        ) from exc

    if not body.strip():
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_empty")

    # Same snapshot the PATCH path takes.
    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('heading')} 다시 씀",
        )
    )
    # The same door the first pass goes through — stray ideographs read back,
    # the tokenizer's 「120 만 원」 closed up — so a rewritten section is not
    # the one section in the document written differently.
    target["content"] = hangul.tidy_spacing(hangul.read_back(body)[0])
    # The model writes Markdown. A section that had been edited into HTML goes
    # back to Markdown when it is rewritten, because what is stored now *is*
    # Markdown — leaving the old flag on would have the panel render `**가**`
    # as literal asterisks.
    target["format"] = "markdown"
    target["status"] = "done"
    data["sections"] = sections
    data["wordCount"] = report_service.word_count(sections)
    # 다시 쓴 절은 다시 검사한다 — 고친 지적이 그대로 남아 있으면 안 된다.
    data = _relint(artifact.kind, data)
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="report.rewrite",
        session_id=artifact.session_id,
        model=model["id"],
        surface="report",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/slides/rewrite", response_model=ArtifactOut)
async def rewrite_slide(artifact_id: str, payload: SlideRewrite, user: CurrentUser, db: DbSession):
    """Rewrites one slide of a deck and keeps the previous version.

    The deck's half of `rewrite_section`. `deck.rewrite_slide` has existed for
    a while and was reachable only by asking in the conversation — so anything
    that wanted to correct one slide from a panel had to send a sentence to the
    chat and hope, which is a request rather than an action. The checks list is
    the caller that needed this: it names a slide and says what is wrong with
    it, and had nowhere to send that.

    Charged like any other model call and snapshotted like any other edit, so a
    worse rewrite is one click from undone.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "slides" in m["kinds"]),
        key=model_service.fallback_order,
    )
    session = await db.get(ChatSession, artifact.session_id) if artifact.session_id else None
    model = model_service.find(catalogue["models"], (session.model if session else "") or "") or (
        usable[0] if usable else None
    )
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        written, usage = await deck_service.rewrite_slide(
            request=artifact.title or "",
            slides=slides,
            target_id=payload.slide_id,
            model=model["id"],
            api_key=api_key,
            note=payload.note,
        )
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("slide rewrite failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_failed"
        ) from exc

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('title')} 다시 씀",
        )
    )
    slides[slides.index(target)] = written
    data["slides"] = slides
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(
        db,
        user,
        credits,
        reason="deck.rewrite",
        session_id=artifact.session_id,
        model=model["id"],
        surface="slides",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.get("/artifacts/{artifact_id}/versions", response_model=list[ArtifactVersionOut])
async def list_artifact_versions(artifact_id: str, user: CurrentUser, db: DbSession):
    """Superseded revisions, newest first.

    From stored rows, not the version number: a list derived from the number
    would be N identical rows all stamped with the current time.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    rows = (
        await db.exec(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .order_by(col(ArtifactVersion.version).desc())
        )
    ).all()
    return [ArtifactVersionOut.of(r) for r in rows]


@router.get(
    "/artifacts/{artifact_id}/versions/{version}",
    response_model=ArtifactVersionDetailOut,
)
async def get_artifact_version(artifact_id: str, version: int, user: CurrentUser, db: DbSession):
    """One historical body, fetched only when the user asks to inspect it."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    row = (
        await db.exec(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .where(ArtifactVersion.version == version)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such version")
    return ArtifactVersionDetailOut.of(row)


@router.post("/artifacts/{artifact_id}/restore", response_model=ArtifactOut)
async def restore_artifact(
    artifact_id: str, payload: ArtifactRestore, user: CurrentUser, db: DbSession
):
    """Puts a superseded revision back, as a new version.

    Restoring is itself an edit: the current body is snapshotted first, so the
    history stays append-only and an undo can be undone.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    target = (
        await db.exec(
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact.id)
            .where(ArtifactVersion.version == payload.version)
        )
    ).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such version")

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"v{payload.version} 로 되돌리기 전",
        )
    )
    artifact.version += 1
    artifact.data = target.data
    artifact.storage_key = target.storage_key
    # Reports carry their title inside `data`; snapshots without one fall back
    # to the artifact's current title.
    if isinstance(target.data, dict) and str(target.data.get("title") or "").strip():
        artifact.title = str(target.data["title"])[:200]
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(artifact_id: str, user: CurrentUser, db: DbSession):
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    await db.exec(delete(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id))
    await db.delete(artifact)
    await db.commit()


@router.post("/artifacts/delete")
async def delete_artifacts(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Several at once.

    The conversations that produced them are left alone — deleting a document
    is not a statement about the asking that led to it, and the transcript is
    still a readable record with a chip that no longer opens.
    """
    rows = await _owned_many(db, Artifact, "user_id", user, payload.ids)
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(ids)))
    await db.exec(delete(Artifact).where(col(Artifact.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids)}


# ══ skills ═════════════════════════════════════════════════════════════


# ══ the store ══════════════════════════════════════════════════════════
#
# Agents and skills are shared the same way and copied the same way, so both
# halves of it live here rather than once per resource.


async def _admin_ids(db: DbSession, owner_ids: set[str]) -> set[str]:
    """Which of these accounts are administrators.

    Read per request rather than stamped on the row: an entry published by
    somebody who is later made an administrator is an official entry from that
    day, and one published by an administrator who is later demoted stops
    claiming to be. A stored flag would have to be rewritten by the role change
    to say the same thing, and would quietly lie until it was.
    """
    if not owner_ids:
        return set()
    rows = await db.exec(
        select(User.id).where(col(User.id).in_(owner_ids), User.role == UserRole.admin)
    )
    return set(rows.all())


async def _owner_names(db: DbSession, owner_ids: set[str]) -> dict[str, str]:
    if not owner_ids:
        return {}
    rows = await db.exec(select(User).where(col(User.id).in_(owner_ids)))
    return {u.id: u.name for u in rows.all()}


async def _shared(db: DbSession, model, user: User, item_id: str):
    """One row from the store: shared, and somebody else's.

    Your own row is refused rather than copied. Nothing good comes of an
    account holding two of the same procedure with the same name, and the
    button that would do it is not offered.
    """
    row = await db.get(model, item_id)
    if row is None or row.visibility is not Visibility.org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if row.owner_id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_yours")
    return row


def _copy_of(rows: list, origin) -> Any | None:
    """This account's copy of one shared row, if it has taken one.

    Matched on `origin_id` first and then on the catalogue key, because the two
    answer different questions: the first is "I copied this row", the second is
    "I already have this procedure" — which is still true for a copy taken
    before origins were recorded, or taken from a different account's copy of
    the same catalogue entry.
    """
    for row in rows:
        if row.origin_id == origin.id:
            return row
    if origin.catalog_key:
        for row in rows:
            if row.catalog_key == origin.catalog_key:
                return row
        # Seeded into this account back when every account got its own copy of
        # the catalogue. Those rows carry no key — agents never had one — so
        # without this every existing account would be told it holds none of
        # the entries it has held all along, and 가져오기 would hand it a
        # second row with the same name. Only ever consulted for a catalogue
        # entry, so a personal item that happens to share a name is safe.
        for row in rows:
            if row.slug == origin.slug:
                return row
    return None


async def _install_skill(db: DbSession, user: User, origin: Skill) -> Skill:
    """Copies one shared skill into this account, or returns the copy already there.

    A copy, not a reference: the original's owner keeps editing theirs, and an
    edit over there never reaches a procedure somebody is relying on over here.
    Idempotent, so pressing 가져오기 twice is not two rows.
    """
    mine = list((await db.exec(select(Skill).where(Skill.owner_id == user.id))).all())
    if (existing := _copy_of(mine, origin)) is not None:
        return existing

    copy = Skill(
        owner_id=user.id,
        name=origin.name,
        slug=origin.slug,
        description=origin.description,
        when_to_use=origin.when_to_use,
        body=origin.body,
        catalog_key=origin.catalog_key,
        # Where it came from, in the source column the screen already reads:
        # a shipped procedure stays 기본, anything a colleague wrote arrives as
        # 워크스페이스 rather than pretending this account authored it.
        source=(
            SkillSource.built_in if origin.source is SkillSource.built_in else SkillSource.workspace
        ),
        kinds=list(origin.kinds or []),
        required_tools=list(origin.required_tools or []),
        estimated_tokens=origin.estimated_tokens
        or starter.estimate_tokens(origin.when_to_use, origin.body, origin.description),
        version=origin.version,
        enabled=True,
        # Copies are private. Publishing is a decision the new owner makes, and
        # a store that fills up with copies of its own entries is not a store.
        visibility=Visibility.private,
        origin_id=origin.id,
    )
    db.add(copy)
    origin.installs += 1
    db.add(origin)
    return copy


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(select(Skill).where(Skill.owner_id == user.id).order_by(col(Skill.name)))
    ).all()
    return [SkillOut.of(s) for s in rows]


@router.get("/tools", response_model=list[ToolCatalogOut])
async def list_tool_catalog(user: CurrentUser, db: DbSession):
    return [ToolCatalogOut(**row) for row in await tool_registry.tool_catalog(db, user)]


@router.get("/skills/store", response_model=list[StoreSkillOut])
async def list_skill_store(user: CurrentUser, db: DbSession):
    """Everything shared with the workspace that is not already yours.

    Deliberately not folded into `GET /skills`: that list is what the composer
    offers for a turn, and a skill is only ever run out of its owner's account.
    A shared row mixed into it would be a picker entry that resolves to
    `skill_not_found` at the moment it matters.
    """
    rows = (
        await db.exec(
            select(Skill)
            .where(Skill.visibility == Visibility.org, Skill.owner_id != user.id)
            .order_by(col(Skill.name))
        )
    ).all()
    owner_ids = {row.owner_id for row in rows}
    names = await _owner_names(db, owner_ids)
    admins = await _admin_ids(db, owner_ids)
    mine = list((await db.exec(select(Skill).where(Skill.owner_id == user.id))).all())
    return [
        StoreSkillOut.store(
            row,
            owner_name=names.get(row.owner_id, ""),
            official=row.owner_id in admins,
            installed=_copy_of(mine, row) is not None,
        )
        for row in rows
    ]


@router.post(
    "/skills/{skill_id}/install",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill(skill_id: str, user: CurrentUser, db: DbSession):
    copy = await _install_skill(db, user, await _shared(db, Skill, user, skill_id))
    await db.commit()
    await db.refresh(copy)
    return SkillOut.of(copy)


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillIn, user: CurrentUser, db: DbSession):
    await _validate_tool_names(db, user, payload.required_tools)
    values = payload.model_dump()
    skill = Skill(
        owner_id=user.id,
        slug=_slug(payload.name),
        estimated_tokens=starter.estimate_tokens(
            payload.when_to_use, payload.body, payload.description
        ),
        **values,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return SkillOut.of(skill)


@router.patch("/skills/{skill_id}", response_model=SkillOut)
async def patch_skill(skill_id: str, payload: SkillIn, user: CurrentUser, db: DbSession):
    skill = await _own(db, Skill, "owner_id", user, skill_id)
    changes = payload.model_dump(exclude_unset=True)
    if "required_tools" in changes:
        await _validate_tool_names(
            db,
            user,
            changes["required_tools"],
            grandfathered=set(skill.required_tools or []),
        )
    for field, value in changes.items():
        setattr(skill, field, value)
    skill.slug = _slug(skill.name)
    skill.estimated_tokens = starter.estimate_tokens(
        skill.when_to_use, skill.body, skill.description
    )
    skill.updated_at = utcnow()
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return SkillOut.of(skill)


@router.post("/skills/{skill_id}/toggle", response_model=SkillOut)
async def toggle_skill(skill_id: str, user: CurrentUser, db: DbSession):
    skill = await _own(db, Skill, "owner_id", user, skill_id)
    skill.enabled = not skill.enabled
    skill.updated_at = utcnow()
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return SkillOut.of(skill)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: str, user: CurrentUser, db: DbSession):
    await db.delete(await _own(db, Skill, "owner_id", user, skill_id))
    await db.commit()


@router.post("/skills/delete")
async def delete_skills(payload: BulkDelete, user: CurrentUser, db: DbSession):
    rows = await _owned_many(db, Skill, "owner_id", user, payload.ids)
    for row in rows:
        await db.delete(row)
    await db.commit()
    return {"deleted": len(rows)}


# ══ memories ═══════════════════════════════════════════════════════════


@router.get("/memory", response_model=list[MemoryOut])
async def list_memories(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Memory)
            .where(Memory.user_id == user.id)
            .order_by(col(Memory.pinned).desc(), col(Memory.updated_at).desc())
        )
    ).all()
    return [MemoryOut.of(m) for m in rows]


@router.post("/memory", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(payload: MemoryIn, user: CurrentUser, db: DbSession):
    memory = Memory(user_id=user.id, **payload.model_dump())
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.of(memory)


@router.patch("/memory/{memory_id}", response_model=MemoryOut)
async def patch_memory(memory_id: str, payload: MemoryIn, user: CurrentUser, db: DbSession):
    memory = await _own(db, Memory, "user_id", user, memory_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(memory, field, value)
    memory.updated_at = utcnow()
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.of(memory)


@router.post("/memory/{memory_id}/pin", response_model=MemoryOut)
async def pin_memory(memory_id: str, user: CurrentUser, db: DbSession):
    memory = await _own(db, Memory, "user_id", user, memory_id)
    memory.pinned = not memory.pinned
    memory.updated_at = utcnow()
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.of(memory)


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, user: CurrentUser, db: DbSession):
    await db.delete(await _own(db, Memory, "user_id", user, memory_id))
    await db.commit()


# ══ agents ═════════════════════════════════════════════════════════════


async def _agent_has_knowledge(db: DbSession, user_id: str, agent_id: str) -> bool:
    row = (
        await db.exec(
            select(StoredFile.id)
            .where(
                StoredFile.user_id == user_id,
                StoredFile.agent_id == agent_id,
                col(StoredFile.text) != "",
            )
            .limit(1)
        )
    ).first()
    return row is not None


def _without_legacy_copies(rows: list[Agent], *, user_id: str) -> list[Agent]:
    """One card per name: the person's own, where they have one.

    Before the shipped agents became a shared catalogue, every account was
    handed its own copy of each. The move made the originals org-owned and left
    the copies where they were, so an account that existed before it sees every
    built-in twice — 리포트 도우미 above 리포트 도우미, one of them with a
    different sentence under it, and no way to tell which is which. Somebody
    opening KloudChat on such an account reads that as two different agents.

    The catalogue entry is the one hidden, not the copy. Which of the two is
    untouched cannot be known — the rows were rewritten by the migration that
    made them, so their timestamps say a day passed and mean nothing — and
    between a row the person may have edited and a row they can install again
    from the store at any time, the safe one to hide is the one that can come
    back. An account with no copy of its own, which is every account made since,
    sees the catalogue exactly as before.
    """
    # `origin_id` is what separates the two ways an account comes to hold a
    # copy. Taking one from the store sets it, and the store then marks the
    # original 이미 가져감 and keeps both on screen on purpose — that pairing is
    # the feature. The seeded copies predate the store and carry nothing, which
    # is exactly the set to collapse.
    legacy = {
        row.name
        for row in rows
        if row.owner_id == user_id and row.visibility != "org" and row.origin_id is None
    }
    return [row for row in rows if row.visibility != "org" or row.name not in legacy]


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(user: CurrentUser, db: DbSession):
    # Own agents plus anything shared with the workspace.
    rows = (
        await db.exec(
            select(Agent)
            .where((Agent.owner_id == user.id) | (Agent.visibility == "org"))
            .order_by(col(Agent.name))
        )
    ).all()
    rows = _without_legacy_copies(rows, user_id=user.id)

    # One lookup for the page: the store shows who made each agent, and
    # whether that was an administrator publishing an official one.
    owner_ids = {a.owner_id for a in rows}
    names = await _owner_names(db, owner_ids)
    admins = await _admin_ids(db, owner_ids)
    mine = [a for a in rows if a.owner_id == user.id]
    # Knowledge search is turn-local and only receives files readable by the
    # caller. A shared agent's owner's shelf is intentionally not implied by
    # the agent row, so it remains unavailable until the agent is copied and
    # given the caller's own documents.
    readable_shelves = set(
        (
            await db.exec(
                select(StoredFile.agent_id).where(
                    StoredFile.user_id == user.id,
                    col(StoredFile.agent_id).in_([agent.id for agent in rows]),
                    col(StoredFile.text) != "",
                )
            )
        ).all()
    )
    return [
        AgentOut.of(
            agent,
            owner_name=names.get(agent.owner_id, ""),
            has_knowledge=agent.id in readable_shelves,
            official=agent.owner_id in admins,
            # Only meaningful for somebody else's row, and false on your own so
            # a card never tells you that you have a copy of yourself.
            installed=(agent.owner_id != user.id and _copy_of(mine, agent) is not None),
            viewer_id=user.id,
        )
        for agent in rows
    ]


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentIn, user: CurrentUser, db: DbSession):
    await _validate_skill_ids(db, user, payload.skill_ids)
    await _validate_tool_names(db, user, payload.tools)
    slug = await _claim_slug(db, user.id, payload.slug or payload.name)
    agent = Agent(owner_id=user.id, slug=slug, **payload.model_dump(exclude={"slug"}))
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(
        agent,
        has_knowledge=await _agent_has_knowledge(db, user.id, agent.id),
    )


@router.post(
    "/agents/{agent_id}/install",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_agent(agent_id: str, user: CurrentUser, db: DbSession):
    """Copies a shared agent into this account, with the skills it runs on.

    The prompt used to travel alone. It arrived working differently from the
    agent it was copied from and said nothing about why: an allow-list of skill
    ids is a list of rows in the author's account, and the same ids resolve to
    nothing here. So the shared ones among them are installed too and the list
    is rewritten against the copies — the agent answers the way the card
    promised, out of procedures this account owns and can edit.

    Knowledge stays behind. A shelf is the author's documents, readable by
    them; copying an agent is not a grant over the files it was given.
    """
    origin = await _shared(db, Agent, user, agent_id)
    mine = list((await db.exec(select(Agent).where(Agent.owner_id == user.id))).all())
    if (existing := _copy_of(mine, origin)) is not None:
        return AgentOut.of(
            existing,
            has_knowledge=await _agent_has_knowledge(db, user.id, existing.id),
        )

    skill_ids: list[str] | None = None
    if origin.skill_ids is not None:
        wanted = list(origin.skill_ids)
        rows = (
            (await db.exec(select(Skill).where(col(Skill.id).in_(wanted)))).all() if wanted else []
        )
        by_id = {row.id: row for row in rows}
        copied: list[str] = []
        for skill_id in wanted:
            source = by_id.get(skill_id)
            # Unshared, or deleted since the agent was written. Silently
            # dropped: the alternative is refusing the whole install over a
            # procedure the author never offered.
            if source is None or source.visibility is not Visibility.org:
                continue
            if source.owner_id == user.id:
                copied.append(source.id)
                continue
            copy = await _install_skill(db, user, source)
            await db.flush()
            copied.append(copy.id)
        # `[]` is not "none of them survived" — it is a hard deny that would
        # refuse every skill the new owner ever switches on. An allow-list
        # emptied by the copy becomes 상속 instead, which grants nothing they
        # could not grant themselves in the editor.
        skill_ids = copied if copied or not wanted else None

    # What did not travel, said on the copy rather than in a toast: the
    # question it answers — why does my copy answer worse than the one I tried?
    # — is asked days afterwards. Only when there was a shelf to miss, and in an
    # ordinary editable field, so wiring up your own documents and deleting the
    # line is how it is dismissed.
    description = origin.description
    if await _agent_has_knowledge(db, origin.owner_id, origin.id):
        note = "지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 올려 주세요."
        description = f"{description} · {note}" if description else note

    # 「가져갈 수는 있되 세부 내용을 비공개로」: a sealed original's copy
    # never holds the prompt. It reads the original's at run time — so it
    # keeps working the way the card promised, and follows the author's edits.
    sealed = origin.share_mode == "sealed"
    copy = Agent(
        owner_id=user.id,
        name=origin.name,
        slug=origin.slug,
        description=description,
        model=origin.model,
        system_prompt="" if sealed else origin.system_prompt,
        sealed=sealed,
        guide=origin.guide,
        starters=list(origin.starters or []),
        tools=None if origin.tools is None else list(origin.tools),
        skill_ids=skill_ids,
        kinds=list(origin.kinds or []),
        temperature=origin.temperature,
        color=origin.color,
        enabled=True,
        # Private, for the same reason a copied skill is: the store lists
        # originals, not everybody's copy of one.
        visibility=Visibility.private,
        catalog_key=origin.catalog_key,
        origin_id=origin.id,
    )
    db.add(copy)
    origin.installs += 1
    db.add(origin)
    await db.commit()
    await db.refresh(copy)
    return AgentOut.of(
        copy,
        has_knowledge=await _agent_has_knowledge(db, user.id, copy.id),
    )


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def patch_agent(agent_id: str, payload: AgentIn, user: CurrentUser, db: DbSession):
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    changes = payload.model_dump(exclude_unset=True)
    if "skill_ids" in changes:
        await _validate_skill_ids(
            db,
            user,
            changes["skill_ids"],
            grandfathered=set(agent.skill_ids or []),
        )
    if "tools" in changes:
        await _validate_tool_names(
            db,
            user,
            changes["tools"],
            grandfathered=set(agent.tools or []),
        )
    wanted = changes.pop("slug", None)
    if agent.sealed:
        # The prompt is the sharer's, read from the original; the copy has no
        # prompt of its own to write, and `sealed` is not a thing to unset.
        changes.pop("system_prompt", None)
        changes.pop("share_mode", None)
    for field, value in changes.items():
        setattr(agent, field, value)
    # The typed handle wins; a rename with the field left blank re-derives it
    # from the new name, as before. Either way it is checked against the rest
    # of this owner's agents, this one excepted.
    agent.slug = await _claim_slug(db, user.id, wanted or agent.name, except_id=agent.id)
    agent.updated_at = utcnow()
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(
        agent,
        has_knowledge=await _agent_has_knowledge(db, user.id, agent.id),
    )


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: CurrentUser, db: DbSession):
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    # Read before the row goes. `files.agent_id` cascades, so KloudChat's own
    # copies are handled — but the index is another service and knows nothing
    # about this delete. A collection left behind is documents still searchable
    # by whoever holds the key, which is the one leak this design can produce.
    key = agent.index_key
    await db.delete(agent)
    await db.commit()
    if key:
        await index_client.forget_collection(collection=key)


@router.post("/agents/delete")
async def delete_agents(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Several at once, each taking its search index with it.

    The keys are read before the rows go and the collections dropped after the
    commit — one that fails leaves documents searchable by whoever holds the
    key, so it is logged rather than swallowed.
    """
    rows = await _owned_many(db, Agent, "owner_id", user, payload.ids)
    keys = [row.index_key for row in rows if row.index_key]
    for row in rows:
        await db.delete(row)
    await db.commit()
    for key in keys:
        try:
            await index_client.forget_collection(collection=key)
        except Exception:  # noqa: BLE001 — the rows are already gone
            log.exception("index collection %s outlived its agent", key)
    return {"deleted": len(rows)}


def _attachment(body: bytes, media: str, stem: str, suffix: str) -> Response:
    # RFC 5987 so a Korean filename survives the header.
    filename = quote(f"{stem}.{suffix}")
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _export_deck(artifact: Artifact, format: str) -> Response:
    """A deck as `.pptx`, `.pdf` or Markdown.

    No `.docx` or `.hwpx` — a deck in a word processor is an outline with the
    layout discarded.
    """
    slides = list((artifact.data or {}).get("slides") or [])
    # The design system as it stood when the deck was written. `None` when it
    # was written without one, which is what keeps those exports unchanged.
    tokens = (artifact.data or {}).get("design") or None
    title = artifact.title or "슬라이드"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "deck"
    # The 서식 the deck was written in, when it names one. Its PowerPoint half
    # is what the file is built on.
    chosen = design_templates.get((artifact.data or {}).get("templateId") or "")

    if format == "md":
        return _attachment(
            deck_service.to_markdown(title, slides).encode(),
            "text/markdown; charset=utf-8",
            stem,
            "md",
        )
    if format == "pdf":
        return _attachment(
            deck_export.to_pdf(title, slides, tokens=tokens), "application/pdf", stem, "pdf"
        )
    if format in ("pptx", "docx"):
        # `docx` is the endpoint default, so a deck exported without an explicit
        # format lands here rather than 400-ing.
        return _attachment(
            deck_export.to_pptx(
                title,
                slides,
                tokens=tokens,
                template=chosen.pptx_template if chosen else "",
            ),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            stem,
            "pptx",
        )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")


async def _export_page(artifact: Artifact, format: str) -> Response:
    """An HTML artifact as a file somebody can hand on.

    Three ways out, and the split is deliberate:

    · `.html` is the artifact itself, byte for byte.
    · `.pdf` is that same file printed by a browser, so it carries the 서식's
      own layout — its columns, its margin notes, its paper. See
      `services/printing.py`. Where no printer is configured this falls through
      to the structural renderer below, which still produces a PDF.
    · `.docx` / `.hwpx` / `.pptx` are `page_export` reading the markup back into
      the shapes the Office exporters draw, so the file opens in Word or
      PowerPoint as paragraphs and slides somebody can edit. A design that
      survived as a picture would not be a document.

    Which formats are offered follows the template — a document has no slides, a
    deck no `.hwpx`.
    """
    data = artifact.data or {}
    content = str(data.get("content") or "")
    page_settings = data.get("pageSettings") or None
    title = artifact.title or "문서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "page"

    if format == "html":
        return _attachment(content.encode(), "text/html; charset=utf-8", stem, "html")

    template = design_templates.get(str(data.get("templateId") or ""))
    tokens = dict(data.get("design") or {})
    # HTML templates carry their visual face in the template manifest rather
    # than in the artifact's optional design-system tokens. Editable Office
    # export used to miss that bridge and therefore drew every template in the
    # same default purple look even while PDF faithfully printed its CSS.
    if template and template.look:
        tokens.setdefault("visualStyle", template.look)
    tokens = tokens or None
    # A template can stop existing across an upgrade; the markup still says
    # which kind it is, and the file has to keep exporting either way.
    is_deck = template.kind == "deck" if template else 'class="slide' in content

    if is_deck:
        slides = page_export.to_slides(content, accent=str((tokens or {}).get("accent") or ""))
        if format == "md":
            return _attachment(
                deck_service.to_markdown(title, slides).encode(),
                "text/markdown; charset=utf-8",
                stem,
                "md",
            )
        if format == "pdf":
            printed = await printing.to_pdf(content)
            return _attachment(
                printed or deck_export.to_pdf(title, slides, tokens=tokens),
                "application/pdf",
                stem,
                "pdf",
            )
        if format in ("pptx", "docx"):
            # `docx` is the endpoint default, so a deck exported without an
            # explicit format lands on the presentation rather than 400-ing.
            return _attachment(
                deck_export.to_pptx(
                    title,
                    slides,
                    tokens=tokens,
                    dark=bool(template and template.dark),
                    template=template.pptx_template if template else "",
                ),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                stem,
                "pptx",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    sections = page_export.to_sections(
        content,
        cover_page=template.cover_page if template else True,
    )
    if format == "md":
        body, media, suffix = (
            report_service.to_markdown(title, sections).encode(),
            "text/markdown; charset=utf-8",
            "md",
        )
    elif format == "pdf":
        printed = await printing.to_pdf(content)
        body, media, suffix = (
            printed
            or report_export.to_pdf(title, sections, tokens=tokens, page_settings=page_settings),
            "application/pdf",
            "pdf",
        )
    elif format == "hwpx":
        body, media, suffix = (
            report_export.to_hwpx(title, sections, tokens=tokens, page_settings=page_settings),
            "application/hwp+zip",
            "hwpx",
        )
    elif format == "docx":
        body, media, suffix = (
            report_export.to_docx(title, sections, tokens=tokens, page_settings=page_settings),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")
    return _attachment(body, media, stem, suffix)


@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(artifact_id: str, user: CurrentUser, db: DbSession, format: str = "docx"):
    """A report, a deck, or an HTML artifact as a file.

    Reports take `docx`, `pdf`, `hwpx` or `md`; decks take `pptx`, `pdf` or `md`.
    An artifact written into a rendering template takes `html` plus whichever set
    matches its template.

    Built from what is stored, so the download matches the panel.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind not in (ArtifactKind.report, ArtifactKind.deck, ArtifactKind.html):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_exportable")

    if artifact.kind is ArtifactKind.html:
        return await _export_page(artifact, format)

    if artifact.kind is ArtifactKind.deck:
        return _export_deck(artifact, format)

    # Markdown, whichever way each section was stored. A report somebody edited
    # in the document editor holds HTML; `_markdown_to_lines` in every exporter
    # reads Markdown and would draw the tags as text.
    data = artifact.data or {}
    sections = richtext.normalise(list(data.get("sections") or []))
    sections = report_export.with_references(
        sections,
        list(data.get("sources") or []),
        str(data.get("citationStyle") or "APA"),
    )
    tokens = data.get("design") or None
    page_settings = data.get("pageSettings") or None
    title = artifact.title or "보고서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "report"

    # The 서식 this report wears, so the `.docx` comes out as that 서식 rather
    # than in Word's defaults. Stored on the artifact by the page track and
    # chosen in the panel; absent, the exporter uses its own page setup.
    chosen = design_templates.get(str(data.get("templateId") or ""))
    docx_template = chosen.docx_template if chosen else ""

    if format == "md":
        body = report_service.to_markdown(title, sections).encode()
        media = "text/markdown; charset=utf-8"
        suffix = "md"
    elif format == "pdf":
        body = report_export.to_pdf(title, sections, tokens=tokens, page_settings=page_settings)
        media = "application/pdf"
        suffix = "pdf"
    elif format == "docx":
        body = report_export.to_docx(
            title,
            sections,
            tokens=tokens,
            template=docx_template,
            page_settings=page_settings,
        )
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    elif format == "hwpx":
        # Same sections and structure — see report_export.to_hwpx.
        body = report_export.to_hwpx(title, sections, tokens=tokens, page_settings=page_settings)
        media = "application/hwp+zip"
        suffix = "hwpx"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    return _attachment(body, media, stem, suffix)


# ══ templates ══════════════════════════════════════════════════════════
#
# Two lists in one shape, so the gallery concatenates rather than branches:
# the built-in starting points, which ship in the image and cannot be added
# to, and the ones a person wrote for themselves.


@router.get("/prompt-templates", response_model=list[PromptTemplateOut])
async def list_prompt_templates(user: CurrentUser, surface: str | None = None):
    """Every built-in starting point, or those for one surface.

    These were a static array in the frontend bundle, which was enough while
    picking one only typed a sentence into the composer. A turn now carries the
    id and the server resolves it, so the catalogue the server resolves against
    is the one the gallery has to have been offered.
    """
    rows = prompt_templates.all_templates()
    if surface:
        rows = [t for t in rows if t.kind.value == surface]
    return [PromptTemplateOut.of(t) for t in rows]


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(user: CurrentUser, db: DbSession):
    """Mine, plus everything an administrator shared with the instance."""
    rows = (
        await db.exec(
            select(Template)
            .where(or_(Template.owner_id == user.id, col(Template.shared).is_(True)))
            .order_by(col(Template.shared).desc(), col(Template.created_at).desc())
        )
    ).all()
    # One lookup for the whole page rather than one per row: a gallery of forms
    # is exactly the case where every row has a file.
    ids = {t.file_id for t in rows if t.file_id}
    found: dict[str, StoredFile] = {}
    if ids:
        files = (await db.exec(select(StoredFile).where(col(StoredFile.id).in_(ids)))).all()
        found = {f.id: f for f in files}
    return [TemplateOut.of(t, found.get(t.file_id or ""), owner_id=user.id) for t in rows]


async def _form_file(db: DbSession, template: Template) -> StoredFile | None:
    """The template's attached form, or `None`. Ownership was checked on write."""
    return await db.get(StoredFile, template.file_id) if template.file_id else None


async def _shelf_key(db: DbSession, agent: Agent) -> str:
    """This agent's collection name, minted the first time it needs one.

    Lazy rather than backfilled: an agent with nothing attached needs no shelf,
    and creating collections for every existing row would leave keys standing
    for documents that never arrive.
    """
    if not agent.index_key:
        agent.index_key = index_client.new_collection_key()
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    return agent.index_key


#: Default gallery groups. The wire default is the private one, so a shared
#: template that did not name a group is refiled.
_OWN_GROUP = "내 템플릿"
_SHARED_GROUP = "공용"


def _may_share(user: User, requested: bool) -> None:
    """Refuses `shared` from anybody but an administrator.

    Refused rather than ignored: a template silently saved as private after the
    admin screen offered to share it is worse than an error.
    """
    if requested and user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")


async def _own_file(db: DbSession, user: User, file_id: str | None) -> str | None:
    """The attached form, checked. `None` stays `None`.

    The id arrives from the browser, so one belonging to somebody else must not
    become a template that quietly reads their file on every use.
    """
    if not file_id:
        return None
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
    return stored.id


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateIn, user: CurrentUser, db: DbSession):
    data = payload.model_dump()
    _may_share(user, bool(data.get("shared")))
    data["file_id"] = await _own_file(db, user, data.get("file_id"))
    data["render_template_id"] = _validated_starting_format(
        str(data.get("render_template_id") or ""), str(data.get("kind") or "")
    )
    # A shared template filed under "내 템플릿" reads as somebody's private one
    # in every other account's gallery.
    if data.get("shared") and data.get("group") == _OWN_GROUP:
        data["group"] = _SHARED_GROUP
    template = Template(owner_id=user.id, **data)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    # Resolved here too, not only on the list: the gallery renders the row it
    # gets back, and a card whose form appears only after a reload reads as a
    # form that did not attach.
    return TemplateOut.of(template, await _form_file(db, template), owner_id=user.id)


async def _own_or_shared(db: DbSession, user: User, template_id: str) -> Template:
    """A template this account owns, or any shared one when an administrator asks.

    공용 템플릿 is instance configuration — it is registered and listed on the
    시스템 page, beside the proxy and the mail server — but the row still
    belongs to whichever administrator happened to add it. With ownership as
    the only test, a second administrator saw the list, saw the pencil and the
    bin beside every row, and neither did anything: the delete 404ed and the
    row came back on the next read. Nothing on screen said why, because from
    the screen's point of view nothing had happened.

    Private templates are untouched. Somebody's own drafts are not instance
    configuration and an administrator has no business in them.
    """
    row = await db.get(Template, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if row.owner_id != user.id and not (row.shared and user.role is UserRole.admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


@router.patch("/templates/{template_id}", response_model=TemplateOut)
async def patch_template(template_id: str, payload: TemplateIn, user: CurrentUser, db: DbSession):
    template = await _own_or_shared(db, user, template_id)
    fields = payload.model_dump(exclude_unset=True)
    if "shared" in fields:
        _may_share(user, bool(fields["shared"]))
    if "file_id" in fields:
        fields["file_id"] = await _own_file(db, user, fields["file_id"])
    if "render_template_id" in fields:
        # Against the kind being saved, which a patch may be changing in the
        # same call — checking the stored one would accept a 보고서 서식 onto a
        # starting point that is becoming a 슬라이드.
        fields["render_template_id"] = _validated_starting_format(
            str(fields["render_template_id"] or ""),
            str(fields.get("kind") or template.kind),
        )
    for field, value in fields.items():
        setattr(template, field, value)
    template.updated_at = utcnow()
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return TemplateOut.of(template, await _form_file(db, template), owner_id=user.id)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, user: CurrentUser, db: DbSession):
    template = await _own_or_shared(db, user, template_id)
    await db.delete(template)
    await db.commit()


# ══ design systems ═════════════════════════════════════════════════════
#
# Same ownership shape as templates: mine, plus whatever an administrator
# shared with the instance. A project points at one; everything that project
# produces reads it.


@router.get("/designs", response_model=list[DesignSystemOut])
async def list_designs(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(DesignSystem)
            .where(or_(DesignSystem.owner_id == user.id, col(DesignSystem.shared).is_(True)))
            .order_by(col(DesignSystem.shared).desc(), col(DesignSystem.created_at))
        )
    ).all()
    return [DesignSystemOut.of(d, owner_id=user.id) for d in rows]


@router.post("/designs/extract", response_model=DesignExtractOut)
async def extract_design(payload: DesignExtractIn, user: CurrentUser, db: DbSession):
    """Reads a design system out of a document or a page, and proposes it.

    Nothing is stored. The answer is a draft the editor opens on, because what
    comes back is one model's reading of a document and the person who owns
    that document is the one who can say whether it read it right.

    Charged like any other model call.
    """
    if bool(payload.file_id) == bool(payload.url):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file_or_url")

    if payload.file_id:
        stored = await db.get(StoredFile, payload.file_id)
        if stored is None or stored.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
        source, read_from = stored.text, stored.name
        if not source.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file_unreadable"
            )
    else:
        backends = await settings_store.tools_config()
        if not backends.fetch:
            # Said rather than guessed at: without the scraper this instance
            # cannot read a page at all, and a generic failure would send
            # somebody looking for a broken URL.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="fetch_unavailable"
            )
        source = await builtin_tools.scrape(backends.fetch, payload.url or "")
        read_from = payload.url or ""
        if not source.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="url_unreadable"
            )

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]),
        key=model_service.fallback_order,
    )
    model = usable[0] if usable else None
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        draft, usage = await design_extract.extract(
            source=source, model=model["id"], api_key=api_key
        )
    except design_extract.ExtractError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("design extraction failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="extract_failed"
        ) from exc

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(db, user, credits, reason="design.extract", model=model["id"])
    await db.commit()
    return DesignExtractOut(**draft, source=read_from, credits=credits)


@router.post("/designs", response_model=DesignSystemOut, status_code=status.HTTP_201_CREATED)
async def create_design(payload: DesignSystemIn, user: CurrentUser, db: DbSession):
    data = payload.model_dump()
    _may_share(user, bool(data.get("shared")))
    # Normalised on the way in, so a renderer never has to defend itself
    # against a colour that is not a colour.
    data["tokens"] = design_service.normalise_tokens(data.get("tokens"))
    data["craft"] = design_service.craft_keys(data.get("craft"))
    row = DesignSystem(owner_id=user.id, **data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return DesignSystemOut.of(row, owner_id=user.id)


@router.patch("/designs/{design_id}", response_model=DesignSystemOut)
async def patch_design(design_id: str, payload: DesignSystemIn, user: CurrentUser, db: DbSession):
    row = await _own(db, DesignSystem, "owner_id", user, design_id)
    fields = payload.model_dump(exclude_unset=True)
    if "shared" in fields:
        _may_share(user, bool(fields["shared"]))
    if "tokens" in fields:
        fields["tokens"] = design_service.normalise_tokens(fields["tokens"])
    if "craft" in fields:
        fields["craft"] = design_service.craft_keys(fields["craft"])
    for field, value in fields.items():
        setattr(row, field, value)
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return DesignSystemOut.of(row, owner_id=user.id)


@router.delete("/designs/{design_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_design(design_id: str, user: CurrentUser, db: DbSession):
    """Removes the look. Projects wearing it fall back to the defaults.

    The projects are detached rather than deleted, because a look is a
    decoration and a project is work. Done here rather than left to the
    `ON DELETE SET NULL` in migration 0021 so the rows this request already
    loaded agree with the database it returns to.
    """
    row = await _own(db, DesignSystem, "owner_id", user, design_id)
    await db.exec(
        update(Project).where(col(Project.design_system_id) == row.id).values(design_system_id=None)
    )
    await db.delete(row)
    await db.commit()


@router.post("/designs/delete")
async def delete_designs(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Several at once. Projects wearing any of them fall back to the defaults."""
    rows = await _owned_many(db, DesignSystem, "owner_id", user, payload.ids)
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(
        update(Project).where(col(Project.design_system_id).in_(ids)).values(design_system_id=None)
    )
    await db.exec(delete(DesignSystem).where(col(DesignSystem.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids)}


# ══ design templates ═══════════════════════════════════════════════════
#
# The rendering catalogue: shapes the model writes into. Ships inside the
# image rather than living in a table, so these are read-only — the thing a
# user writes for themselves is a prompt template, which does have one.


@router.get("/design-templates", response_model=list[DesignTemplateOut])
async def list_design_templates(user: CurrentUser, surface: str | None = None):
    """Every rendering template, or those for one surface."""
    rows = design_templates.all_templates()
    if surface:
        rows = [t for t in rows if t.surface.value == surface]
    return [DesignTemplateOut.of(t) for t in rows]


@router.get("/design-templates/usage", response_model=DesignTemplateUsageOut)
async def design_template_usage(user: CurrentUser, db: DbSession):
    """How often each rendering template has actually been started.

    The catalogue is ordered by id, which is the order the files happen to sit
    in and means nothing to anybody. The front door showed the first few of
    that order, so the shapes people reach for most were as likely to be on the
    second screen of the catalogue as on the home page.

    Two counts, because one is not enough on its own. `mine` is what this
    person keeps coming back to, and it is empty for everybody on their first
    day — so `popular` carries the ordering until they have a habit of their
    own, and then gets out of the way.

    `popular` counts sessions across the installation. It is an aggregate over
    a catalogue that ships in the image and is the same for everyone: it says
    how often a shape was picked, never by whom, and there is no id in it that
    is not already public.
    """
    counts: dict[str, dict[str, int]] = {"mine": {}, "popular": {}}
    for key, mine_only in (("mine", True), ("popular", False)):
        query = (
            select(ChatSession.render_template_id, func.count())
            .where(col(ChatSession.render_template_id).is_not(None))
            .group_by(col(ChatSession.render_template_id))
        )
        if mine_only:
            query = query.where(ChatSession.user_id == user.id)
        counts[key] = {template: total for template, total in await db.exec(query) if template}
    return DesignTemplateUsageOut(**counts)


@router.get("/design-templates/{template_id}/preview")
async def design_template_preview(template_id: str):
    """The 서식 itself, miniature — its seed rendered around its own sample.

    Seventeen 서식 differ almost entirely in CSS, and a card that carries only
    a name and a line cannot show CSS: the gallery read as seventeen copies of
    one shape. This returns the finished thing the card can shrink.

    Unauthenticated on purpose. The card reaches it as an iframe `src`, which
    cannot carry a header — and everything here ships inside the image: the
    seed, the sample, the default tokens. There is nothing of anybody's here.
    The frame that shows it is `sandbox=""`, and the seeds carry no script.
    """
    template = design_templates.get(template_id)
    if template is None or not template.sample or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    html = design_templates.render(
        template,
        title=template.name,
        tokens=design_service.normalise_tokens(None),
        body=template.sample,
    )
    return HTMLResponse(
        html,
        # A day: the content changes only when the image does.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/design-templates/{template_id}/style")
async def design_template_style(
    template_id: str,
    user: CurrentUser,
    accent: str | None = None,
    ink: str | None = None,
    muted: str | None = None,
    font: str | None = None,
):
    """This template's CSS, and the wrappers a section is written into.

    The document editor's half of `/preview`. The gallery card wants a finished
    document and shows it in a `sandbox=""` frame, where a sandbox is exactly
    right — nothing in a card is meant to be clicked. An editor has to be
    clicked, so the document lives in the page inside a shadow root, and a
    shadow root takes a stylesheet rather than a URL.

    Authenticated, unlike `/preview`: that route is reachable only by an iframe
    `src`, which cannot carry a header. Nothing here has that constraint, so
    nothing here gives up the check.
    """
    template = design_templates.get(template_id)
    if template is None or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    tokens = design_service.normalise_tokens(
        {"accent": accent, "ink": ink, "muted": muted, "font": font}
    )
    return {
        "css": design_templates.stylesheet(template, tokens),
        "wrapCover": template.wrap_cover,
        "wrapBlock": template.wrap_block,
        "wrapGroup": template.wrap_group,
    }


_FORM_MEDIA = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@router.get("/design-templates/{template_id}/form")
async def download_design_template_form(template_id: str, user: CurrentUser):
    """Return the template's real blank Office file for manual work."""
    chosen = design_templates.get(template_id)
    if chosen is None or not chosen.form_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_form_not_found"
        )
    path = Path(chosen.form_file)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_form_not_found"
        )
    return _attachment(
        path.read_bytes(),
        _FORM_MEDIA.get(path.suffix, "application/octet-stream"),
        re.sub(r'[\\/:*?"<>|]+', "_", chosen.name)[:60] or "form",
        path.suffix.lstrip("."),
    )


@router.get("/agents/{agent_id}/knowledge", response_model=list[FileOut])
async def list_agent_knowledge(agent_id: str, user: CurrentUser, db: DbSession):
    await _own(db, Agent, "owner_id", user, agent_id)
    rows = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.agent_id == agent_id, StoredFile.user_id == user.id)
            .order_by(col(StoredFile.created_at).desc())
        )
    ).all()
    return [FileOut.of(f) for f in rows]


@router.post(
    "/agents/{agent_id}/knowledge",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_agent_file(
    agent_id: str,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
):
    """An uploaded document, extracted and shelved."""
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file_too_large_{settings.max_upload_mb}mb",
        )
    stored = StoredFile(
        user_id=user.id,
        agent_id=agent_id,
        name=file_service.safe_name(file.filename or "file"),
        size=len(data),
        mime=file.content_type or "",
    )
    stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name, data)
    # Same as the project path: extraction failure is recorded, not raised. The
    # row is what makes the failure visible in the list.
    try:
        stored.text = await file_service.text_of(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001
        log.info("extraction failed for %s: %s", stored.name, exc)
        stored.error = str(exc)

    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    await _index(db, agent, stored)
    return FileOut.of(stored)


@router.post(
    "/agents/{agent_id}/knowledge/url",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_agent_url(agent_id: str, payload: KnowledgeUrl, user: CurrentUser, db: DbSession):
    """A page, read once and shelved as text.

    A snapshot, not a subscription — the page can change and this row will not.
    That is the honest behaviour for retrieval: an answer cites what was read,
    and re-reading on every turn would make the same question answerable
    differently on Tuesday for reasons nobody can see.
    """
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_url")

    backends = await settings_store.tools_config()
    if not backends.fetch:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="fetch_unavailable"
        )
    text = await builtin_tools.scrape(backends.fetch, url)
    if not text.strip():
        # 502 rather than an empty row: a shelf entry with no text is a document
        # the agent will report as present and can never quote.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="page_unreadable")

    stored = StoredFile(
        user_id=user.id,
        agent_id=agent_id,
        name=_page_name(url),
        size=len(text.encode()),
        mime="text/markdown",
        source_url=url,
        text=text,
        tokens=file_service.estimate_tokens(text),
    )
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    await _index(db, agent, stored)
    return FileOut.of(stored)


async def _index(db: DbSession, agent: Agent, stored: StoredFile) -> bool:
    """Send one shelved document to the retrieval index.

    After the commit and never blocking it: the row is the source of truth, so a
    document that could not be embedded is still attached and still found
    lexically. The outcome is stamped on the row, or the two states look alike.
    """
    if not stored.text.strip() or not await index_client.available():
        return False
    ok = await index_client.put_document(
        collection=await _shelf_key(db, agent),
        doc_id=stored.id,
        name=stored.name,
        text=stored.text,
        source_url=stored.source_url,
    )
    if ok:
        stored.indexed_at = utcnow()
        db.add(stored)
        # 임베딩한 청크 수를 원장에 남긴다 — 공짜 모델이라 크레딧은 0이지만
        # 사용량 화면이 bge 가 무슨 일을 얼마나 했는지 알아야 한다.
        if index_client.last_chunks:
            record_units(
                db,
                await db.get(User, agent.owner_id),
                reason="index.embed",
                model=index_client.EMBED_MODEL,
                units=index_client.last_chunks,
                unit="chunks",
            )
        await db.commit()
        await db.refresh(stored)
    return ok


def _page_name(url: str) -> str:
    """A readable name for a page: host plus the last path segment."""
    stripped = re.sub(r"^https?://", "", url).rstrip("/")
    host, _, path = stripped.partition("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return file_service.safe_name(f"{host}{f'-{tail}' if tail else ''}"[:120] or "page")


@router.post("/agents/{agent_id}/knowledge/reindex")
async def reindex_agent_knowledge(
    agent_id: str, user: CurrentUser, db: DbSession, force: bool = False
) -> dict[str, Any]:
    """Push this agent's documents into the retrieval index.

    Only the uncovered ones by default. `force=true` re-sends everything, which
    an embedding-model change needs — old vectors sit in a different space.

    Returns counts rather than raising, so a partial run keeps what it did.
    """
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    if not await index_client.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="index_unavailable"
        )
    rows = (
        await db.exec(
            select(StoredFile).where(StoredFile.agent_id == agent_id, StoredFile.user_id == user.id)
        )
    ).all()
    todo = [r for r in rows if r.text.strip() and (force or r.indexed_at is None)]
    done = 0
    for stored in todo:
        if await _index(db, agent, stored):
            done += 1
    return {"total": len(rows), "attempted": len(todo), "indexed": done}


@router.delete("/agents/{agent_id}/knowledge/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_knowledge(agent_id: str, file_id: str, user: CurrentUser, db: DbSession):
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id or stored.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if stored.storage_key:
        file_service.delete_blob(stored.storage_key)
    await db.delete(stored)
    await db.commit()
    # A document removed from the shelf must stop being findable. Left behind,
    # the agent would keep quoting a file its owner can no longer see.
    if agent.index_key:
        await index_client.forget_document(collection=agent.index_key, doc_id=file_id)


# ══ message rating ═════════════════════════════════════════════════════
#
# One turn's verdict, written where the transcript already reads. It lives
# here rather than beside the streaming endpoints because it is workspace
# bookkeeping — nothing about it touches a model or a credit.


@router.patch("/messages/{message_id}/rating", response_model=MessageOut)
async def rate_message(message_id: str, payload: MessageRatingIn, user: CurrentUser, db: DbSession):
    """Records what the reader thought of one answer, or takes it back.

    Ownership goes through the session: a message has no `user_id` of its own,
    and an id typed into this URL must not become a way to write on somebody
    else's transcript. A message that is not the caller's is `not_found`, the
    same answer every other object here gives.
    """
    message = await db.get(Message, message_id)
    session = await db.get(ChatSession, message.session_id) if message else None
    if message is None or session is None or session.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    # The buttons only exist under an answer, and a verdict on one's own
    # question would be a number nobody could act on.
    if message.role is not Role.assistant:
        raise HTTPException(status_code=422, detail="not_an_answer")
    message.rating = payload.rating
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.of(message)
