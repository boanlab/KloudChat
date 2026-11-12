"""PowerPoint document artifacts: one `.pptx` blob per version, saved by the editor or by the
agent (the versions themselves: `office_doc`). A legacy `.ppt` is not opened.

This is the uploaded or company `.pptx` worked as a file beside the chat; a deck KloudChat
writes itself is the `deck` kind (`deck.py`), which also downloads as `.pptx`."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind
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


def blank() -> bytes:
    """One empty 16:9 title slide, with the layouts a new slide takes."""
    return pptx_edit.blank_deck()


def deck(title: str, subtitle: str, slides: list[dict[str, Any]], closing: str = "") -> bytes:
    """A new deck in the house look (`deck_export`): a cover, then a slide per entry, as
    bullets, or a table when it has rows; `closing` adds a last slide with that line."""
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
    return deck_export.to_pptx(title, made)


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
