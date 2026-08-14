"""Projects, files, artifacts, skills, memories, agents.

All six are the same shape: owned by one user, CRUD, no sharing. Connectors are
separate because MCP gives them behaviour of their own.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlmodel import col, delete, select

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentViewer, DbSession
from app.models.chat import ChatSession
from app.models.user import User, utcnow
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Memory,
    Project,
    Skill,
    StoredFile,
)
from app.schemas.workspace import (
    AgentIn,
    AgentOut,
    ArtifactIn,
    ArtifactOut,
    ArtifactPatch,
    ArtifactRestore,
    ArtifactVersionOut,
    FileOut,
    MemoryIn,
    MemoryOut,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    SectionRewrite,
    SkillIn,
    SkillOut,
    SlideFactCheck,
)
from app.services import deck as deck_service
from app.services import deck_export, factcheck, report_export
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import report as report_service
from app.services import transcribe as transcribe_service
from app.services.credits import charge_for_tokens, has_headroom, settle

log = logging.getLogger(__name__)
router = APIRouter(tags=["workspace"])


def _slug(name: str) -> str:
    """ASCII slug where possible; Korean names keep their characters rather
    than collapsing to an empty string.
    """
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


async def _own(db: DbSession, model, owner_field: str, user: User, item_id: str):
    row = await db.get(model, item_id)
    if row is None or getattr(row, owner_field) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


# ══ projects ═══════════════════════════════════════════════════════════


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
    project = Project(user_id=user.id, **payload.model_dump())
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return await _project_out(db, project)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: CurrentUser, db: DbSession):
    return await _project_out(db, await _own(db, Project, "user_id", user, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: str, payload: ProjectPatch, user: CurrentUser, db: DbSession
):
    project = await _own(db, Project, "user_id", user, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = utcnow()
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return await _project_out(db, project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser, db: DbSession):
    project = await _own(db, Project, "user_id", user, project_id)

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
    await db.commit()


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
        stored.text = file_service.extract_text(stored.name, stored.mime, data)
        stored.tokens = file_service.estimate_tokens(stored.text)
    except Exception as exc:  # noqa: BLE001
        log.info("extraction failed for %s: %s", stored.name, exc)
        stored.error = str(exc)

    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    return FileOut.of(stored)


@router.post("/transcribe")
async def transcribe_audio(user: CurrentUser, file: UploadFile = File(...)):
    """Dictated audio → text. Nothing is stored.

    The clip is a means of typing, not a document; keeping it would leave a
    voice recording in the file store for every dictated sentence.
    """
    if not await transcribe_service.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="transcription_unavailable"
        )
    data = await file.read()
    try:
        text = await transcribe_service.transcribe(data, file.filename or "speech.webm")
    except transcribe_service.TranscribeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"text": text}


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


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: str, user: CurrentUser, db: DbSession):
    stored = await _own(db, StoredFile, "user_id", user, file_id)
    file_service.delete_blob(stored.storage_key)
    await db.delete(stored)
    await db.commit()


# ══ artifacts ══════════════════════════════════════════════════════════


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    user: CurrentUser, db: DbSession, kind: str | None = None, project_id: str | None = None
):
    query = select(Artifact).where(Artifact.user_id == user.id)
    if kind:
        query = query.where(Artifact.kind == kind)
    if project_id:
        query = query.where(Artifact.project_id == project_id)
    rows = (await db.exec(query.order_by(col(Artifact.updated_at).desc()))).all()
    return [ArtifactOut.of(a) for a in rows]


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


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def patch_artifact(
    artifact_id: str, payload: ArtifactPatch, user: CurrentUser, db: DbSession
):
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    changes = payload.model_dump(exclude_unset=True)
    summary = changes.pop("summary", "")

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
        (m for m in catalogue["models"] if "chat" in m["kinds"]), key=lambda m: m["creditCost"]
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

    target["factCheck"] = await factcheck.check_slide(
        slide=target, model=model["id"], api_key=api_key
    )
    data["slides"] = slides
    artifact.data = data
    artifact.updated_at = utcnow()
    db.add(artifact)
    # No version snapshot: a verdict annotates the deck rather than editing it.
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
        key=lambda m: m["creditCost"],
    )
    # An artifact carries no model: the session's if it still has one, else the
    # cheapest that can write a report.
    session = (
        await db.get(ChatSession, artifact.session_id) if artifact.session_id else None
    )
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
            request=artifact.title or "",
            heading=target.get("heading") or "",
            sections=sections,
            target_id=payload.section_id,
            model=model["id"],
            api_key=api_key,
            note=payload.note,
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
    target["content"] = body
    target["status"] = "done"
    data["sections"] = sections
    data["wordCount"] = report_service.word_count(sections)
    artifact.data = data
    artifact.version += 1
    artifact.updated_at = utcnow()
    db.add(artifact)

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    settle(db, user, credits, reason="report.rewrite", session_id=artifact.session_id)
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


# ══ skills ═════════════════════════════════════════════════════════════


@router.get("/skills", response_model=list[SkillOut])
async def list_skills(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Skill).where(Skill.owner_id == user.id).order_by(col(Skill.name))
        )
    ).all()
    return [SkillOut.of(s) for s in rows]


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillIn, user: CurrentUser, db: DbSession):
    skill = Skill(owner_id=user.id, slug=_slug(payload.name), **payload.model_dump())
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return SkillOut.of(skill)


@router.patch("/skills/{skill_id}", response_model=SkillOut)
async def patch_skill(skill_id: str, payload: SkillIn, user: CurrentUser, db: DbSession):
    skill = await _own(db, Skill, "owner_id", user, skill_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(skill, field, value)
    skill.slug = _slug(skill.name)
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

    # One lookup for the page: the store shows who made each agent.
    owner_ids = {a.owner_id for a in rows}
    names = {
        u.id: u.name
        for u in (await db.exec(select(User).where(col(User.id).in_(owner_ids)))).all()
    }
    return [AgentOut.of(a, owner_name=names.get(a.owner_id, "")) for a in rows]


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentIn, user: CurrentUser, db: DbSession):
    agent = Agent(owner_id=user.id, slug=_slug(payload.name), **payload.model_dump())
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(agent)


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def patch_agent(agent_id: str, payload: AgentIn, user: CurrentUser, db: DbSession):
    agent = await _own(db, Agent, "owner_id", user, agent_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    agent.slug = _slug(agent.name)
    agent.updated_at = utcnow()
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: CurrentUser, db: DbSession):
    await db.delete(await _own(db, Agent, "owner_id", user, agent_id))
    await db.commit()


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
    title = artifact.title or "슬라이드"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "deck"

    if format == "md":
        return _attachment(
            deck_service.to_markdown(title, slides).encode(),
            "text/markdown; charset=utf-8",
            stem,
            "md",
        )
    if format == "pdf":
        return _attachment(deck_export.to_pdf(title, slides), "application/pdf", stem, "pdf")
    if format in ("pptx", "docx"):
        # `docx` is the endpoint default, so a deck exported without an explicit
        # format lands here rather than 400-ing.
        return _attachment(
            deck_export.to_pptx(title, slides),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            stem,
            "pptx",
        )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")


@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(
    artifact_id: str, user: CurrentUser, db: DbSession, format: str = "docx"
):
    """A report or a deck as a file.

    Reports take `docx`, `pdf`, `hwpx` or `md`; decks take `pptx`, `pdf` or `md`.

    Built from what is stored, so the download matches the panel rather than
    re-running the model.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind not in (ArtifactKind.report, ArtifactKind.deck):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="not_exportable"
        )

    if artifact.kind is ArtifactKind.deck:
        return _export_deck(artifact, format)

    sections = list((artifact.data or {}).get("sections") or [])
    title = artifact.title or "보고서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "report"

    if format == "md":
        body = report_service.to_markdown(title, sections).encode()
        media = "text/markdown; charset=utf-8"
        suffix = "md"
    elif format == "pdf":
        body = report_export.to_pdf(title, sections)
        media = "application/pdf"
        suffix = "pdf"
    elif format == "docx":
        body = report_export.to_docx(title, sections)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    elif format == "hwpx":
        # Same sections and structure — see report_export.to_hwpx.
        body = report_export.to_hwpx(title, sections)
        media = "application/hwp+zip"
        suffix = "hwpx"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    return _attachment(body, media, stem, suffix)
