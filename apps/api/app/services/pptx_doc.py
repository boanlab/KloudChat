"""PowerPoint document artifacts: one `.pptx` blob per version, saved by the editor or by the
agent (the versions themselves: `office_doc`). A legacy `.ppt` is not opened.

This is the uploaded or company `.pptx` worked as a file beside the chat; a deck KloudChat
writes itself is the `deck` kind (`deck.py`), which also downloads as `.pptx`."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, Project
from app.services import deck_export, office_doc, pptx_edit
from app.services.office_doc import Conflict, read, read_version

__all__ = ["Conflict", "read", "read_version"]

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
#: slides `pptx_write` takes at once
MAX_WRITE = 30


def file_format(data: bytes) -> str:
    """`pptx` or ``; the bytes decide, not the name."""
    try:
        pptx_edit.slide_count(data)
    except pptx_edit.PptxError:
        return ""
    return "pptx"


def describe(data: bytes, fmt: str) -> dict[str, Any]:
    """`Artifact.data` for a file: format, size, slide count, and its text for search."""
    try:
        text = pptx_edit.plain_text(data)[:20000]
        slides = pptx_edit.slide_count(data)
    except pptx_edit.PptxError:
        text, slides = "", 0
    return {"format": fmt, "size": len(data), "text": text, "slides": slides}


KIND = office_doc.Kind(ArtifactKind.pptx, "pptx", "PowerPoint 문서", file_format, describe)


def blank(*, tokens: dict[str, str] | None = None, template: str = "") -> bytes:
    """A new deck with one empty title slide: python-pptx's default stretched to 16:9
    (`pptx_edit.blank_deck`), or, given a project's 테마 `tokens` or 서식 `template`, an empty
    cover in that look (`deck_export.to_pptx`), as a web deck made there would have."""
    if not tokens and not template:
        return pptx_edit.blank_deck()
    return pptx_edit.written_bullets_only(
        deck_export.to_pptx(
            "", [{"layout": "title", "title": "", "body": ""}], tokens=tokens, template=template
        )
    )


def deck(
    title: str,
    subtitle: str,
    slides: list[dict[str, Any]],
    closing: str = "",
    *,
    tokens: dict[str, str] | None = None,
    template: str = "",
) -> bytes:
    """A new deck in the house look (`deck_export`): a cover, then a slide per entry, as
    bullets, or a table when it has rows; `closing` adds a last slide with that line. `tokens`
    (the design system's `{accent, ink, muted, font, …}`) and `template` (a slides 서식's
    `template.pptx` path, for its masters, layouts and theme) are what a web deck's `.pptx`
    takes (`deck_export.to_pptx`); `project_look` finds a conversation's."""
    made: list[dict[str, Any]] = [{"layout": "title", "title": title, "body": subtitle}]
    for slide in slides[:MAX_WRITE]:
        rows = [[str(cell) for cell in row] for row in slide.get("rows") or [] if row]
        entry: dict[str, Any] = {
            "title": str(slide.get("title") or ""),
            "notes": str(slide.get("notes") or ""),
        }
        if rows:
            entry.update(layout="table", rows=rows)
        else:
            entry.update(
                layout="bullets",
                bullets=[str(b) for b in slide.get("bullets") or [] if str(b).strip()],
            )
        made.append(entry)
    if closing.strip():
        made.append({"layout": "closing", "title": closing.strip(), "body": ""})
    return pptx_edit.written_bullets_only(
        deck_export.to_pptx(title, made, tokens=tokens, template=template)
    )


async def project_look(
    db: AsyncSession,
    user: User,
    session: ChatSession | None = None,
    *,
    project_id: str | None = None,
    template_id: str | None = None,
) -> tuple[dict[str, str] | None, str]:
    """`(tokens, template)` a PowerPoint deck made here takes, as the web decks do: the design
    system (테마) of the conversation's agent or project (`workspace_context.design_for`), else
    of `project_id`'s project, and the `template.pptx` of `template_id` (a 서식 the person
    picked) else of the project's default slides 서식 (`Project.render_templates`). `(None, "")`
    is the house look."""
    from app.services import design, design_templates, workspace_context

    pid = (session.project_id if session is not None else None) or project_id
    project = await db.get(Project, pid) if pid else None
    if project is not None and project.user_id != user.id:
        project = None
    chosen = None
    try:
        if session is not None:
            chosen = await workspace_context.design_for(db, user, session)
        elif project is not None:
            chosen = await workspace_context.design_for_project(db, user, project)
    except workspace_context.WorkspaceContextError:
        chosen = None
    tokens = design.tokens_of(chosen) if chosen is not None else None
    picked = template_id or (
        design_templates.default_for(project.render_templates, SessionKind.slides)
        if project is not None else None
    )
    found = design_templates.rendering(picked) if picked else None
    template = (found.pptx_template or "") if found is not None else ""
    return tokens, template


async def session_document(db: AsyncSession, user_id: str, session_id: str) -> Artifact | None:
    """The conversation's PowerPoint document: its newest."""
    return await office_doc.session_document(db, KIND, user_id, session_id)


async def create(db: AsyncSession, **fields: Any) -> Artifact:
    return await office_doc.create(db, KIND, **fields)


async def save(db: AsyncSession, artifact: Artifact, data: bytes, **fields: Any) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first."""
    return await office_doc.save(db, KIND, artifact, data, **fields)


def download_name(title: str) -> str:
    return office_doc.download_name(title, "pptx")
