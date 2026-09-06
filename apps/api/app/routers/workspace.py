"""Owned-resource CRUD: projects, files, artifacts, skills, memories, agents,
templates, design systems, and message ratings.
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
    """Slug from a name; Hangul characters are kept."""
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


async def _claim_slug(
    db: DbSession, owner_id: str, wanted: str, *, except_id: str | None = None
) -> str:
    """Slug for an agent, unique among this owner's agents (409 otherwise)."""
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
    """Rows among `ids` owned by `user`; unknown or foreign ids are skipped, not 404."""
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
    """Every skill id is owned, enabled, and unique; no cross-account references."""
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
    """Design system must be owned by the caller or shared."""
    if design_id is None:
        return
    row = await db.get(DesignSystem, design_id)
    if row is None or (row.owner_id != user.id and not row.shared):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design_system_not_found")


def _validated_render_templates(raw: dict[str, str] | None) -> dict[str, str] | None:
    """Per-surface render template ids, validated. An unknown id is an error;
    an empty value removes that surface from the map.
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
    """Render template id for a prompt template, validated against `kind`. Empty is allowed."""
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
    """Deletes a project and its knowledge files; sessions are detached. Uncommitted."""
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
    """Bulk delete. Returns the number removed."""
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
        # The client's `Content-Type` is a claim; the bytes decide.
        mime=file_service.detected_mime(file.filename or "file", file.content_type, data),
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
    # Inline only for what the bytes prove to be a picture, PDF or media; the stored type is
    # not trusted for this, and a rendered response gets no script and no origin.
    media, inline = file_service.served_as(stored.name, stored.mime, data)
    return Response(
        content=data,
        media_type=media,
        headers=file_service.download_headers(media, inline, stored.name),
    )


@router.post("/files/{file_id}/open-as-document", response_model=OpenedDocument)
async def open_file_as_document(file_id: str, user: CurrentUser, db: DbSession):
    """Opens an uploaded `.hwpx` as an editable report session. No model call, no charge."""
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

    # Document title, else the filename without extension.
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
async def delete_file(file_id: str, user: CurrentUser, db: DbSession):
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    file_service.delete_blob(stored.storage_key)
    await db.delete(stored)
    await db.commit()


# ══ artifacts ══════════════════════════════════════════════════════════


#: Default page size for the artifact gallery.
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
    """A page of the caller's artifacts as cards, newest first.

    Keyset pagination: `(before_at, before_id)` is the last row the client has.
    `q` matches the title only.
    """
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
    """Artifact count per kind across all pages, for the gallery's filter tabs."""
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
    """Report data with every `format: "html"` section body sanitised before storage."""
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
    """Data with `lint` findings recomputed from the current body."""
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

    # Optimistic concurrency: the version check has to happen in this transaction.
    if expected is not None and expected != artifact.version:
        raise HTTPException(
            status_code=409,
            detail=(
                "이 결과물은 다른 곳에서 이미 수정되었습니다. 최신 내용을 받은 뒤 다시 저장하세요."
            ),
        )

    if "data" in changes and artifact.kind is ArtifactKind.report:
        # Browser-supplied HTML is sanitised here, the only boundary it crosses.
        changes["data"] = _clean_report_data(changes["data"])

    if "data" in changes and isinstance(changes["data"], dict):
        changes["data"] = _relint(artifact.kind, changes["data"])

    if "data" in changes:
        # Snapshot before overwriting.
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
    """Fact-checks one slide against the web and stores the verdicts. Charged."""
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
    """Stores a browser-rendered mermaid diagram image for the exporters.

    A cache keyed by diagram source, beside the section body. No version
    snapshot and no charge.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.report:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_a_report")
    if not pictures.decode(payload.src):
        # Only embedded `data:` images; never fetched.
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
    artifact_id: str, payload: SectionFactCheck, user: CurrentUser, db: DbSession
):
    """Fact-checks one report section against the web and stores the verdicts. Charged."""
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


def _reviewable(artifact: Artifact) -> tuple[str, str]:
    """`(body, rubric)` for the critique model, or `("", "")` when there is nothing."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.report:
        # Markdown, not raw HTML, so the reviewer reads prose rather than tags.
        parts = [
            {"heading": s.get("heading") or "", "text": richtext.as_markdown(s)}
            for s in (data.get("sections") or [])
        ]
        return critique.document(parts), ""
    if artifact.kind is ArtifactKind.deck:
        # Every field a slide can carry, via the linter's reader.
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
    """Model review of a finished artifact, stored as `data.critique`. Charged; blocks nothing."""
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
    # No version snapshot: a review annotates the document rather than editing it.
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


#: Largest image inlined as base64 into an artifact body.
_MAX_EMBED_BYTES = 3 * 1024 * 1024


async def _picture_bytes(db: DbSession, user: User, artifact_id: str) -> tuple[str, bytes]:
    """`(mime, bytes)` of an owned `image` artifact that is embeddable and under the size cap."""
    picture = await _own(db, Artifact, "user_id", user, artifact_id)
    if picture.kind is not ArtifactKind.image:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_an_image")
    # `src` is this server's own download URL: /api/files/{id}/content
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
    """Embeds an owned image artifact on one slide as a `data:` URI. Snapshotted, no charge."""
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
    """Embeds an owned image artifact into one block of an HTML page. Snapshotted, no charge."""
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
    # Same sanitiser as model output.
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
    """Appends an owned image artifact to one report section, as a `<figure>` for
    HTML bodies or a Markdown image line otherwise. Snapshotted, no charge.
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
        target["content"] = design_templates.sanitise(f"{body}{figure}", editable_styles=True)
    else:
        # Exporters print the alt text as the caption.
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
    """Rewrites one block of an HTML artifact and re-renders `content` from the blocks.
    Charged and snapshotted.
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
        # Blocks without stored markup cannot be re-rendered without loss.
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
    # Pictures embedded in the block survive the rewrite.
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
    """Rewrites one report section. Charged and snapshotted."""
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
    # The session's model if any, else the cheapest that can write a report.
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

    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=artifact.version,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=f"{target.get('heading')} 다시 씀",
        )
    )
    target["content"] = hangul.tidy_spacing(hangul.read_back(body)[0])
    # The model writes Markdown, whatever format the section had before.
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
async def rewrite_slide(artifact_id: str, payload: SlideRewrite, user: CurrentUser, db: DbSession):
    """Rewrites one slide of a deck. Charged and snapshotted."""
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
    """Restores a superseded revision as a new version; the current body is snapshotted first."""
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
    # Reports carry their title inside `data`.
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
    """Bulk delete. The sessions that produced them are left alone."""
    rows = await _owned_many(db, Artifact, "user_id", user, payload.ids)
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(ids)))
    await db.exec(delete(Artifact).where(col(Artifact.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids)}


# ══ skills and the store ═══════════════════════════════════════════════
#
# Agents and skills share the same publish/install helpers.


async def _admin_ids(db: DbSession, owner_ids: set[str]) -> set[str]:
    """Subset of `owner_ids` that are administrators, read live rather than stored."""
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
    """An org-shared row owned by somebody else; 409 on the caller's own row."""
    row = await db.get(model, item_id)
    if row is None or row.visibility is not Visibility.org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if row.owner_id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_yours")
    return row


def _copy_of(rows: list, origin) -> Any | None:
    """This account's copy of a shared row, if any.

    Matched on `origin_id`, then `catalog_key`, then slug: seeded catalogue
    copies carry neither an origin nor (for agents) a key.
    """
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
    """Copies a shared skill into this account, or returns the existing copy. Uncommitted."""
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
        source=(
            SkillSource.built_in if origin.source is SkillSource.built_in else SkillSource.workspace
        ),
        kinds=list(origin.kinds or []),
        required_tools=list(origin.required_tools or []),
        estimated_tokens=origin.estimated_tokens
        or starter.estimate_tokens(origin.when_to_use, origin.body, origin.description),
        version=origin.version,
        enabled=True,
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
    """Org-shared skills owned by others. Kept apart from `GET /skills`, which
    lists only runnable (owned) skills.
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
    """Hides an org-shared agent when the user holds a seeded copy of the same name.

    Seeded copies have no `origin_id`; store installs do, and those keep both rows.
    """
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
            .where((Agent.owner_id == user.id) | (Agent.visibility == "org"))
            .order_by(col(Agent.name))
        )
    ).all()
    rows = _without_legacy_copies(rows, user_id=user.id)

    owner_ids = {a.owner_id for a in rows}
    names = await _owner_names(db, owner_ids)
    admins = await _admin_ids(db, owner_ids)
    mine = [a for a in rows if a.owner_id == user.id]
    # `has_knowledge` counts only the caller's own files; a shared agent's shelf is not readable.
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
    """Copies a shared agent into this account, installing its shared skills too.

    Knowledge files are the author's and are not copied.
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
            # Unshared or deleted skills are dropped, not refused.
            if source is None or source.visibility is not Visibility.org:
                continue
            if source.owner_id == user.id:
                copied.append(source.id)
                continue
            copy = await _install_skill(db, user, source)
            await db.flush()
            copied.append(copy.id)
        # `[]` is a hard deny; an allow-list emptied by the copy becomes inherit (`None`).
        skill_ids = copied if copied or not wanted else None

    # Note the missing knowledge on the copy's description, where it can be edited away.
    description = origin.description
    if await _agent_has_knowledge(db, origin.owner_id, origin.id):
        note = "지식 문서는 원본 소유자의 것이라 함께 오지 않습니다. 직접 올려 주세요."
        description = f"{description} · {note}" if description else note

    # A sealed original's copy carries no prompt; it reads the original's at run time.
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
        # A sealed copy has no prompt of its own and cannot be unsealed.
        changes.pop("system_prompt", None)
        changes.pop("share_mode", None)
    for field, value in changes.items():
        setattr(agent, field, value)
    # Explicit slug wins; blank re-derives it from the name.
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
    # The index collection must go too, or its documents stay searchable.
    key = agent.index_key
    await db.delete(agent)
    await db.commit()
    if key:
        await index_client.forget_collection(collection=key)


@router.post("/agents/delete")
async def delete_agents(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Bulk delete; index collections are dropped after the commit."""
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
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _export_deck(artifact: Artifact, format: str) -> Response:
    """A deck as `.pptx`, `.pdf` or Markdown."""
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
        # `docx` is the endpoint default; for a deck it means `.pptx`.
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
    """An HTML artifact as a file.

    `html` is the stored content; `pdf` is printed via `services/printing.py`,
    falling back to the structural exporters; Office formats go through
    `page_export`. Deck templates take deck formats, documents take report formats.
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
    # The template's look drives the Office exporters, not only the CSS.
    if template and template.look:
        tokens.setdefault("visualStyle", template.look)
    tokens = tokens or None
    # A missing template is still exportable; the markup says which kind it is.
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
            # `docx` is the endpoint default; for a deck it means `.pptx`.
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
    """A report, deck, or HTML artifact as a file.

    Reports take `docx`, `pdf`, `hwpx` or `md`; decks take `pptx`, `pdf` or `md`;
    HTML artifacts take `html` plus the set matching their template.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind not in (ArtifactKind.report, ArtifactKind.deck, ArtifactKind.html):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_exportable")

    if artifact.kind is ArtifactKind.html:
        return await _export_page(artifact, format)

    if artifact.kind is ArtifactKind.deck:
        return _export_deck(artifact, format)

    # Exporters read Markdown; HTML-edited sections are converted first.
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
        body = report_export.to_hwpx(title, sections, tokens=tokens, page_settings=page_settings)
        media = "application/hwp+zip"
        suffix = "hwpx"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    return _attachment(body, media, stem, suffix)


# ══ templates ══════════════════════════════════════════════════════════
#
# Built-in prompt templates ship in the image and are read-only; user-written
# ones live in the `templates` table. Both share one wire shape.


@router.get("/prompt-templates", response_model=list[PromptTemplateOut])
async def list_prompt_templates(user: CurrentUser, surface: str | None = None):
    """Built-in prompt templates, optionally for one surface."""
    rows = prompt_templates.all_templates()
    if surface:
        rows = [t for t in rows if t.kind.value == surface]
    return [PromptTemplateOut.of(t) for t in rows]


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(user: CurrentUser, db: DbSession):
    """The caller's templates plus every shared one."""
    rows = (
        await db.exec(
            select(Template)
            .where(or_(Template.owner_id == user.id, col(Template.shared).is_(True)))
            .order_by(col(Template.shared).desc(), col(Template.created_at).desc())
        )
    ).all()
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
    """The agent's index collection key, minted on first use."""
    if not agent.index_key:
        agent.index_key = index_client.new_collection_key()
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    return agent.index_key


#: Default gallery groups; a shared template defaulting to the private group is refiled.
_OWN_GROUP = "내 템플릿"
_SHARED_GROUP = "공용"


def _may_share(user: User, requested: bool) -> None:
    """403 when a non-administrator asks for `shared`."""
    if requested and user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")


async def _own_file(db: DbSession, user: User, file_id: str | None) -> str | None:
    """`file_id` if the caller owns that file; `None` stays `None`; 404 otherwise."""
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
    if data.get("shared") and data.get("group") == _OWN_GROUP:
        data["group"] = _SHARED_GROUP
    template = Template(owner_id=user.id, **data)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return TemplateOut.of(template, await _form_file(db, template), owner_id=user.id)


async def _own_or_shared(db: DbSession, user: User, template_id: str) -> Template:
    """A template the caller owns, or any shared one when the caller is an administrator."""
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
        # Validated against the kind being saved, which this patch may change.
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
# Same ownership shape as templates. A project points at one.


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
    """Proposes a design system read from a file or URL. Nothing is stored. Charged."""
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
    """Deletes a design system; projects using it are detached in the same transaction."""
    row = await _own(db, DesignSystem, "owner_id", user, design_id)
    await db.exec(
        update(Project).where(col(Project.design_system_id) == row.id).values(design_system_id=None)
    )
    await db.delete(row)
    await db.commit()


@router.post("/designs/delete")
async def delete_designs(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Bulk delete; projects using any of them are detached."""
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
# The rendering catalogue ships in the image and is read-only.


@router.get("/design-templates", response_model=list[DesignTemplateOut])
async def list_design_templates(user: CurrentUser, surface: str | None = None):
    """Every rendering template, or those for one surface."""
    rows = design_templates.all_templates()
    if surface:
        rows = [t for t in rows if t.surface.value == surface]
    return [DesignTemplateOut.of(t) for t in rows]


@router.get("/design-templates/usage", response_model=DesignTemplateUsageOut)
async def design_template_usage(user: CurrentUser, db: DbSession):
    """Sessions started per rendering template: `mine` for the caller, `popular`
    across the installation. Aggregates only; no user is identifiable.
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
    """The template's seed rendered around its sample, for the gallery card's iframe.

    Unauthenticated: an iframe `src` cannot carry a header, and everything
    served here ships in the image.
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
    """The template's stylesheet and section wrappers, for the document editor's shadow root."""
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
    """The template's blank Office form file."""
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
        # The client's `Content-Type` is a claim; the bytes decide.
        mime=file_service.detected_mime(file.filename or "file", file.content_type, data),
    )
    stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name, data)
    # Extraction failure is recorded, not raised.
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
    """Reads a web page once and shelves the snapshot as agent knowledge."""
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
    """Sends one shelved document to the retrieval index; stamps `indexed_at` on success.

    Called after the row is committed; a failed embed leaves the row attached.
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
        # Embedded chunk count goes to the usage ledger at zero credits.
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
    """Indexes this agent's unindexed documents; `force=true` re-sends all. Returns counts."""
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
    if agent.index_key:
        await index_client.forget_document(collection=agent.index_key, doc_id=file_id)


# ══ message rating ═════════════════════════════════════════════════════


@router.patch("/messages/{message_id}/rating", response_model=MessageOut)
async def rate_message(message_id: str, payload: MessageRatingIn, user: CurrentUser, db: DbSession):
    """Sets or clears the rating on one assistant message. Ownership is checked via the session."""
    message = await db.get(Message, message_id)
    session = await db.get(ChatSession, message.session_id) if message else None
    if message is None or session is None or session.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if message.role is not Role.assistant:
        raise HTTPException(status_code=422, detail="not_an_answer")
    message.rating = payload.rating
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.of(message)
