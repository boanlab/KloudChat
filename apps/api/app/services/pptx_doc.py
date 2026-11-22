"""PowerPoint document artifacts: one `.pptx` blob per version (`office_doc`); `.ppt` is not opened.

Distinct from the `deck` kind (`deck.py`), which KloudChat writes itself."""

from __future__ import annotations

import re
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, Project
from app.services import deck_export, form_marks, office_doc, pptx_edit
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
    out = {"format": fmt, "size": len(data), "text": text, "slides": slides}
    if (form := form_marks.read(data)) is not None:
        # form this version was written on (`pptx_form`), for the 「양식을 따름」 line
        out["form"] = form
    return out


KIND = office_doc.Kind(ArtifactKind.pptx, "pptx", "PowerPoint 문서", file_format, describe)


def on_form(form: Any, title: str, slides: list[dict[str, Any]]) -> bytes:
    """`slides` written on a PowerPoint form (`form_files.Resolved`), marked with what was
    followed."""
    from app.services import pptx_form

    data, followed = pptx_form.write(form.data, title, slides, profile=form.profile,
                                     name=form.choice.name)
    return form_marks.mark(data, {
        "kind": "pptx", "name": form.choice.name, "source": form.choice.source,
        "fileId": form.choice.file_id, "followed": followed.wire(),
    })


def blank(*, tokens: dict[str, str] | None = None, template: str = "",
          form: Any = None) -> bytes:
    """A new deck with one empty title slide.

    Default is 16:9 (`pptx_edit.blank_deck`); with `tokens` or `template`, an empty cover in that
    look (`deck_export.to_pptx`); with a `form`, its own cover minus the sample slides."""
    if form is not None:
        return on_form(form, "", [{"layout": "title", "title": "", "body": ""}])
    if not tokens and not template:
        return pptx_edit.blank_deck()
    return deck_export.to_pptx(
        "", [{"layout": "title", "title": "", "body": ""}], tokens=tokens, template=template
    )


def deck(
    title: str,
    subtitle: str,
    slides: list[dict[str, Any]],
    closing: str = "",
    *,
    tokens: dict[str, str] | None = None,
    template: str = "",
    form: Any = None,
    overflow: list[dict] | None = None,
) -> bytes:
    """A new deck in the house look (`deck_export`): cover, then a bullet or table slide per entry.

    `closing` adds a last slide. `tokens` is the design system's `{accent, ink, muted, font, …}`;
    `template` is a slides 서식's `template.pptx` path; `project_look` finds both."""
    made: list[dict[str, Any]] = [{"layout": "title", "title": title, "body": subtitle}]
    for slide in slides[:MAX_WRITE]:
        rows = [[str(cell) for cell in row] for row in slide.get("rows") or [] if row]
        bullets = [str(b) for b in slide.get("bullets") or [] if str(b).strip()]
        entry: dict[str, Any] = {
            "title": str(slide.get("title") or ""),
            "notes": str(slide.get("notes") or ""),
        }
        chart = slide.get("chart")
        image = slide.get("image")
        if isinstance(chart, dict) and chart.get("series"):
            # a native chart (its worksheet editable in PowerPoint), bullets beside it
            entry.update(layout="chart", chart=chart, bullets=bullets)
        elif isinstance(image, dict) and image.get("data"):
            # a picture (a figure cropped from a paper, an attached image) with its caption
            picture = {"data": image["data"], "caption": str(image.get("caption") or ""),
                       "size": "large"}
            if bullets and _wide(image["data"]):
                # a wide figure beside text is unreadable: it takes the full width, a few
                # short lines sit under it, the rest go to the speaker notes
                kept, moved = strip_split(bullets)
                notes = "\n".join([entry["notes"], *moved]).strip()
                entry.update(layout="bullets", bullets=kept,
                             image={**picture, "strip": True}, notes=notes)
            else:
                entry.update(layout="bullets", bullets=bullets, image=picture)
        elif rows:
            entry.update(layout="table", rows=rows)
        else:
            entry.update(layout="bullets", bullets=bullets)
        made.append(entry)
    if closing.strip():
        made.append({"layout": "closing", "title": closing.strip(), "body": ""})
    if form is not None:
        # on the conversation's PowerPoint form: its layouts, type and theme
        return on_form(form, title, made)
    # `to_pptx` leaves no master bullet under a plain line (`written_bullets_only`)
    # `overflow` gets each slide whose text runs past its place at the floor size
    return deck_export.to_pptx(title, made, tokens=tokens, template=template, overflow=overflow)


#: lines kept visible under a wide figure
STRIP_LINES = 3
_DIGIT = re.compile(r"\d")


def strip_split(bullets: list[str]) -> tuple[list[str], list[str]]:
    """`(kept, moved)`: up to `STRIP_LINES` lines stay on the slide (numeric ones first), the
    rest go to the notes."""
    ranked = sorted(range(len(bullets)), key=lambda i: (not _DIGIT.search(bullets[i]), i))
    keep = set(ranked[:STRIP_LINES])
    return ([b for i, b in enumerate(bullets) if i in keep],
            [b for i, b in enumerate(bullets) if i not in keep])


def _wide(data: bytes) -> bool:
    """A picture at least twice as wide as it is tall."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
    except Exception:  # noqa: BLE001 — an unreadable picture keeps the usual place
        return False
    return bool(height) and width / height >= 2.0


async def project_look(
    db: AsyncSession,
    user: User,
    session: ChatSession | None = None,
    *,
    project_id: str | None = None,
    template_id: str | None = None,
) -> tuple[dict[str, str] | None, str]:
    """`(tokens, template)` a PowerPoint deck takes, as web decks do.

    Tokens: the design system (테마) of the conversation's agent or project
    (`workspace_context.design_for`), else of `project_id`. Template: the `template.pptx` of
    `template_id`. `(None, "")` is the house look."""
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
    tokens = design.tokens_of(chosen) if chosen is not None and chosen.tokens else None
    # only an explicitly picked template gives its base
    picked = template_id
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
