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
    # Carried on every token in the family, not only the first, so 세션 목록 can
    # describe a chain from whichever row it happens to read — and so a family
    # that started before this column existed becomes describable at its next
    # rotation instead of staying blank forever.
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


#: What belongs on somebody's own 접속기록. The admin trail carries everything;
#: this screen is about access to this account, so a branding change or another
#: person's suspension is noise on it.
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
        # Same message as an invalid password: no account enumeration.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_unavailable")

    is_first = await user_count(db) == 0
    policy = await settings_store.signup_policy()
    if policy.mode == "closed" and not is_first:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="signup_closed")
    if not is_first and not policy.allows(email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="signup_domain_not_allowed"
        )

    # First account bootstraps the instance: admin, active, funded. Otherwise
    # `open` activates immediately, `approval` parks the row as pending — and
    # with verification on, everyone waits as pending until the mailed link
    # is clicked; `verify_email` below then does what the mode says.
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
        # Same starting workspace as an approved account.
        await starter.seed_designs(db, user.id)
        # The first account on an instance is its administrator, and nobody
        # else can put the shared catalogue there.
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


#: How long a mailed signup link works. Long enough for a mail that lands in
#: the morning to be clicked after lunch.
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
    """What approval does, for an account that needs none: funded, keyed, seeded."""
    user.status = UserStatus.active
    grant_initial_allowance(db, user, settings.default_monthly_credits)
    await provision_user(user)
    await starter.seed_designs(db, user.id)
    db.add(user)


@router.post("/verify-email", response_model=EmailVerifyResponse)
async def verify_email(payload: EmailVerify, request: Request, response: Response, db: DbSession):
    """The mailed link. Marks the address confirmed, then does what the signup
    mode would have done at signup: `open` activates and signs in, `approval`
    leaves the row for an administrator."""
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
    """A fresh link for the waiting screen. One a minute, and only while the
    address is still unconfirmed and mail can go out."""
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
        # Versioned, idempotent catalogue sync. Only the administrator holding
        # the shared catalogue touches the database here; for everyone else it
        # returns immediately.
        await starter.sync_catalog(db, user)

    user.last_active_at = utcnow()
    db.add(user)
    await _audit(db, request, "login", user.id, target=email)
    await db.commit()

    # A pending user gets a real session: the waiting screen needs an identity
    # to poll with. `current_user` still blocks every other route.
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
                await starter.sync_catalog(db, user)
            return await _issue_session(
                db, response, user, family_id=row.family_id, request=request
            )

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
        # An administrator can remain signed in indefinitely by rotating
        # refresh tokens and never visit `/login`. Catalogue sync belongs in
        # this same transaction as the successful rotation, so a release's new
        # entries reach the store without anybody signing in again.
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
        if prefs.get("privacy_default_action") == "send_raw_external":
            # Saving a raw-external default grants future egress authority, so
            # a process-local policy cache is not acceptable here.
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
    """Requires the current password, and ends every other session.

    A password change is what follows a suspected leak, so the sessions an
    intruder already holds have to go with it.
    """
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

    # This session survives: a new refresh cookie replaces the revoked one.
    await _issue_session(db, response, user, request=request)


#: Long enough to reach a mail client, short enough that a link left in an
#: inbox is not a standing key.
RESET_TTL_MINUTES = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/me/access", response_model=list[AccessEventOut])
async def my_access_log(user: CurrentUser, db: DbSession):
    """This account's own security events, newest first.

    Deliberately not "sessions": there is no live session list to revoke from
    here, and a screen that looked like one would imply a button that does not
    exist. This is the record of what happened — signed in, failed to sign in,
    changed a password, made a key — which is what somebody checks when they
    want to know whether anyone else has been in.

    Failed attempts included, and they are the point: a run of them from an
    address nobody recognises is the one thing on this screen worth acting on.
    """
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
    """Hash of the refresh cookie the caller is holding, if it is holding one.

    What marks "this browser" in the session list, and what 다른 기기 로그아웃
    spares. A request arriving without the cookie — an API key, an expired tab —
    simply has no current session, and every family is then somebody else's.
    """
    raw = request.cookies.get(settings.refresh_cookie_name)
    return hash_refresh_token(raw) if raw else None


@router.get("/me/sessions", response_model=list[ActiveSessionOut])
async def my_sessions(user: CurrentUser, request: Request, db: DbSession):
    """Every browser this account is currently signed in on.

    One entry per refresh-token *family*, not per token: rotation writes a new
    row every quarter of an hour, and a list that counted those would report
    ninety-six sessions for one laptop left open overnight. The family is what
    a person means by "signed in on the lab PC".

    Newest first, and the one being read from is marked — ending that one signs
    the reader out here and now, which is worth knowing before pressing it.
    """
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

    # A live family has exactly one unrevoked token — its newest. Grouping is
    # still done rather than assumed: the concurrent-refresh grace window can
    # briefly leave two, and a list that showed the same machine twice would
    # read as an intruder.
    current_digest = _current_digest(request)
    by_family: dict[str, list[RefreshToken]] = {}
    for row in rows:
        by_family.setdefault(row.family_id, []).append(row)

    out: list[ActiveSessionOut] = []
    for family_id, tokens in by_family.items():
        newest = max(tokens, key=lambda t: t.created_at)
        # `created_at` of the oldest *live* token is not when the family began —
        # its ancestors are revoked and still in the table — so the start is
        # taken from the whole chain.
        started = (
            await db.exec(
                select(RefreshToken.created_at)
                .where(RefreshToken.family_id == family_id)
                .order_by(col(RefreshToken.created_at).asc())
                .limit(1)
            )
        ).first() or newest.created_at
        # The description is whatever the chain last recorded. A family that
        # started before these columns existed fills them in at its next
        # rotation, so prefer any token in it that has one.
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
    """End one sign-in, from anywhere.

    Scoped to the caller's own rows: `family_id` arrives from a browser, so a
    family belonging to somebody else must 404 rather than revoke. Ending the
    current one is allowed and is the ordinary "sign out this browser" — the
    cookie is cleared on the way out so the tab does not keep presenting a
    token the server has already burned.
    """
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
    """Sign out everywhere except here.

    The button somebody presses after realising they left themselves signed in
    somewhere and cannot remember where. Keeping the current family is what
    makes it pressable at all — revoking everything would sign the person out
    mid-action and leave them unsure whether it had worked.
    """
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
async def auth_config():
    """What this instance is able to offer.

    A control that cannot work must not be drawn: no mail means no reset link,
    no Whisper backend means no microphone. The browser cannot read the admin
    settings to find either out.
    """
    # Display hint only. Saving/using raw external delivery re-reads the policy
    # from the database, so this short cache can at worst show a stale option
    # that the authorizing endpoint immediately rejects.
    policy = await governance_service.current()
    signup = await settings_store.signup_policy()
    return {
        "passwordResetEnabled": await settings_store.mail_enabled(),
        "dictationEnabled": await transcribe_service.available(),
        # So the signup form can say which addresses are welcome and whether a
        # mail is coming, before the person finds out from a refusal.
        "signup": {
            "mode": signup.mode,
            "domains": signup.domains,
            "emailVerification": signup.verification,
        },
        # Served before authentication: the sign-in screen has to render the
        # name and logo too.
        "brand": await settings_store.brand(),
        "enabledKinds": await settings_store.enabled_kinds(),
        "privacy": {
            "externalDataGuard": policy.external_data_guard,
            "allowUserRawExternal": (policy.allow_user_raw_external and not policy.pii_masking),
        },
        # Served unauthenticated with the rest of the instance's shape. It is a
        # duration, not a secret, and the browser has to know it before it can
        # start counting.
        "idleTimeoutMinutes": policy.idle_timeout_minutes,
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
    await _issue_session(db, response, user, request=request)


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
