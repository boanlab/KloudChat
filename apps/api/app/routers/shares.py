"""Read-only share links.

Two routes that need a session, and one that must not have one.

The public route is deliberately narrow: a token in, exactly the shared thing
out, and no way to reach anything else — no owner name, no sibling artifacts,
no walkable ids.

The one thing it sends beyond the transcript is the account the conversation
gives of itself: which agent answered, which project it was held in, which
서식 the result was written into. A reader who cannot see that cannot tell why
the document came out as it did, and has nobody to ask. Names only — an
agent's system prompt and a project's instructions are the work itself, and
this token bought one conversation rather than the workspace behind it.

`workspace` scope requires a session, from any member of the instance. `link`
scope does not, which is the case where the recipient has no account here.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession, client_ip, current_user
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import Agent, Artifact, Project, Share, ShareScope, ShareView
from app.schemas.chat import MessageOut
from app.schemas.workspace import ShareIn, ShareOut, ShareViewOut
from app.services import design_templates

router = APIRouter(tags=["shares"])

#: 32 bytes of urlsafe randomness. The token is the entire authorisation.
_TOKEN_BYTES = 32

#: Repeat opens inside this window are the same visit. A reader refreshing a
#: long report is one person reading it, and a row per refresh would bury the
#: other readers under them.
_VISIT_WINDOW = timedelta(hours=1)


async def _record_view(db: DbSession, share: Share, request: Request, viewer) -> None:
    """Writes down who this was, or the little that can be known about them.

    A signed-in reader is named — the name and email are copied rather than
    joined, so the record still reads a year from now whether or not the
    account does. An anonymous one is a `link`-scope recipient, who has no
    account here by design; their address is all this server ever learns.
    """
    ip = client_ip(request)
    since = utcnow() - _VISIT_WINDOW
    # Same person, still here: one visit. Identity is the account when there is
    # one and the address when there is not — an anonymous reader is only ever
    # their address, so that is what "same reader" can mean for them.
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
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
        return None, row
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nothing_to_share")


@router.post("/shares", response_model=ShareOut, status_code=status.HTTP_201_CREATED)
async def create_share(payload: ShareIn, user: CurrentUser, db: DbSession):
    """Makes a link, or returns the one that already exists.

    Idempotent per (thing, scope): a second Share returns the same URL rather
    than minting one that outlives the first revocation.
    """
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
    """Who has opened this link. Owner only, newest visit first.

    A revoked link keeps its visits: revoking answers "can anyone still read
    this", and the question that prompted it is usually "who already did".
    """
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
    """Kills the link. The row stays so the owner can still see it existed."""
    share = await db.get(Share, share_id)
    if share is None or share.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    share.revoked_at = utcnow()
    db.add(share)
    await db.commit()


@router.get("/shared/{token}")
async def read_shared(token: str, request: Request, db: DbSession):
    """The public read. No session for `link` scope; a session for `workspace`.

    Returns the shared thing and nothing around it. Revoked and unknown tokens
    give the same 404: distinguishing them discloses somebody else's account.
    """
    share = (await db.exec(select(Share).where(Share.token == token))).first()
    if share is None or share.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    # Resolved by hand and never required: the route has to stay open for
    # `link` scope, which a dependency would 401. Attempted for every scope
    # though, because a `link` reader who happens to have an account can be
    # named, and a name is worth more in the log than an address.
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
    # The artifact the conversation produced, which is the reason to open the
    # link at all. Read through the session's own `artifact_id`, never by
    # listing: nothing else in the owner's workspace is reachable from a token.
    artifact = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if artifact is not None and artifact.user_id != session.user_id:
        artifact = None
    # What this conversation started with, which the empty screen in the app
    # has told its owner ever since a 시작점 stopped being typed into the
    # composer. The recipient of the link is the one reader who was told none
    # of it, and the one who cannot ask.
    #
    # Each of these is a name and nothing else. `agent.system_prompt` and
    # `project.instructions` are the bodies behind two of them, and neither
    # goes near this route.
    agent = await db.get(Agent, session.agent_id) if session.agent_id else None
    project = await db.get(Project, session.project_id) if session.project_id else None
    if project is not None and project.user_id != session.user_id:
        project = None
    shape = design_templates.get(session.render_template_id)
    await db.commit()
    return {
        "kind": "session",
        "title": session.title,
        "sessionKind": session.kind.value,
        "startedWith": {
            "agent": agent.name if agent is not None else None,
            "project": f"{project.emoji} {project.name}".strip() if project is not None else None,
            # Both halves of the name, because the page that renders it picks
            # by the language on screen exactly as every other 서식 name does.
            "format": (
                {"name": shape.name, "nameEn": shape.name_en} if shape is not None else None
            ),
        },
        "messages": [MessageOut.of(m) for m in messages],
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
