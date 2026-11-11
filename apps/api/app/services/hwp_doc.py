"""HWP document artifacts: one HWPX blob per version, saved by the editor or by the agent.

`Artifact.data` holds `{"format", "size", "text"}`; the file is `storage_key`. A version
moves only from the version its writer read (`expected`), in one conditional UPDATE, so a
hand save and an agent edit cannot both land on the same version.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import utcnow
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.services import doc_formats, files, hwpx_edit, report_export
from app.services.search_text import artifact_text

log = logging.getLogger(__name__)

HWPX_MIME = "application/hwp+zip"
#: `.hwp` (HWP 5 binary) magic: an OLE compound file
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_TITLE = 200


class Conflict(Exception):
    """The artifact moved past the version the writer read."""


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


def blank() -> bytes:
    """An empty A4 page in the house HWPX shapes: one body-size paragraph."""
    data = report_export.to_hwpx("", [])
    return hwpx_edit.restyle_first(data, para_pr="3", char_pr="0")


def official(
    title: str, body: str, fields: dict[str, str] | None = None, fmt_id: str = "official"
) -> bytes:
    """A document in a `doc_formats` head (공문 by default); a field not given stays a
    bracket blank such as 「[수신]」, never a guess."""
    fmt = doc_formats.BY_ID.get(fmt_id) or doc_formats.BY_ID["official"]
    given = {k: " ".join(str(v or "").split()) for k, v in (fields or {}).items()}
    block: dict[str, Any] = {
        "format": fmt.id,
        "label": fmt.label,
        "head": fmt.head,
        "numbering": fmt.numbering,
        "fields": [
            [name, given.get(name, "")]
            for name in fmt.fields
            if given.get(name) or name not in fmt.optional
        ],
    }
    if fmt.lists:
        block["lists"] = fmt.lists
    if fmt.closing:
        block["closing"] = True
    text = body
    if fmt.numbering == "official":
        text = doc_formats.official_body(doc_formats.official_notation(body))
    return report_export.to_hwpx(
        title, [{"id": "body", "heading": "", "content": text}], title_block=block
    )


def plain(title: str, body: str) -> bytes:
    """A document with no format head: a title line, then the body."""
    return report_export.to_hwpx(title, [{"id": "body", "heading": "", "content": body}])


async def session_document(db: AsyncSession, user_id: str, session_id: str) -> Artifact | None:
    """The conversation's HWP document: its newest."""
    if not session_id:
        return None
    rows = (
        await db.exec(
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.session_id == session_id,
                Artifact.kind == ArtifactKind.hwp,
            )
            .order_by(col(Artifact.updated_at).desc())
            .limit(1)
        )
    ).all()
    return rows[0] if rows else None


def read(artifact: Artifact) -> bytes:
    """The current version's file."""
    if not artifact.storage_key:
        raise FileNotFoundError(artifact.id)
    return files.read_blob(artifact.storage_key)


async def read_version(db: AsyncSession, artifact: Artifact, version: int) -> bytes:
    """An older version's file; `ArtifactVersion` rows keep their own blob."""
    if version == artifact.version:
        return read(artifact)
    row = (
        await db.exec(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == version
            )
        )
    ).first()
    if row is None or not row.storage_key:
        raise FileNotFoundError(f"{artifact.id} v{version}")
    return files.read_blob(row.storage_key)


def _store(artifact: Artifact, version: int, data: bytes, fmt: str) -> str:
    return files.write_blob(
        artifact.user_id, f"{artifact.id}-v{version}-{uuid4().hex[:8]}", f"document.{fmt}", data
    )


async def create(
    db: AsyncSession,
    *,
    user_id: str,
    title: str,
    data: bytes,
    session_id: str | None = None,
    project_id: str | None = None,
) -> Artifact:
    fmt = file_format(data)
    if not fmt:
        raise ValueError("not_a_hwp_file")
    artifact = Artifact(
        user_id=user_id,
        session_id=session_id,
        project_id=project_id,
        kind=ArtifactKind.hwp,
        title=(title.strip() or "한글 문서")[:MAX_TITLE],
        data=describe(data, fmt),
    )
    artifact.storage_key = _store(artifact, 1, data, fmt)
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


async def save(
    db: AsyncSession,
    artifact: Artifact,
    data: bytes,
    *,
    expected: int,
    summary: str = "",
    title: str | None = None,
) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first. Only HWPX:
    an `.hwp` is turned into HWPX by the editor before anything edits it."""
    if file_format(data) != "hwpx":
        raise ValueError("not_a_hwpx_file")
    if expected != artifact.version:
        raise Conflict(artifact.id)
    # the superseded body, taken before the UPDATE syncs the loaded row
    before = ArtifactVersion(
        artifact_id=artifact.id,
        version=expected,
        data=artifact.data,
        storage_key=artifact.storage_key,
        summary=summary[:200],
    )
    key = _store(artifact, expected + 1, data, "hwpx")
    described = describe(data, "hwpx")
    values: dict[str, Any] = {
        "version": expected + 1,
        "data": described,
        "storage_key": key,
        "search_text": artifact_text(described),
        "updated_at": utcnow(),
    }
    if title:
        values["title"] = title.strip()[:MAX_TITLE]
    moved = await db.exec(
        update(Artifact)
        .where(col(Artifact.id) == artifact.id, col(Artifact.version) == expected)
        .values(**values)
    )
    if moved.rowcount != 1:
        await db.rollback()
        files.delete_blob(key)
        raise Conflict(artifact.id)
    db.add(before)
    await db.commit()
    await db.refresh(artifact)
    return artifact


def download_name(title: str, fmt: str = "hwpx") -> str:
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title or "").strip()[:80] or "document"
    return f"{stem}.{fmt}"
