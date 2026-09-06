"""Authentication routes: signup, login, refresh, sessions, password reset.

Signup lands in `pending` unless this is the first account or the signup mode
is `open`. Login issues a short access JWT plus a rotating refresh cookie.
Every `/refresh` revokes the presented token; reuse of a revoked token outside
the grace window revokes the whole family.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentIdentity, CurrentUser, DbSession, client_ip, user_count
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
    EmailVerification,
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
    AccessEventOut,
    ActiveSessionOut,
    EmailVerify,
    EmailVerifyResponse,
    LoginRequest,
    PasswordChange,
    PasswordForgot,
    PasswordReset,
    ProfilePatch,
    SessionOut,
    SessionRevokeResult,
    SignupRequest,
    SignupResponse,
    UserOut,
)
from app.services import geoip, settings_store, starter
from app.services import governance as governance_service
from app.services import mail as mail_service
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
    db: AsyncSession,
    response: Response,
    user: User,
    family_id: str | None = None,
    request: Request | None = None,
) -> SessionOut:
    raw, digest = new_refresh_token()
    # ip/user_agent are recorded on every token in the family, not only the first.
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=digest,
            family_id=family_id or new_family_id(),
            expires_at=refresh_expiry(),
            ip=client_ip(request) if request else "",
            user_agent=(request.headers.get("User-Agent", "")[:400] if request else ""),
            last_used_at=utcnow(),
        )
    )
    await db.commit()

    _set_refresh_cookie(response, raw)
    token, expires_in = create_access_token(user.id, user.role.value)
    return SessionOut(access_token=token, expires_in=expires_in, user=UserOut.of(user))


#: Audit actions shown on a user's own access log.
_ACCOUNT_ACTIONS = (
    "login",
    "signup",
    "signup.verified",
    "signup.verification_resent",
    "password.change",
    "password.reset",
    "password.reset_requested",
    "key.create",
    "key.revoke",
)


async def _audit(db: AsyncSession, request: Request, action: str, actor: str | None, **kw) -> None:
    db.add(
        AuditEvent(
            actor_id=actor,
            action=action,
            target=kw.get("target", ""),
            detail=kw.get("detail", ""),
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
            severity=kw.get("severity", "info"),
        )
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, response: Response, db: DbSession):
    email = payload.email.lower().strip()

    existing = (await db.exec(select(User).where(User.email == email))).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_unavailable")

    is_first = await user_count(db) == 0
    policy = await settings_store.signup_policy()
    if policy.mode == "closed" and not is_first:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="signup_closed")
    if not is_first and not policy.allows(email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="signup_domain_not_allowed"
        )

    # First account: admin, active. Otherwise `open` activates unless email
    # verification is on; everything else waits as pending.
    verify = policy.verification and not is_first
    if is_first:
        role, user_status = UserRole.admin, UserStatus.active
    elif policy.mode == "open" and not verify:
        role, user_status = UserRole.user, UserStatus.active
    else:
        role, user_status = UserRole.user, UserStatus.pending

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        name=payload.name.strip(),
        role=role,
        status=user_status,
        email_verified_at=None if verify else utcnow(),
    )
    db.add(user)
    try:
        # The unique index, not the SELECT above, guards against a concurrent signup.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email_unavailable"
        ) from None

    if user_status is UserStatus.active:
        grant_initial_allowance(db, user, settings.default_monthly_credits)
        await provision_user(user)
        await starter.seed_designs(db, user.id)
        # Only the administrator holds the shared catalogue.
        if user.role is UserRole.admin:
            await starter.seed_catalog(db, user.id)
        db.add(user)

    await _audit(db, request, "signup", user.id, target=email, detail=f"status={user_status}")
    if verify:
        token = _issue_verification(db, user)
    await db.commit()
    await db.refresh(user)
    if verify:
        await _mail_verification(user, token)

    session = (
        await _issue_session(db, response, user, request=request)
        if user_status is UserStatus.active
        else None
    )
    return SignupResponse(user=UserOut.of(user), session=session)


#: Lifetime of a mailed signup link.
VERIFY_TTL_MINUTES = 24 * 60


def _issue_verification(db: AsyncSession, user: User) -> str:
    """Adds a ticket row and returns the token to mail. The caller commits."""
    token = secrets.token_urlsafe(32)
    db.add(
        EmailVerification(
            user_id=user.id,
            token_hash=_hash_token(token),
            expires_at=utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES),
        )
    )
    return token


async def _mail_verification(user: User, token: str) -> None:
    config = await settings_store.smtp_config()
    link = f"{config['baseUrl'].rstrip('/')}/verify?token={token}"
    subject, body = mail_service.verification_message(
        name=user.name or user.email, link=link, minutes=VERIFY_TTL_MINUTES
    )
    try:
        await mail_service.send(to=user.email, subject=subject, body=body)
    except mail_service.MailError as exc:
        log.warning("verification mail failed: %s", exc)


async def _activate(db: AsyncSession, user: User) -> None:
    """Activates an account: allowance, proxy user, starter designs."""
    user.status = UserStatus.active
    grant_initial_allowance(db, user, settings.default_monthly_credits)
    await provision_user(user)
    await starter.seed_designs(db, user.id)
    db.add(user)


@router.post("/verify-email", response_model=EmailVerifyResponse)
async def verify_email(payload: EmailVerify, request: Request, response: Response, db: DbSession):
    """Confirms the address; `open` mode then activates and signs in."""
    row = (
        await db.exec(
            select(EmailVerification).where(
                EmailVerification.token_hash == _hash_token(payload.token)
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_verify_token")
    if row.used_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="verify_token_used")
    if row.expires_at < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="verify_token_expired")
    user = await db.get(User, row.user_id)
    if user is None or user.status is UserStatus.suspended:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_verify_token")

    row.used_at = utcnow()
    db.add(row)
    user.email_verified_at = user.email_verified_at or utcnow()
    policy = await settings_store.signup_policy()
    if user.status is UserStatus.pending and policy.mode == "open":
        await _activate(db, user)
    db.add(user)
    await _audit(db, request, "signup.verified", user.id, target=user.email)
    await db.commit()
    await db.refresh(user)

    session = (
        await _issue_session(db, response, user, request=request)
        if user.status is UserStatus.active
        else None
    )
    return EmailVerifyResponse(status=user.status, session=session)


@router.post("/verify-email/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(user: CurrentIdentity, request: Request, db: DbSession):
    """Re-mails the verification link, at most once a minute."""
    if user.email_verified_at is not None or user.status is not UserStatus.pending:
        return
    if not await settings_store.mail_enabled():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="mail_not_configured")
    latest = (
        await db.exec(
            select(EmailVerification)
            .where(EmailVerification.user_id == user.id)
            .order_by(col(EmailVerification.created_at).desc())
        )
    ).first()
    if latest and (utcnow() - latest.created_at).total_seconds() < 60:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="verify_resend_too_soon")
    token = _issue_verification(db, user)
    await _audit(db, request, "signup.verification_resent", user.id, target=user.email)
    await db.commit()
    await _mail_verification(user, token)


async def _locked_until(db: AsyncSession, email: str) -> datetime | None:
    """When a run of failed sign-ins on `email` stops refusing logins, or None if not locked.

    The last `login_max_failures` sign-in events for the address are read from the audit
    log; when every one of them failed and the newest is inside the lockout window, the
    address is locked until that window ends. A success anywhere in the run ends it.
    Attempts made while locked are recorded as `locked`, not `failed`, so hammering a
    locked address cannot keep it locked forever.
    """
    limit = max(1, settings.login_max_failures)
    rows = (
        await db.exec(
            select(AuditEvent)
            .where(
                AuditEvent.action == "login",
                AuditEvent.target == email,
                col(AuditEvent.detail).in_(("", "failed")),
            )
            .order_by(col(AuditEvent.at).desc())
            .limit(limit)
        )
    ).all()
    if len(rows) < limit or any(row.detail != "failed" for row in rows):
        return None
    newest = rows[0].at
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    until = newest + timedelta(minutes=settings.login_lockout_min)
    return until if until > utcnow() else None


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    email = payload.email.lower().strip()

    # Five failures in a row lock the address for a while, before any password work.
    if (until := await _locked_until(db, email)) is not None:
        await _audit(db, request, "login", None, target=email, detail="locked", severity="warn")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="account_locked",
            headers={"Retry-After": str(max(1, int((until - utcnow()).total_seconds())))},
        )

    user = (await db.exec(select(User).where(User.email == email))).first()

    # Verified even for a missing user so timing does not leak registration.
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
        await starter.sync_catalog(db, user)

    user.last_active_at = utcnow()
    db.add(user)
    await _audit(db, request, "login", user.id, target=email)
    await db.commit()

    # Pending users get a session too: the waiting screen polls with it.
    return await _issue_session(db, response, user, request=request)


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

    # Checked before replay so a suspension is not logged as token theft.
    if user is None or user.status is UserStatus.suspended:
        await _revoke_family(db, row.family_id, RevokeReason.suspended)
        await db.commit()
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account_suspended")

    if not row.is_usable():
        if row.revoked_reason is RevokeReason.rotated and _within_grace(row):
            # Concurrent refresh from two tabs: the loser gets its own successor.
            if user.status is UserStatus.active:
                await starter.sync_catalog(db, user)
            return await _issue_session(
                db, response, user, family_id=row.family_id, request=request
            )

        _clear_refresh_cookie(response)
        if row.revoked_reason is RevokeReason.rotated:
            # Replay outside the grace window: a copy escaped, burn the family.
            await _revoke_family(db, row.family_id, RevokeReason.reuse)
            await _audit(
                db, request, "login", row.user_id, detail="refresh_reuse", severity="alert"
            )
            await db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_reused")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_ended")

    if user.status is UserStatus.active:
        # Synced here as well as at login: a long-lived session never logs in again.
        await starter.sync_catalog(db, user)
    row.revoked_at = utcnow()
    row.revoked_reason = RevokeReason.rotated
    db.add(row)
    return await _issue_session(db, response, user, family_id=row.family_id, request=request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession):
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        digest = hash_refresh_token(raw)
        row = (await db.exec(select(RefreshToken).where(RefreshToken.token_hash == digest))).first()
        if row:
            await _revoke_family(db, row.family_id, RevokeReason.logout)
            await db.commit()
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentIdentity):
    """Reachable while pending: the waiting screen polls it."""
    return UserOut.of(user)


@router.patch("/me", response_model=UserOut)
async def update_me(payload: ProfilePatch, user: CurrentIdentity, db: DbSession):
    """Profile fields. Email is not editable."""
    patch = payload.model_dump(exclude_unset=True)
    # Preferences are merged, not replaced.
    if (prefs := patch.pop("preferences", None)) is not None:
        if prefs.get("privacy_default_action") == "send_raw_external":
            # Grants future egress authority: read the policy fresh, never from cache.
            try:
                policy = await governance_service.current_for_egress()
            except governance_service.GovernanceUnavailable:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="governance_unavailable",
                ) from None
            if policy.pii_masking or not policy.allow_user_raw_external:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="raw_external_not_allowed",
                )
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
    """Requires the current password and ends every other session."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="wrong_current_password")

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

    # This session survives with a fresh refresh cookie.
    await _issue_session(db, response, user, request=request)


#: Lifetime of a mailed password-reset link.
RESET_TTL_MINUTES = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/me/access", response_model=list[AccessEventOut])
async def my_access_log(user: CurrentUser, db: DbSession):
    """This account's own security events, newest first, failed logins included."""
    rows = (
        await db.exec(
            select(AuditEvent)
            .where(AuditEvent.actor_id == user.id, col(AuditEvent.action).in_(_ACCOUNT_ACTIONS))
            .order_by(col(AuditEvent.at).desc())
            .limit(100)
        )
    ).all()
    return [AccessEventOut.of(e) for e in rows]


def _current_digest(request: Request) -> str | None:
    """Digest of the caller's refresh cookie, or None when the request carries none."""
    raw = request.cookies.get(settings.refresh_cookie_name)
    return hash_refresh_token(raw) if raw else None


@router.get("/me/sessions", response_model=list[ActiveSessionOut])
async def my_sessions(user: CurrentUser, request: Request, db: DbSession):
    """Live sign-ins, one per refresh-token family, newest first; the caller's own is marked."""
    now = utcnow()
    rows = (
        await db.exec(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                col(RefreshToken.revoked_at).is_(None),
                col(RefreshToken.expires_at) > now,
            )
            .order_by(col(RefreshToken.created_at).desc())
        )
    ).all()

    # The grace window can briefly leave two live tokens in one family.
    current_digest = _current_digest(request)
    by_family: dict[str, list[RefreshToken]] = {}
    for row in rows:
        by_family.setdefault(row.family_id, []).append(row)

    out: list[ActiveSessionOut] = []
    for family_id, tokens in by_family.items():
        newest = max(tokens, key=lambda t: t.created_at)
        # Family start comes from the whole chain, revoked ancestors included.
        started = (
            await db.exec(
                select(RefreshToken.created_at)
                .where(RefreshToken.family_id == family_id)
                .order_by(col(RefreshToken.created_at).asc())
                .limit(1)
            )
        ).first() or newest.created_at
        # Newest token that recorded a user agent or IP.
        described = next(
            (
                t
                for t in sorted(tokens, key=lambda t: t.created_at, reverse=True)
                if t.user_agent or t.ip
            ),
            newest,
        )
        out.append(
            ActiveSessionOut(
                family_id=family_id,
                started_at=started,
                last_seen_at=newest.last_used_at or newest.created_at,
                expires_at=newest.expires_at,
                ip=described.ip,
                region=geoip.lookup(described.ip),
                user_agent=described.user_agent,
                current=any(t.token_hash == current_digest for t in tokens),
            )
        )

    out.sort(key=lambda s: s.last_seen_at, reverse=True)
    return out


@router.delete("/me/sessions/{family_id}", response_model=SessionRevokeResult)
async def end_session(
    family_id: str, user: CurrentUser, request: Request, response: Response, db: DbSession
):
    """Revokes one of the caller's own families; another user's family is a 404."""
    rows = (
        await db.exec(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id, RefreshToken.user_id == user.id
            )
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_such_session")

    await _revoke_family(db, family_id, RevokeReason.logout)
    await _audit(db, request, "session.revoke", user.id, target=family_id)
    await db.commit()
    if any(t.token_hash == _current_digest(request) for t in rows):
        _clear_refresh_cookie(response)
    return SessionRevokeResult(revoked=1)


@router.post("/me/sessions/revoke-others", response_model=SessionRevokeResult)
async def end_other_sessions(user: CurrentUser, request: Request, db: DbSession):
    """Revokes every family except the caller's current one."""
    now = utcnow()
    current_digest = _current_digest(request)
    keep = None
    if current_digest:
        row = (
            await db.exec(select(RefreshToken).where(RefreshToken.token_hash == current_digest))
        ).first()
        if row and row.user_id == user.id:
            keep = row.family_id

    live = (
        await db.exec(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                col(RefreshToken.revoked_at).is_(None),
                col(RefreshToken.expires_at) > now,
            )
        )
    ).all()
    families = {r.family_id for r in live} - ({keep} if keep else set())
    for family_id in families:
        await _revoke_family(db, family_id, RevokeReason.logout)
    if families:
        await _audit(db, request, "session.revoke", user.id, detail=f"others={len(families)}")
    await db.commit()
    return SessionRevokeResult(revoked=len(families))


@router.get("/config")
async def auth_config(db: DbSession):
    """Unauthenticated instance capabilities for the sign-in and signup screens."""
    # Display hint only; the authorizing endpoints re-read the policy uncached.
    policy = await governance_service.current()
    signup = await settings_store.signup_policy()
    return {
        "passwordResetEnabled": await settings_store.mail_enabled(),
        "dictationEnabled": await transcribe_service.available(),
        "signup": {
            "mode": signup.mode,
            "domains": signup.domains,
            "emailVerification": signup.verification,
        },
        "brand": await settings_store.brand(),
        "contactEmail": await settings_store.contact_email(db),
        "enabledKinds": await settings_store.enabled_kinds(),
        "privacy": {
            "externalDataGuard": policy.external_data_guard,
            "allowUserRawExternal": (policy.allow_user_raw_external and not policy.pii_masking),
        },
        "idleTimeoutMinutes": policy.idle_timeout_minutes,
    }


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(payload: PasswordForgot, request: Request, db: DbSession):
    """Mails a reset link. Always 204 so the response cannot reveal whether an account exists."""
    email = payload.email.strip().lower()
    user = (await db.exec(select(User).where(User.email == email))).first()

    if user is None or user.status is not UserStatus.active:
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
    """Sets a new password from a mailed ticket and ends every session."""
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

    # Spend every outstanding ticket, not just this one.
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

    # Signed in on the spot: holding the address was just proved.
    await _issue_session(db, response, user, request=request)


def _within_grace(row: RefreshToken) -> bool:
    """True when the token was rotated within `refresh_grace_sec` (a concurrent refresh)."""
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
