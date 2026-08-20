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
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import func, or_, tuple_
from sqlmodel import col, delete, select, update

from app.core.config import settings
from app.core.deps import CurrentUser, CurrentViewer, DbSession
from app.models.chat import ChatSession, Message, Role
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
    StoredFile,
    Template,
)
from app.schemas.chat import MessageOut, MessageRatingIn
from app.schemas.workspace import (
    AgentIn,
    AgentOut,
    ArtifactIn,
    ArtifactOut,
    ArtifactPatch,
    ArtifactRestore,
    ArtifactVersionOut,
    BlockImage,
    BlockRewrite,
    DesignExtractIn,
    DesignExtractOut,
    DesignSystemIn,
    DesignSystemOut,
    DesignTemplateOut,
    FileOut,
    KnowledgeUrl,
    MemoryIn,
    MemoryOut,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    PromptTemplateOut,
    SectionRewrite,
    SkillIn,
    SkillOut,
    SlideFactCheck,
    SlideImage,
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
    index_client,
    lint,
    page_export,
    pictures,
    prompt_templates,
    report_export,
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
from app.services import transcribe as transcribe_service
from app.services.credits import charge_for_tokens, has_headroom, settle
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


async def _own(db: DbSession, model, owner_field: str, user: User, item_id: str):
    row = await db.get(model, item_id)
    if row is None or getattr(row, owner_field) != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_system_not_found"
        )


def _validated_render_templates(raw: dict[str, str] | None) -> dict[str, str] | None:
    """The formats this project starts its work in, refused rather than trimmed.

    The same rule the composer's own pick answers to in
    `sessions._resolved_template_id`, applied a surface at a time: an id the
    catalogue cannot place is an error, not a key quietly dropped on the way
    in. A default that disappears while it is being saved is a project whose
    documents come out in the wrong shape with nobody told why.

    An empty value is that surface leaving the map — the built-in track — so
    clearing one picker never has to be spelled differently from setting it.
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
async def patch_project(
    project_id: str, payload: ProjectPatch, user: CurrentUser, db: DbSession
):
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

    Everything used to arrive at once and whole: 385 rows, 4.0 MB, every HTML
    document's full markup, on a screen that draws them as thumbnails. Three
    things changed and each one is visible in that sentence — the rows are a
    page, the bodies are cards, and there is a `q` so a person with hundreds of
    them can find one without scrolling.

    Keyset rather than offset: the list is ordered by a timestamp that changes
    when somebody edits, and an offset would skip or repeat rows underneath
    them. `(before_at, before_id)` is simply the last row the client has.
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
            tuple_(col(Artifact.updated_at), col(Artifact.id))
            < tuple_(before_at, before_id or "")
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
                "이 결과물은 다른 곳에서 이미 수정되었습니다. "
                "최신 내용을 받은 뒤 다시 저장하세요."
            ),
        )

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

    verdicts, usage = await factcheck.check_slide(
        slide=target, model=model["id"], api_key=api_key
    )
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
    settle(db, user, credits, reason="deck.factcheck", session_id=artifact.session_id)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


#: How a document reaches the reviewer, per kind. The linter reads the same
#: three shapes; this turns them into headings and prose instead of parts.
def _reviewable(artifact: Artifact) -> tuple[str, str]:
    """`(body, rubric)` for one artifact, or `("", "")` when there is nothing."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.report:
        parts = [
            {"heading": s.get("heading") or "", "text": s.get("content") or ""}
            for s in (data.get("sections") or [])
        ]
        return critique.document(parts), ""
    if artifact.kind is ArtifactKind.deck:
        parts = [
            {
                "heading": s.get("title") or "",
                "text": " · ".join(
                    [*(s.get("bullets") or []), s.get("body") or ""]
                ).strip(" ·"),
            }
            for s in (data.get("slides") or [])
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

    Asked for explicitly and charged, unlike the linter beside it: that one is
    free and certain, this one costs a call and is an opinion. The score says
    so — it is a reading, not a gate, and nothing is blocked by it.

    One reviewer, one pass. OpenDesign seats five and runs three rounds; here
    every call is somebody's credit.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    body, rubric = _reviewable(artifact)
    if not body.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_reviewable")

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]),
        key=lambda m: m["creditCost"],
    )
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
    settle(db, user, credits, reason="artifact.critique", session_id=artifact.session_id)
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
async def add_slide_image(
    artifact_id: str, payload: SlideImage, user: CurrentUser, db: DbSession
):
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
async def add_block_image(
    artifact_id: str, payload: BlockImage, user: CurrentUser, db: DbSession
):
    """Puts a picture this workspace already made into one block of a page.

    The writing model cannot produce a picture and is not allowed to reference
    one — `sanitise` drops every `src` that is not already inside the file. So
    the path runs the other way: a person picks an image they made on the
    image surface, and the server inlines its bytes as a `data:` URI on their
    instruction. The artifact stays one file that prints, downloads and shares
    with the picture in it, and nothing is fetched when a reader opens it.

    Free — no model call — and snapshotted like a rewrite, so it is one click
    from undone.
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="blocks_not_editable"
        )

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


@router.post("/artifacts/{artifact_id}/blocks/rewrite", response_model=ArtifactOut)
async def rewrite_block(
    artifact_id: str, payload: BlockRewrite, user: CurrentUser, db: DbSession
):
    """Rewrites one block of an HTML artifact and re-renders the file.

    The blocks are the source and `content` is what they render to, so this
    replaces one block and assembles the document again from the same seed —
    rather than splicing markup into a finished file, where the seams are
    wherever the model last put them.

    Charged and snapshotted like the report's section rewrite, so a worse
    rewrite is one click from undone.
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="blocks_not_editable"
        )

    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if template.surface.value in m["kinds"]),
        key=lambda m: m["creditCost"],
    )
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
    settle(db, user, credits, reason="page.rewrite", session_id=artifact.session_id)
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


@router.get("/tools", response_model=list[ToolCatalogOut])
async def list_tool_catalog(user: CurrentUser, db: DbSession):
    return [ToolCatalogOut(**row) for row in await tool_registry.tool_catalog(db, user)]


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
        )
        for agent in rows
    ]


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentIn, user: CurrentUser, db: DbSession):
    await _validate_skill_ids(db, user, payload.skill_ids)
    await _validate_tool_names(db, user, payload.tools)
    agent = Agent(owner_id=user.id, slug=_slug(payload.name), **payload.model_dump())
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(
        agent,
        has_knowledge=await _agent_has_knowledge(db, user.id, agent.id),
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
    for field, value in changes.items():
        setattr(agent, field, value)
    agent.slug = _slug(agent.name)
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
            deck_export.to_pptx(title, slides, tokens=tokens),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            stem,
            "pptx",
        )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")


def _export_page(artifact: Artifact, format: str) -> Response:
    """An HTML artifact as a file somebody can hand on.

    The `.html` is the faithful copy — it is the artifact. The other formats
    are `page_export` reading the markup back into the shapes the existing
    exporters draw, which is what a rendering engine would otherwise be for.
    A deck therefore opens in PowerPoint as editable slides in the right order
    with the right accent, laid out by this product's own deck renderer rather
    than by its template's stylesheet. That trade is the point: fidelity lives
    in the `.html`, editability lives here.

    Which formats are offered follows the template the artifact was written
    into — a document has no slides and a deck has no `.hwpx`.
    """
    data = artifact.data or {}
    content = str(data.get("content") or "")
    tokens = data.get("design") or None
    title = artifact.title or "문서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "page"

    if format == "html":
        return _attachment(content.encode(), "text/html; charset=utf-8", stem, "html")

    template = design_templates.get(str(data.get("templateId") or ""))
    # A template can stop existing across an upgrade; the markup still says
    # which kind it is, and the file has to keep exporting either way.
    is_deck = template.kind == "deck" if template else 'class="slide' in content

    if is_deck:
        slides = page_export.to_slides(
            content, accent=str((tokens or {}).get("accent") or "")
        )
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
            # `docx` is the endpoint default, so a deck exported without an
            # explicit format lands on the presentation rather than 400-ing.
            return _attachment(
                deck_export.to_pptx(
                    title, slides, tokens=tokens, dark=bool(template and template.dark)
                ),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                stem,
                "pptx",
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")

    sections = page_export.to_sections(content)
    if format == "md":
        body, media, suffix = (
            report_service.to_markdown(title, sections).encode(),
            "text/markdown; charset=utf-8",
            "md",
        )
    elif format == "pdf":
        body, media, suffix = (
            report_export.to_pdf(title, sections, tokens=tokens),
            "application/pdf",
            "pdf",
        )
    elif format == "hwpx":
        body, media, suffix = (
            report_export.to_hwpx(title, sections, tokens=tokens),
            "application/hwp+zip",
            "hwpx",
        )
    elif format == "docx":
        body, media, suffix = (
            report_export.to_docx(title, sections, tokens=tokens),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_format")
    return _attachment(body, media, stem, suffix)


@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(
    artifact_id: str, user: CurrentUser, db: DbSession, format: str = "docx"
):
    """A report, a deck, or an HTML artifact as a file.

    Reports take `docx`, `pdf`, `hwpx` or `md`; decks take `pptx`, `pdf` or
    `md`. An artifact written into a rendering template takes `html` — the
    file itself — plus whichever of the two sets matches the template it came
    from.

    Built from what is stored, so the download matches the panel rather than
    re-running the model.
    """
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind not in (ArtifactKind.report, ArtifactKind.deck, ArtifactKind.html):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="not_exportable"
        )

    if artifact.kind is ArtifactKind.html:
        return _export_page(artifact, format)

    if artifact.kind is ArtifactKind.deck:
        return _export_deck(artifact, format)

    sections = list((artifact.data or {}).get("sections") or [])
    tokens = (artifact.data or {}).get("design") or None
    title = artifact.title or "보고서"
    stem = re.sub(r'[\\/:*?"<>|]+', "_", title)[:60] or "report"

    if format == "md":
        body = report_service.to_markdown(title, sections).encode()
        media = "text/markdown; charset=utf-8"
        suffix = "md"
    elif format == "pdf":
        body = report_export.to_pdf(title, sections, tokens=tokens)
        media = "application/pdf"
        suffix = "pdf"
    elif format == "docx":
        body = report_export.to_docx(title, sections, tokens=tokens)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        suffix = "docx"
    elif format == "hwpx":
        # Same sections and structure — see report_export.to_hwpx.
        body = report_export.to_hwpx(title, sections, tokens=tokens)
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
        files = (
            await db.exec(select(StoredFile).where(col(StoredFile.id).in_(ids)))
        ).all()
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


@router.patch("/templates/{template_id}", response_model=TemplateOut)
async def patch_template(
    template_id: str, payload: TemplateIn, user: CurrentUser, db: DbSession
):
    template = await _own(db, Template, "owner_id", user, template_id)
    fields = payload.model_dump(exclude_unset=True)
    if "shared" in fields:
        _may_share(user, bool(fields["shared"]))
    if "file_id" in fields:
        fields["file_id"] = await _own_file(db, user, fields["file_id"])
    for field, value in fields.items():
        setattr(template, field, value)
    template.updated_at = utcnow()
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return TemplateOut.of(template, await _form_file(db, template), owner_id=user.id)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, user: CurrentUser, db: DbSession):
    template = await _own(db, Template, "owner_id", user, template_id)
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file_or_url"
        )

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
        key=lambda m: m["creditCost"],
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
    settle(db, user, credits, reason="design.extract")
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
async def patch_design(
    design_id: str, payload: DesignSystemIn, user: CurrentUser, db: DbSession
):
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
        update(Project)
        .where(col(Project.design_system_id) == row.id)
        .values(design_system_id=None)
    )
    await db.delete(row)
    await db.commit()


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


@router.get("/design-templates/{template_id}/preview")
async def preview_design_template(
    template_id: str,
    accent: str | None = None,
    ink: str | None = None,
    muted: str | None = None,
    font: str | None = None,
):
    """This template's own shape, filled with its sample and worn in a look.

    Served as a document rather than as a string in JSON because the gallery
    renders it in a sandboxed iframe, which needs a URL.

    The four tokens arrive as query parameters because that iframe is the only
    thing that can ask for this document, and it can only ask by address. They
    are the same four every renderer reads, and they go through
    `design.normalise_tokens` on the way in — so a card shows a colour the
    exporters can also draw, or the default, and never the string it was sent.
    A design system is not named here: what the caller sends is the look
    itself, which keeps the route as free of anybody's rows as it was.

    **Unauthenticated, like the branding logo.** The body is still a constant
    of this image plus four validated values — there is no user data in it to
    protect. An iframe `src` cannot carry an Authorization header, and the
    `?t=` escape hatch `current_viewer` provides puts a live access token into
    the proxy's access log. Paying that for a static asset would buy nothing.

    The client still sandboxes the frame; the headers here keep the document
    inert on its own terms.
    """
    template = design_templates.get(template_id)
    if template is None or not template.seed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    return Response(
        content=design_templates.preview(
            template, {"accent": accent, "ink": ink, "muted": muted, "font": font}
        ),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=300",
        },
    )


# ══ agent knowledge ════════════════════════════════════════════════════
#
# Documents an agent can search, as opposed to project files, which are pushed
# into every turn whole. The difference is size: a shelf too big to inject is
# exactly the shelf worth searching.


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
        stored.text = file_service.extract_text(stored.name, stored.mime, data)
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
async def add_agent_url(
    agent_id: str, payload: KnowledgeUrl, user: CurrentUser, db: DbSession
):
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
            select(StoredFile).where(
                StoredFile.agent_id == agent_id, StoredFile.user_id == user.id
            )
        )
    ).all()
    todo = [r for r in rows if r.text.strip() and (force or r.indexed_at is None)]
    done = 0
    for stored in todo:
        if await _index(db, agent, stored):
            done += 1
    return {"total": len(rows), "attempted": len(todo), "indexed": done}


@router.delete(
    "/agents/{agent_id}/knowledge/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_agent_knowledge(
    agent_id: str, file_id: str, user: CurrentUser, db: DbSession
):
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
async def rate_message(
    message_id: str, payload: MessageRatingIn, user: CurrentUser, db: DbSession
):
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
