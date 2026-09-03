"""Admin surface: approve signups, assign allowances, suspend accounts.

Approval is where the LiteLLM user is provisioned. Doing it at signup would
create a proxy user for every registration that is never approved.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlmodel import col, delete, select

from app.core.config import settings
from app.core.deps import AdminUser, DbSession, client_ip
from app.models.chat import ChatSession, Message
from app.models.user import (
    ApiKey,
    AuditEvent,
    CreditLedger,
    EmailVerification,
    PasswordReset,
    RefreshToken,
    RevokeReason,
    User,
    UserRole,
    UserStatus,
    utcnow,
)
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactVersion,
    Connector,
    ConnectorCredential,
    ConnectorTool,
    Job,
    Memory,
    Project,
    Skill,
    StoredFile,
)
from app.schemas.admin import (
    AllowedModelsRequest,
    ApproveRequest,
    SetCreditsRequest,
    SetRoleRequest,
    SmtpTestRequest,
    SystemSettingsIn,
)
from app.schemas.auth import UserOut
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import mail as mail_service
from app.services import models as model_service
from app.services import settings_store, starter
from app.services.credits import grant_initial_allowance, set_allowance
from app.services.litellm import provision_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

#: (feature key, UI label, settings_store key). Shared by the admin screen and
#: the connection probe.
_TOOL_FEATURES: list[tuple[str, str, str]] = [
    ("search", "웹 검색", settings_store.TOOLS_SEARCH_URL),
    ("fetch", "문서 가져오기", settings_store.TOOLS_FETCH_URL),
    ("exec", "코드 실행", settings_store.TOOLS_EXEC_URL),
    ("research", "심층 조사", settings_store.TOOLS_RESEARCH_URL),
    ("stt", "음성 전사", settings_store.TOOLS_STT_URL),
    ("index", "자료 검색", settings_store.TOOLS_INDEX_URL),
]

#: Per-feature probe path. Each service exposes its health check differently.
_TOOL_PROBES: dict[str, str] = {
    "search": "/healthz",
    "fetch": "/health",
    "exec": "/health",
    "research": "/mcp",
    "stt": "/health",
    "index": "/health",
}


async def _load(db: DbSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    return user


def _audit(db, request: Request, admin: User, action: str, target: str, detail: str = "") -> None:
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action=action,
            target=target,
            detail=detail,
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )


# ── system settings ────────────────────────────────────────────────────


@router.get("/settings")
async def get_settings(admin: AdminUser):
    """Current proxy configuration and the provenance of each value.

    The master key is never returned: only whether one is set, plus its last
    four characters.
    """
    values = await settings_store.all_values(force=True)
    smtp = await settings_store.smtp_config()
    signup = await settings_store.signup_policy()
    backends = await settings_store.tools_config()
    stored_base = values.get(settings_store.LITELLM_BASE_URL, "")
    stored_key = values.get(settings_store.LITELLM_MASTER_KEY, "")
    base, key = await settings_store.litellm_config()
    return {
        "litellm": {
            "baseUrl": base,
            # Provenance: why an unfilled field has a value, and that clearing
            # it falls back rather than breaks.
            "baseUrlSource": (
                "database"
                if stored_base
                else "backend"
                if values.get(settings_store.BACKEND_BASE_URL)
                else "environment"
            ),
            "masterKeySet": bool(key),
            "masterKeyPreview": settings_store.preview(key),
            "masterKeySource": "database" if stored_key else "environment",
        },
        # Mail is optional and off by default; an empty form and a disabled
        # feature look identical without this.
        "smtp": {
            "host": smtp.get("host", ""),
            "port": smtp.get("port", ""),
            "security": smtp.get("security", "") or "starttls",
            "username": smtp.get("username", ""),
            "from": smtp.get("sender", ""),
            "appBaseUrl": smtp.get("baseUrl", ""),
            "passwordSet": bool(smtp.get("password")),
            "passwordPreview": settings_store.preview(smtp.get("password", "")),
            "hostSource": "database" if values.get(settings_store.SMTP_HOST) else "environment",
            # What the sign-in page keys its reset link off, surfaced so a
            # half-filled form shows its consequence.
            "passwordResetEnabled": await settings_store.mail_enabled(),
        },
        "status": "ok" if await litellm_service.health(quick=True) else "unavailable",
        "brand": await settings_store.brand(),
        # Who may register, from where, and whether the address is checked.
        "signup": {
            "mode": signup.mode,
            "modeSource": signup.mode_source,
            "domains": signup.domains,
            "verifyEmail": signup.verify_email,
            # Asked for but not happening: no mail server to send the link.
            "verificationActive": signup.verification,
        },
        "enabledKinds": await settings_store.enabled_kinds(),
        # Feature integration: what points where, and where each value came from.
        "tools": {
            "backendBaseUrl": values.get(settings_store.BACKEND_BASE_URL, ""),
            "features": [
                {
                    "key": feature,
                    "label": label,
                    "url": backends.get(feature),
                    "source": (
                        "database"
                        if values.get(setting_key)
                        else "backend"
                        if values.get(settings_store.BACKEND_BASE_URL)
                        else "environment"
                    ),
                }
                for feature, label, setting_key in _TOOL_FEATURES
            ],
        },
        # Exchange rate and headroom, served rather than duplicated client-side
        # so the quoted figure cannot drift from the applied one.
        "credits": {
            "perUsd": settings.credits_per_usd,
            "budgetHeadroom": settings.litellm_budget_headroom,
        },
        # Models the proxy serves but nobody could price: withheld rather than
        # billed at a guess. The only place that reports it.
        "unpricedModels": [
            {"id": model_id, "provider": provider}
            for model_id, provider in sorted(model_service.unpriced().items())
        ],
    }


@router.put("/settings")
async def put_settings(
    payload: SystemSettingsIn, request: Request, admin: AdminUser, db: DbSession
):
    """Writes what was supplied and leaves the rest alone.

    Omitted keeps the stored value; an empty string clears it and falls back to
    the environment. Without the distinction, an override cannot be undone.
    """
    changed: list[str] = []
    if payload.base_url is not None:
        await settings_store.put(
            db, settings_store.LITELLM_BASE_URL, payload.base_url.strip(), admin.id
        )
        changed.append("baseUrl")
    if payload.master_key is not None:
        await settings_store.put(
            db, settings_store.LITELLM_MASTER_KEY, payload.master_key.strip(), admin.id
        )
        changed.append("masterKey")

    for field, key in (
        ("brand_name", settings_store.BRAND_NAME),
        ("enabled_kinds", settings_store.ENABLED_KINDS),
        ("backend_base_url", settings_store.BACKEND_BASE_URL),
        ("tools_search_url", settings_store.TOOLS_SEARCH_URL),
        ("tools_fetch_url", settings_store.TOOLS_FETCH_URL),
        ("tools_exec_url", settings_store.TOOLS_EXEC_URL),
        ("tools_research_url", settings_store.TOOLS_RESEARCH_URL),
        ("tools_stt_url", settings_store.TOOLS_STT_URL),
        ("tools_index_url", settings_store.TOOLS_INDEX_URL),
        ("smtp_host", settings_store.SMTP_HOST),
        ("smtp_port", settings_store.SMTP_PORT),
        ("smtp_security", settings_store.SMTP_SECURITY),
        ("smtp_username", settings_store.SMTP_USERNAME),
        ("smtp_password", settings_store.SMTP_PASSWORD),
        ("smtp_from", settings_store.SMTP_FROM),
        ("app_base_url", settings_store.APP_BASE_URL),
        ("signup_mode", settings_store.SIGNUP_MODE),
        ("signup_domains", settings_store.SIGNUP_DOMAINS),
        ("signup_verify_email", settings_store.SIGNUP_VERIFY_EMAIL),
    ):
        value = getattr(payload, field)
        if value is not None:
            await settings_store.put(db, key, value.strip(), admin.id)
            changed.append(field)

    _audit(db, request, admin, "settings.update", "litellm", ",".join(changed))
    await db.commit()

    settings_store.invalidate()
    # Catalogue was fetched with the old credentials.
    model_service.invalidate_cache()
    return await get_settings(admin)


@router.post("/settings/test")
async def test_settings(admin: AdminUser):
    """Probes the proxy with the settings currently in effect."""
    if not await litellm_service.health(quick=True):
        return {"ok": False, "detail": "연결하지 못했습니다. 주소와 마스터 키를 확인하세요."}
    try:
        entries = await litellm_service.model_info()
    except litellm_service.LiteLLMError as exc:
        # The reason goes to the log rather than the response: it carries the
        # upstream URL and whatever the client library appended to it.
        log.warning("model listing failed during settings test: %s", exc)
        return {
            "ok": False,
            "detail": "연결은 되지만 모델 목록을 읽지 못했습니다. 서버 로그를 확인하세요.",
        }
    return {"ok": True, "models": len(entries)}


@router.post("/settings/test-tool/{feature}")
async def test_tool(feature: str, admin: AdminUser):
    """Sends a real request to one feature's address to see whether it answers.

    A 4xx counts as reachable: an MCP endpoint answers GET with 405, and that
    means the server is alive. What has to be distinguished here is "wrong
    address" from "service is not running", not HTTP semantics.
    """
    if feature not in _TOOL_PROBES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_feature")

    backends = await settings_store.tools_config()
    base = backends.get(feature)
    if not base:
        return {"ok": False, "detail": "주소를 먼저 입력하세요."}

    url = base.rstrip("/") + _TOOL_PROBES[feature]
    try:
        async with httpx.AsyncClient(timeout=settings.litellm_probe_timeout_sec) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return {"ok": False, "detail": "연결하지 못했습니다. 주소를 확인하세요."}

    if response.status_code < 500:
        return {"ok": True, "detail": f"연결됨 (http {response.status_code})"}
    if response.status_code in (502, 503):
        return {"ok": False, "detail": "서버에는 연결되지만 이 기능이 아직 실행되지 않았습니다."}
    return {"ok": False, "detail": f"http {response.status_code}"}


@router.post("/settings/smtp-test")
async def test_smtp(payload: SmtpTestRequest, admin: AdminUser):
    """Sends one real message with the settings in effect.

    A real send, not a connection check: authentication, TLS mode and the
    envelope sender each fail at a different point, and only delivery exercises
    all three. The default recipient is the administrator making the request —
    a mail server test that mails somebody else is how test mail reaches users.
    """
    # Sending needs the server and a sender. The service address only goes
    # *into* mails as a link, so a missing one is reported after the send,
    # not used to refuse it — the way it was, a filled-in relay reported
    # 모두 채워야 with nothing said about which one.
    config = await settings_store.smtp_config()
    missing = [
        label
        for key, label in (("host", "SMTP 서버"), ("sender", "보내는 주소"))
        if not config.get(key)
    ]
    if missing:
        return {"ok": False, "detail": f"{', '.join(missing)}를 채운 뒤 저장해야 보낼 수 있습니다."}
    recipient = (payload.to or "").strip() or admin.email
    try:
        await mail_service.send(
            to=recipient,
            subject="KloudChat 메일 설정 테스트",
            body=(
                "이 메일이 도착했다면 KloudChat 의 메일 설정이 올바릅니다.\n"
                "비밀번호 재설정 링크도 같은 경로로 발송됩니다.\n"
            ),
        )
    except mail_service.MailError as exc:
        return {"ok": False, "detail": str(exc)}
    if not config.get("baseUrl"):
        return {
            "ok": True,
            "detail": (
                f"{recipient} 로 테스트 메일을 보냈습니다. 서비스 주소가 비어 있어 "
                "재설정·확인 링크는 아직 만들 수 없습니다."
            ),
        }
    return {"ok": True, "detail": f"{recipient} 로 테스트 메일을 보냈습니다."}


@router.get("/users", response_model=list[UserOut])
async def list_users(admin: AdminUser, db: DbSession):
    # Pending first — the approval queue is why this screen gets opened.
    users = (await db.exec(select(User).order_by(User.created_at.desc()))).all()
    rank = {UserStatus.pending: 0, UserStatus.active: 1, UserStatus.suspended: 2}
    users.sort(key=lambda u: rank.get(u.status, 3))
    return [UserOut.of(u) for u in users]


@router.post("/users/{user_id}/approve", response_model=UserOut)
async def approve(
    user_id: str, payload: ApproveRequest, request: Request, admin: AdminUser, db: DbSession
):
    user = await _load(db, user_id)
    if user.status is UserStatus.active:
        return UserOut.of(user)

    allowance = (
        payload.monthly_credits
        if payload.monthly_credits is not None
        else (user.monthly_credits or settings.default_monthly_credits)
    )
    user.status = UserStatus.active
    # Approving is vouching: the link the person never clicked no longer matters.
    user.email_verified_at = user.email_verified_at or utcnow()
    grant_initial_allowance(db, user, allowance)
    await provision_user(user)
    # A look to start from, and nothing else. The agents and skills a new
    # account used to be handed are one shared catalogue now, owned by the
    # administrator and taken from the store when they are wanted — see
    # services/starter.py.
    await starter.seed_designs(db, user.id)
    db.add(user)

    _audit(db, request, admin, "user.approve", user.email, f"credits={allowance}")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/suspend", response_model=UserOut)
async def suspend(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    user = await _load(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_suspend_self")

    user.status = UserStatus.suspended
    # The key never leaves this process, so blocking it is not what stops them —
    # revoking their sessions is. It keeps the proxy's view of who is active in
    # step with KloudChat's, which is the whole point of per-user keys.
    await litellm_service.set_key_blocked(user, True)
    db.add(user)

    # Ends live sessions, not just new logins: the access token stays
    # signature-valid for its remaining minutes, but refresh must stop.
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
        token.revoked_reason = RevokeReason.suspended
        db.add(token)

    _audit(db, request, admin, "user.suspend", user.email, f"sessions_revoked={len(tokens)}")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/reject", response_model=UserOut)
async def reject(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Turns down a pending signup.

    Lands in `suspended` rather than deleting: the decision stays in the audit
    trail, is one click to undo, and the email is not freed for a re-signup.
    """
    user = await _load(db, user_id)
    if user.status is not UserStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_pending")

    user.status = UserStatus.suspended
    # Usually no key to revoke — unless signup ran in `open` mode and the
    # account was turned down afterwards.
    await litellm_service.revoke_key(user)
    db.add(user)
    _audit(db, request, admin, "user.reject", user.email)
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/reinstate", response_model=UserOut)
async def reinstate(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Undo of suspend. Lands in `active`, not `pending`: the account was
    already vetted once.
    """
    user = await _load(db, user_id)
    if user.status is not UserStatus.suspended:
        return UserOut.of(user)

    user.status = UserStatus.active
    await litellm_service.set_key_blocked(user, False)
    if user.cycle_resets_at is None:
        grant_initial_allowance(db, user, user.monthly_credits or settings.default_monthly_credits)
    await litellm_service.sync_budget(user)
    db.add(user)
    _audit(db, request, admin, "user.reinstate", user.email)
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/role", response_model=UserOut)
async def set_role(
    user_id: str, payload: SetRoleRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Grants or revokes admin.

    Without it, one lost password locks the instance out of approvals and
    credit assignment permanently.
    """
    user = await _load(db, user_id)
    if user.id == admin.id and payload.role is not UserRole.admin:
        # Self-demotion can leave an instance with no administrator.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_demote_self")

    user.role = payload.role
    db.add(user)
    _audit(db, request, admin, "user.role", user.email, f"role={payload.role.value}")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/litellm-key", response_model=UserOut)
async def rotate_litellm_key(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Issues a new virtual key and revokes the old one.

    For an account created while the proxy was unreachable, or a key believed
    leaked. The old key is deleted first, leaving no usable orphan.
    """
    user = await _load(db, user_id)
    # Full timeout, not the screen's quick probe. That one exists so a bad host
    # cannot hold a settings *read* open; refusing a rotation the administrator
    # just asked for because a liveness check took four seconds is a refusal the
    # proxy did not make.
    if not await litellm_service.health():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="litellm_unavailable"
        )

    # Unconditional and first: branching on whether a user record exists would
    # leave the old key live on the proxy.
    await litellm_service.revoke_key(user)
    await litellm_service.ensure_user(user)
    await litellm_service.issue_key(user)

    if not user.litellm_key:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="key_issuance_failed")
    if user.status is UserStatus.suspended:
        # A rotation must not re-enable a suspended account.
        await litellm_service.set_key_blocked(user, True)

    db.add(user)
    _audit(db, request, admin, "user.litellm_key", user.email, "rotated")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    request: Request,
    admin: AdminUser,
    db: DbSession,
    purge_files: bool = Query(True, alias="purgeFiles"),
):
    """Removes an account and everything it owns. Not recoverable.

    Suspension is the reversible answer; this is for accounts that should never
    have existed. The proxy key goes first — a row deleted while its key is
    live leaves a credential nobody tracks.

    `purgeFiles` takes the account's directory on disk with it — uploads,
    pictures, clips. On by default: the rows that named those files are gone
    in the same transaction, and a directory nobody can reach is what the
    storage sweep exists to clean up after. Off keeps the bytes for a
    retention hold; the sweep reclaims them once the volume is full.
    """
    user = await _load(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_delete_self")
    if user.role is UserRole.admin:
        remaining = (
            await db.exec(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.admin, User.id != user.id)
            )
        ).one()
        if not remaining:
            # An instance with no administrator can never approve, fund or
            # configure anything again.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="last_admin")

    await litellm_service.revoke_key(user)
    email = user.email

    # Sessions last: messages and jobs both reference them.
    owned_sessions = (
        await db.exec(select(ChatSession).where(ChatSession.user_id == user.id))
    ).all()
    session_ids = [s.id for s in owned_sessions]
    if session_ids:
        await db.exec(delete(Message).where(col(Message.session_id).in_(session_ids)))
        await db.exec(delete(Job).where(col(Job.session_id).in_(session_ids)))
    await db.exec(delete(Job).where(Job.user_id == user.id))
    if session_ids:
        await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(session_ids)))

    connector_ids = [
        c.id for c in (await db.exec(select(Connector).where(Connector.owner_id == user.id))).all()
    ]
    if connector_ids:
        await db.exec(
            delete(ConnectorTool).where(col(ConnectorTool.connector_id).in_(connector_ids))
        )
        await db.exec(
            delete(ConnectorCredential).where(
                col(ConnectorCredential.connector_id).in_(connector_ids)
            )
        )
        await db.exec(delete(Connector).where(col(Connector.id).in_(connector_ids)))

    artifact_ids = [
        a.id for a in (await db.exec(select(Artifact).where(Artifact.user_id == user.id))).all()
    ]
    if artifact_ids:
        await db.exec(
            delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(artifact_ids))
        )
        await db.exec(delete(Artifact).where(col(Artifact.id).in_(artifact_ids)))

    for model, column in (
        (Project, Project.user_id),
        (StoredFile, StoredFile.user_id),
        (Skill, Skill.owner_id),
        (Memory, Memory.user_id),
        (Agent, Agent.owner_id),
        (CreditLedger, CreditLedger.user_id),
        (RefreshToken, RefreshToken.user_id),
        # Outstanding reset tickets hold the account row open by foreign key.
        (PasswordReset, PasswordReset.user_id),
        (EmailVerification, EmailVerification.user_id),
    ):
        await db.exec(delete(model).where(column == user.id))

    await db.delete(user)
    # Outlives the account: "who deleted whom" is what the trail is for.
    _audit(db, request, admin, "user.delete", email if not purge_files else f"{email} (파일 포함)")
    await db.commit()
    # After the commit, never before: a directory removed for an account whose
    # rows then failed to delete would be files with owners and no bytes.
    if purge_files:
        removed = file_service.remove_user_files(user_id)
        if removed:
            log.info("user.delete: removed %d bytes of files for %s", removed, email)


@router.post("/users/{user_id}/models", response_model=UserOut)
async def set_allowed_models(
    user_id: str, payload: AllowedModelsRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Restricts an account to a list of models. Empty means the whole catalogue.

    Pushed to every key the account holds, user-carried ones included: enforced
    app-side only, the allowlist is a suggestion.
    """
    user = await _load(db, user_id)
    user.allowed_models = list(payload.models)
    db.add(user)

    secrets = [
        settings_store.decrypt_secret(k.secret)
        for k in (
            await db.exec(
                select(ApiKey).where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
            )
        ).all()
    ]
    await litellm_service.sync_allowed_models(user, secrets)

    detail = f"{len(payload.models)}개" if payload.models else "전체"
    _audit(db, request, admin, "user.models", user.email, detail)
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/credits", response_model=UserOut)
async def set_credits(
    user_id: str, payload: SetCreditsRequest, request: Request, admin: AdminUser, db: DbSession
):
    user = await _load(db, user_id)
    set_allowance(db, user, payload.monthly_credits)
    # KloudChat owns the limit and the proxy mirrors it; unsynced, the backstop
    # keeps enforcing the old number.
    await litellm_service.sync_budget(user)
    _audit(db, request, admin, "credits.set", user.email, f"monthly={payload.monthly_credits}")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)
