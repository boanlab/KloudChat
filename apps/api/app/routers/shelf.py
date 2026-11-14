"""지식 서가: a project's sources with their guides and per-conversation switches, the source
viewer's passage lookup, the studio (outputs, quizzes, flashcards) and the
project's notes (메모함)."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import Field
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession
from app.models.shelf import ProjectNote, SourceGuide, StudioItem
from app.models.user import User, utcnow
from app.models.workspace import Project, StoredFile
from app.schemas.auth import Wire
from app.schemas.workspace import FileOut
from app.services import (
    digest_jobs,
    shelf_calls,
    shelf_sources,
    source_guides,
    source_selection,
    studio,
    studio_kinds,
)
from app.services import files as file_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["shelf"])

#: pasted text and notes as sources
MAX_TEXT_CHARS = 400_000
MAX_NOTE_CHARS = 100_000


def _refused(exc: shelf_calls.ShelfError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


async def _project(db: DbSession, user: User, project_id: str) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return project


async def _session(db: DbSession, user: User, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id or session.publication_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


async def _note_files(db: DbSession, project_id: str) -> set[str]:
    rows = (await db.exec(select(ProjectNote.file_id).where(
        ProjectNote.project_id == project_id, col(ProjectNote.file_id).is_not(None)))).all()
    return {str(r) for r in rows if r}


async def _index(db: DbSession, user: User, project: Project, stored: StoredFile) -> None:
    """Into the project's search index, as an uploaded file is, with its digest asked for
    (`digest_jobs`)."""
    from app.routers.workspace import _index_document, _project_shelf_key

    try:
        await _index_document(db, user.id, await _project_shelf_key(db, project), stored)
    except Exception as exc:  # noqa: BLE001 — word search still finds it; 색인하기 retries
        log.info("shelf source not indexed: %s", type(exc).__name__)
    if digest_jobs.mark(stored):
        db.add(stored)
        await db.commit()
        digest_jobs.wake()


# ── sources ───────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/shelf")
async def shelf(project_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """The project's sources with their kind, digest and guide state, and what this account
    may do with them (the strict-local rule)."""
    project = await _project(db, user, project_id)
    files = await source_guides.project_files(db, project.id, user.id)
    notes = await _note_files(db, project.id)
    guides = {g.subject: g for g in (await db.exec(
        select(SourceGuide).where(SourceGuide.project_id == project.id))).all()}
    try:
        rules = await shelf_calls.policy()
        strict = bool(rules.internal_data_strict_local)
    except shelf_calls.ShelfError:
        strict = True
    sources = []
    for f in files:
        guide = guides.get(f.id)
        sources.append({
            **FileOut.of(f).model_dump(by_alias=True, mode="json"),
            "kind": shelf_sources.kind_of(f, notes),
            "digest": shelf_sources.digest_of(f),
            "guideState": ("none" if guide is None else source_guides.wire(guide)["state"]),
            "readable": bool((f.text or "").strip()),
        })
    return {"projectId": project.id, "sources": sources, "strictLocal": strict,
            "kinds": list(studio_kinds.KINDS)}


class PastedText(Wire):
    title: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


@router.post("/projects/{project_id}/shelf/text", response_model=FileOut,
             status_code=status.HTTP_201_CREATED)
async def add_text(project_id: str, payload: PastedText, user: CurrentUser, db: DbSession):
    """Pasted text as a project source (kept as a `.txt` file the person gave)."""
    project = await _project(db, user, project_id)
    return await _text_source(db, user, project, payload.title, payload.text, "text/plain",
                              ".txt")


async def _text_source(db: DbSession, user: User, project: Project, title: str, text: str,
                       mime: str, suffix: str) -> StoredFile:
    body = text.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty")
    head = title.strip() or next((ln.strip(" #") for ln in body.splitlines() if ln.strip()), "")
    name = file_service.safe_name((head[:60] or "text") + suffix)
    stored = StoredFile(user_id=user.id, project_id=project.id, name=name, mime=mime,
                        size=len(body.encode()), text=body,
                        tokens=file_service.estimate_tokens(body))
    stored.storage_key = file_service.write_blob(user.id, stored.id, name, body.encode())
    db.add(stored)
    await db.commit()
    await db.refresh(stored)
    await _index(db, user, project, stored)
    return stored


@router.get("/projects/{project_id}/shelf/guide/{subject}")
async def read_guide(project_id: str, subject: str, user: CurrentUser, db: DbSession):
    """A source's guide (or the project's, `subject = project`); `state: none` before one
    is asked for."""
    project = await _project(db, user, project_id)
    stored = None
    if subject != source_guides.PROJECT:
        stored = await db.get(StoredFile, subject)
        if stored is None or stored.project_id != project.id or stored.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    guide = (await db.exec(select(SourceGuide).where(
        SourceGuide.project_id == project.id, SourceGuide.subject == subject))).first()
    return source_guides.wire(guide, stored)


@router.post("/projects/{project_id}/shelf/guide/{subject}")
async def make_guide(project_id: str, subject: str, user: CurrentUser, db: DbSession,
                     force: bool = False):
    """Asks for the guide when none is current (`force`: again); returns its state."""
    project = await _project(db, user, project_id)
    stored = None
    if subject != source_guides.PROJECT:
        stored = await db.get(StoredFile, subject)
        if stored is None or stored.project_id != project.id or stored.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        guide = await source_guides.ensure(db, user, project.id, subject, force=force)
    except shelf_calls.ShelfError as exc:
        raise _refused(exc) from exc
    if guide is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="nothing_to_read")
    return source_guides.wire(guide, stored)


#: chars of context around a located passage
_WINDOW = 2_400


@router.get("/projects/{project_id}/shelf/locate")
async def locate(project_id: str, user: CurrentUser, db: DbSession, file_id: str = "",
                 name: str = "", q: str = "", page: int | None = None):
    """The passage a citation points at: the source (by id, or by the name an answer's
    source list gives), its text around the passage, and where it is (page, time code)."""
    project = await _project(db, user, project_id)
    files = await source_guides.project_files(db, project.id, user.id)
    stored = next((f for f in files if f.id == file_id), None) if file_id else None
    if stored is None and name:
        wanted = name.strip()
        stored = next((f for f in files if f.name == wanted), None) or next(
            (f for f in files if wanted and (wanted in f.name or f.name in wanted)), None)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source_not_found")
    text = stored.text or ""
    found = shelf_sources.locate(text, q[:2000], page)
    lo = max(0, found["start"] - _WINDOW)
    hi = min(len(text), found["end"] + _WINDOW)
    notes = await _note_files(db, project.id)
    return {
        "fileId": stored.id, "name": stored.name, "mime": stored.mime,
        "kind": shelf_sources.kind_of(stored, notes), "sourceUrl": stored.source_url,
        "chars": len(text), "windowStart": lo, "window": text[lo:hi],
        **found,
    }


class Selection(Wire):
    off: list[str] = Field(default_factory=list, max_length=source_selection.MAX_OFF)


@router.get("/sessions/{session_id}/sources")
async def read_selection(session_id: str, user: CurrentUser, db: DbSession):
    """The project sources this conversation leaves out (empty: all on)."""
    session = await _session(db, user, session_id)
    return {"off": sorted(await source_selection.excluded(db, session.id))}


@router.put("/sessions/{session_id}/sources")
async def store_selection(session_id: str, payload: Selection, user: CurrentUser,
                          db: DbSession):
    """Which sources this conversation's next questions may use."""
    session = await _session(db, user, session_id)
    if not session.project_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_in_a_project")
    known = {f.id for f in await source_guides.project_files(db, session.project_id, user.id)}
    off = await source_selection.store(db, session.id, user.id,
                                       [i for i in payload.off if i in known])
    await db.commit()
    return {"off": off}


# ── studio ────────────────────────────────────────────────────────────

Kind = Literal["study_guide", "faq", "briefing", "timeline", "glossary", "mindmap", "quiz",
               "flashcards"]


class StudioRequest(Wire):
    kind: Kind
    #: sources to ground it in; empty = all of the project's
    source_ids: list[str] = Field(default_factory=list, max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)


async def _item(db: DbSession, user: User, item_id: str) -> StudioItem:
    item = await db.get(StudioItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return item


@router.get("/projects/{project_id}/studio")
async def studio_items(project_id: str, user: CurrentUser, db: DbSession):
    project = await _project(db, user, project_id)
    rows = (await db.exec(select(StudioItem).where(
        StudioItem.project_id == project.id, StudioItem.user_id == user.id)
        .order_by(col(StudioItem.created_at).desc()).limit(200))).all()
    return [studio.wire(row) for row in rows]


@router.post("/projects/{project_id}/studio/estimate")
async def studio_estimate(project_id: str, payload: StudioRequest, user: CurrentUser,
                          db: DbSession):
    """Length and cost before anything is generated."""
    project = await _project(db, user, project_id)
    try:
        return await studio.estimate(db, user, project, payload.kind, payload.source_ids,
                                     studio.options_of(payload.kind, payload.options))
    except shelf_calls.ShelfError as exc:
        raise _refused(exc) from exc


@router.post("/projects/{project_id}/studio", status_code=status.HTTP_202_ACCEPTED)
async def studio_start(project_id: str, payload: StudioRequest, user: CurrentUser,
                       db: DbSession):
    """Starts an output; it is written in the background (`state: running`)."""
    project = await _project(db, user, project_id)
    try:
        item = await studio.start(db, user, project, payload.kind, payload.source_ids,
                                  studio.options_of(payload.kind, payload.options))
    except shelf_calls.ShelfError as exc:
        raise _refused(exc) from exc
    return studio.wire(item)


@router.get("/studio/{item_id}")
async def studio_item(item_id: str, user: CurrentUser, db: DbSession):
    return studio.wire(await _item(db, user, item_id))


@router.post("/studio/{item_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def studio_regenerate(item_id: str, user: CurrentUser, db: DbSession):
    item = await _item(db, user, item_id)
    try:
        return studio.wire(await studio.regenerate(db, user, item))
    except shelf_calls.ShelfError as exc:
        raise _refused(exc) from exc


@router.delete("/studio/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def studio_delete(item_id: str, user: CurrentUser, db: DbSession):
    """The output goes from the studio; an artifact saved from it stays in 아티팩트."""
    item = await _item(db, user, item_id)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _download_name(title: str, suffix: str) -> str:
    return file_service.safe_name(re.sub(r"\s+", " ", title or "studio")[:60] + suffix)


@router.get("/studio/{item_id}/export")
async def studio_export(item_id: str, user: CurrentUser, db: DbSession,
                        format: Literal["csv", "anki"] = "csv"):  # noqa: A002 — wire name
    """A quiz or card set as CSV, or as Anki-importable text."""
    item = await _item(db, user, item_id)
    content = item.content or {}
    items = content.get("questions") if item.kind == "quiz" else content.get("cards")
    if item.kind not in studio_kinds.SETS or not items:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_set")
    names = {int(s["n"]): str(s["name"]) for s in content.get("sources") or []}
    if format == "csv":
        body = studio_kinds.export_csv(item.kind, items, names)
        media, suffix = "text/csv; charset=utf-8", ".csv"
    else:
        body = studio_kinds.export_anki(item.kind, items)
        media, suffix = "text/plain; charset=utf-8", ".txt"
    headers = file_service.download_headers(media.split(";")[0], False,
                                            _download_name(item.title, suffix))
    return Response(content=body.encode("utf-8"), media_type=media, headers=headers)


class SheetRequest(Wire):
    format: Literal["web", "hwp", "docx"] = "hwp"


@router.post("/studio/{item_id}/sheet")
async def studio_sheet(item_id: str, payload: SheetRequest, user: CurrentUser, db: DbSession):
    """A quiz as a printable sheet (web report, 한글 or Word) in a conversation of its own."""
    item = await _item(db, user, item_id)
    try:
        session_id, artifact_id = await studio.save_sheet(db, user, item, payload.format)
    except shelf_calls.ShelfError as exc:
        raise _refused(exc) from exc
    return {"sessionId": session_id, "artifactId": artifact_id}


# ── notes ─────────────────────────────────────────────────────────────


class NoteIn(Wire):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=MAX_NOTE_CHARS)
    origin: Literal["manual", "answer", "selection"] = "manual"
    session_id: str | None = Field(default=None, max_length=32)
    message_id: str | None = Field(default=None, max_length=32)


class NotePatch(Wire):
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    #: the version (updatedAt) the edit started from; a later save wins only once
    expected: str | None = None


def _note(note: ProjectNote) -> dict[str, Any]:
    return {"id": note.id, "projectId": note.project_id, "title": note.title,
            "body": note.body, "origin": note.origin, "sessionId": note.session_id,
            "messageId": note.message_id, "fileId": note.file_id,
            "createdAt": note.created_at.isoformat(), "updatedAt": note.updated_at.isoformat()}


def _title_of(title: str, body: str) -> str:
    if title.strip():
        return title.strip()[:200]
    first = next((ln.strip(" #>*-") for ln in body.splitlines() if ln.strip(" #>*-")), "")
    return first[:60]


async def _owned_note(db: DbSession, user: User, note_id: str) -> ProjectNote:
    note = await db.get(ProjectNote, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return note


@router.get("/projects/{project_id}/notes")
async def notes(project_id: str, user: CurrentUser, db: DbSession):
    project = await _project(db, user, project_id)
    rows = (await db.exec(select(ProjectNote).where(
        ProjectNote.project_id == project.id, ProjectNote.user_id == user.id)
        .order_by(col(ProjectNote.updated_at).desc()).limit(500))).all()
    return [_note(row) for row in rows]


@router.post("/projects/{project_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(project_id: str, payload: NoteIn, user: CurrentUser, db: DbSession):
    """A note: written by hand, or pinned from an answer or a selected passage."""
    project = await _project(db, user, project_id)
    if not payload.body.strip() and not payload.title.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty")
    if payload.session_id:
        session = await _session(db, user, payload.session_id)
        if session.project_id != project.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="session_not_in_project")
    note = ProjectNote(user_id=user.id, project_id=project.id,
                       title=_title_of(payload.title, payload.body), body=payload.body,
                       origin=payload.origin, session_id=payload.session_id,
                       message_id=payload.message_id)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _note(note)


@router.patch("/notes/{note_id}")
async def edit_note(note_id: str, payload: NotePatch, user: CurrentUser, db: DbSession):
    """Saves an edit; refused (409 `note_conflict`) when another save landed since
    `expected`. A note made into a source updates that source too."""
    note = await _owned_note(db, user, note_id)
    if payload.expected and payload.expected != note.updated_at.isoformat():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="note_conflict")
    if payload.body is not None:
        note.body = payload.body
    if payload.title is not None:
        note.title = _title_of(payload.title, note.body)
    note.updated_at = utcnow()
    db.add(note)
    await db.commit()
    await db.refresh(note)
    if note.file_id:
        stored = await db.get(StoredFile, note.file_id)
        project = await db.get(Project, note.project_id)
        if stored is not None and project is not None and stored.user_id == user.id:
            stored.text = note.body.strip() or note.title
            stored.tokens = file_service.estimate_tokens(stored.text)
            stored.size = len(stored.text.encode())
            stored.storage_key = file_service.write_blob(user.id, stored.id, stored.name,
                                                         stored.text.encode())
            stored.indexed_at = None
            db.add(stored)
            await db.commit()
            await db.refresh(stored)
            await _index(db, user, project, stored)
    return _note(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, user: CurrentUser, db: DbSession):
    """The note goes; a source made from it stays a source until deleted there."""
    note = await _owned_note(db, user, note_id)
    await db.delete(note)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notes/{note_id}/source", response_model=FileOut)
async def note_to_source(note_id: str, user: CurrentUser, db: DbSession):
    """The note as a project source, indexed like an uploaded file; once (later edits of the
    note update it)."""
    note = await _owned_note(db, user, note_id)
    project = await _project(db, user, note.project_id)
    if note.file_id:
        stored = await db.get(StoredFile, note.file_id)
        if stored is not None:
            return stored
    stored = await _text_source(db, user, project, note.title, note.body or note.title,
                                "text/markdown", ".md")
    note.file_id = stored.id
    db.add(note)
    await db.commit()
    return stored


__all__ = ["router"]
