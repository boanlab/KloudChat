"""Admin routes: signup approval, allowances, suspension, settings; LiteLLM user at approval."""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import case, func, or_
from sqlmodel import col, delete, select

from app.core.config import settings
from app.core.deps import AdminUser, DbSession, client_ip
from app.core.security import hash_password
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
    AdminKeysOut,
    AllowedModelsRequest,
    ApproveRequest,
    KloudChatKeyOut,
    ReplaceKeyRequest,
    ResetPasswordRequest,
    SetCreditsRequest,
    SetRoleRequest,
    SmtpTestRequest,
    SystemSettingsIn,
    UpdateUserRequest,
    UserPage,
    UserSummary,
    UserTabCounts,
)
from app.schemas.auth import UserOut
from app.schemas.workspace import ApiKeyOut
from app.services import audit as audit_service
from app.services import connector_defaults, ocr, settings_store, starter, tools_gateway
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import mail as mail_service
from app.services import models as model_service
from app.services.credits import grant_initial_allowance, set_allowance
from app.services.litellm import provision_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_TOOL_FEATURES: list[tuple[str, str, str]] = [
    ("search", "웹 검색", settings_store.TOOLS_SEARCH_URL),
    ("fetch", "문서 가져오기", settings_store.TOOLS_FETCH_URL),
    ("exec", "코드 실행", settings_store.TOOLS_EXEC_URL),
    ("research", "심층 조사", settings_store.TOOLS_RESEARCH_URL),
    ("stt", "음성 전사", settings_store.TOOLS_STT_URL),
    ("index", "자료 검색", settings_store.TOOLS_INDEX_URL),
    ("ocr", "글자 읽기(OCR)", settings_store.TOOLS_OCR_URL),
]

#: per-feature health probe path
_TOOL_PROBES: dict[str, str] = {
    "search": "/healthz",
    "fetch": "/health",
    "exec": "/health",
    "research": "/mcp",
    "stt": "/health",
    "index": "/health",
    "ocr": "/health",
}


async def _load(db: DbSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    return user


def _audit(
    db,
    request: Request,
    admin: User,
    action: str,
    target: str,
    detail: str = "",
    *,
    changes: dict[str, list] | None = None,
    metadata: dict | None = None,
) -> None:
    """One admin audit event; `changes` = `{field: [old, new]}`."""
    extra = dict(metadata or {})
    if changes:
        extra["changes"] = changes
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action=action,
            target=target,
            detail=detail,
            event_metadata=extra or None,
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )


#: settings section per audited field (audit target)
_SETTING_SECTION: dict[str, str] = {
    "baseUrl": "proxy",
    "masterKey": "proxy",
    "brand_name": "branding",
    "contact_email": "branding",
    "enabled_kinds": "features",
    "backend_base_url": "tools",
    "tools_search_url": "tools",
    "tools_fetch_url": "tools",
    "tools_exec_url": "tools",
    "tools_research_url": "tools",
    "tools_stt_url": "tools",
    "tools_index_url": "tools",
    "tools_ocr_url": "tools",
    "ocr_enabled": "ocr",
    "ocr_mode": "ocr",
    "ocr_vision_model": "ocr",
    "ocr_max_pages": "ocr",
    "model_chat": "models",
    "model_report": "models",
    "model_slides": "models",
    "model_image": "models",
    "model_audio": "models",
    "model_video": "models",
    "model_title": "models",
    "model_code": "models",
    "model_code_fallback": "models",
    "thinking_self_hosted": "models",
    "thinking_external": "models",
    "default_monthly_credits": "signup",
    "smtp_host": "mail",
    "smtp_port": "mail",
    "smtp_security": "mail",
    "smtp_username": "mail",
    "smtp_password": "mail",
    "smtp_from": "mail",
    "app_base_url": "mail",
    "signup_mode": "signup",
    "signup_domains": "signup",
    "signup_verify_email": "signup",
}


async def _ocr_settings(values: dict[str, str]) -> dict:
    """OCR section: stored, in force, allowed page models."""
    in_force = await ocr.config()
    catalogue = await model_service.list_models()
    return {
        "enabled": (values.get(settings_store.OCR_ENABLED) or "1") != "0",
        "mode": values.get(settings_store.OCR_MODE) or "both",
        "visionModel": values.get(settings_store.OCR_VISION_MODEL) or "",
        "maxPages": in_force.max_pages,
        # narrowed to reachable readers; null if none
        "modeInForce": in_force.mode,
        "visionModelInForce": in_force.vision_model,
        "engineConfigured": bool(in_force.engine_url),
        "visionChoices": [
            {"id": model["id"], "label": model.get("label") or model["id"]}
            for model in ocr.vision_choices(catalogue)
        ],
    }


async def _check_ocr(payload: SystemSettingsIn) -> None:
    """OCR guard: pages only to strict-local vision models."""
    if payload.ocr_enabled is not None and payload.ocr_enabled.strip() not in ("", "0", "1"):
        raise HTTPException(status_code=422, detail="ocr_enabled_invalid")
    if payload.ocr_mode is not None and payload.ocr_mode.strip() not in ("", *ocr.MODES):
        raise HTTPException(status_code=422, detail="ocr_mode_invalid")
    if payload.ocr_max_pages is not None and payload.ocr_max_pages.strip():
        raw = payload.ocr_max_pages.strip()
        if not raw.isdigit() or not 1 <= int(raw) <= ocr.MAX_PAGES_CEILING:
            raise HTTPException(status_code=422, detail="ocr_max_pages_invalid")
    if payload.ocr_vision_model is not None and payload.ocr_vision_model.strip():
        allowed = {
            model["id"]
            for model in ocr.vision_choices(await model_service.list_models_for_egress())
        }
        if payload.ocr_vision_model.strip() not in allowed:
            raise HTTPException(status_code=422, detail="ocr_vision_model_not_strict_local")


#: (stored key, choice list, "" allowed) per model-role field
_MODEL_ROLE_FIELDS: tuple[tuple[str, str, str, bool], ...] = (
    ("model_chat", settings_store.MODEL_CHAT, "chat", False),
    ("model_report", settings_store.MODEL_REPORT, "report", True),
    ("model_slides", settings_store.MODEL_SLIDES, "slides", True),
    ("model_image", settings_store.MODEL_IMAGE, "image", False),
    ("model_audio", settings_store.MODEL_AUDIO, "audio", False),
    ("model_video", settings_store.MODEL_VIDEO, "video", False),
    ("model_title", settings_store.MODEL_TITLE, "helper", True),
    ("model_code", settings_store.MODEL_CODE, "helper", True),
    ("model_code_fallback", settings_store.MODEL_CODE_FALLBACK, "helper", True),
)

#: required only while the catalogue offers one: an instance without them has nothing to pick
_REQUIRED_WHEN_OFFERED = frozenset({"model_audio", "model_video"})

#: default monthly credit cap (≈ $10,000)
_MAX_DEFAULT_CREDITS = 1_000_000_000


def _model_choices(catalogue: dict) -> dict[str, list[dict]]:
    """Allowed models per role; conversation-reading side work stays on instance-run models."""
    models = catalogue.get("models") or []
    self_hosted = {
        m["id"] for m in models
        if m.get("dataBoundary") == "self_hosted" and "chat" in (m.get("kinds") or [])
    }

    def runs_here(model: dict) -> bool:
        if model["id"] in self_hosted:
            return True
        name = model["id"].removeprefix("local/")
        return model["id"].startswith("local/") and f"strict-local/{name}" in self_hosted

    def row(model: dict) -> dict:
        return {
            "id": model["id"],
            "label": model.get("label") or model["id"],
            "strictLocal": bool(model.get("strictLocal")),
        }

    def of_kind(kind: str, modality: str | None = None) -> list[dict]:
        return [
            row(m)
            for m in models
            if kind in (m.get("kinds") or [])
            and (modality is None or m.get("modality") == modality)
        ]

    return {
        "chat": of_kind("chat"),
        "report": of_kind("report"),
        "slides": of_kind("slides"),
        "image": of_kind("image"),
        "audio": of_kind("av", "audio"),
        "video": of_kind("av", "video"),
        "helper": [row(m) for m in models if "chat" in (m.get("kinds") or []) and runs_here(m)],
    }


async def _model_role_settings() -> dict:
    """모델 section: in-force choice and options per job."""
    roles = await settings_store.model_roles()
    return {
        "chat": roles.chat,
        "report": roles.report,
        "slides": roles.slides,
        "image": roles.image,
        "audio": roles.audio,
        "video": roles.video,
        "title": roles.title,
        "code": roles.code,
        "codeFallback": roles.code_fallback,
        "selfHostedThinking": roles.self_hosted_thinking,
        "externalThinking": roles.external_thinking,
        "defaultMonthlyCredits": await settings_store.default_monthly_credits(),
        "choices": _model_choices(await model_service.list_models()),
    }


async def _check_model_roles(payload: SystemSettingsIn) -> None:
    """Model-role guard; in-force value always accepted (no fail on retired models)."""
    wanted = [
        (field, key, role, optional, getattr(payload, field).strip())
        for field, key, role, optional in _MODEL_ROLE_FIELDS
        if getattr(payload, field) is not None
    ]
    if wanted:
        stored = await settings_store.all_values(force=True)
        in_force = settings_store.model_roles_from(stored)
        choices = _model_choices(await model_service.list_models())
        current = {
            settings_store.MODEL_CHAT: in_force.chat,
            settings_store.MODEL_REPORT: in_force.report,
            settings_store.MODEL_SLIDES: in_force.slides,
            settings_store.MODEL_IMAGE: in_force.image,
            settings_store.MODEL_AUDIO: in_force.audio,
            settings_store.MODEL_VIDEO: in_force.video,
            settings_store.MODEL_TITLE: in_force.title,
            settings_store.MODEL_CODE: in_force.code,
            settings_store.MODEL_CODE_FALLBACK: in_force.code_fallback,
        }
        for field, key, role, optional, value in wanted:
            if not value:
                if not optional and (field not in _REQUIRED_WHEN_OFFERED or choices[role]):
                    raise HTTPException(status_code=422, detail=f"{field}_required")
                continue
            if value == current[key]:
                continue
            if value not in {model["id"] for model in choices[role]}:
                raise HTTPException(status_code=422, detail=f"{field}_invalid")
    for field in ("thinking_self_hosted", "thinking_external"):
        value = getattr(payload, field)
        if value is not None and value.strip() not in ("", "0", "1"):
            raise HTTPException(status_code=422, detail=f"{field}_invalid")
    if payload.default_monthly_credits is not None and payload.default_monthly_credits.strip():
        raw = payload.default_monthly_credits.strip()
        if not raw.isdigit() or int(raw) > _MAX_DEFAULT_CREDITS:
            raise HTTPException(status_code=422, detail="default_monthly_credits_invalid")


@router.get("/settings")
async def get_settings(admin: AdminUser, db: DbSession):
    """Settings with value sources; master key never returned."""
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
            "passwordResetEnabled": await settings_store.mail_enabled(),
        },
        "status": "ok" if await litellm_service.health(quick=True) else "unavailable",
        "brand": await settings_store.brand(),
        "contact": {
            "email": await settings_store.contact_email(db),
            "source": "database" if values.get(settings_store.CONTACT_EMAIL) else "admin",
        },
        "signup": {
            "mode": signup.mode,
            "modeSource": signup.mode_source,
            "domains": signup.domains,
            "verifyEmail": signup.verify_email,
            # False if verification requested but no mail server
            "verificationActive": signup.verification,
        },
        "enabledKinds": await settings_store.enabled_kinds(),
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
        "ocr": await _ocr_settings(values),
        "modelRoles": await _model_role_settings(),
        "credits": {
            "perUsd": settings.credits_per_usd,
            "budgetHeadroom": settings.litellm_budget_headroom,
        },
        # served but unpriced: withheld from users
        "unpricedModels": [
            {"id": model_id, "provider": provider}
            for model_id, provider in sorted(model_service.unpriced().items())
        ],
    }


@router.put("/settings")
async def put_settings(
    payload: SystemSettingsIn, request: Request, admin: AdminUser, db: DbSession
):
    """Supplied fields written; omitted kept, "" clears."""
    await _check_ocr(payload)
    await _check_model_roles(payload)
    before_stored = await settings_store.stored(db)
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    if payload.base_url is not None:
        await settings_store.put(
            db, settings_store.LITELLM_BASE_URL, payload.base_url.strip(), admin.id
        )
        before["baseUrl"] = before_stored.get(settings_store.LITELLM_BASE_URL, "")
        after["baseUrl"] = payload.base_url.strip()
    if payload.master_key is not None:
        await settings_store.put(
            db, settings_store.LITELLM_MASTER_KEY, payload.master_key.strip(), admin.id
        )
        before["masterKey"] = before_stored.get(settings_store.LITELLM_MASTER_KEY, "")
        after["masterKey"] = payload.master_key.strip()

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
        ("tools_ocr_url", settings_store.TOOLS_OCR_URL),
        ("ocr_enabled", settings_store.OCR_ENABLED),
        ("ocr_mode", settings_store.OCR_MODE),
        ("ocr_vision_model", settings_store.OCR_VISION_MODEL),
        ("ocr_max_pages", settings_store.OCR_MAX_PAGES),
        *((field, key) for field, key, _role, _optional in _MODEL_ROLE_FIELDS),
        ("thinking_self_hosted", settings_store.THINKING_SELF_HOSTED),
        ("thinking_external", settings_store.THINKING_EXTERNAL),
        ("default_monthly_credits", settings_store.CREDITS_DEFAULT_MONTHLY),
        ("smtp_host", settings_store.SMTP_HOST),
        ("smtp_port", settings_store.SMTP_PORT),
        ("smtp_security", settings_store.SMTP_SECURITY),
        ("smtp_username", settings_store.SMTP_USERNAME),
        ("smtp_password", settings_store.SMTP_PASSWORD),
        ("smtp_from", settings_store.SMTP_FROM),
        ("app_base_url", settings_store.APP_BASE_URL),
        ("contact_email", settings_store.CONTACT_EMAIL),
        ("signup_mode", settings_store.SIGNUP_MODE),
        ("signup_domains", settings_store.SIGNUP_DOMAINS),
        ("signup_verify_email", settings_store.SIGNUP_VERIFY_EMAIL),
    ):
        value = getattr(payload, field)
        if value is not None:
            await settings_store.put(db, key, value.strip(), admin.id)
            before[field] = before_stored.get(key, "")
            after[field] = value.strip()

    # moved fields only; screens save whole sections
    changes = audit_service.diff(before, after)
    if changes:
        sections = sorted({_SETTING_SECTION.get(f, "other") for f in changes})
        _audit(
            db,
            request,
            admin,
            "settings.update",
            "시스템 설정",
            ",".join(changes),
            changes=changes,
            metadata={"sections": sections},
        )
    await db.commit()

    settings_store.invalidate()
    model_service.invalidate_cache()
    return await get_settings(admin, db)


@router.post("/settings/test")
async def test_settings(admin: AdminUser):
    """Proxy probe with settings in effect."""
    if not await litellm_service.health(quick=True):
        return {"ok": False, "detail": "연결하지 못했습니다. 주소와 마스터 키를 확인하세요."}
    try:
        entries = await litellm_service.model_info()
    except litellm_service.LiteLLMError as exc:
        # logged, not returned: message carries upstream URL
        log.warning("model listing failed during settings test: %s", exc)
        return {
            "ok": False,
            "detail": "연결은 되지만 모델 목록을 읽지 못했습니다. 서버 로그를 확인하세요.",
        }
    return {"ok": True, "models": len(entries)}


@router.post("/settings/test-tool/{feature}")
async def test_tool(feature: str, admin: AdminUser):
    """Single tool backend probe."""
    # <500 = reachable (MCP GET → 405), except gateway 401 (tools key)
    if feature not in _TOOL_PROBES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_feature")

    backends = await settings_store.tools_config()
    base = backends.get(feature)
    if not base:
        return {"ok": False, "detail": "주소를 먼저 입력하세요."}

    url = base.rstrip("/") + _TOOL_PROBES[feature]
    try:
        async with tools_gateway.client(timeout=settings.litellm_probe_timeout_sec) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        return {"ok": False, "detail": "연결하지 못했습니다. 주소를 확인하세요."}

    if response.status_code == 401 and await tools_gateway.is_tools_url(url):
        detail = (
            "게이트웨이가 도구 키를 거부했습니다. 양쪽 TOOLS_GATEWAY_KEY가 같은지 확인하세요."
            if settings.tools_gateway_key
            else "게이트웨이가 도구 키를 요구합니다. API의 TOOLS_GATEWAY_KEY를 설정하세요."
        )
        return {"ok": False, "detail": detail}
    if response.status_code < 500:
        return {"ok": True, "detail": f"연결됨 (http {response.status_code})"}
    if response.status_code in (502, 503):
        return {"ok": False, "detail": "서버에는 연결되지만 이 기능이 아직 실행되지 않았습니다."}
    return {"ok": False, "detail": f"http {response.status_code}"}


@router.post("/settings/smtp-test")
async def test_smtp(payload: SmtpTestRequest, admin: AdminUser):
    """Real test mail via stored SMTP; default recipient = requesting admin."""
    # missing service address reported after send, not a refusal
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
        return {"ok": False, "detail": exc.detail}
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
    users = (await db.exec(select(User).order_by(User.created_at.desc()))).all()
    rank = {UserStatus.pending: 0, UserStatus.active: 1, UserStatus.suspended: 2}
    users.sort(key=lambda u: rank.get(u.status, 3))
    return [UserOut.of(u) for u in users]


UserSort = Literal["name", "status", "credits", "last_active"]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _user_search(q: str):
    """Name or address containing `q`, case-insensitively; None when there is nothing to find."""
    needle = q.strip()[:200]
    if not needle:
        return None
    pattern = f"%{_escape_like(needle)}%"
    return or_(
        col(User.name).ilike(pattern, escape="\\"),
        col(User.email).ilike(pattern, escape="\\"),
    )


def _user_order(sort: UserSort, order: Literal["asc", "desc"]) -> list:
    """The sort column, then fixed tie-breakers ending at the id, so a page never shifts."""
    desc = order == "desc"
    name = func.lower(col(User.name))
    if sort == "name":
        key = [name.desc() if desc else name.asc(), func.lower(col(User.email))]
    elif sort == "status":
        rank = case(
            (col(User.status) == UserStatus.pending, 0),
            (col(User.status) == UserStatus.active, 1),
            else_=2,
        )
        # within a status: newest sign-up first
        key = [rank.desc() if desc else rank.asc(), col(User.created_at).desc(), name]
    elif sort == "credits":
        used = col(User.credits_used)
        key = [used.desc() if desc else used.asc(), name]
    else:
        seen = col(User.last_active_at)
        # never-active accounts last either way
        key = [
            case((seen.is_(None), 1), else_=0),
            seen.desc() if desc else seen.asc(),
            name,
        ]
    return [*key, col(User.id)]


@router.get("/users/page", response_model=UserPage)
async def list_users_page(
    admin: AdminUser,
    db: DbSession,
    tab: Literal["all", "pending", "active", "suspended"] = Query("all", alias="status"),
    q: str = Query("", max_length=200),
    sort: UserSort = "status",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """The 사용자 list a page at a time: search, then tab, then order, then the page."""
    search = _user_search(q)
    searched = [search] if search is not None else []

    per_status = (
        await db.exec(
            select(User.status, func.count()).where(*searched).group_by(col(User.status))
        )
    ).all()
    counts = UserTabCounts()
    for state, n in per_status:
        key = state.value if isinstance(state, UserStatus) else str(state)
        if key in ("pending", "active", "suspended"):
            setattr(counts, key, n)
        counts.all += n

    where = [*searched]
    if tab != "all":
        where.append(col(User.status) == UserStatus(tab))
    total = counts.all if tab == "all" else getattr(counts, tab)
    rows = (
        await db.exec(
            select(User)
            .where(*where)
            .order_by(*_user_order(sort, order))
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()

    whole = (
        await db.exec(
            select(
                func.count(),
                func.coalesce(func.sum(col(User.monthly_credits)), 0),
                func.coalesce(func.sum(col(User.credits_used)), 0),
                func.min(col(User.cycle_resets_at)),
            )
        )
    ).one()
    pending = (
        await db.exec(
            select(func.count()).select_from(User).where(col(User.status) == UserStatus.pending)
        )
    ).one()
    summary = UserSummary(
        users=whole[0],
        pending=pending,
        credits_granted=int(whole[1]),
        credits_used=int(whole[2]),
        next_reset=whole[3],
    )
    return UserPage(
        items=[UserOut.of(u) for u in rows],
        total=total,
        page=page,
        limit=limit,
        counts=counts,
        summary=summary,
    )


@router.post("/users/{user_id}/approve", response_model=UserOut)
async def approve(
    user_id: str, payload: ApproveRequest, request: Request, admin: AdminUser, db: DbSession
):
    user = await _load(db, user_id)
    if user.status is UserStatus.active:
        return UserOut.of(user)
    old_status, old_credits = user.status.value, user.monthly_credits

    allowance = (
        payload.monthly_credits
        if payload.monthly_credits is not None
        else (user.monthly_credits or await settings_store.default_monthly_credits())
    )
    user.status = UserStatus.active
    # approval counts as verification
    user.email_verified_at = user.email_verified_at or utcnow()
    grant_initial_allowance(db, user, allowance)
    await provision_user(user)
    await starter.seed_designs(db, user.id)
    await connector_defaults.provide(db, user)
    db.add(user)

    _audit(
        db,
        request,
        admin,
        "user.approve",
        user.email,
        f"credits={allowance}",
        changes=audit_service.diff(
            {"status": old_status, "monthly_credits": old_credits},
            {"status": user.status.value, "monthly_credits": allowance},
        ),
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/suspend", response_model=UserOut)
async def suspend(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    user = await _load(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_suspend_self")

    old_status = user.status.value
    user.status = UserStatus.suspended
    await litellm_service.set_key_blocked(user, True)
    db.add(user)

    # access tokens live until expiry; refresh stops now
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

    _audit(
        db,
        request,
        admin,
        "user.suspend",
        user.email,
        f"sessions_revoked={len(tokens)}",
        changes=audit_service.diff({"status": old_status}, {"status": user.status.value}),
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/reject", response_model=UserOut)
async def reject(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Pending signup rejection → `suspended` (email stays taken)."""
    user = await _load(db, user_id)
    if user.status is not UserStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not_pending")

    user.status = UserStatus.suspended
    # key exists only after `open`-mode signup
    await litellm_service.revoke_key(user)
    db.add(user)
    _audit(
        db, request, admin, "user.reject", user.email, changes={"status": ["pending", "suspended"]}
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/reinstate", response_model=UserOut)
async def reinstate(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Suspend undo → `active`."""
    user = await _load(db, user_id)
    if user.status is not UserStatus.suspended:
        return UserOut.of(user)

    user.status = UserStatus.active
    await litellm_service.set_key_blocked(user, False)
    if user.cycle_resets_at is None:
        allowance = user.monthly_credits or await settings_store.default_monthly_credits()
        grant_initial_allowance(db, user, allowance)
    await litellm_service.sync_budget(user)
    db.add(user)
    _audit(
        db,
        request,
        admin,
        "user.reinstate",
        user.email,
        changes={"status": ["suspended", "active"]},
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/role", response_model=UserOut)
async def set_role(
    user_id: str, payload: SetRoleRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Grants or revokes admin."""
    user = await _load(db, user_id)
    if user.id == admin.id and payload.role is not UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot_demote_self")

    old_role = user.role.value
    user.role = payload.role
    db.add(user)
    _audit(
        db,
        request,
        admin,
        "user.role",
        user.email,
        f"role={payload.role.value}",
        changes=audit_service.diff({"role": old_role}, {"role": payload.role.value}),
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str, payload: UpdateUserRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Name/address fix; in-use address refused."""
    user = await _load(db, user_id)
    changes: list[str] = []
    before = {"name": user.name, "email": user.email}
    if payload.name is not None and payload.name.strip() != user.name:
        user.name = payload.name.strip()
        changes.append("name")
    if payload.email is not None:
        email = payload.email.strip().lower()
        if email != user.email:
            taken = (await db.exec(select(User).where(User.email == email))).first()
            if taken is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_taken")
            user.email = email
            changes.append("email")
    if changes:
        db.add(user)
        _audit(
            db,
            request,
            admin,
            "user.update",
            user.email,
            ",".join(changes),
            changes=audit_service.diff(before, {"name": user.name, "email": user.email}),
        )
        await db.commit()
        await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/password", response_model=UserOut)
async def reset_password(
    user_id: str, payload: ResetPasswordRequest, request: Request, admin: AdminUser, db: DbSession
):
    """New password; all sign-ins ended."""
    user = await _load(db, user_id)
    user.password_hash = hash_password(payload.password)
    db.add(user)
    await db.exec(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    _audit(db, request, admin, "user.password_reset", user.email)
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.get("/users/{user_id}/keys", response_model=AdminKeysOut)
async def list_user_keys(user_id: str, admin: AdminUser, db: DbSession):
    """All account keys: KloudChat's (preview only) and live named keys."""
    user = await _load(db, user_id)
    rows = (
        await db.exec(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
            .order_by(col(ApiKey.created_at).desc())
        )
    ).all()
    return AdminKeysOut(
        kloudchat=KloudChatKeyOut(
            preview=user.litellm_key_preview, issued_at=user.litellm_key_issued_at
        )
        if user.litellm_key
        else None,
        named=[ApiKeyOut.of(row) for row in rows],
    )


@router.delete("/users/{user_id}/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_key(
    user_id: str, key_id: str, request: Request, admin: AdminUser, db: DbSession
):
    """User-issued key revoke: proxy delete, revoked row kept."""
    user = await _load(db, user_id)
    row = await db.get(ApiKey, key_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")
    await litellm_service.delete_key(settings_store.decrypt_secret(row.secret))
    row.revoked_at = utcnow()
    db.add(row)
    _audit(db, request, admin, "key.revoke", user.email, row.name)
    await db.commit()


@router.put("/users/{user_id}/litellm-key", response_model=UserOut)
async def replace_litellm_key(
    user_id: str, payload: ReplaceKeyRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Bind a proxy-known key as the account's key; old one revoked."""
    user = await _load(db, user_id)
    if not await litellm_service.health():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="litellm_unavailable"
        )
    key = payload.key.strip()
    if await litellm_service.key_spend(key) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key_unknown")

    await litellm_service.revoke_key(user)
    user.litellm_key = settings_store.encrypt_secret(key)
    user.litellm_key_preview = settings_store.preview(key)
    user.litellm_key_issued_at = utcnow()
    await litellm_service.sync_budget(user)
    if user.status is UserStatus.suspended:
        await litellm_service.set_key_blocked(user, True)
    db.add(user)
    _audit(db, request, admin, "user.litellm_key", user.email, "replaced")
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.get("/models")
async def catalogue(admin: AdminUser):
    """Full catalogue, not caller-narrowed."""
    rows = (await model_service.list_models())["models"]
    return [
        {"id": m["id"], "label": m.get("label") or m["id"], "kinds": list(m.get("kinds") or [])}
        for m in rows
    ]


@router.post("/users/{user_id}/litellm-key", response_model=UserOut)
async def rotate_litellm_key(user_id: str, request: Request, admin: AdminUser, db: DbSession):
    """New virtual key; old revoked first."""
    user = await _load(db, user_id)
    # full timeout, not the quick probe
    if not await litellm_service.health():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="litellm_unavailable"
        )

    # revoke first: old key never left live
    await litellm_service.revoke_key(user)
    await litellm_service.ensure_user(user)
    await litellm_service.issue_key(user)

    if not user.litellm_key:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="key_issuance_failed")
    if user.status is UserStatus.suspended:
        # rotation must not unblock a suspended account
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
    """Account and owned data removal; `purgeFiles=false` leaves files to the sweep."""
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="last_admin")

    await litellm_service.revoke_key(user)
    email = user.email

    # messages/jobs reference sessions: delete first
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
        (PasswordReset, PasswordReset.user_id),
        (EmailVerification, EmailVerification.user_id),
    ):
        await db.exec(delete(model).where(column == user.id))

    await db.delete(user)
    _audit(db, request, admin, "user.delete", email if not purge_files else f"{email} (파일 포함)")
    await db.commit()
    # after commit only: rows never outlive bytes
    if purge_files:
        removed = file_service.remove_user_files(user.id)
        if removed:
            log.info("user.delete: removed %d bytes of files for %s", removed, email)


@router.post("/users/{user_id}/models", response_model=UserOut)
async def set_allowed_models(
    user_id: str, payload: AllowedModelsRequest, request: Request, admin: AdminUser, db: DbSession
):
    """Model allowlist; empty = whole catalogue."""
    # pushed to every key for proxy enforcement
    user = await _load(db, user_id)
    old_models = list(user.allowed_models or [])
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
    _audit(
        db,
        request,
        admin,
        "user.models",
        user.email,
        detail,
        changes=audit_service.diff(
            {"allowed_models": old_models}, {"allowed_models": list(payload.models)}
        ),
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)


@router.post("/users/{user_id}/credits", response_model=UserOut)
async def set_credits(
    user_id: str, payload: SetCreditsRequest, request: Request, admin: AdminUser, db: DbSession
):
    user = await _load(db, user_id)
    old_allowance = user.monthly_credits
    set_allowance(db, user, payload.monthly_credits)
    # proxy budget mirrors allowance
    await litellm_service.sync_budget(user)
    _audit(
        db,
        request,
        admin,
        "credits.set",
        user.email,
        f"monthly={payload.monthly_credits}",
        changes=audit_service.diff(
            {"monthly_credits": old_allowance}, {"monthly_credits": payload.monthly_credits}
        ),
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.of(user)
