"""Request dependencies: who is calling, and are they allowed."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.models.user import User, UserRole, UserStatus, utcnow

DbSession = Annotated[AsyncSession, Depends(get_session)]

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="unauthorized",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


async def current_identity(request: Request, db: DbSession) -> User:
    """Resolves the access token to a live user row, blocking only suspension.

    The DB lookup is what makes suspension immediate: the JWT stays
    signature-valid for its remaining minutes, so status is re-checked rather
    than trusted from the claims.

    Pending accounts pass on purpose — the waiting screen needs an identity to
    poll with. `current_user` is the gate that keeps them out of everything else.
    """
    token = _bearer(request)
    if not token:
        raise UNAUTHORIZED
    claims = decode_access_token(token)
    if not claims:
        raise UNAUTHORIZED

    user = await db.get(User, claims.get("sub"))
    if user is None:
        raise UNAUTHORIZED
    if user.status is UserStatus.suspended:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")

    user.last_active_at = utcnow()
    db.add(user)
    await db.commit()
    return user


CurrentIdentity = Annotated[User, Depends(current_identity)]


async def current_viewer(request: Request, db: DbSession) -> User:
    """Like `current_user`, but also accepts the access token as `?t=`.

    For the one route that serves raw bytes into an element: `<img>`, `<audio>`
    and `<video>` cannot carry an Authorization header, and the token lives in
    memory rather than a cookie.

    Confined to that route. The token is the ordinary 15-minute access token,
    but it lands in the proxy's access log, which a header would not — the cost
    of being usable as a `src`, and why this is not the default dependency.
    """
    token = _bearer(request) or request.query_params.get("t")
    if not token:
        raise UNAUTHORIZED
    claims = decode_access_token(token)
    if not claims:
        raise UNAUTHORIZED
    user = await db.get(User, claims.get("sub"))
    if user is None:
        raise UNAUTHORIZED
    if user.status is UserStatus.suspended:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")
    if user.status is UserStatus.pending:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_pending")
    return user


CurrentViewer = Annotated[User, Depends(current_viewer)]


async def current_user(user: CurrentIdentity) -> User:
    """An account cleared to actually do things."""
    if user.status is UserStatus.pending:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_pending")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def user_count(db: AsyncSession) -> int:
    result = await db.exec(select(func.count()).select_from(User))
    return int(result.one())


def client_ip(request: Request) -> str:
    """First hop of `X-Forwarded-For` only. KloudChat sits behind a reverse proxy,
    and audit rows want the real client.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
