"""User templates, plain text only (no HTML/CSS), owner-only.

Read-only: they can be listed, deleted and turned into agents (`POST
/user-templates/{id}/to-agent`);
create and update return 410."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import Field
from sqlmodel import col, select, update

from app.core.deps import CurrentUser, DbSession
from app.models.user import User, utcnow
from app.models.workspace import Agent, UserTemplate
from app.schemas.auth import Wire
from app.services import audit, design_templates, user_templates

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


def _retired() -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail="templates_retired")


@router.post("/user-templates", status_code=status.HTTP_201_CREATED)
async def create_template(payload: TemplateIn, user: CurrentUser):
    """Retired (410)."""
    del payload, user
    raise _retired()


@router.get("/user-templates/{template_id}")
async def get_template(template_id: str, user: CurrentUser, db: DbSession):
    row = await db.get(UserTemplate, user_templates.row_id(template_id) or template_id)
    if not user_templates.readable(row, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return user_templates.wire(row, viewer_id=user.id)


@router.put("/user-templates/{template_id}")
async def update_template(template_id: str, payload: TemplateIn, user: CurrentUser):
    """Retired (410)."""
    del template_id, payload, user
    raise _retired()


@router.delete("/user-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, user: CurrentUser, db: DbSession,
                          request: Request = None):  # type: ignore[assignment]
    row = await _owned(db, user, template_id)
    wire_id = user_templates.template_id(row)
    # agents reset to no template; conversations keep the id (shared seed)
    await db.exec(update(Agent).where(col(Agent.render_template_id) == wire_id)
                  .values(render_template_id=None))
    await db.delete(row)
    db.add(audit.deletion(user, request, action="template.delete", ids=[row.id],
                          counts={"templates": 1}))
    await db.commit()
    design_templates.forget(wire_id)


@router.post("/user-templates/{template_id}/form")
async def upload_form(template_id: str, user: CurrentUser, file: UploadFile = File(...)):
    """Retired (410)."""
    del template_id, user, file
    raise _retired()


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


def agent_fields(row: UserTemplate) -> dict[str, Any]:
    """An agent's fields from a person's template: its format, questions, checks, process and
    look carried over; its writing rules, genre and checklist become the agent's instructions."""
    from app.services import doc_formats

    fmt = dict(row.doc_format or {})
    label = str(fmt.get("label") or "").strip()
    format_id = next((f.id for f in doc_formats.FORMATS if label and f.label == label
                      and f.id not in doc_formats.RETIRED), None)
    parts = [f"# {row.name}"]
    if row.description.strip():
        parts.append(row.description.strip())
    if row.instructions.strip():
        parts.append("## 쓰는 법\n" + row.instructions.strip())
    if row.genre.strip():
        parts.append("## 문서의 결\n" + row.genre.strip())
    if format_id is None and fmt:
        head = ", ".join(str(f) for f in fmt.get("fields") or [] if str(f).strip())
        lines = [f"- 문서 이름: {label}"] if label else []
        if head:
            lines.append(f"- 머리에 적을 칸: {head}")
        if fmt.get("numbering"):
            lines.append(f"- 번호: {fmt['numbering']}")
        if lines:
            parts.append("## 문서 형식\n" + "\n".join(lines))
    if row.checklist.strip():
        parts.append("## 다 쓴 뒤 확인할 것\n" + row.checklist.strip())
    brief = {"serif": "본문은 명조.", "gothic": "본문은 고딕."}.get(row.body_face, "")
    return {
        "name": row.name[:120], "description": row.description,
        "system_prompt": "\n\n".join(parts)[:24_000],
        "kinds": ["report"], "surface": "report", "doc_format": format_id,
        "gap_questions": [dict(q) for q in row.questions or []] or None,
        "process_settings": dict(row.process) if row.process else None,
        "check_skills": list(row.check_skills or []) or None,
        "check_options": {key: True for key in row.check_options or []} or None,
        "starters": [row.example_prompt.strip()] if row.example_prompt.strip() else [],
        "design_brief": brief,
    }


@router.post("/user-templates/{template_id}/to-agent", status_code=status.HTTP_201_CREATED)
async def template_to_agent(template_id: str, user: CurrentUser, db: DbSession):
    """A person's own template as a new agent of theirs (the template is kept)."""
    from app.routers.workspace import _slug
    from app.schemas.workspace import AgentOut

    row = await _owned(db, user, template_id)
    fields = agent_fields(row)
    taken = set((await db.exec(select(Agent.slug).where(Agent.owner_id == user.id))).all())
    slug = base = _slug(fields["name"])
    n = 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    agent = Agent(owner_id=user.id, slug=slug, **fields)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.of(agent)
