"""Office document artifacts (HWP, Word): one file blob per version, saved by the editor
beside the chat or by the agent's tools.

`Artifact.data` holds `{"format", "size", "text"}`, and `sourceFileId` when it was opened
from a file-box file; the file is `storage_key`. A version
moves only from the version its writer read (`expected`), in one conditional UPDATE, so a
hand save and an agent edit cannot both land on the same version. `hwp_doc` and `docx_doc`
say what their files are; everything else is here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import utcnow
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.services import doc_formats, files
from app.services.office_edit import BLANK
from app.services.search_text import artifact_text

MAX_TITLE = 200


class Conflict(Exception):
    """The artifact moved past the version the writer read."""


@dataclass(frozen=True)
class Kind:
    kind: ArtifactKind
    #: the format every saved version is in (`hwpx`, `docx`)
    editable: str
    #: title when none is given
    untitled: str
    #: `editable`, another format of this kind still to convert, or `` for none
    file_format: Callable[[bytes], str]
    #: `Artifact.data` for a file of a format
    describe: Callable[[bytes, str], dict[str, Any]]


#: a hand save's version summary when the editor sent none (it sends one in the UI language)
EDITOR_SAVE = "편집기에서 저장"

#: `Artifact.data` key naming the file-box file a document was opened from (its copy's origin)
SOURCE_KEY = "sourceFileId"


def source_of(data: dict[str, Any] | None) -> str | None:
    """The file-box file a document was opened from, if any."""
    value = (data or {}).get(SOURCE_KEY)
    return value if isinstance(value, str) and value else None


def with_source(data: dict[str, Any], source_file_id: str | None) -> dict[str, Any]:
    """`data` carrying the file it came from; every version keeps it."""
    return {**data, SOURCE_KEY: source_file_id} if source_file_id else data


async def session_document(
    db: AsyncSession, kind: Kind, user_id: str, session_id: str
) -> Artifact | None:
    """The conversation's document of this kind: its newest."""
    if not session_id:
        return None
    rows = (
        await db.exec(
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.session_id == session_id,
                Artifact.kind == kind.kind,
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
    kind: Kind,
    *,
    user_id: str,
    title: str,
    data: bytes,
    session_id: str | None = None,
    project_id: str | None = None,
    source_file_id: str | None = None,
) -> Artifact:
    fmt = kind.file_format(data)
    if not fmt:
        raise ValueError(f"not_a_{kind.kind.value}_file")
    artifact = Artifact(
        user_id=user_id,
        session_id=session_id,
        project_id=project_id,
        kind=kind.kind,
        title=(title.strip() or kind.untitled)[:MAX_TITLE],
        data=with_source(kind.describe(data, fmt), source_file_id),
    )
    artifact.storage_key = _store(artifact, 1, data, fmt)
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


async def save(
    db: AsyncSession,
    kind: Kind,
    artifact: Artifact,
    data: bytes,
    *,
    expected: int,
    summary: str = "",
    title: str | None = None,
) -> Artifact:
    """New version from `expected`; `Conflict` if another save landed first. Only the
    editable format: anything else is converted by the editor before anything edits it."""
    if kind.file_format(data) != kind.editable:
        raise ValueError(f"not_a_{kind.editable}_file")
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
    key = _store(artifact, expected + 1, data, kind.editable)
    described = with_source(kind.describe(data, kind.editable), source_of(artifact.data))
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


def download_name(title: str, fmt: str) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title or "").strip()[:80] or "document"
    return f"{stem}.{fmt}"


def formatted(
    title: str, body: str, fields: dict[str, str] | None = None, fmt_id: str = "official"
) -> tuple[list[dict], dict[str, Any]]:
    """Sections and head block of a document in a `doc_formats` head (공문 by default); a
    field not given stays a bracket blank such as 「[수신]」, never a guess."""
    fmt = doc_formats.BY_ID.get(fmt_id) or doc_formats.BY_ID["official"]
    given = {k: " ".join(str(v or "").split()) for k, v in (fields or {}).items()}
    # a value that is itself a blank (「[YYYY. MM. DD.]」) is not given: the field keeps its
    # own named blank, so 시행일 and a date in the body never share one
    given = {k: v for k, v in given.items() if not BLANK.fullmatch(v)}
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
    return [{"id": "body", "heading": "", "content": text}], block


def body_only(body: str) -> list[dict]:
    """The sections of a document with no format head: the body under its title."""
    return [{"id": "body", "heading": "", "content": body}]
