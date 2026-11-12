"""HWP document artifacts: one HWPX blob per version, saved by the editor or by the agent
(the versions themselves: `office_doc`). An uploaded `.hwp` is kept as it came until the
editor saves it back as HWPX."""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind
from app.services import hwpx_edit, office_doc, report_export
from app.services.office_doc import Conflict, read, read_version

__all__ = ["Conflict", "read", "read_version"]

HWPX_MIME = "application/hwp+zip"
#: `.hwp` (HWP 5 binary) magic: an OLE compound file
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_TITLE = office_doc.MAX_TITLE


def file_format(data: bytes) -> str:
    """`hwpx`, `hwp` or ``; the bytes decide, not the name."""
    if data[:4] == b"PK\x03\x04":
        try:
            hwpx_edit.outline(data)
        except hwpx_edit.HwpxError:
            return ""
        return "hwpx"
    if data[:8] == _OLE:
        return "hwp"
    return ""


def describe(data: bytes, fmt: str) -> dict[str, Any]:
    """`Artifact.data` for a file: format, size, and body text for search (HWPX only)."""
    text = ""
    if fmt == "hwpx":
        try:
            text = hwpx_edit.plain_text(data)[:20000]
        except hwpx_edit.HwpxError:
            text = ""
    return {"format": fmt, "size": len(data), "text": text}


KIND = office_doc.Kind(ArtifactKind.hwp, "hwpx", "한글 문서", file_format, describe)


def blank() -> bytes:
    """An empty A4 page in the house HWPX shapes: one body-size paragraph."""
    data = report_export.to_hwpx("", [])
    return hwpx_edit.restyle_first(data, para_pr="3", char_pr="0")


def official(
    title: str, body: str, fields: dict[str, str] | None = None, fmt_id: str = "official"
) -> bytes:
    """A document in a `doc_formats` head (공문 by default); a field not given stays a
    bracket blank such as 「[수신]」, never a guess."""
    sections, block = office_doc.formatted(title, body, fields, fmt_id)
    return report_export.to_hwpx(title, sections, title_block=block)


def plain(title: str, body: str) -> bytes:
    """A document with no format head: a title line, then the body."""
    return report_export.to_hwpx(title, office_doc.body_only(body))


async def session_document(db: AsyncSession, user_id: str, session_id: str) -> Artifact | None:
    """The conversation's HWP document: its newest."""
    return await office_doc.session_document(db, KIND, user_id, session_id)


async def create(db: AsyncSession, **fields: Any) -> Artifact:
    return await office_doc.create(db, KIND, **fields)


async def save(db: AsyncSession, artifact: Artifact, data: bytes, **fields: Any) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first. Only HWPX:
    an `.hwp` is turned into HWPX by the editor before anything edits it."""
    return await office_doc.save(db, KIND, artifact, data, **fields)


def download_name(title: str, fmt: str = "hwpx") -> str:
    return office_doc.download_name(title, fmt)
