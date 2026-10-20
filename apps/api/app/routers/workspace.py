"""Owned-resource CRUD: projects, files, artifacts, skills, memories, agents, designs, ratings."""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, tuple_
from sqlmodel import col, delete, select, update

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentViewer, DbSession
from app.models.chat import ChatSession, Message, Role, SessionKind, follow_document_title
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
    UserPipeline,
    Visibility,
)
from app.pipeline import compose
from app.schemas.chat import MessageOut, MessageRatingIn
from app.schemas.workspace import (
    AgentCreateIn,
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
    FileTextOut,
    KnowledgeUrl,
    MemoryIn,
    MemoryOut,
    OpenedDocument,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    SectionFactCheck,
    SectionImage,
    SectionRewrite,
    SkillIn,
    SkillOut,
    SkillWarning,
    SlideDiagramPicture,
    SlideFactCheck,
    SlideImage,
    SlideRewrite,
    StoreSkillOut,
    ToolCatalogOut,
)
from app.services import (
    audit,
    critique,
    deck_export,
    design_extract,
    design_templates,
    diagram_render,
    doc_formats,
    elk_layout,
    factcheck,
    hangul,
    hwpx_import,
    index_client,
    lint,
    netguard,
    ocr_jobs,
    page_export,
    pictures,
    printing,
    report_export,
    richtext,
    settings_store,
    skill_import,
    skill_packages,
    starter,
    user_templates,
    workspace_context,
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
    """Name slug; Hangul kept."""
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


async def _claim_slug(
    db: DbSession, owner_id: str, wanted: str, *, except_id: str | None = None
) -> str:
    """Owner-unique agent slug (409 otherwise)."""
    slug = _slug(wanted)
    rows = (await db.exec(select(Agent).where(Agent.owner_id == owner_id))).all()
    if any(row.slug == slug and row.id != except_id for row in rows):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_taken")
    return slug


async def _own(db: DbSession, model, owner_field: str, user: User, item_id: str):
    row = await db.get(model, item_id)
    # published bot copies and their knowledge: immutable, owner included
    if row is None or getattr(row, owner_field) != user.id or await _bot_owned(db, row):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


async def _bot_owned(db: DbSession, row) -> bool:
    """Published version's agent copy or its file."""
    if isinstance(row, Agent):
        return bool(row.snapshot_of)
    if isinstance(row, StoredFile) and row.agent_id:
        agent = await db.get(Agent, row.agent_id)
        return agent is not None and bool(agent.snapshot_of)
    return False


async def _owned_many(db: DbSession, model, owner_field: str, user: User, ids: list[str]):
    """Owned rows among `ids`; unknown/foreign skipped, not 404."""
    if not ids:
        return []
    rows = (
        await db.exec(
            select(model).where(col(model.id).in_(ids), getattr(model, owner_field) == user.id)
        )
    ).all()
    return [row for row in rows if not await _bot_owned(db, row)]


async def _validate_skill_ids(
    db: DbSession,
    user: User,
    skill_ids: list[str] | None,
    *,
    grandfathered: set[str] | None = None,
) -> None:
    """Skill ids owned, enabled, unique; no cross-account refs."""
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


async def _skill_warnings(db: DbSession, agent: Agent) -> list[SkillWarning]:
    """Kept-on and pickable skills that apply on none of the agent's surfaces."""
    active = [str(x) for x in agent.active_skill_ids or []]
    available = [str(x) for x in agent.skill_ids or []]
    ids = list(dict.fromkeys([*active, *available]))
    if not ids:
        return []
    rows = {row.id: row for row in (
        await db.exec(select(Skill).where(col(Skill.id).in_(ids)))).all()}
    kept_on = [rows[i] for i in active if i in rows]
    pickable = [rows[i] for i in available if i in rows and i not in active]
    found = [*workspace_context.off_surface(agent.kinds, kept_on, place="active"),
             *workspace_context.off_surface(agent.kinds, pickable, place="available")]
    return [SkillWarning(**row) for row in found]


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


# ══ projects ══


async def _validated_checks(db: DbSession, user: User,
                            values: dict[str, Any]) -> dict[str, Any]:
    """Runnable check keys (shipped key or own `skill:<id>`), deduped; known `check_options` on."""
    from app.services import check_skills, skill_packages, skill_specs

    out: dict[str, Any] = {}
    if "check_skills" in values and values["check_skills"] is not None:
        keys = [str(k) for k in values["check_skills"]]
        own = [k.removeprefix(skill_specs.USER) for k in keys if k.startswith(skill_specs.USER)]
        mine = {row.id: row for row in (await db.exec(select(Skill).where(
            col(Skill.id).in_(own), Skill.owner_id == user.id))).all()} if own else {}

        def runnable(key: str) -> bool:
            if key.startswith(skill_specs.USER):
                row = mine.get(key.removeprefix(skill_specs.USER))
                return row is not None and bool(skill_specs.of_row(row).stage)
            return bool((p := skill_packages.package_for(key)) and p.stage)

        if not all(runnable(k) for k in keys):
            raise HTTPException(status_code=422, detail="check_skill_unknown")
        out["check_skills"] = list(dict.fromkeys(keys))
    if "check_options" in values:
        out["check_options"] = check_skills.clean_options(values["check_options"]) or None
    if "gap_questions" in values:
        from app.services import gap_questions

        raw = values["gap_questions"]
        try:
            out["gap_questions"] = None if raw is None else gap_questions.clean(raw)
        except gap_questions.QuestionError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
    if "process_settings" in values:
        from app.services import process_settings

        raw = values["process_settings"]
        try:
            out["process_settings"] = (process_settings.clean(raw) or None) if raw is not None \
                else None
        except process_settings.ProcessError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        if out["process_settings"] is not None:
            out["process_settings"] = process_settings.stored_form(out["process_settings"])
    return out


async def _validate_design_system_id(db: DbSession, user: User, design_id: str | None) -> None:
    """Design system owned or shared."""
    if design_id is None:
        return
    row = await db.get(DesignSystem, design_id)
    if row is None or (row.owner_id != user.id and not row.shared):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design_system_not_found")


#: agent fields binding a new conversation's opening
_AGENT_BINDINGS = ("surface", "render_template_id", "doc_format", "design_system_id")


async def _validate_agent_bindings(
    db: DbSession, user: User, values: dict[str, Any], *, check_design: bool = True,
    keep_format: str | None = None,
) -> dict[str, Any]:
    """Bindings with blanks → None, cross-checked against `kinds`."""
    out = {name: (values.get(name) or None) for name in _AGENT_BINDINGS}
    kinds = [str(k) for k in (values.get("kinds") or [])]
    allowed = set(kinds) if kinds else {kind.value for kind in SessionKind}
    if (surface := out["surface"]) is not None:
        if surface not in {kind.value for kind in SessionKind}:
            raise HTTPException(status_code=422, detail="agent_surface_unknown")
        if surface not in allowed:
            raise HTTPException(status_code=422, detail="agent_surface_not_in_kinds")
    if (template_id := out["render_template_id"]) is not None:
        # usable template: own or shared
        if not await user_templates.usable(db, user.id, template_id):
            raise HTTPException(status_code=404, detail="design_template_not_found")
        chosen = design_templates.choosable(template_id)
        if chosen is None or chosen.kind not in design_templates.HTML_KINDS:
            raise HTTPException(status_code=404, detail="design_template_not_found")
        if chosen.surface.value not in allowed:
            raise HTTPException(status_code=422, detail="design_template_surface_mismatch")
    if (format_id := out["doc_format"]) is not None:
        if format_id not in doc_formats.BY_ID or (
                format_id in doc_formats.RETIRED and format_id != keep_format):
            raise HTTPException(status_code=422, detail="doc_format_unknown")
        if SessionKind.report.value not in allowed:
            raise HTTPException(status_code=422, detail="doc_format_needs_report")
    if check_design:
        await _validate_design_system_id(db, user, out["design_system_id"])
    return _format_of_template(out)


def _format_of_template(bindings: dict[str, Any]) -> dict[str, Any]:
    """Stored format never contradicts a format-fixing template (same rule as `_apply_base`)."""
    template_id = bindings.get("render_template_id")
    chosen = design_templates.choosable(template_id) if template_id else None
    fixed = chosen.doc_format if chosen is not None else ""
    if fixed and bindings.get("doc_format") not in (None, fixed):
        bindings["doc_format"] = fixed if fixed in doc_formats.BY_ID \
            and fixed not in doc_formats.RETIRED else None
    return bindings


def _validated_render_templates(raw: dict[str, str] | None) -> dict[str, str] | None:
    """Validated per-surface template ids; unknown errors, empty removes."""
    if raw is None:
        return None
    chosen: dict[str, str] = {}
    for surface, template_id in raw.items():
        if not template_id:
            continue
        template = design_templates.choosable(template_id)
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


async def _remove_project(db: DbSession, project: Project) -> int:
    """Project and knowledge files deleted; sessions detached. Uncommitted; returns file count."""
    files = (
        await db.exec(select(StoredFile).where(StoredFile.project_id == project.id))
    ).all()
    for stored in files:
        file_service.delete_blob(stored.storage_key)
        await db.delete(stored)
    if project.index_key:
        # docs and conversation uploads share the collection
        await index_client.forget_collection(collection=project.index_key)
    for session in (
        await db.exec(select(ChatSession).where(ChatSession.project_id == project.id))
    ).all():
        session.project_id = None
        db.add(session)
    await db.delete(project)
    return len(files)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser, db: DbSession,
                         request: Request = None):  # type: ignore[assignment]
    files = await _remove_project(db, await _own(db, Project, "user_id", user, project_id))
    db.add(audit.deletion(user, request, action="project.delete", ids=[project_id],
                          counts={"projects": 1, "files": files}))
    await db.commit()


@router.post("/projects/delete")
async def delete_projects(payload: BulkDelete, user: CurrentUser, db: DbSession,
                          request: Request = None):  # type: ignore[assignment]
    """Bulk delete; returns count."""
    rows = await _owned_many(db, Project, "user_id", user, payload.ids)
    files = 0
    for row in rows:
        files += await _remove_project(db, row)
    if rows:
        db.add(audit.deletion(user, request, action="project.bulk_delete",
                              ids=[row.id for row in rows],
                              counts={"projects": len(rows), "files": files}))
    await db.commit()
    return {"deleted": len(rows)}


# ══ files ══


@router.get("/files", response_model=list[FileOut])
async def list_files(
    user: CurrentUser, db: DbSession, project_id: str | None = None, session_id: str | None = None
):
    # published knowledge copies belong to the bot
    copies = select(Agent.id).where(col(Agent.snapshot_of).is_not(None),
                                    Agent.owner_id == user.id)
    query = select(StoredFile).where(
        StoredFile.user_id == user.id,
        or_(col(StoredFile.agent_id).is_(None), col(StoredFile.agent_id).not_in(copies)),
    )
    if project_id:
        query = query.where(StoredFile.project_id == project_id)
    if session_id:
        query = query.where(StoredFile.session_id == session_id)
    rows = (await db.exec(query.order_by(col(StoredFile.created_at).desc()))).all()
    return [FileOut.of(f) for f in rows]


@router.get("/files/{file_id}", response_model=FileOut)
async def get_file(file_id: str, user: CurrentUser, db: DbSession):
    """One own file (page-reading progress screen)."""
    return FileOut.of(await _own(db, StoredFile, "user_id", user, file_id))


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
        # Content-Type is a claim; bytes decide
        mime=file_service.detected_mime(file.filename or "file", file.content_type, data),
    )
    stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name, data)

    # extraction failure recorded, not raised
    try:
        stored.text = await file_service.text_of(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001
        log.info("extraction failed for %s: %s", stored.name, exc)
        stored.error = str(exc)
    # scanned pages read in background; text layer kept meanwhile
    reading = await ocr_jobs.queue_if_scanned(stored, data)

    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    if reading:
        ocr_jobs.wake()
    # indexed for `search_knowledge`; never blocks the upload
    if stored.text.strip():
        if project_id:
            project = await db.get(Project, project_id)
            if project is not None:
                await _index_document(db, user.id, await _project_shelf_key(db, project), stored)
        elif session_id:
            session = await db.get(ChatSession, session_id)
            if session is not None and session.user_id == user.id:
                await _index_document(db, user.id, await _session_shelf_key(db, session), stored)
    return FileOut.of(stored)


async def reindex_session_files(db: DbSession, user_id: str, session: ChatSession) -> int:
    """Moved conversation's uploads re-indexed into the new collection; returns count."""
    rows = (
        await db.exec(
            select(StoredFile).where(
                StoredFile.session_id == session.id, StoredFile.user_id == user_id
            )
        )
    ).all()
    moved = 0
    for stored in rows:
        if not stored.indexed_at or not stored.text.strip():
            continue
        if stored.index_collection and not await index_client.forget_document(
            collection=stored.index_collection, doc_id=stored.id
        ):
            # old entry not removed: record kept pointing at it, no second copy
            log.warning(
                "file %s stays in %s: index delete failed", stored.id, stored.index_collection
            )
            continue
        if await _index_document(db, user_id, await _session_shelf_key(db, session), stored):
            moved += 1
    return moved


async def forget_session_files(db: DbSession, session_ids: list[str]) -> int:
    """Pre-delete vector removal for conversation uploads; rows stay in file box."""
    if not session_ids:
        return 0
    rows = (
        await db.exec(select(StoredFile).where(col(StoredFile.session_id).in_(session_ids)))
    ).all()
    gone = 0
    for stored in rows:
        if stored.indexed_at and stored.index_collection:
            if not await index_client.forget_document(
                collection=stored.index_collection, doc_id=stored.id
            ):
                # still indexed: record kept for retry
                log.warning(
                    "file %s stays in %s: index delete failed", stored.id, stored.index_collection
                )
                continue
            gone += 1
        stored.indexed_at = None
        stored.index_collection = None
        db.add(stored)
    return gone


async def _project_shelf_key(db: DbSession, project: Project) -> str:
    """Project collection key, minted on first use."""
    if not project.index_key:
        project.index_key = index_client.new_collection_key()
        db.add(project)
        await db.commit()
        await db.refresh(project)
    return project.index_key


async def _session_shelf_key(db: DbSession, session: ChatSession) -> str:
    """Conversation upload collection: project's if any, else own (minted on first use)."""
    if session.project_id:
        project = await db.get(Project, session.project_id)
        if project is not None:
            return await _project_shelf_key(db, project)
    if not session.index_key:
        session.index_key = index_client.new_collection_key()
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session.index_key


@router.post(
    "/projects/{project_id}/knowledge/url",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_url(project_id: str, payload: KnowledgeUrl, user: CurrentUser, db: DbSession):
    """Web page snapshot as project knowledge."""
    await _own(db, Project, "user_id", user, project_id)
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_url")
    if reason := await netguard.refusal(url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
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
    project = await db.get(Project, project_id)
    if project is not None:
        await _index_document(db, user.id, await _project_shelf_key(db, project), stored)
    return FileOut.of(stored)


@router.get("/files/{file_id}/content")
async def download_file(file_id: str, user: CurrentViewer, db: DbSession):
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    try:
        data = file_service.read_blob(stored.storage_key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    # inline only for byte-proven picture/PDF/media; no script, no origin
    media, inline = file_service.served_as(stored.name, stored.mime, data)
    return Response(
        content=data,
        media_type=media,
        headers=file_service.download_headers(media, inline, stored.name),
    )


#: characters the file box preview shows; the rest is in the download
PREVIEW_CHARS = 40_000


@router.get("/files/{file_id}/text", response_model=FileTextOut)
async def file_text(file_id: str, user: CurrentViewer, db: DbSession):
    """An own file's extracted text, for previewing what has no inline view."""
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    text = stored.text or ""
    return FileTextOut(text=text[:PREVIEW_CHARS], truncated=len(text) > PREVIEW_CHARS)


@router.post("/files/{file_id}/open-as-document", response_model=OpenedDocument)
async def open_file_as_document(file_id: str, user: CurrentUser, db: DbSession):
    """Uploaded `.hwpx` → editable report session; no model call or charge."""
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
async def delete_file(file_id: str, user: CurrentUser, db: DbSession,
                      request: Request = None):  # type: ignore[assignment]
    owned = await _own(db, StoredFile, "user_id", user, file_id)
    # shelf (key mint commits) before row lock: nothing commits between lock and delete
    shelf = await shelf_of(db, owned) if owned.ocr_state else None
    # locked fresh read: concurrent page reader is seen here or sees the row gone
    stored = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _drop_file(db, stored, shelf)
    db.add(audit.deletion(user, request, action="file.delete", ids=[stored.id],
                          counts={"files": 1}, extra={"bytes": stored.size}))
    await db.commit()


async def _drop_file(db: DbSession, stored: StoredFile, shelf: str | None = None) -> None:
    """A file's blob, its search entries and its row; the caller commits."""
    file_service.delete_blob(stored.storage_key)
    if shelf and not stored.indexed_at:
        # read pages may be heading into the shelf
        await index_client.forget_document(collection=shelf, doc_id=stored.id)
    if stored.indexed_at:
        # recorded collection, else every candidate (project's or conversation's)
        collections: list[str] = [stored.index_collection] if stored.index_collection else []
        if stored.project_id and not collections:
            project = await db.get(Project, stored.project_id)
            if project is not None and project.index_key:
                collections.append(project.index_key)
        elif stored.session_id and not collections:
            session = await db.get(ChatSession, stored.session_id)
            if session is not None:
                if session.project_id:
                    project = await db.get(Project, session.project_id)
                    if project is not None and project.index_key:
                        collections.append(project.index_key)
                if session.index_key:
                    collections.append(session.index_key)
        for collection in collections:
            if not await index_client.forget_document(collection=collection, doc_id=stored.id):
                log.warning("file %s: index delete from %s failed", stored.id, collection)
    await db.delete(stored)


async def delete_session_files(db: DbSession, session_ids: list[str]) -> int:
    """Before conversations are deleted, when the person chose so: the files attached or made
    in them go too. A file a project or an agent holds stays. The caller commits."""
    if not session_ids:
        return 0
    rows = (await db.exec(select(StoredFile).where(
        col(StoredFile.session_id).in_(session_ids), col(StoredFile.project_id).is_(None),
        col(StoredFile.agent_id).is_(None)))).all()
    for stored in rows:
        shelf = await shelf_of(db, stored) if stored.ocr_state else None
        await _drop_file(db, stored, shelf)
    return len(rows)


# ══ artifacts ══


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
    """Artifact cards page, newest first; `q` matches title."""
    # keyset pagination on (before_at, before_id)
    query = select(Artifact).where(Artifact.user_id == user.id)
    if kind:
        query = query.where(Artifact.kind == kind)
    if project_id:
        query = query.where(Artifact.project_id == project_id)
    if q and q.strip():
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
    """Per-kind artifact counts for gallery tabs."""
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
    fields = payload.model_dump()
    # no unsanitised HTML reaches storage
    fields["data"] = _clean_report_data(fields.get("data"))
    artifact = Artifact(user_id=user.id, **fields)
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(artifact_id: str, user: CurrentUser, db: DbSession):
    return ArtifactOut.of(await _own(db, Artifact, "user_id", user, artifact_id))


def _clean_report_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Report data with HTML section bodies sanitised."""
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
    """Data with `lint` recomputed."""
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
    except Exception:  # noqa: BLE001 — unreadable data must not block a save
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

    # optimistic concurrency: check in this transaction
    if expected is not None and expected != artifact.version:
        raise HTTPException(
            status_code=409,
            detail=(
                "이 아티팩트는 다른 곳에서 이미 수정되었습니다. "
                "최신 내용을 받은 뒤 다시 저장하세요."
            ),
        )

    if changes.get("project_id"):
        # own project only; foreign → 404
        await _own(db, Project, "user_id", user, changes["project_id"])

    if "data" in changes and artifact.kind is ArtifactKind.report:
        # browser HTML sanitised at its only boundary
        changes["data"] = _clean_report_data(changes["data"])

    if "data" in changes and isinstance(changes["data"], dict):
        changes["data"] = _relint(artifact.kind, changes["data"])

    if "data" in changes:
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
    if "title" in changes:
        await _name_session_after(db, user, artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


async def _name_session_after(db: DbSession, user: User, artifact: Artifact) -> None:
    """Owning report/slides conversation retitled."""
    if not artifact.session_id:
        return
    session = await db.get(ChatSession, artifact.session_id)
    if (
        session is not None
        and session.user_id == user.id
        and session.artifact_id == artifact.id
        and follow_document_title(session, artifact.title)
    ):
        db.add(session)



async def _edit_model(
    db: DbSession,
    user: User,
    request: Request | None,
    artifact: Artifact | None,
    listing: dict | list[dict],
    *,
    kind: str,
    text: str,
    follow_session: bool = True,
    searches: bool = False,
    own_file: bool = False,
) -> dict:
    """The model a document edit may use, inside the chat turn's data boundary.

    Internal material, a strict-local conversation or personal data stay on
    strict-local models, refused when there is none; the account's allowlist holds.
    A web fact-check sends its claims to search engines, so it is refused inside.
    `listing`: `list_models()`, whose admin defaults apply, or its bare model rows.
    """
    from app.routers import sessions as chat

    catalogue = listing["models"] if isinstance(listing, dict) else listing
    defaults = (
        model_service.configured_defaults(listing, kind) if isinstance(listing, dict) else ()
    )

    session = (
        await db.get(ChatSession, artifact.session_id)
        if artifact is not None and artifact.session_id
        else None
    )
    boundary = await chat._edit_boundary(db, session, catalogue, text, own_file=own_file)
    if boundary.strict and searches:
        await chat._audit_media_boundary(
            user, request, session, boundary, refused=True, surface="edit"
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="factcheck_needs_web")
    asked = (session.model or "") if session is not None and follow_session else ""
    if boundary.strict and asked.startswith("local/"):
        # the same model's strict-local entry, when the account may use it
        asked = "strict-local/" + asked.split("/", 1)[1]
    model = chat._media_model(
        user, catalogue, kind=kind, boundary=boundary, asked=asked, defaults=defaults
    )
    if model is None:
        if boundary.strict:
            await chat._audit_media_boundary(
                user, request, session, boundary, refused=True, surface="edit"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="internal_data_requires_strict_local",
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    await chat._audit_media_boundary(user, request, session, boundary, surface="edit")
    return model

@router.post("/artifacts/{artifact_id}/slides/factcheck", response_model=ArtifactOut)
async def factcheck_slide(
    artifact_id: str, payload: SlideFactCheck, user: CurrentUser, db: DbSession, request: Request
):
    """Web fact-check of one slide; verdicts stored. Charged."""
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
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind="chat", follow_session=False, searches=True,
        text=json.dumps(target, ensure_ascii=False),
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

    verdicts, usage = await factcheck.check_slide(slide=target, model=model["id"], api_key=api_key)
    target["factCheck"] = verdicts
    data["slides"] = slides
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    # no version snapshot: annotation only

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
    """Browser-rendered mermaid image stored for exporters."""
    # source-keyed cache: no snapshot, no charge
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")
    if not pictures.decode(payload.src):
        # embedded `data:` only; never fetched
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_embedded")

    data = dict(artifact.data or {})
    sections = [dict(row) for row in (data.get("sections") or [])]
    target = next((row for row in sections if row.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    store = dict(target.get("diagrams") or {})
    if store.get(payload.key) == payload.src:
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
    artifact_id: str, payload: SectionFactCheck, user: CurrentUser, db: DbSession, request: Request
):
    """Web fact-check of one report section; verdicts stored. Charged."""
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
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind="chat", follow_session=False, searches=True,
        text=f"{target.get('heading') or ''}\n{target.get('content') or ''}",
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
    # no version snapshot: annotation only

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


def _reviewable(artifact: Artifact) -> tuple[str, str]:
    """`(body, rubric)` for critique, or `("", "")`."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.report:
        # Markdown, not raw HTML, for the reviewer
        parts = [
            {"heading": s.get("heading") or "", "text": richtext.as_markdown(s)}
            for s in (data.get("sections") or [])
        ]
        return critique.document(parts), ""
    if artifact.kind is ArtifactKind.deck:
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
async def critique_artifact(artifact_id: str, user: CurrentUser, db: DbSession, request: Request):
    """Model critique stored as `data.critique`. Charged; non-blocking."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    body, rubric = _reviewable(artifact)
    if not body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_reviewable")

    catalogue = await model_service.list_models()
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind="chat", text=f"{artifact.title or ''}\n{body}",
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
    except Exception as exc:  # noqa: BLE001 — reason, not a 500
        log.warning("critique failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="critique_failed"
        ) from exc

    data = dict(artifact.data or {})
    # no version snapshot: annotation only
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
        surface="slides" if artifact.kind == ArtifactKind.deck else "report",
    )
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


#: max inlined base64 image
_MAX_EMBED_BYTES = 3 * 1024 * 1024


async def _picture_bytes(db: DbSession, user: User, artifact_id: str) -> tuple[str, bytes]:
    """`(mime, bytes)` of an owned embeddable image under the cap."""
    picture = await _own(db, Artifact, "user_id", user, artifact_id)
    if picture.kind is not ArtifactKind.image:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_an_image")
    # `src` = /api/files/{id}/content
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


async def _figure_of(db: DbSession, user: User, artifact_id: str) -> dict | None:
    """Owned drawn-figure image data (keeps flowchart source), else None."""
    picture = await db.get(Artifact, artifact_id)
    if picture is None or picture.user_id != user.id or picture.kind is not ArtifactKind.image:
        return None
    data = picture.data or {}
    source = str(data.get("source") or "")
    return data if data.get("style") == "figure" and source.strip() else None


@router.post("/artifacts/{artifact_id}/slides/image", response_model=ArtifactOut)
async def add_slide_image(artifact_id: str, payload: SlideImage, user: CurrentUser, db: DbSession):
    """Owned image on one slide as `data:` URI. Snapshotted, no charge."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")

    mime, blob = await _picture_bytes(db, user, payload.artifact_id)
    figure = await _figure_of(db, user, payload.artifact_id)
    if figure is not None:
        # drawn figure keeps source: .pptx shapes; picture redrawn in slide accent
        source = str(figure.get("source") or "")
        key = report_export.diagram_key(source)
        caption = payload.caption.strip() or str(figure.get("caption") or "")
        png = await diagram_render.render_png(
            source, str(target.get("accent") or "") or "#2563EB"
        )
        target["diagram"] = {
            "figure": str(figure.get("figure") or "method"),
            "description": str(figure.get("prompt") or ""),
            "source": source,
            "caption": caption,
            "key": key,
        }
        target["image"] = {
            "src": diagram_render.data_uri(png) if png else pictures.encode(mime, blob),
            "caption": caption,
            "fit": "contain",
            "position": "right",
            "size": "full",
            "diagram": True,
            **({"renderer": "pptx", "key": key} if png or figure.get("renderer") == "pptx"
               else {}),
        }
    else:
        target["image"] = {
            "src": pictures.encode(mime, blob),
            "caption": payload.caption.strip(),
        }
        # hand-placed picture replaces deck's own figure
        target.pop("diagram", None)

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


@router.post("/artifacts/{artifact_id}/slides/diagram", response_model=ArtifactOut)
async def store_slide_diagram(
    artifact_id: str, payload: SlideDiagramPicture, user: CurrentUser, db: DbSession
):
    """Browser raster of a slide's figure stored as its picture."""
    # exporters read `image.src`; no snapshot, no charge
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")
    if not pictures.decode(payload.src):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_embedded")

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")
    diagram = target.get("diagram") or {}
    if not diagram.get("source"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no_diagram")
    if payload.key != report_export.diagram_key(str(diagram["source"])):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="diagram_stale")

    current = dict(target.get("image") or {})
    if current.get("src") and not current.get("diagram"):
        # user picture wins over figure raster
        return ArtifactOut.of(artifact)
    if current.get("renderer") == "pptx" and current.get("key") == payload.key:
        # server-drawn figure wins over browser mermaid raster
        return ArtifactOut.of(artifact)
    if current.get("src") == payload.src:
        return ArtifactOut.of(artifact)
    target["image"] = {
        "src": payload.src,
        "caption": str(current.get("caption") or diagram.get("caption") or ""),
        "fit": "contain",
        "position": str(current.get("position") or "right"),
        "size": str(current.get("size") or "full"),
        "diagram": True,
    }
    data["slides"] = slides
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/artifacts/{artifact_id}/blocks/image", response_model=ArtifactOut)
async def add_block_image(artifact_id: str, payload: BlockImage, user: CurrentUser, db: DbSession):
    """Owned image into one HTML block. Snapshotted, no charge."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.html:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_page")

    data = dict(artifact.data or {})
    blocks = [dict(b) for b in (data.get("blocks") or [])]
    if payload.index >= len(blocks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="block_not_found")
    template = design_templates.rendering(str(data.get("templateId") or ""))
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
    # same sanitiser as model output
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
    """Owned image appended to a report section. Snapshotted, no charge."""
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
        target["content"] = design_templates.sanitise(f"{body}{figure}", editable_styles=True)
    else:
        # alt text = exported caption
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
async def rewrite_block(
    artifact_id: str, payload: BlockRewrite, user: CurrentUser, db: DbSession, request: Request
):
    """One HTML block rewrite, `content` re-rendered. Charged, snapshotted."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.html:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_page")

    data = dict(artifact.data or {})
    blocks = [dict(b) for b in (data.get("blocks") or [])]
    if payload.index >= len(blocks):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="block_not_found")
    template = design_templates.rendering(str(data.get("templateId") or ""))
    if template is None or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="design_template_missing"
        )
    if "html" not in blocks[payload.index]:
        # no stored markup: lossless re-render impossible
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blocks_not_editable")

    catalogue = await model_service.list_models()
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind=template.surface.value,
        text=f"{artifact.title or ''}\n{payload.note or ''}\n"
        + json.dumps(blocks, ensure_ascii=False),
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
    except Exception as exc:  # noqa: BLE001 — reason, not a 500
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
    # embedded pictures survive the rewrite
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
    artifact_id: str, payload: SectionRewrite, user: CurrentUser, db: DbSession, request: Request
):
    """One report section rewrite. Charged, snapshotted."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")

    data = dict(artifact.data or {})
    sections = [dict(s) for s in (data.get("sections") or [])]
    target = next((s for s in sections if s.get("id") == payload.section_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="section_not_found")

    catalogue = await model_service.list_models()
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind="report",
        text=f"{data.get('request') or artifact.title or ''}\n{payload.note or ''}\n"
        + json.dumps(sections, ensure_ascii=False),
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
            sections=richtext.normalise(sections),
            target_id=payload.section_id,
            model=model["id"],
            api_key=api_key,
            note=payload.note,
            sources=list(data.get("sources") or []),
            doc_format=str((data.get("titleBlock") or {}).get("format") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — reason, not a 500
        log.warning("section rewrite failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_failed"
        ) from exc

    if not body.strip():
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="rewrite_empty")

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('heading')} 다시 씀",
        )
    )
    target["content"] = pictures.drop_broken(hangul.tidy_spacing(hangul.read_back(body)[0]))
    # model output is Markdown
    target["format"] = "markdown"
    target["status"] = "done"
    data["sections"] = sections
    data["wordCount"] = report_service.word_count(sections)
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
async def rewrite_slide(
    artifact_id: str, payload: SlideRewrite, user: CurrentUser, db: DbSession, request: Request
):
    """One slide rewrite. Charged, snapshotted."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.deck:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_deck")

    data = dict(artifact.data or {})
    slides = [dict(s) for s in (data.get("slides") or [])]
    target = next((s for s in slides if s.get("id") == payload.slide_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")

    catalogue = await model_service.list_models()
    model = await _edit_model(
        db, user, request, artifact, catalogue,
        kind="slides",
        text=f"{artifact.title or ''}\n{payload.note or ''}\n"
        + json.dumps(slides, ensure_ascii=False),
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
    except Exception as exc:  # noqa: BLE001 — reason, not a 500
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
    """Superseded revisions, newest first."""
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
    """One superseded revision with its body."""
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
    """Old revision restored as new version; current snapshotted first."""
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
    artifact.data = _clean_report_data(target.data)
    artifact.storage_key = target.storage_key
    # reports keep title in `data`
    if isinstance(target.data, dict) and str(target.data.get("title") or "").strip():
        artifact.title = str(target.data["title"])[:200]
        await _name_session_after(db, user, artifact)
    artifact.updated_at = utcnow()
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(artifact_id: str, user: CurrentUser, db: DbSession,
                          request: Request = None):  # type: ignore[assignment]
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    await db.exec(delete(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id))
    await db.delete(artifact)
    db.add(audit.deletion(user, request, action="artifact.delete", ids=[artifact.id],
                          counts={"artifacts": 1}, extra={"kind": str(artifact.kind)}))
    await db.commit()


@router.post("/artifacts/delete")
async def delete_artifacts(payload: BulkDelete, user: CurrentUser, db: DbSession,
                           request: Request = None):  # type: ignore[assignment]
    """Bulk delete; source sessions untouched."""
    rows = await _owned_many(db, Artifact, "user_id", user, payload.ids)
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(ids)))
    await db.exec(delete(Artifact).where(col(Artifact.id).in_(ids)))
    db.add(audit.deletion(user, request, action="artifact.bulk_delete", ids=ids,
                          counts={"artifacts": len(ids)}))
    await db.commit()
    return {"deleted": len(ids)}


# ══ skills, store (shared publish/install helpers) ══


async def _admin_ids(db: DbSession, owner_ids: set[str]) -> set[str]:
    """Admin subset of `owner_ids`, read live."""
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
    """Org-shared row owned by another; 409 on own."""
    row = await db.get(model, item_id)
    if row is None or row.visibility is not Visibility.org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if row.owner_id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_yours")
    return row


def _copy_of(rows: list, origin) -> Any | None:
    """This account's copy: by `origin_id`, then `catalog_key`, then slug (seeded copies)."""
    for row in rows:
        if row.origin_id == origin.id:
            return row
    if origin.catalog_key:
        for row in rows:
            if row.catalog_key == origin.catalog_key:
                return row
        for row in rows:
            if row.slug == origin.slug:
                return row
    return None


async def _install_skill(db: DbSession, user: User, origin: Skill) -> Skill:
    """Shared skill copied in, or existing copy. Uncommitted."""
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
        source=SkillSource.workspace,
        kinds=list(origin.kinds or []),
        required_tools=list(origin.required_tools or []),
        estimated_tokens=origin.estimated_tokens
        or starter.estimate_tokens(origin.when_to_use, origin.body, origin.description),
        version=origin.version,
        enabled=True,
        visibility=Visibility.private,
        origin_id=origin.id,
        # server fields/scripts travel (pre-approved); shipped copies store none, follow package
        server_fields=dict(origin.server_fields)
        if origin.server_fields is not None and not origin.catalog_key else None,
        script_files=[dict(f) for f in origin.script_files or []] or None,
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
    # copies say whose shared skill they came from
    origin_ids = [s.origin_id for s in rows if s.origin_id]
    origins: dict[str, Skill] = {}
    if origin_ids:
        origins = {row.id: row for row in (await db.exec(select(Skill).where(
            col(Skill.id).in_(origin_ids)))).all()}
    owners = {origin.owner_id for origin in origins.values()}
    names = await _owner_names(db, owners)
    admins = await _admin_ids(db, owners)
    out = []
    for s in rows:
        item = SkillOut.of(s)
        origin = origins.get(s.origin_id or "")
        if origin is not None:
            item.origin_owner_name = names.get(origin.owner_id, "")
            # an administrator's shared skill reads 「공식」, as their agents do
            item.origin_official = origin.owner_id in admins
            item.edited = any(getattr(s, f) != getattr(origin, f)
                              for f in ("name", "description", "when_to_use", "body"))
        out.append(item)
    return out


@router.get("/tools", response_model=list[ToolCatalogOut])
async def list_tool_catalog(user: CurrentUser, db: DbSession):
    return [ToolCatalogOut(**row) for row in await tool_registry.tool_catalog(db, user)]


@router.get("/skills/store", response_model=list[StoreSkillOut])
async def list_skill_store(user: CurrentUser, db: DbSession):
    """Org-shared skills of others; unlisted shipped ones admin-only (「숨김 포함」)."""
    rows = (
        await db.exec(
            select(Skill)
            .where(Skill.visibility == Visibility.org, Skill.owner_id != user.id)
            .order_by(col(Skill.name))
        )
    ).all()
    if user.role is not UserRole.admin:
        rows = [row for row in rows if skill_packages.listed(row.catalog_key)]
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


def _server_fields(raw: Any, *, own_scripts: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """`server` fields vs shipped allowlists; 422 on first refusal; None if empty."""
    from app.services import skill_specs

    data = raw.model_dump() if hasattr(raw, "model_dump") else raw
    fields, errors = skill_specs.validate(data, own_scripts=own_scripts)
    if errors:
        raise HTTPException(status_code=422, detail=errors[0].code)
    return None if skill_specs.empty(fields) else fields


@router.get("/skills/server-catalogue")
async def skill_server_catalogue(user: CurrentUser):
    """Allowed skill server-field values, each with its shipped user."""
    from app.services import skill_specs

    return skill_specs.catalogue()


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillIn, user: CurrentUser, db: DbSession):
    await _validate_tool_names(db, user, payload.required_tools)
    values = payload.model_dump(exclude={"server"})
    values["server_fields"] = _server_fields(payload.server) if payload.server else None
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


_SHARED_TEXT = ("visibility", "name", "description", "when_to_use", "body")


def _script_names(skill: Skill) -> tuple[str, ...]:
    return tuple(str(f.get("name") or "") for f in skill.script_files or []
                 if isinstance(f, dict))


def _check_skill_share(skill: Skill, changes: dict[str, Any]) -> None:
    """Share guard: import scan re-run; block-level findings (secrets, bidi controls) refused."""
    shared = changes.get("visibility", skill.visibility) == Visibility.org
    if not shared or not any(field in changes for field in _SHARED_TEXT):
        return
    found = skill_import.blocking_on_share(
        *(str(changes.get(f, getattr(skill, f)) or "") for f in _SHARED_TEXT[1:])
    )
    if found:
        codes = ",".join(sorted({f.code for f in found}))
        raise HTTPException(
            status_code=422, detail=f"skill_share_blocked:{codes}"
        )


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
    _check_skill_share(skill, changes)
    if "server" in changes:
        changes.pop("server")
        skill.server_fields = _server_fields(
            payload.server or {}, own_scripts=_script_names(skill))
    if changes.get("visibility") == Visibility.org and skill.script_files \
            and skill.review != "approved":
        # scripts: shared only after admin approval
        changes["visibility"] = Visibility.private
        skill.review, skill.review_note = "pending", ""
    elif changes.get("visibility") == Visibility.private and skill.review == "pending":
        skill.review = ""
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
async def delete_skill(skill_id: str, user: CurrentUser, db: DbSession,
                       request: Request = None):  # type: ignore[assignment]
    await db.delete(await _own(db, Skill, "owner_id", user, skill_id))
    db.add(audit.deletion(user, request, action="skill.delete", ids=[skill_id],
                          counts={"skills": 1}))
    await db.commit()


@router.post("/skills/delete")
async def delete_skills(payload: BulkDelete, user: CurrentUser, db: DbSession,
                        request: Request = None):  # type: ignore[assignment]
    rows = await _owned_many(db, Skill, "owner_id", user, payload.ids)
    for row in rows:
        await db.delete(row)
    if rows:
        db.add(audit.deletion(user, request, action="skill.bulk_delete",
                              ids=[row.id for row in rows], counts={"skills": len(rows)}))
    await db.commit()
    return {"deleted": len(rows)}


# ══ memories ══


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
async def delete_memory(memory_id: str, user: CurrentUser, db: DbSession,
                        request: Request = None):  # type: ignore[assignment]
    await db.delete(await _own(db, Memory, "user_id", user, memory_id))
    db.add(audit.deletion(user, request, action="memory.delete", ids=[memory_id],
                          counts={"memories": 1}))
    await db.commit()


# ══ agents ══


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
    """Org agents hidden when a same-name seeded copy (no `origin_id`) is held."""
    legacy = {
        row.name
        for row in rows
        if row.owner_id == user_id and row.visibility != "org" and row.origin_id is None
    }
    return [row for row in rows if row.visibility != "org" or row.name not in legacy]


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Agent)
            .where((Agent.owner_id == user.id) | (Agent.visibility == "org"),
                   col(Agent.snapshot_of).is_(None))
            .order_by(col(Agent.name))
        )
    ).all()
    rows = _without_legacy_copies(rows, user_id=user.id)
    # retired agents hidden; drafts admin-only; withdrawn catalogue rows offered to no one
    # (their copies, conversations and bots keep working)
    hidden = starter.withdrawn_agents() | (
        starter.draft_agents() if user.role is not UserRole.admin else frozenset())
    rows = [a for a in rows if not a.retired and not (
        a.catalog_key and a.origin_id is None and a.catalog_key in hidden)]

    owner_ids = {a.owner_id for a in rows}
    names = await _owner_names(db, owner_ids)
    admins = await _admin_ids(db, owner_ids)
    mine = [a for a in rows if a.owner_id == user.id]
    # `has_knowledge`: own files only (shared shelves unreadable)
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
    design_ids = {a.design_system_id for a in rows if a.design_system_id}
    design_names = (
        dict(
            (
                await db.exec(
                    select(DesignSystem.id, DesignSystem.name).where(
                        col(DesignSystem.id).in_(design_ids)
                    )
                )
            ).all()
        )
        if design_ids
        else {}
    )
    processes = await _processes(db, rows, viewer_id=user.id)
    published = await _published(db, user, mine)
    return [
        AgentOut.of(
            agent,
            owner_name=names.get(agent.owner_id, ""),
            design_name=design_names.get(agent.design_system_id or "", ""),
            has_knowledge=agent.id in readable_shelves,
            official=agent.owner_id in admins,
            installed=(agent.owner_id != user.id and _copy_of(mine, agent) is not None),
            viewer_id=user.id,
            process=processes.get(agent.id),
            publication=published.get(agent.id),
        )
        for agent in rows
    ]


async def _published(db: DbSession, user: User, mine: list[Agent]) -> dict[str, dict]:
    """Owner's bots by source agent."""
    from app.models.publishing import Publication, PublicationStatus

    if not mine:
        return {}
    rows = (await db.exec(select(Publication).where(
        Publication.owner_id == user.id, col(Publication.agent_id).in_([a.id for a in mine]),
        Publication.status != PublicationStatus.revoked.value))).all()
    return {p.agent_id: {"id": p.id, "slug": p.slug, "status": p.status} for p in rows}


async def _processes(db: DbSession, rows: list[Agent], *, viewer_id: str) -> dict[str, dict]:
    """Each agent's 진행 과정, pipelines in one query."""
    from app.routers.user_pipelines import _output, bundles_named
    from app.services import bases

    ids = {a.pipeline_id for a in rows if a.pipeline_id}
    pipes = {
        p.id: p
        for p in (
            await db.exec(select(UserPipeline).where(col(UserPipeline.id).in_(ids)))
        ).all()
    } if ids else {}
    named = {a.id: compose.AgentInfo(id=a.id, name=a.name, output=_output(a)) for a in rows}
    known = await bundles_named(db, [s for a in rows for s in (a.bundles or [])], viewer_id)
    out = {}
    for agent in rows:
        row = pipes.get(agent.pipeline_id or "")
        if row is not None and row.owner_id != agent.owner_id:
            row = None
        titles = None
        if row is not None:
            agents = {**named, compose.SELF: compose.AgentInfo(
                id=compose.SELF, name=agent.name, output=_output(agent))}
            titles = {s["id"]: compose.step_title(s, agents)
                      for s in compose.normalize(row.stages)}
        process = bases.process_of(agent, row, owner=agent.owner_id == viewer_id,
                                   titles=titles, known=known)
        if process.get("steps"):
            process["steps"] = [{**s, "title": (titles or {}).get(s["id"], "")}
                                for s in process["steps"]]
        out[agent.id] = process
    return out


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentCreateIn, user: CurrentUser, db: DbSession):
    """New agent; with `process`, its 진행 과정 in the same transaction (a refused process
    leaves no agent behind)."""
    from pydantic import ValidationError

    from app.routers import user_pipelines

    process = None
    if (raw := getattr(payload, "process", None)) is not None:
        try:
            process = user_pipelines.ProcessIn.model_validate(raw)
        except ValidationError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="process_invalid") from None
    await _validate_skill_ids(db, user, payload.skill_ids)
    await _validate_skill_ids(db, user, payload.active_skill_ids)
    await _validate_tool_names(db, user, payload.tools)
    fields = payload.model_dump(exclude={"process"})
    bindings = await _validate_agent_bindings(db, user, fields)
    checks = await _validated_checks(db, user, fields)
    slug = await _claim_slug(db, user.id, payload.slug or payload.name)
    agent = Agent(
        owner_id=user.id,
        slug=slug,
        **{**{k: v for k, v in fields.items() if k != "slug"}, **bindings, **checks},
    )
    db.add(agent)
    if process is not None:
        await db.flush()
        try:
            await user_pipelines.apply_process(db, user, agent, process)
        except Exception:
            await db.rollback()
            raise
    await db.commit()
    await db.refresh(agent)
    out = AgentOut.of(
        agent,
        has_knowledge=await _agent_has_knowledge(db, user.id, agent.id),
        process=(await _processes(db, [agent], viewer_id=user.id)).get(agent.id),
    )
    out.skill_warnings = await _skill_warnings(db, agent)
    return out


@router.post(
    "/agents/{agent_id}/install",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_agent(agent_id: str, user: CurrentUser, db: DbSession):
    """Shared agent and shared skills copied in, no knowledge; takes original's 진행 과정."""
    from app.routers import user_pipelines

    origin = await _shared(db, Agent, user, agent_id)
    if origin.retired or (origin.catalog_key or "") in starter.withdrawn_agents():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="agent_retired")
    if (origin.catalog_key or "") in starter.draft_agents() and user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    mine = list((await db.exec(select(Agent).where(Agent.owner_id == user.id))).all())
    if (existing := _copy_of(mine, origin)) is not None:
        return AgentOut.of(
            existing,
            has_knowledge=await _agent_has_knowledge(db, user.id, existing.id),
        )

    async def copied_skills(wanted: list[str]) -> list[str]:
        """The shared ones installed for this person; unshared/deleted dropped, not refused."""
        rows = (
            (await db.exec(select(Skill).where(col(Skill.id).in_(wanted)))).all() if wanted else []
        )
        by_id = {row.id: row for row in rows}
        copied: list[str] = []
        for skill_id in wanted:
            source = by_id.get(skill_id)
            if source is None or source.visibility is not Visibility.org:
                continue
            if source.owner_id == user.id:
                copied.append(source.id)
                continue
            copy = await _install_skill(db, user, source)
            await db.flush()
            copied.append(copy.id)
        return copied

    skill_ids: list[str] | None = None
    if origin.skill_ids is not None:
        wanted = list(origin.skill_ids)
        copied = await copied_skills(wanted)
        # `[]` = hard deny; emptied allow-list → inherit (None)
        skill_ids = copied if copied or not wanted else None
    # the skills it is built from, as the person's own copies
    active_skill_ids = await copied_skills(list(origin.active_skill_ids or [])) or None

    # missing-knowledge note in editable description
    description = origin.description
    if await _agent_has_knowledge(db, origin.owner_id, origin.id):
        note = "지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 올려 주세요."
        description = f"{description} · {note}" if description else note

    # sealed: no prompt copied; original's read at run time
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
        contact=origin.contact,
        starters=list(origin.starters or []),
        tools=None if origin.tools is None else list(origin.tools),
        skill_ids=skill_ids,
        active_skill_ids=active_skill_ids,
        kinds=list(origin.kinds or []),
        # same bindings; invisible design ignored at run time, not dropped
        **{name: getattr(origin, name) for name in _AGENT_BINDINGS},
        temperature=origin.temperature,
        output_language=origin.output_language,
        web_search_default=origin.web_search_default,
        use_memory=origin.use_memory,
        color=origin.color,
        check_skills=list(origin.check_skills) if origin.check_skills is not None else None,
        check_options=dict(origin.check_options) if origin.check_options else None,
        gap_questions=[dict(q) for q in origin.gap_questions]
        if origin.gap_questions is not None else None,
        process_settings=dict(origin.process_settings)
        if origin.process_settings is not None else None,
        enabled=True,
        visibility=Visibility.private,
        catalog_key=origin.catalog_key,
        origin_id=origin.id,
    )
    db.add(copy)
    await db.flush()
    await user_pipelines.clone_process(db, origin, copy, user)
    origin.installs += 1
    db.add(origin)
    await db.commit()
    await db.refresh(copy)
    return AgentOut.of(
        copy,
        has_knowledge=await _agent_has_knowledge(db, user.id, copy.id),
        process=(await _processes(db, [copy], viewer_id=user.id)).get(copy.id),
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
    if "active_skill_ids" in changes:
        await _validate_skill_ids(
            db,
            user,
            changes["active_skill_ids"],
            grandfathered=set(agent.active_skill_ids or []),
        )
    if "tools" in changes:
        await _validate_tool_names(
            db,
            user,
            changes["tools"],
            grandfathered=set(agent.tools or []),
        )
    if "kinds" in changes or any(name in changes for name in _AGENT_BINDINGS):
        final = {
            name: changes[name] if name in changes else getattr(agent, name)
            for name in ("kinds", *_AGENT_BINDINGS)
        }
        # kept design not re-checked
        keeps_design = final["design_system_id"] == agent.design_system_id
        changes.update(
            await _validate_agent_bindings(db, user, final, check_design=not keeps_design,
                                           keep_format=agent.doc_format)
        )
    changes.update(await _validated_checks(db, user, changes))
    wanted = changes.pop("slug", None)
    if agent.sealed:
        # sealed copy: no own prompt, no unsealing
        changes.pop("system_prompt", None)
        changes.pop("share_mode", None)
    shared_now = (changes.get("visibility") == Visibility.org
                  and agent.visibility is not Visibility.org)
    for field, value in changes.items():
        setattr(agent, field, value)
    # explicit slug wins; blank → from name
    agent.slug = await _claim_slug(db, user.id, wanted or agent.name, except_id=agent.id)
    agent.updated_at = utcnow()
    db.add(agent)
    if shared_now:
        # shared 직접 구성 needing review → admins
        from app.routers import user_pipelines

        await user_pipelines.submit_shared_process(db, user, agent)
    await db.commit()
    await db.refresh(agent)
    out = AgentOut.of(
        agent,
        has_knowledge=await _agent_has_knowledge(db, user.id, agent.id),
        process=(await _processes(db, [agent], viewer_id=user.id)).get(agent.id),
    )
    out.skill_warnings = await _skill_warnings(db, agent)
    return out


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: CurrentUser, db: DbSession,
                       request: Request = None):  # type: ignore[assignment]
    from app.routers import user_pipelines

    agent = await _own(db, Agent, "owner_id", user, agent_id)
    # drop index collection too (else still searchable)
    key = agent.index_key
    await user_pipelines.drop_processes(db, [agent.id])
    await db.delete(agent)
    db.add(audit.deletion(user, request, action="agent.delete", ids=[agent_id],
                          counts={"agents": 1}))
    await db.commit()
    if key:
        await index_client.forget_collection(collection=key)


@router.post("/agents/delete")
async def delete_agents(payload: BulkDelete, user: CurrentUser, db: DbSession,
                        request: Request = None):  # type: ignore[assignment]
    """Bulk delete; index collections dropped after commit."""
    from app.routers import user_pipelines

    rows = await _owned_many(db, Agent, "owner_id", user, payload.ids)
    keys = [row.index_key for row in rows if row.index_key]
    await user_pipelines.drop_processes(db, [row.id for row in rows])
    for row in rows:
        await db.delete(row)
    if rows:
        db.add(audit.deletion(user, request, action="agent.bulk_delete",
                              ids=[row.id for row in rows], counts={"agents": len(rows)}))
    await db.commit()
    for key in keys:
        try:
            await index_client.forget_collection(collection=key)
        except Exception:  # noqa: BLE001 — rows already gone
            log.exception("index collection %s outlived its agent", key)
    return {"deleted": len(rows)}


def _attachment(body: bytes, media: str, stem: str, suffix: str) -> Response:
    # RFC 5987 for Korean filenames
    filename = quote(f"{stem}.{suffix}")
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


_SOURCE_EXTENSIONS = {
    "csv": "csv", "tsv": "tsv", "json": "json", "yaml": "yaml", "yml": "yml",
    "python": "py", "py": "py", "javascript": "js", "js": "js", "jsx": "jsx",
    "typescript": "ts", "ts": "ts", "tsx": "tsx", "bash": "sh", "shell": "sh",
    "sh": "sh", "zsh": "zsh", "sql": "sql", "css": "css", "xml": "xml",
    "markdown": "md", "md": "md", "text": "txt", "txt": "txt", "plain": "txt",
}
_SOURCE_MEDIA = {
    "csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json",
}


def _export_code_source(artifact: Artifact) -> Response:
    """Stored source download; no execution or conversion."""
    data = artifact.data or {}
    content = data.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing_source")
    language = str(data.get("language") or "").strip().lower()
    suffix = _SOURCE_EXTENSIONS.get(language, "txt")
    media = _SOURCE_MEDIA.get(suffix, "text/plain")
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', "_", artifact.title or "").strip(" .")
    if stem.lower().endswith("." + suffix):
        stem = stem[: -len(suffix) - 1].rstrip(" .")
    response = _attachment(content.encode("utf-8"), media, stem[:60] or "code", suffix)
    response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    return response


async def _export_deck(artifact: Artifact, format: str) -> Response:
    """Deck as `.pptx`, `.pdf` or Markdown."""
    slides = list((artifact.data or {}).get("slides") or [])
    tokens = (artifact.data or {}).get("design") or None
    title = artifact.title or "슬라이드"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "deck"
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
        # `docx` default → `.pptx` for decks
        await elk_layout.prepare(deck_export.figure_sources(slides))
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
    """HTML artifact export: `html` stored, `pdf` via printing, Office via `page_export`."""
    data = artifact.data or {}
    content = str(data.get("content") or "")
    page_settings = data.get("pageSettings") or None
    title = artifact.title or "문서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "page"

    if format == "html":
        return _attachment(content.encode(), "text/html; charset=utf-8", stem, "html")

    template = design_templates.rendering(str(data.get("templateId") or ""))
    tokens = dict(data.get("design") or {})
    # template look drives Office exporters too
    if template and template.look:
        tokens.setdefault("visualStyle", template.look)
    tokens = tokens or None
    # no template: kind inferred from markup
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
            # `docx` default → `.pptx` for decks
            await elk_layout.prepare(deck_export.figure_sources(slides))
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
    """Owned artifact export in requested format."""
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is ArtifactKind.code and format == "source":
        return _export_code_source(artifact)
    if artifact.kind not in (ArtifactKind.report, ArtifactKind.deck, ArtifactKind.html):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_exportable")
    return await _export_document(artifact, format)


def export_formats(artifact: Artifact) -> tuple[str, ...]:
    """Export formats, most common first."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.deck:
        return ("pdf", "pptx", "md")
    if artifact.kind is ArtifactKind.html:
        template = design_templates.rendering(str(data.get("templateId") or ""))
        content = str(data.get("content") or "")
        is_deck = template.kind == "deck" if template else 'class="slide' in content
        return ("pdf", "pptx", "md") if is_deck else ("pdf", "docx", "hwpx", "md")
    if artifact.kind is ArtifactKind.report:
        return ("pdf", "docx", "hwpx", "md")
    return ()


async def artifact_file(artifact: Artifact, format: str) -> tuple[bytes, str]:
    """Export via the 파일 탭 path: (bytes, filename)."""
    response = await _export_document(artifact, format)
    disposition = response.headers.get("content-disposition", "")
    name = unquote(disposition.split("''", 1)[-1]) if "''" in disposition else f"문서.{format}"
    return bytes(response.body), name


async def _export_document(artifact: Artifact, format: str) -> Response:
    """Report, deck or HTML artifact as a file."""
    if artifact.kind is ArtifactKind.html:
        return await _export_page(artifact, format)

    if artifact.kind is ArtifactKind.deck:
        return await _export_deck(artifact, format)

    # exporters read Markdown; HTML sections converted first
    data = artifact.data or {}
    sections = richtext.normalise(list(data.get("sections") or []))
    sections = report_export.with_references(
        sections,
        list(data.get("sources") or []),
        str(data.get("citationStyle") or "APA"),
    )
    tokens = data.get("design") or None
    page_settings = data.get("pageSettings") or None
    #: purpose-specific head (`doc_formats`): cover, field table, memo rows
    title_block = data.get("titleBlock") or None
    title = artifact.title or "보고서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "report"

    chosen = design_templates.rendering(str(data.get("templateId") or ""))
    docx_template = chosen.docx_template if chosen else ""

    if format == "md":
        body = report_service.to_markdown(title, sections).encode()
        media = "text/markdown; charset=utf-8"
        suffix = "md"
    elif format == "pdf":
        body = report_export.to_pdf(
            title, sections, tokens=tokens, page_settings=page_settings, title_block=title_block
        )
        media = "application/pdf"
        suffix = "pdf"
    elif format == "docx":
        body = report_export.to_docx(
            title,
            sections,
            tokens=tokens,
            template=docx_template,
            page_settings=page_settings,
            title_block=title_block,
            body_from_design=bool(chosen and chosen.body_face_from_design),
        )
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    elif format == "hwpx":
        body = report_export.to_hwpx(
            title, sections, tokens=tokens, page_settings=page_settings, title_block=title_block
        )
        media = "application/hwp+zip"
        suffix = "hwpx"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    return _attachment(body, media, stem, suffix)


async def _shelf_key(db: DbSession, agent: Agent) -> str:
    """Agent collection key, minted on first use."""
    if not agent.index_key:
        agent.index_key = index_client.new_collection_key()
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    return agent.index_key


def _may_share(user: User, requested: bool) -> None:
    """403 on `shared` for non-admins."""
    if requested and user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")


# ══ design systems ══


@router.get("/designs", response_model=list[DesignSystemOut])
async def list_designs(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(DesignSystem)
            .where(or_(DesignSystem.owner_id == user.id, col(DesignSystem.shared).is_(True)))
            .order_by(col(DesignSystem.shared).desc(), col(DesignSystem.created_at))
        )
    ).all()
    owner = await starter.catalog_owner_id(db)
    return [DesignSystemOut.of(d, owner_id=user.id, catalog_owner=owner) for d in rows]


@router.post("/designs/extract", response_model=DesignExtractOut)
async def extract_design(
    payload: DesignExtractIn, user: CurrentUser, db: DbSession, request: Request
):
    """Design system proposal from file or URL; not stored. Charged."""
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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="fetch_unavailable"
            )
        if reason := await netguard.refusal(payload.url or ""):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
        source = await builtin_tools.scrape(backends.fetch, payload.url or "")
        read_from = payload.url or ""
        if not source.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="url_unreadable"
            )

    catalogue = await model_service.list_models()
    model = await _edit_model(
        db, user, request, None, catalogue,
        kind="chat", follow_session=False, own_file=bool(payload.file_id),
        text=source if payload.file_id else "",
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
    except Exception as exc:  # noqa: BLE001 — reason, not a 500
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
    data["tokens"] = design_service.normalise_tokens(data.get("tokens"))
    data["craft"] = design_service.craft_keys(data.get("craft"))
    row = DesignSystem(owner_id=user.id, **data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return DesignSystemOut.of(row, owner_id=user.id,
                              catalog_owner=await starter.catalog_owner_id(db))


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
    return DesignSystemOut.of(row, owner_id=user.id,
                              catalog_owner=await starter.catalog_owner_id(db))


@router.delete("/designs/{design_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_design(design_id: str, user: CurrentUser, db: DbSession,
                        request: Request = None):  # type: ignore[assignment]
    """Design system delete; users detached in one transaction."""
    row = await _own(db, DesignSystem, "owner_id", user, design_id)
    await db.exec(
        update(Project).where(col(Project.design_system_id) == row.id).values(design_system_id=None)
    )
    await db.exec(
        update(Agent).where(col(Agent.design_system_id) == row.id).values(design_system_id=None)
    )
    await db.delete(row)
    db.add(audit.deletion(user, request, action="design.delete", ids=[design_id],
                          counts={"designs": 1}))
    await db.commit()


@router.post("/designs/delete")
async def delete_designs(payload: BulkDelete, user: CurrentUser, db: DbSession,
                         request: Request = None):  # type: ignore[assignment]
    """Bulk delete; users detached."""
    rows = await _owned_many(db, DesignSystem, "owner_id", user, payload.ids)
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(
        update(Project).where(col(Project.design_system_id).in_(ids)).values(design_system_id=None)
    )
    await db.exec(
        update(Agent).where(col(Agent.design_system_id).in_(ids)).values(design_system_id=None)
    )
    await db.exec(delete(DesignSystem).where(col(DesignSystem.id).in_(ids)))
    db.add(audit.deletion(user, request, action="design.bulk_delete", ids=ids,
                          counts={"designs": len(ids)}))
    await db.commit()
    return {"deleted": len(ids)}


# ══ design templates (shipped, read-only) ══


@router.get("/design-templates", response_model=list[DesignTemplateOut])
async def list_design_templates(user: CurrentUser, db: DbSession = None,
                                surface: str | None = None):
    """Choosable templates (optionally per surface): shipped, then own and shared (`user: true`)."""
    from app.models.workspace import UserTemplate

    own = (await db.exec(select(UserTemplate).where(
        (UserTemplate.owner_id == user.id) | (UserTemplate.visibility == "org"))
        .order_by(col(UserTemplate.updated_at).desc()))).all() \
        if db is not None and user is not None else []
    await user_templates.ensure(db, [user_templates.template_id(r) for r in own])
    people = [t for r in own if (t := design_templates.get(user_templates.template_id(r)))]
    rows = [*design_templates.offered(), *people]
    if surface:
        rows = [t for t in rows if t.surface.value == surface]
    return [DesignTemplateOut.of(t, user.id if user is not None else "") for t in rows]


@router.get("/design-templates/usage", response_model=DesignTemplateUsageOut)
async def design_template_usage(user: CurrentUser, db: DbSession):
    """Sessions per template: caller's and installation-wide."""
    # aggregates only; no user identifiable
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
async def design_template_preview(template_id: str, slide: int | None = None):
    """Seed + sample render for gallery iframe; `slide` (1-based) renders one slide."""
    # unauthenticated (iframe src can't carry headers); shipped templates only
    template = None if user_templates.is_user(template_id) else design_templates.get(template_id)
    if template is None or not template.sample or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    tokens = design_service.normalise_tokens(None)
    if slide is not None:
        html = design_templates.render_slide(template, tokens=tokens, n=slide)
        if html is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="slide_not_found")
    else:
        html = design_templates.preview(template, tokens)
    return HTMLResponse(
        html,
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
    """Template stylesheet and wrappers for editor shadow root."""
    template = design_templates.rendering(template_id)
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
    """Blank Office form file."""
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
    """Uploaded document, extracted and shelved."""
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
        # Content-Type is a claim; bytes decide
        mime=file_service.detected_mime(file.filename or "file", file.content_type, data),
    )
    stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name, data)
    # extraction failure recorded, not raised
    try:
        stored.text = await file_service.text_of(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001
        log.info("extraction failed for %s: %s", stored.name, exc)
        stored.error = str(exc)
    reading = await ocr_jobs.queue_if_scanned(stored, data)

    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    if reading:
        ocr_jobs.wake()
    await _index(db, agent, stored)
    return FileOut.of(stored)


class KnowledgeFromFiles(BulkDelete):
    """Own files (conversation uploads) to shelve as agent knowledge."""


@router.post("/agents/{agent_id}/knowledge/from-files", response_model=list[FileOut],
             status_code=status.HTTP_201_CREATED)
async def add_agent_files_from(agent_id: str, payload: KnowledgeFromFiles, user: CurrentUser,
                               db: DbSession):
    """「에이전트로 저장」: own conversation files copied to agent shelf (independent copies)."""
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    ids = list(dict.fromkeys(payload.ids))[:20]
    rows = (await db.exec(select(StoredFile).where(
        col(StoredFile.id).in_(ids), StoredFile.user_id == user.id))).all() if ids else []
    made: list[StoredFile] = []
    for source in rows:
        if not source.storage_key:
            continue
        copy = StoredFile(user_id=user.id, agent_id=agent.id, name=source.name,
                          size=source.size, mime=source.mime, text=source.text,
                          tokens=source.tokens, error=source.error,
                          # pages still being read: re-read for copy
                          ocr_state="queued" if source.ocr_state in ocr_jobs.OPEN_STATES
                          else source.ocr_state,
                          ocr_total=source.ocr_total, ocr_done=0
                          if source.ocr_state in ocr_jobs.OPEN_STATES else source.ocr_done,
                          ocr_review=source.ocr_review)
        try:
            copy.storage_key = file_service.copy_blob(user.id, copy.id, copy.name,
                                                      source.storage_key)
        except OSError:
            log.info("file %s not copied to an agent: its blob is gone", source.id)
            continue
        db.add(copy)
        made.append(copy)
    await db.commit()
    if any(copy.ocr_state == "queued" for copy in made):
        ocr_jobs.wake()
    for copy in made:
        await db.refresh(copy)
        await _index(db, agent, copy)
    return [FileOut.of(f) for f in made]


@router.post(
    "/agents/{agent_id}/knowledge/url",
    response_model=FileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_agent_url(agent_id: str, payload: KnowledgeUrl, user: CurrentUser, db: DbSession):
    """Web page snapshot as agent knowledge."""
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_url")
    if reason := await netguard.refusal(url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

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
    """One agent document indexed in its collection."""
    if not stored.text.strip() or not await index_client.available():
        return False
    return await _index_document(db, agent.owner_id, await _shelf_key(db, agent), stored)


async def shelf_of(db: DbSession, stored: StoredFile) -> str | None:
    """File's searching collection (agent, project or conversation), or None."""
    if stored.agent_id:
        agent = await db.get(Agent, stored.agent_id)
        return await _shelf_key(db, agent) if agent is not None else None
    if stored.project_id:
        project = await db.get(Project, stored.project_id)
        return await _project_shelf_key(db, project) if project is not None else None
    if stored.session_id:
        session = await db.get(ChatSession, stored.session_id)
        if session is None or session.user_id != stored.user_id:
            return None
        return await _session_shelf_key(db, session)
    return None


async def reindex_file(db: DbSession, stored: StoredFile, collection: str) -> bool:
    """Index entry replaced in `collection` after text change."""
    if not stored.text.strip():
        return False
    return await _index_document(db, stored.user_id, collection, stored)


async def _index_document(
    db: DbSession, owner_id: str, collection: str, stored: StoredFile
) -> bool:
    """One document to retrieval index; `indexed_at` on success. Post-commit."""
    if not stored.text.strip() or not await index_client.available():
        return False
    ok = await index_client.put_document(
        collection=collection,
        doc_id=stored.id,
        name=stored.name,
        text=stored.text,
        source_url=stored.source_url,
    )
    if ok:
        stored.indexed_at = utcnow()
        stored.index_collection = collection
        db.add(stored)
        # chunk count to ledger at zero credits
        if index_client.last_chunks:
            record_units(
                db,
                await db.get(User, owner_id),
                reason="index.embed",
                model=index_client.EMBED_MODEL,
                units=index_client.last_chunks,
                unit="chunks",
            )
        await db.commit()
        await db.refresh(stored)
    return ok


def _page_name(url: str) -> str:
    """Readable page name: host + last path segment."""
    stripped = re.sub(r"^https?://", "", url).rstrip("/")
    host, _, path = stripped.partition("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return file_service.safe_name(f"{host}{f'-{tail}' if tail else ''}"[:120] or "page")


@router.post("/projects/{project_id}/knowledge/reindex")
async def reindex_project_knowledge(
    project_id: str, user: CurrentUser, db: DbSession, force: bool = False
) -> dict[str, Any]:
    """Index unindexed project docs (`force=true`: all); returns counts."""
    project = await _own(db, Project, "user_id", user, project_id)
    if not await index_client.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="index_unavailable"
        )
    rows = (
        await db.exec(
            select(StoredFile).where(
                StoredFile.project_id == project_id, StoredFile.user_id == user.id
            )
        )
    ).all()
    todo = [r for r in rows if r.text.strip() and (force or r.indexed_at is None)]
    done = 0
    for stored in todo:
        if await _index_document(db, user.id, await _project_shelf_key(db, project), stored):
            done += 1
    return {"total": len(rows), "attempted": len(todo), "indexed": done}


@router.post("/agents/{agent_id}/knowledge/reindex")
async def reindex_agent_knowledge(
    agent_id: str, user: CurrentUser, db: DbSession, force: bool = False
) -> dict[str, Any]:
    """Index unindexed agent docs (`force=true`: all); returns counts."""
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
async def delete_agent_knowledge(agent_id: str, file_id: str, user: CurrentUser, db: DbSession,
                                 request: Request = None):  # type: ignore[assignment]
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id or stored.agent_id != agent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if stored.storage_key:
        file_service.delete_blob(stored.storage_key)
    await db.delete(stored)
    db.add(audit.deletion(user, request, action="knowledge.delete", ids=[file_id],
                          counts={"files": 1}, extra={"agentId": agent_id, "bytes": stored.size}))
    await db.commit()
    if agent.index_key:
        await index_client.forget_document(collection=agent.index_key, doc_id=file_id)


# ══ message rating ══


@router.patch("/messages/{message_id}/rating", response_model=MessageOut)
async def rate_message(message_id: str, payload: MessageRatingIn, user: CurrentUser, db: DbSession):
    """Set/clear one assistant message rating; ownership via session."""
    message = await db.get(Message, message_id)
    session = await db.get(ChatSession, message.session_id) if message else None
    if (message is None or session is None or session.user_id != user.id
            or session.publication_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if message.role is not Role.assistant:
        raise HTTPException(status_code=422, detail="not_an_answer")
    message.rating = payload.rating
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.of(message)
