"""API keys a user holds themselves.

Distinct from the virtual key kchat uses on their behalf: this one leaves the
server, once, at creation. Everything after that is a preview — there is no
route that returns a secret again, because a key you can re-read is a key that
leaks from wherever it is shown.

Spend, budget and the account's model allowlist all follow the key, so handing
one out does not hand out more than the person already has.
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

#: Enough rope to organise, not enough to lose track of.
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
        )
    )
    await db.commit()
    await db.refresh(row)

    out = ApiKeyOut.of(row)
    # The only time it exists outside the database.
    out.secret = secret
    return out


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, request: Request, user: CurrentUser, db: DbSession):
    row = await db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")

    await litellm_service.delete_key(settings_store.decrypt_secret(row.secret))
    # Kept as a revoked row rather than deleted: "this key existed and was
    # retired on this date" is the question an audit asks.
    row.revoked_at = utcnow()
    db.add(row)
    db.add(
        AuditEvent(
            actor_id=user.id,
            action="key.revoke",
            target=user.email,
            detail=row.name,
            ip=client_ip(request),
        )
    )
    await db.commit()
