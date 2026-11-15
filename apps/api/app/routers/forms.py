"""Form files (양식) saved as a project's or agent's 기본 양식 파일, and the 디자인 지침 reader.

A saved form is a `role=form` copy of the person's file: kept out of the file box and never read
as material, so a project's knowledge file can be its form without its sample text becoming a
source. Slots are set with the project's or agent's `formFiles`.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import Field

from app.core.deps import CurrentUser, DbSession
from app.models.workspace import StoredFile
from app.schemas.auth import Wire
from app.schemas.workspace import FileOut
from app.services import files as file_service
from app.services import form_files, form_profile, style_spec

router = APIRouter(tags=["forms"])


class FromFileIn(Wire):
    file_id: str = Field(min_length=1, max_length=64)


class BriefIn(Wire):
    text: str = Field(default="", max_length=style_spec.MAX_BRIEF)


class BriefOut(Wire):
    #: the style parameters the writers will use (`StyleSpec.wire`)
    spec: dict
    #: tone sentences, which go to the prompt instead
    tone: list[str]


def _refuse(row: StoredFile) -> None:
    if (code := form_files.refusal(row)) is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=code)


@router.post("/forms", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_form(user: CurrentUser, db: DbSession, file: UploadFile = File(...)):
    """A new saved form from an upload; refused unless it is a readable PowerPoint, Word or
    HWPX file (a `.hwp` is told to come back as HWPX)."""
    from app.routers.workspace import upload_bytes

    data = await upload_bytes(file)
    name = file_service.safe_name(file.filename or "form")
    row = StoredFile(
        user_id=user.id, name=name, size=len(data), role="form",
        mime=file_service.detected_mime(name, file.content_type, data),
    )
    try:
        row.text = await file_service.text_of(row.name, row.mime, data)
    except Exception:  # noqa: BLE001 — the profile says whether it can be followed
        row.text = ""
    row.form_profile = form_profile.profile(row.name, data, row.text)
    _refuse(row)
    row.tokens = file_service.estimate_tokens(row.text)
    row.storage_key = file_service.write_blob(user.id, row.id, row.name, data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return FileOut.of(row)


@router.post("/forms/from-file", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def form_from_file(payload: FromFileIn, user: CurrentUser, db: DbSession):
    """A saved form copied from one of the person's files (file box or project knowledge)."""
    source = await db.get(StoredFile, payload.file_id)
    if source is None or source.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if source.form_profile is None or source.form_profile.get("version") != form_profile.VERSION:
        data = file_service.read_blob(source.storage_key)
        source.form_profile = form_profile.profile(source.name, data, source.text)
        db.add(source)
    _refuse(source)
    row = form_files.new_form_copy(user.id, source)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return FileOut.of(row)


@router.post("/design-brief/read", response_model=BriefOut)
async def read_brief(payload: BriefIn, user: CurrentUser):
    """What a 디자인 지침 sets, as the writers will read it (the 「읽은 값」 chips)."""
    spec = style_spec.parse(payload.text)
    return BriefOut(spec={k: v for k, v in spec.wire().items() if k != "tone"}, tone=spec.tone)
