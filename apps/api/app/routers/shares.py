"""Read-only share links; public read returns names only, no owner or walkable ids."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession, client_ip, current_user
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import Agent, Artifact, Project, Share, ShareScope, ShareView
from app.schemas.workspace import ShareIn, ShareOut, ShareViewOut
from app.services import design_templates, message_links

router = APIRouter(tags=["shares"])

#: token = entire authorisation
_TOKEN_BYTES = 32

#: repeat opens within window = one visit
_VISIT_WINDOW = timedelta(hours=1)


async def _record_view(db: DbSession, share: Share, request: Request, viewer) -> None:
    """View record: signed-in by name/email, anonymous by address."""
    ip = client_ip(request)
    since = utcnow() - _VISIT_WINDOW
    same = (
        (ShareView.viewer_id == viewer.id)
        if viewer is not None
        else (col(ShareView.viewer_id).is_(None), ShareView.ip == ip)
    )
    conditions = (same,) if not isinstance(same, tuple) else same
    open_visit = (
        await db.exec(
            select(ShareView)
            .where(ShareView.share_id == share.id, ShareView.last_at >= since, *conditions)
            .order_by(col(ShareView.last_at).desc())
        )
    ).first()
    if open_visit is not None:
        open_visit.last_at = utcnow()
        open_visit.opens += 1
        db.add(open_visit)
        return

    db.add(
        ShareView(
            share_id=share.id,
            viewer_id=viewer.id if viewer is not None else None,
            viewer_name=(getattr(viewer, "name", "") or "") if viewer is not None else "",
            viewer_email=(getattr(viewer, "email", "") or "") if viewer is not None else "",
            ip=ip,
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )


async def _owned_target(db: DbSession, user, payload: ShareIn):
    if payload.artifact_id:
        row = await db.get(Artifact, payload.artifact_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return row, None
    if payload.session_id:
        row = await db.get(ChatSession, payload.session_id)
        # published bot's conversation not shareable by owner
        if row is None or row.user_id != user.id or row.publication_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return None, row
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nothing_to_share")


@router.post("/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
async def create_share(payload: ShareIn, user: CurrentUser, db: DbSession):
    """New link, or the live one for same (thing, scope)."""
    await _owned_target(db, user, payload)

    existing = (
        await db.exec(
            select(Share).where(
                Share.owner_id == user.id,
                Share.scope == payload.scope,
                col(Share.revoked_at).is_(None),
                Share.artifact_id == payload.artifact_id,
                Share.session_id == payload.session_id,
            )
        )
    ).first()
    if existing is not None:
        return ShareOut.of(existing)

    share = Share(
        token=secrets.token_urlsafe(_TOKEN_BYTES),
        owner_id=user.id,
        artifact_id=payload.artifact_id,
        session_id=payload.session_id,
        scope=payload.scope,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)
    return ShareOut.of(share)


@router.get("/shares", response_model=list[ShareOut])
async def list_shares(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Share)
            .where(Share.owner_id == user.id, col(Share.revoked_at).is_(None))
            .order_by(col(Share.created_at).desc())
        )
    ).all()
    return [ShareOut.of(s) for s in rows]


@router.get("/shares/{share_id}/views", response_model=list[ShareViewOut])
async def list_share_views(share_id: str, user: CurrentUser, db: DbSession):
    """Link visits; owner only, newest first."""
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    rows = (
        await db.exec(
            select(ShareView)
            .where(ShareView.share_id == share_id)
            .order_by(col(ShareView.last_at).desc())
            .limit(200)
        )
    ).all()
    return [ShareViewOut.of(v) for v in rows]


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(share_id: str, user: CurrentUser, db: DbSession):
    """Revoke; row kept."""
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    share.revoked_at = utcnow()
    db.add(share)
    await db.commit()


@router.get("/shared/{token}")
async def read_shared(token: str, request: Request, db: DbSession):
    """Public read; revoked and unknown tokens both 404."""
    share = (await db.exec(select(Share).where(Share.token == token))).first()
    if share is None or share.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    # optional auth by hand: a dependency would 401 `link` readers
    try:
        viewer = await current_user(await _identity(request, db))
    except HTTPException:
        viewer = None

    if share.scope is ShareScope.workspace and viewer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign_in_required")

    share.views += 1
    db.add(share)
    await _record_view(db, share, request, viewer)

    if share.artifact_id:
        artifact = await db.get(Artifact, share.artifact_id)
        if artifact is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        await db.commit()
        return {
            "kind": "artifact",
            "title": artifact.title,
            "artifactKind": artifact.kind.value,
            "data": artifact.data,
            "updatedAt": artifact.updated_at,
        }

    session = await db.get(ChatSession, share.session_id) if share.session_id else None
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    messages = (
        await db.exec(
            select(Message)
            .where(Message.session_id == session.id)
            .order_by(col(Message.created_at), col(Message.id))
        )
    ).all()
    # via session's own artifact_id only, never listing
    artifact = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if artifact is not None and artifact.user_id != session.user_id:
        artifact = None
    # names only: system_prompt/instructions never leave
    agent = await db.get(Agent, session.agent_id) if session.agent_id else None
    project = await db.get(Project, session.project_id) if session.project_id else None
    if project is not None and project.user_id != session.user_id:
        project = None
    shape = design_templates.get(session.render_template_id)
    links = await message_links.run_links(db, session.id)
    await db.commit()
    return {
        "kind": "session",
        "title": session.title,
        "sessionKind": session.kind.value,
        "startedWith": {
            "agent": agent.name if agent is not None else None,
            "project": f"{project.emoji} {project.name}".strip() if project is not None else None,
            # both languages; page picks by UI language
            "format": (
                {"name": shape.name, "nameEn": shape.name_en} if shape is not None else None
            ),
        },
        "messages": [message_links.linked(m, links) for m in messages],
        "artifact": (
            {
                "title": artifact.title,
                "artifactKind": artifact.kind.value,
                "data": artifact.data,
                "updatedAt": artifact.updated_at,
            }
            if artifact is not None
            else None
        ),
        "updatedAt": session.updated_at,
    }


async def _identity(request: Request, db: DbSession):
    from app.core.deps import current_identity

    return await current_identity(request, db)
