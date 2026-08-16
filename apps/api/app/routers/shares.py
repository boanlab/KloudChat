"""Read-only share links.

Two routes that need a session, and one that must not have one.

The public route is deliberately narrow: a token in, exactly the shared thing
out, and no way to reach anything else — no owner name, no project, no sibling
artifacts, no walkable ids.

`workspace` scope requires a session, from any member of the instance. `link`
scope does not, which is the case where the recipient has no account here.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession, current_user
from app.models.chat import ChatSession, Message
from app.models.user import utcnow
from app.models.workspace import Artifact, Share, ShareScope
from app.schemas.chat import MessageOut
from app.schemas.workspace import ShareIn, ShareOut

router = APIRouter(tags=["shares"])

#: 32 bytes of urlsafe randomness. The token is the entire authorisation.
_TOKEN_BYTES = 32


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

    if share.scope is ShareScope.workspace:
        # Resolved by hand: the route is anonymous for `link` scope, which a
        # required dependency would 401.
        try:
            await current_user(await _identity(request, db))
        except HTTPException:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="sign_in_required"
            ) from None

    share.views += 1
    db.add(share)

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
    artifact = (
        await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    )
    if artifact is not None and artifact.user_id != session.user_id:
        artifact = None
    await db.commit()
    return {
        "kind": "session",
        "title": session.title,
        "sessionKind": session.kind.value,
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
