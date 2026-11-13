"""Word document artifacts: one `.docx` blob per version, saved by the editor or by the agent
(the versions themselves: `office_doc`). A legacy `.doc` is not opened."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind
from app.services import docx_edit, office_doc, office_look, report_export
from app.services.office_doc import Conflict, read, read_version

__all__ = ["Conflict", "read", "read_version"]

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def file_format(data: bytes) -> str:
    """`docx` or ``; the bytes decide, not the name."""
    try:
        docx_edit.outline(data)
    except docx_edit.DocxError:
        return ""
    return "docx"


def describe(data: bytes, fmt: str) -> dict[str, Any]:
    """`Artifact.data` for a file: format, size, and body text for search."""
    try:
        text = docx_edit.plain_text(data)[:20000]
    except docx_edit.DocxError:
        text = ""
    return {"format": fmt, "size": len(data), "text": text}


KIND = office_doc.Kind(ArtifactKind.docx, "docx", "Word 문서", file_format, describe)


def blank(look: office_look.Look | None = None) -> bytes:
    """An empty A4 page in the house Word styles (the template's base and the theme when
    given): one body-text paragraph."""
    data = report_export.to_docx("", [], **office_look.docx(look))
    try:
        return docx_edit.restyle_first(data, "Body Text")
    except (KeyError, ValueError, docx_edit.DocxError):
        # a template base without the house body style keeps its own Normal
        return data


def official(
    title: str, body: str, fields: dict[str, str] | None = None, fmt_id: str = "official",
    look: office_look.Look | None = None,
) -> bytes:
    """A document in a `doc_formats` head (공문 by default); a field not given stays a
    bracket blank such as 「[수신]」, never a guess."""
    sections, block = office_doc.formatted(title, body, fields, fmt_id)
    return report_export.to_docx(title, sections, title_block=block, **office_look.docx(look))


def plain(title: str, body: str, look: office_look.Look | None = None) -> bytes:
    """A document with no format head: a title line, then the body."""
    return report_export.to_docx(title, office_doc.body_only(body), **office_look.docx(look))


async def session_document(db: AsyncSession, user_id: str, session_id: str) -> Artifact | None:
    """The conversation's Word document: its newest."""
    return await office_doc.session_document(db, KIND, user_id, session_id)


async def create(db: AsyncSession, **fields: Any) -> Artifact:
    return await office_doc.create(db, KIND, **fields)


async def save(db: AsyncSession, artifact: Artifact, data: bytes, **fields: Any) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first."""
    return await office_doc.save(db, KIND, artifact, data, **fields)


def download_name(title: str) -> str:
    return office_doc.download_name(title, "docx")
