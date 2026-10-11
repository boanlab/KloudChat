"""User templates: stored as plain text only (no HTML/CSS); owner-only edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import Field
from sqlmodel import col, select, update

from app.core.deps import CurrentUser, DbSession
from app.models.user import User, utcnow
from app.models.workspace import Agent, UserTemplate
from app.schemas.auth import Wire
from app.services import design_templates, user_templates

router = APIRouter(tags=["user-templates"])

_FORM_TYPES = (".docx", ".hwpx")
_FORM_BYTES = 10 * 1024 * 1024


class TemplateIn(Wire):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=20)
    fills: list[str] = Field(default_factory=list, max_length=6)
    example_prompt: str = Field(default="", max_length=1000)
    instructions: str = Field(default="", max_length=8000)
    checklist: str = Field(default="", max_length=4000)
    genre: str = Field(default="", max_length=4000)
    doc_format: dict[str, Any] = Field(default_factory=dict)
    cover_page: bool = False
    body_face: str = Field(default="", max_length=10)
    check_skills: list[str] = Field(default_factory=list, max_length=8)
    check_options: list[str] = Field(default_factory=list, max_length=4)
    questions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    process: dict[str, Any] = Field(default_factory=dict)
    visibility: str = Field(default="private", max_length=10)


def _cleaned(payload: TemplateIn) -> dict[str, Any]:
    try:
        return user_templates.clean(payload.model_dump())
    except user_templates.TemplateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=exc.code) from exc


async def _owned(db: DbSession, user: User, template_id: str) -> UserTemplate:
    row = await db.get(UserTemplate, user_templates.row_id(template_id) or template_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return row


async def _names(db: DbSession, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = (await db.exec(select(User).where(col(User.id).in_(sorted(ids))))).all()
    return {u.id: u.name for u in rows}


@router.get("/user-templates")
async def list_templates(user: CurrentUser, db: DbSession):
    rows = (await db.exec(select(UserTemplate).where(
        (UserTemplate.owner_id == user.id) | (UserTemplate.visibility == "org"))
        .order_by(col(UserTemplate.updated_at).desc()))).all()
    names = await _names(db, {r.owner_id for r in rows if r.owner_id != user.id})
    return {
        "mine": [user_templates.wire(r, viewer_id=user.id) for r in rows
                 if r.owner_id == user.id],
        "shared": [user_templates.wire(r, viewer_id=user.id, owner_name=names.get(r.owner_id, ""))
                   for r in rows if r.owner_id != user.id],
    }


@router.get("/user-templates/catalogue")
async def template_catalogue(user: CurrentUser):
    return user_templates.catalogue()


@router.get("/user-templates/start/{template_id}")
async def start_from(template_id: str, user: CurrentUser):
    """Shipped 서식 as editor fields."""
    template = design_templates.choosable(template_id)
    if template is None or template.kind != "document" or template.user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="design_template_not_found")
    return user_templates.draft_of(template)


@router.post("/user-templates", status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateIn, user: CurrentUser, db: DbSession):
    row = UserTemplate(owner_id=user.id, **_cleaned(payload))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await user_templates.ensure(db, [user_templates.template_id(row)])
    return user_templates.wire(row, viewer_id=user.id)


@router.get("/user-templates/{template_id}")
async def get_template(template_id: str, user: CurrentUser, db: DbSession):
    row = await db.get(UserTemplate, user_templates.row_id(template_id) or template_id)
    if not user_templates.readable(row, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return user_templates.wire(row, viewer_id=user.id)


@router.put("/user-templates/{template_id}")
async def update_template(template_id: str, payload: TemplateIn, user: CurrentUser,
                          db: DbSession):
    row = await _owned(db, user, template_id)
    for key, value in _cleaned(payload).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await user_templates.ensure(db, [user_templates.template_id(row)])
    return user_templates.wire(row, viewer_id=user.id)


@router.delete("/user-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, user: CurrentUser, db: DbSession):
    row = await _owned(db, user, template_id)
    wire_id = user_templates.template_id(row)
    # agents reset to no template; conversations keep the id (shared seed)
    await db.exec(update(Agent).where(col(Agent.render_template_id) == wire_id)
                  .values(render_template_id=None))
    await db.delete(row)
    await db.commit()
    design_templates.forget(wire_id)


@router.post("/user-templates/{template_id}/form")
async def upload_form(template_id: str, user: CurrentUser, db: DbSession,
                      file: UploadFile = File(...)):
    """Source form (DOCX/HWPX) text kept as writer reference."""
    from app.services import files as file_service

    row = await _owned(db, user, template_id)
    name = Path(file.filename or "form").name[:200]
    if Path(name).suffix.lower() not in _FORM_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="template_form_type")
    data = await file.read(_FORM_BYTES + 1)
    if len(data) > _FORM_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="template_form_too_large")
    try:
        text = file_service.extract_text(name, file.content_type or "", data)
    except Exception as exc:  # noqa: BLE001 — unreadable form refused
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="template_form_unreadable") from exc
    row.form_name = user_templates.plain(name, 200)
    row.form_text = user_templates.plain(text, 20000, lines=True)
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await user_templates.ensure(db, [user_templates.template_id(row)])
    return user_templates.wire(row, viewer_id=user.id)


@router.delete("/user-templates/{template_id}/form")
async def remove_form(template_id: str, user: CurrentUser, db: DbSession):
    row = await _owned(db, user, template_id)
    row.form_name, row.form_text, row.updated_at = "", "", utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await user_templates.ensure(db, [user_templates.template_id(row)])
    return user_templates.wire(row, viewer_id=user.id)


__all__ = ["router"]
