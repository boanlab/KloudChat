"""Request dependencies: who is calling, and are they allowed."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.models.user import User, UserRole, UserStatus, utcnow
from app.services.credits import refill_if_due

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
    """Live user for the access token; blocks suspension only (row status, not claims)."""
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

    # due credit cycle refilled on first request
    refill_if_due(db, user)

    user.last_active_at = utcnow()
    db.add(user)
    await db.commit()
    return user


CurrentIdentity = Annotated[User, Depends(current_identity)]


async def current_viewer(request: Request, db: DbSession) -> User:
    """`current_user` plus `?t=` token, for media routes only (query token hits proxy logs)."""
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
    """Approved, non-pending account."""
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
    """First `X-Forwarded-For` hop (behind reverse proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
