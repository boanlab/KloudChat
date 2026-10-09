"""Skills in and out as files: `POST /skills/import` reads SKILL.md, .zip or .skill
uploads (a dry run shows what would be made), `GET /skills/{id}/export` writes one back.

Imported skills are rows like any other personal skill. Their files are never stored and
their scripts never run; see `services/skill_import.py`.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import Field

from app.core.deps import CurrentUser, DbSession, client_ip
from app.core.logs import safe
from app.models.user import AuditEvent
from app.models.workspace import Skill, SkillSource, Visibility
from app.routers.workspace import _own, _slug
from app.schemas.auth import Wire
from app.schemas.workspace import SkillOut
from app.services import skill_import, starter
from app.services.tools import registry as tool_registry

log = logging.getLogger(__name__)

router = APIRouter(tags=["workspace"])

#: Import calls (dry runs included) per account per window.
RATE_LIMIT = 30
RATE_WINDOW_SEC = 600
_calls: dict[str, deque[float]] = defaultdict(deque)
#: Multipart overhead allowed on top of the files themselves.
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
        )


class ImportOut(Wire):
    dry_run: bool
    items: list[ImportPreviewOut]
    #: Rows written; empty on a dry run.
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
    """Reads each upload into a skill. With `dryRun` nothing is written; otherwise every
    file must pass (no block-level finding) and each becomes one personal skill."""
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
        # One byte past the cap is enough to know it is too big.
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
    """The caller's own skill as SKILL.md; anyone else's is not found."""
    skill = await _own(db, Skill, "owner_id", user, skill_id)
    text = skill_import.to_skill_md(
        name=skill.name,
        description=skill.description,
        when_to_use=skill.when_to_use,
        kinds=list(skill.kinds or []),
        required_tools=list(skill.required_tools or []),
        version=skill.version,
        body=skill.body,
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
