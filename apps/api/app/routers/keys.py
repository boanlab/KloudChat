"""API keys a user holds themselves.

The secret leaves the server once, at creation; no route returns it again.
Spend, budget and the model allowlist follow the key on the proxy.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession, client_ip
from app.models.user import ApiKey, AuditEvent, utcnow
from app.schemas.workspace import ApiKeyCreate, ApiKeyOut
from app.services import litellm as litellm_service
from app.services import settings_store

router = APIRouter(prefix="/keys", tags=["keys"])

_MAX_KEYS = 10


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
            .order_by(col(ApiKey.created_at).desc())
        )
    ).all()
    return [ApiKeyOut.of(k) for k in rows]


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED)
async def create_key(payload: ApiKeyCreate, request: Request, user: CurrentUser, db: DbSession):
    """Mints a key and returns it once. `secret` is absent from every later read."""
    live = (
        await db.exec(
            select(ApiKey).where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
        )
    ).all()
    if len(live) >= _MAX_KEYS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="too_many_keys")

    await litellm_service.ensure_key(user)
    issued = await litellm_service.issue_named_key(user, payload.name)
    if issued is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="litellm_unavailable"
        )
    secret, _alias = issued

    row = ApiKey(
        user_id=user.id,
        name=payload.name.strip()[:80],
        secret=settings_store.encrypt_secret(secret),
        preview=settings_store.preview(secret),
    )
    db.add(row)
    db.add(
        AuditEvent(
            actor_id=user.id,
            action="key.create",
            target=user.email,
            detail=row.name,
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
    await db.refresh(row)

    out = ApiKeyOut.of(row)
    # The only response that carries the secret.
    out.secret = secret
    return out


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, request: Request, user: CurrentUser, db: DbSession):
    row = await db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")

    await litellm_service.delete_key(settings_store.decrypt_secret(row.secret))
    # Kept as a revoked row for the audit trail.
    row.revoked_at = utcnow()
    db.add(row)
    db.add(
        AuditEvent(
            actor_id=user.id,
            action="key.revoke",
            target=user.email,
            detail=row.name,
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
