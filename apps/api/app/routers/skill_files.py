"""Skill import/export as files and admin queue for script-bearing shares; scripts sandbox-only."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import Field
from sqlmodel import col, select

from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.core.logs import safe
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Skill, SkillSource, Visibility
from app.routers.workspace import _own, _slug
from app.schemas.auth import Wire
from app.schemas.workspace import SkillOut
from app.services import skill_import, starter
from app.services.tools import registry as tool_registry

log = logging.getLogger(__name__)

router = APIRouter(tags=["workspace"])

#: imports (dry runs too) per account per window
RATE_LIMIT = 30
RATE_WINDOW_SEC = 600
_calls: dict[str, deque[float]] = defaultdict(deque)
#: multipart overhead allowance
_MULTIPART_SLACK = 64 * 1024


class FindingOut(Wire):
    severity: str
    code: str
    message: str
    file: str = ""
    line: int | None = None


class ExcludedOut(Wire):
    path: str
    reason: str


class ScriptPreviewOut(Wire):
    name: str
    summary: str = ""
    size: int = 0
    source: str = ""


class ImportPreviewOut(Wire):
    filename: str
    ok: bool
    name: str
    description: str
    when_to_use: str
    kinds: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    version: str
    body_chars: int
    estimated_tokens: int
    kept: list[str] = Field(default_factory=list)
    excluded: list[ExcludedOut] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    server: dict | None = None
    #: kept; sandbox-only
    scripts: list[ScriptPreviewOut] = Field(default_factory=list)

    @classmethod
    def of(cls, p: skill_import.Preview) -> ImportPreviewOut:
        return cls(
            filename=p.filename,
            ok=p.ok,
            name=p.name,
            description=p.description,
            when_to_use=p.when_to_use,
            kinds=list(p.kinds),
            required_tools=list(p.required_tools),
            version=p.version,
            body_chars=len(p.body),
            estimated_tokens=starter.estimate_tokens(p.when_to_use, p.body, p.description)
            if p.ok
            else 0,
            kept=list(p.kept),
            excluded=[ExcludedOut(path=e.path, reason=e.reason) for e in p.excluded],
            findings=[
                FindingOut(
                    severity=f.severity, code=f.code, message=f.message, file=f.file, line=f.line
                )
                for f in p.findings
            ],
            server=_server_wire(p.server),
            scripts=[ScriptPreviewOut(name=x.name, summary=x.summary, size=x.size,
                                      source=x.source) for x in p.scripts],
        )


def _server_wire(fields: dict | None) -> dict | None:
    if not fields:
        return None
    from app.schemas.workspace import SkillServer

    return SkillServer.model_validate(fields).model_dump(by_alias=True)


class ImportOut(Wire):
    dry_run: bool
    items: list[ImportPreviewOut]
    #: empty on dry run
    created: list[SkillOut] = Field(default_factory=list)


def _rate(user_id: str) -> None:
    now = time.monotonic()
    window = _calls[user_id]
    while window and now - window[0] > RATE_WINDOW_SEC:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="skill_import_rate_limited",
            headers={"Retry-After": str(int(RATE_WINDOW_SEC - (now - window[0])) + 1)},
        )
    window.append(now)


@router.post("/skills/import", response_model=ImportOut)
async def import_skills(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    files: list[UploadFile] = File(...),
    dry_run: bool = Query(False, alias="dryRun"),
    draft: bool = Query(False),
):
    """Upload → skills; all-or-nothing on block findings; `dryRun` writes nothing."""
    limit = skill_import.MAX_FILES * skill_import.MAX_UPLOAD_BYTES + _MULTIPART_SLACK
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="skill_import_too_large"
        )
    if not files:
        raise HTTPException(422, detail="no_files")
    if len(files) > skill_import.MAX_FILES:
        raise HTTPException(422, detail="too_many_files")
    _rate(user.id)

    known = {str(row["name"]) for row in await tool_registry.tool_catalog(db, user)}
    previews: list[skill_import.Preview] = []
    for upload in files:
        # cap+1 bytes detects oversize
        data = await upload.read(skill_import.MAX_UPLOAD_BYTES + 1)
        previews.append(skill_import.inspect(upload.filename or "SKILL.md", data, known))
    items = [ImportPreviewOut.of(p) for p in previews]
    if dry_run:
        return ImportOut(dry_run=True, items=items)
    if not all(p.ok for p in previews):
        raise HTTPException(422, detail="skill_import_blocked")

    created: list[Skill] = []
    for preview in previews:
        skill = Skill(
            owner_id=user.id,
            name=preview.name,
            slug=_slug(preview.name),
            description=preview.description,
            when_to_use=preview.when_to_use,
            body=preview.body,
            catalog_key=None,
            source=SkillSource.personal,
            visibility=Visibility.private,
            kinds=list(preview.kinds),
            required_tools=list(preview.required_tools),
            version=preview.version,
            enabled=not draft,
            server_fields=preview.server,
            script_files=[x.stored() for x in preview.scripts] or None,
            estimated_tokens=starter.estimate_tokens(
                preview.when_to_use, preview.body, preview.description
            ),
        )
        db.add(skill)
        created.append(skill)
        db.add(
            AuditEvent(
                actor_id=user.id,
                action="skill.import",
                target=skill.id,
                detail=preview.name[:160],
                event_metadata={
                    "files": len(preview.kept),
                    "excluded": len(preview.excluded),
                    "warnings": sorted({f.code for f in preview.findings}),
                    "draft": draft,
                    "scripts": len(preview.scripts),
                },
                ip=client_ip(request),
                user_agent=request.headers.get("User-Agent", "")[:400],
            )
        )
    await db.commit()
    for skill in created:
        await db.refresh(skill)
    log.info("skill import by %s: %d skill(s)", safe(user.id), len(created))
    return ImportOut(dry_run=False, items=items, created=[SkillOut.of(s) for s in created])


@router.get("/skills/{skill_id}/export")
async def export_skill(skill_id: str, user: CurrentUser, db: DbSession):
    """Own skill as SKILL.md; others 404."""
    skill = await _own(db, Skill, "owner_id", user, skill_id)
    text = skill_import.to_skill_md(
        name=skill.name,
        description=skill.description,
        when_to_use=skill.when_to_use,
        kinds=list(skill.kinds or []),
        required_tools=list(skill.required_tools or []),
        version=skill.version,
        body=skill.body,
        server=skill.server_fields,
    )
    filename = quote(f"{skill.slug or 'skill'}-SKILL.md")
    return Response(
        content=text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=SKILL.md; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


class SkillReviewOut(Wire):
    skill: SkillOut
    owner_name: str = ""


class SkillRejectIn(Wire):
    note: str = Field(default="", max_length=400)


_REVIEWS = ("pending", "approved", "rejected")


@router.get("/admin/skills", response_model=list[SkillReviewOut])
async def admin_skills(admin: AdminUser, db: DbSession, review: str = "pending"):
    """Script-bearing skills awaiting share review, newest first."""
    query = select(Skill).where(col(Skill.script_files).is_not(None)).order_by(
        col(Skill.updated_at).desc())
    if review in _REVIEWS:
        query = query.where(Skill.review == review)
    rows = (await db.exec(query)).all()
    owners = {r.owner_id for r in rows}
    names = dict((await db.exec(select(User.id, User.name).where(col(User.id).in_(owners))))
                 .all()) if owners else {}
    return [SkillReviewOut(skill=SkillOut.of(r), owner_name=names.get(r.owner_id, ""))
            for r in rows]


async def _decide(db: DbSession, admin: User, skill_id: str, *, approve: bool,
                  request: Request | None, note: str = "") -> SkillReviewOut:
    row = await db.get(Skill, skill_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if row.review != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_pending")
    row.review = "approved" if approve else "rejected"
    row.visibility = Visibility.org if approve else Visibility.private
    row.review_note = note.strip()[:400]
    row.reviewed_by, row.reviewed_at = admin.id, utcnow()
    db.add(row)
    db.add(AuditEvent(
        actor_id=admin.id, action="skill.approve" if approve else "skill.reject",
        target=row.name[:200], detail=row.review_note,
        event_metadata={"skillId": row.id, "scripts": len(row.script_files or []),
                        "changes": {"review": ["pending", row.review]}},
        ip=client_ip(request) if request else "",
        user_agent=request.headers.get("User-Agent", "")[:400] if request else ""))
    await db.commit()
    await db.refresh(row)
    log.info("skill %s %s by %s", safe(row.id), row.review, safe(admin.id))
    return SkillReviewOut(skill=SkillOut.of(row))


@router.post("/admin/skills/{skill_id}/approve", response_model=SkillReviewOut)
async def approve_skill(skill_id: str, admin: AdminUser, db: DbSession,
                        request: Request = None):  # type: ignore[assignment]
    return await _decide(db, admin, skill_id, approve=True, request=request)


@router.post("/admin/skills/{skill_id}/reject", response_model=SkillReviewOut)
async def reject_skill(skill_id: str, payload: SkillRejectIn, admin: AdminUser, db: DbSession,
                       request: Request = None):  # type: ignore[assignment]
    if not payload.note.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="reason_required")
    return await _decide(db, admin, skill_id, approve=False, request=request, note=payload.note)
