"""Sheet artifacts: create one (blank, or from an uploaded `.xlsx`), read the editor's model of
a version, change cells, download the workbook. The base routes are the office-document set
(`office_docs`); the cell routes are this surface's own."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Response, status
from pydantic import Field

from app.core.deps import CurrentUser, DbSession
from app.models.workspace import Artifact, ArtifactKind
from app.routers.office_docs import Routes, _conflict, router_for
from app.routers.workspace import _own
from app.schemas.auth import Wire
from app.schemas.workspace import ArtifactOut
from app.services import office_doc, sheet_doc, sheet_model, sheet_patch

router = router_for(Routes(
    slug="xlsx",
    kind=sheet_doc.KIND,
    blank=sheet_doc.blank,
    blank_title="새 시트",
    suffix=re.compile(r"\.xlsx$", re.I),
    mime=lambda fmt: sheet_doc.XLSX_MIME,
))

MAX_OPS = 5000


class CellChanges(Wire):
    #: the version the editor read; another save since then is a 409
    expected_version: int
    #: `sheet_patch.apply` ops: `{sheet, r, c, v?, f?, s?}`, `width`, `merge`, `unmerge`
    ops: list[dict[str, Any]] = Field(max_length=MAX_OPS)
    summary: str = Field(default="", max_length=200)


async def _sheet(db: DbSession, user: CurrentUser, artifact_id: str) -> Artifact:
    artifact = await _own(db, Artifact, "user_id", user, artifact_id)
    if artifact.kind is not ArtifactKind.xlsx:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not_found")
    return artifact


@router.get("/artifacts/{artifact_id}/xlsx/model")
async def sheet_model_of(artifact_id: str, user: CurrentUser, db: DbSession):
    """The current version as the grid's model, with its version number."""
    artifact = await _sheet(db, user, artifact_id)
    try:
        data = office_doc.read(artifact)
    except (OSError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    try:
        model = sheet_model.to_model(data)
    except Exception:  # noqa: BLE001 - a package the reader cannot walk is not a sheet
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_xlsx_file"
        ) from None
    return {"version": artifact.version, "title": artifact.title, **model}


@router.patch("/artifacts/{artifact_id}/xlsx/cells", response_model=ArtifactOut)
async def change_cells(
    artifact_id: str, payload: CellChanges, user: CurrentUser, db: DbSession
):
    """Applies the changed cells to the stored workbook as the next version; 409 when another
    save landed since `expectedVersion`."""
    artifact = await _sheet(db, user, artifact_id)
    if payload.expected_version != artifact.version:
        raise _conflict()
    try:
        patched = sheet_patch.apply(office_doc.read(artifact), payload.ops)
    except (OSError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    except sheet_patch.PatchError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from None
    try:
        saved = await office_doc.save(
            db, sheet_doc.KIND, artifact, patched, expected=payload.expected_version,
            summary=payload.summary or office_doc.EDITOR_SAVE,
        )
    except office_doc.Conflict:
        raise _conflict() from None
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail="not_a_xlsx_file"
        ) from None
    return ArtifactOut.of(saved)


class EditsFile(Wire):
    #: the version the editor read; the edits go onto that one, saved or not
    base_version: int
    ops: list[dict[str, Any]] = Field(max_length=MAX_OPS)


@router.post("/artifacts/{artifact_id}/xlsx/edits-file")
async def edits_file(artifact_id: str, payload: EditsFile, user: CurrentUser, db: DbSession):
    """The workbook of `baseVersion` with the edits applied, for download. Nothing is saved: this
    is how a person keeps their edits when their save lost to a newer version."""
    artifact = await _sheet(db, user, artifact_id)
    try:
        if payload.base_version == artifact.version:
            data = office_doc.read(artifact)
        else:
            data = await office_doc.read_version(db, artifact, payload.base_version)
        patched = sheet_patch.apply(data, payload.ops)
    except (OSError, FileNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="blob_missing") from None
    except sheet_patch.PatchError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from None
    name = office_doc.download_name(artifact.title, "xlsx")
    return Response(
        content=patched,
        media_type=sheet_doc.XLSX_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                 "Cache-Control": "no-store"},
    )
