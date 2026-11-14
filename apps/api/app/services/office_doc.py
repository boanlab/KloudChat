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


async def session_documents(
    db: AsyncSession, kind: Kind, user_id: str, session_id: str
) -> list[Artifact]:
    """The conversation's documents of this kind, newest first."""
    if not session_id:
        return []
    return list(
        (
            await db.exec(
                select(Artifact)
                .where(
                    Artifact.user_id == user_id,
                    Artifact.session_id == session_id,
                    Artifact.kind == kind.kind,
                )
                .order_by(col(Artifact.updated_at).desc())
            )
        ).all()
    )


async def session_document(
    db: AsyncSession, kind: Kind, user_id: str, session_id: str
) -> Artifact | None:
    """The conversation's newest document of this kind: that the chat has one at all (its
    tools join the turn). Which one a tool works on is `target`'s to say."""
    rows = await session_documents(db, kind, user_id, session_id)
    return rows[0] if rows else None


async def owned(
    db: AsyncSession, artifact_id: str, *, user_id: str, session_id: str,
    kind: Kind | None = None,
) -> Artifact | None:
    """`artifact_id` when it is this person's, in this conversation, and of `kind`."""
    if not artifact_id:
        return None
    found = await db.get(Artifact, artifact_id)
    if (
        found is None
        or found.user_id != user_id
        or not session_id
        or found.session_id != session_id
        or (kind is not None and found.kind is not kind.kind)
    ):
        return None
    return found


@dataclass(frozen=True)
class Target:
    """The document a tool works on, or why there is none: `choices` when the conversation has
    several of the kind and none is open beside it (the model asks which)."""

    artifact: Artifact | None
    choices: tuple[Artifact, ...] = ()


async def target(
    db: AsyncSession, kind: Kind, *, user_id: str, session_id: str,
    worked_on: str = "", opened: str = "", named: str = "",
) -> Target:
    """Which document of this kind a chat edit works on, never "the newest of its kind":
    the one this turn already read or wrote (`worked_on`), else the one open in the panel
    (`opened`), else the one the model named after asking (`named`, an id or a title), else
    the conversation's only one. Several and none of these: `choices`, to ask about."""
    for artifact_id in (worked_on, opened):
        if found := await owned(db, artifact_id, user_id=user_id, session_id=session_id,
                                kind=kind):
            return Target(found)
    rows = await session_documents(db, kind, user_id, session_id)
    if named := " ".join(str(named or "").split()):
        picked = [a for a in rows if a.id == named or " ".join(a.title.split()) == named]
        if len(picked) == 1:
            return Target(picked[0])
    if len(rows) == 1:
        return Target(rows[0])
    return Target(None, tuple(rows))


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


#: the kinds that keep a file per version (`storage_key`)
FILE_KINDS = (ArtifactKind.hwp, ArtifactKind.docx, ArtifactKind.pptx)


async def kept_files(db: AsyncSession, rows: list[Artifact]) -> set[str]:
    """The files these artifacts keep, one per office version; read before their rows are
    deleted, removed with `drop_files` after the commit. A restored version shares its key
    with the version it came from, so the keys are a set."""
    office = [a.id for a in rows if a.kind in FILE_KINDS]
    if not office:
        return set()
    keys = {a.storage_key for a in rows if a.id in office and a.storage_key}
    keys |= {key for key in (await db.exec(
        select(ArtifactVersion.storage_key).where(col(ArtifactVersion.artifact_id).in_(office))
    )).all() if key}
    return keys


def drop_files(keys: set[str]) -> None:
    """After the commit only: a failed delete leaves rows that still find their files."""
    for key in keys:
        files.delete_blob(key)


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
