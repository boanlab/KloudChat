"""KloudChat's own authentication.

Signup lands in `pending` unless this is the bootstrap account or `SIGNUP_MODE`
is `open`. Login issues a short access JWT plus a rotating refresh cookie.

Rotation rule: every `/refresh` mints a new token and revokes the one
presented. An already-revoked token means a copy escaped, so the whole family
is revoked.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentIdentity, DbSession, client_ip, user_count
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_family_id,
    new_refresh_token,
    refresh_expiry,
    verify_password,
)
from app.models.user import (
    AuditEvent,
    RefreshToken,
    RevokeReason,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from app.models.user import (
    PasswordReset as PasswordResetRow,
)
from app.schemas.auth import (
    LoginRequest,
    PasswordChange,
    PasswordForgot,
    PasswordReset,
    ProfilePatch,
    SessionOut,
    SignupRequest,
    SignupResponse,
    UserOut,
)
from app.services import mail as mail_service
from app.services import settings_store, starter
from app.services import transcribe as transcribe_service
from app.services.credits import grant_initial_allowance
from app.services.litellm import provision_user

router = APIRouter(prefix="/auth", tags=["auth"])

log = logging.getLogger(__name__)


def _set_refresh_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=f"{settings.api_prefix}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.refresh_cookie_name,
        domain=settings.cookie_domain,
        path=f"{settings.api_prefix}/auth",
    )


async def _issue_session(
    db: AsyncSession, response: Response, user: User, family_id: str | None = None
) -> SessionOut:
    raw, digest = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=digest,
            family_id=family_id or new_family_id(),
            expires_at=refresh_expiry(),
        )
    )
    await db.commit()

    _set_refresh_cookie(response, raw)
    token, expires_in = create_access_token(user.id, user.role.value)
    return SessionOut(access_token=token, expires_in=expires_in, user=UserOut.of(user))


async def _audit(db: AsyncSession, request: Request, action: str, actor: str | None, **kw) -> None:
    db.add(
        AuditEvent(
            actor_id=actor,
            action=action,
            target=kw.get("target", ""),
            detail=kw.get("detail", ""),
            ip=client_ip(request),
            severity=kw.get("severity", "info"),
        )
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, response: Response, db: DbSession):
    email = payload.email.lower().strip()

    existing = (await db.exec(select(User).where(User.email == email))).first()
    if existing:
        # Same message as an invalid password: no account enumeration.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_unavailable")

    is_first = await user_count(db) == 0
    if settings.signup_mode == "closed" and not is_first:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="signup_closed")

    # First account bootstraps the instance: admin, active, funded. Otherwise
    # `open` activates immediately, `approval` parks the row as pending.
    if is_first:
        role, user_status = UserRole.admin, UserStatus.active
    elif settings.signup_mode == "open":
        role, user_status = UserRole.user, UserStatus.active
    else:
        role, user_status = UserRole.user, UserStatus.pending

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        name=payload.name.strip(),
        role=role,
        status=user_status,
    )
    db.add(user)
    try:
        # Flush first: satisfies the ledger's FK, and surfaces a concurrent
        # signup. The SELECT above cannot see an uncommitted row, so the unique
        # index is the real guard.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email_unavailable"
        ) from None

    if user_status is UserStatus.active:
        grant_initial_allowance(db, user, settings.default_monthly_credits)
        # After the insert is safe: provisioning first orphans a LiteLLM user
        # behind every failed signup.
        await provision_user(user)
        # Same starting workspace as an approved account. The bootstrap admin
        # skips approval, so it has to be seeded here too.
        await starter.seed(db, user.id)
        db.add(user)

    await _audit(db, request, "signup", user.id, target=email, detail=f"status={user_status}")
    await db.commit()
    await db.refresh(user)

    session = (
        await _issue_session(db, response, user) if user_status is UserStatus.active else None
    )
    return SignupResponse(user=UserOut.of(user), session=session)


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    email = payload.email.lower().strip()
    user = (await db.exec(select(User).where(User.email == email))).first()

    # Hashed even for a missing user: response time must not leak registration.
    stored = user.password_hash if user else hash_password("no-such-user-placeholder")
    if not verify_password(payload.password, stored) or user is None:
        await _audit(db, request, "login", None, target=email, detail="failed", severity="warn")
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    if user.status is UserStatus.suspended:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    if user.status is UserStatus.active:
        # Versioned, idempotent catalogue sync for accounts created before a
        # shipped skill existed. It fills metadata and missing rows only; user
        # edits are never overwritten.
        await starter.sync_catalog(db, user.id)

    user.last_active_at = utcnow()
    db.add(user)
    await _audit(db, request, "login", user.id, target=email)
    await db.commit()

    # A pending user gets a real session: the waiting screen needs an identity
    # to poll with. `current_user` still blocks every other route.
    return await _issue_session(db, response, user)


@router.post("/refresh", response_model=SessionOut)
async def refresh(request: Request, response: Response, db: DbSession):
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no_refresh_token")

    digest = hash_refresh_token(raw)
    row = (await db.exec(select(RefreshToken).where(RefreshToken.token_hash == digest))).first()
    if row is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_refresh")

    user = await db.get(User, row.user_id)

    # Suspension checked before replay, so an admin ending a session is not
    # logged as a stolen token. Same for expired and logged-out chains below.
    if user is None or user.status is UserStatus.suspended:
        await _revoke_family(db, row.family_id, RevokeReason.suspended)
        await db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")

    if not row.is_usable():
        if row.revoked_reason is RevokeReason.rotated and _within_grace(row):
            # Concurrent refresh: both clients hold the cookie legitimately, so
            # the loser gets its own successor rather than a burned chain.
            if user.status is UserStatus.active:
                await starter.sync_catalog(db, user.id)
            return await _issue_session(db, response, user, family_id=row.family_id)

        _clear_refresh_cookie(response)
        if row.revoked_reason is RevokeReason.rotated:
            # Exchanged too long ago for an honest client to still hold it.
            # A copy escaped, and the attacker cannot be told from the victim.
            await _revoke_family(db, row.family_id, RevokeReason.reuse)
            await _audit(
                db, request, "login", row.user_id, detail="refresh_reuse", severity="alert"
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_reused")
        # Logged out, revoked or expired — ordinary end of session.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_ended")

    if user.status is UserStatus.active:
        # A browser can remain signed in indefinitely by rotating refresh
        # tokens and never visit `/login`. Catalogue sync belongs in this same
        # transaction as the successful rotation so those accounts receive new
        # shipped skills too.
        await starter.sync_catalog(db, user.id)
    row.revoked_at = utcnow()
    row.revoked_reason = RevokeReason.rotated
    db.add(row)
    return await _issue_session(db, response, user, family_id=row.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession):
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        digest = hash_refresh_token(raw)
        row = (await db.exec(select(RefreshToken).where(RefreshToken.token_hash == digest))).first()
        if row:
            # Log out everywhere in this chain, not just this tab.
            await _revoke_family(db, row.family_id, RevokeReason.logout)
            await db.commit()
    # None keeps the injected response, and its Set-Cookie, intact.
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentIdentity):
    """Reachable while pending — the approval-waiting screen polls it."""
    return UserOut.of(user)


@router.patch("/me", response_model=UserOut)
async def update_me(payload: ProfilePatch, user: CurrentIdentity, db: DbSession):
    """Display name and avatar colour.

    Email is not editable: it is the login identity and the key an admin
    approved, so changing it belongs behind a verification flow.
    """
    patch = payload.model_dump(exclude_unset=True)
    # Merged, not replaced: a whole-dict write erases every switch the client
    # did not send.
    if (prefs := patch.pop("preferences", None)) is not None:
        user.preferences = {**(user.preferences or {}), **prefs}
    for field, value in patch.items():
        setattr(user, field, value)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    user: CurrentIdentity,
    db: DbSession,
):
    """Requires the current password, and ends every other session.

    A password change is what follows a suspected leak, so the sessions an
    intruder already holds have to go with it.
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="wrong_current_password"
        )

    user.password_hash = hash_password(payload.new_password)
    db.add(user)

    tokens = (
        await db.exec(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).all()
    now = utcnow()
    for token in tokens:
        token.revoked_at = now
        token.revoked_reason = RevokeReason.logout
        db.add(token)

    await _audit(db, request, "password.change", user.id, target=user.email)
    await db.commit()

    # This session survives: a new refresh cookie replaces the revoked one.
    await _issue_session(db, response, user)


#: Long enough to reach a mail client, short enough that a link left in an
#: inbox is not a standing key.
RESET_TTL_MINUTES = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/config")
async def auth_config():
    """What this instance is able to offer.

    A control that cannot work must not be drawn: no mail means no reset link,
    no Whisper backend means no microphone. The browser cannot read the admin
    settings to find either out.
    """
    return {
        "passwordResetEnabled": await settings_store.mail_enabled(),
        "dictationEnabled": await transcribe_service.available(),
        # Served before authentication: the sign-in screen has to render the
        # name and logo too.
        "brand": await settings_store.brand(),
        "enabledKinds": await settings_store.enabled_kinds(),
    }


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(payload: PasswordForgot, request: Request, db: DbSession):
    """Mails a reset link, and says nothing about whether it did.

    Always 204: a response that varied with registration, account status or
    delivery would answer "does this person have an account here?".

    Failures are logged, not returned.
    """
    email = payload.email.strip().lower()
    user = (await db.exec(select(User).where(User.email == email))).first()

    if user is None or user.status is not UserStatus.active:
        # Same shape and timing class, no mail sent.
        log.info("password reset requested for unusable address")
        return

    if not await settings_store.mail_enabled():
        log.warning("password reset requested but mail is not configured")
        return

    token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetRow(
            user_id=user.id,
            token_hash=_hash_token(token),
            expires_at=utcnow() + timedelta(minutes=RESET_TTL_MINUTES),
            requested_ip=client_ip(request),
        )
    )
    await _audit(db, request, "password.reset_requested", user.id, target=user.email)
    await db.commit()

    config = await settings_store.smtp_config()
    link = f"{config['baseUrl'].rstrip('/')}/reset?token={token}"
    subject, body = mail_service.reset_message(
        name=user.name or user.email, link=link, minutes=RESET_TTL_MINUTES
    )
    try:
        await mail_service.send(to=user.email, subject=subject, body=body)
    except mail_service.MailError as exc:
        log.warning("password reset mail failed: %s", exc)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    payload: PasswordReset, request: Request, response: Response, db: DbSession
):
    """Sets a new password from a mailed ticket, and ends every session.

    Whoever asked could not sign in, so no session is worth keeping — and if the
    request was not theirs, the sessions ended are the intruder's.
    """
    row = (
        await db.exec(
            select(PasswordResetRow).where(
                PasswordResetRow.token_hash == _hash_token(payload.token)
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_reset_token")
    if row.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="reset_token_used")
    if row.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="reset_token_expired")

    user = await db.get(User, row.user_id)
    if user is None or user.status is not UserStatus.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_reset_token")

    user.password_hash = hash_password(payload.new_password)
    db.add(user)
    row.used_at = utcnow()
    db.add(row)

    # Every outstanding ticket for the account is spent, not just this one.
    others = (
        await db.exec(
            select(PasswordResetRow).where(
                PasswordResetRow.user_id == user.id, PasswordResetRow.used_at.is_(None)
            )
        )
    ).all()
    for other in others:
        other.used_at = utcnow()
        db.add(other)

    tokens = (
        await db.exec(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).all()
    now = utcnow()
    for token in tokens:
        token.revoked_at = now
        token.revoked_reason = RevokeReason.logout
        db.add(token)

    await _audit(db, request, "password.reset", user.id, target=user.email)
    await db.commit()

    # Signed in on the spot — holding the address has just been proved.
    await _issue_session(db, response, user)


def _within_grace(row: RefreshToken) -> bool:
    """True if this token was rotated moments ago — a concurrent refresh, not a replay."""
    if row.revoked_at is None:
        return False
    return (utcnow() - row.revoked_at).total_seconds() <= settings.refresh_grace_sec


async def _revoke_family(db: AsyncSession, family_id: str, reason: RevokeReason) -> None:
    rows = (
        await db.exec(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason
        db.add(row)
