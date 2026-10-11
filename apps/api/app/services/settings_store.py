"""Admin settings: database, then env, then code default; per-process TTL cache."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings as env_settings
from app.core.db import SessionLocal
from app.models.settings import SystemSetting
from app.models.user import ApiKey, User, UserRole, UserStatus, utcnow
from app.models.workspace import Connector

log = logging.getLogger(__name__)

LITELLM_BASE_URL = "litellm.base_url"
LITELLM_MASTER_KEY = "litellm.master_key"

# one gateway for every feature path; per-feature URL overrides
BACKEND_BASE_URL = "backend.base_url"
TOOLS_SEARCH_URL = "tools.search_url"
TOOLS_FETCH_URL = "tools.fetch_url"
TOOLS_EXEC_URL = "tools.exec_url"
TOOLS_RESEARCH_URL = "tools.research_url"
TOOLS_STT_URL = "tools.stt_url"
TOOLS_INDEX_URL = "tools.index_url"
TOOLS_OCR_URL = "tools.ocr_url"

#: per-feature paths on the backend address
_TOOL_PATHS: dict[str, str] = {
    "litellm": "/litellm",
    "search": "/tools/search",
    "fetch": "/tools/fetch",
    "exec": "/tools/exec",
    "research": "/tools/research",
    "stt": "/tools/stt",
    "index": "/tools/index",
    "ocr": "/tools/ocr",
}

#: UI service name; empty → code default
BRAND_NAME = "brand.name"
#: logo filename and MIME; file in the file store
BRAND_LOGO = "brand.logo"
BRAND_LOGO_MIME = "brand.logo_mime"
#: 관리자에게 문의 target; empty → oldest admin
CONTACT_EMAIL = "contact.email"

#: enabled surfaces CSV; chat always on
ENABLED_KINDS = "kinds.enabled"

#: non-chat surfaces; image/video default off
OPTIONAL_KINDS = ("report", "slides", "image", "av")
DEFAULT_ENABLED_KINDS = ("report", "slides")

#: "1"/"0"; unset = on when a reader exists
OCR_ENABLED = "ocr.enabled"
#: "both" (engine, then vision for unsure pages), "engine", "vision"
OCR_MODE = "ocr.mode"
#: strict-local vision models only
OCR_VISION_MODEL = "ocr.vision_model"
#: max pages per document
OCR_MAX_PAGES = "ocr.max_pages"

# model roles: env seeds a fresh install only (migration 0068); stored "" is a choice
MODEL_CHAT = "models.chat"
MODEL_REPORT = "models.report"
MODEL_SLIDES = "models.slides"
MODEL_IMAGE = "models.image"
MODEL_AUDIO = "models.audio"
MODEL_VIDEO = "models.video"
#: titles, memories, running summary
MODEL_TITLE = "models.title"
#: `run_code` Python writer; fallback gets one more try
MODEL_CODE = "models.code"
MODEL_CODE_FALLBACK = "models.code_fallback"
#: "1" think, "0" don't
THINKING_SELF_HOSTED = "thinking.self_hosted"
THINKING_EXTERNAL = "thinking.external"
#: new-account monthly credits
CREDITS_DEFAULT_MONTHLY = "credits.default_monthly"

#: stored key → seeding `Settings` field
MODEL_ROLE_ENV: dict[str, str] = {
    MODEL_CHAT: "default_chat_model",
    MODEL_REPORT: "default_report_model",
    MODEL_SLIDES: "default_slides_model",
    MODEL_IMAGE: "default_image_model",
    MODEL_AUDIO: "default_audio_model",
    MODEL_VIDEO: "default_video_model",
    MODEL_TITLE: "title_model",
    MODEL_CODE: "code_model",
    MODEL_CODE_FALLBACK: "code_fallback_model",
}

SMTP_HOST = "smtp.host"
SMTP_PORT = "smtp.port"
#: "starttls" | "ssl" | "none"
SMTP_SECURITY = "smtp.security"
SMTP_USERNAME = "smtp.username"
SMTP_PASSWORD = "smtp.password"
SMTP_FROM = "smtp.from"
#: mailed link base; never from attacker-controlled Host
APP_BASE_URL = "app.base_url"

#: signup mode (overrides env), allowed domains CSV, link click required
SIGNUP_MODE = "signup.mode"
SIGNUP_DOMAINS = "signup.domains"
SIGNUP_VERIFY_EMAIL = "signup.verify_email"

#: encrypted at rest; API returns only set flag and last four chars
SECRET_KEYS = {LITELLM_MASTER_KEY, SMTP_PASSWORD}

_TTL = 15.0
_cache: dict[str, Any] = {"at": 0.0, "values": None}


def _derived(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


_rings: dict[tuple[str, str], tuple[MultiFernet, Fernet]] = {}


def _ring() -> tuple[MultiFernet, Fernet]:
    """`(opens, seals)`: seal with `SECRET_KEY`; open with it or the `JWT_SECRET`-derived key."""
    pair = (env_settings.secret_key, env_settings.jwt_secret)
    ring = _rings.get(pair)
    if ring is None:
        primary = _derived(pair[0] or pair[1])
        keys = [primary, _derived(pair[1])] if pair[0] else [primary]
        ring = _rings[pair] = (MultiFernet(keys), primary)
    return ring


def has_own_key() -> bool:
    """Whether secrets have their own key (not `JWT_SECRET`-derived)."""
    return bool(env_settings.secret_key)


def encrypt_secret(value: str) -> str:
    """Seal a secret at rest."""
    return _ring()[1].encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _ring()[0].decrypt(value.encode()).decode()
    except InvalidToken:
        # usually a rotated key; empty = not configured
        log.warning(
            "stored secret could not be decrypted — SECRET_KEY or JWT_SECRET may have changed"
        )
        return ""


#: Fernet token prefix (version byte 0x80)
_SEALED = "gAAAAA"


def is_sealed(value: str) -> bool:
    """Whether `value` is sealed."""
    return value.startswith(_SEALED)


def encrypt_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Connector env with every value sealed (hides which one is the credential)."""
    if env is None:
        return None
    return {key: encrypt_secret(str(value)) for key, value in env.items()}


def decrypt_env(env: dict[str, str] | None) -> dict[str, str]:
    """Connector env in the clear; pre-sealing values pass through."""
    return {
        key: decrypt_secret(str(value)) if is_sealed(str(value)) else str(value)
        for key, value in (env or {}).items()
    }


def resealed(value: str, *, plain: bool = False) -> str | None:
    """`value` sealed under `SECRET_KEY`, or None if sealed already, unopenable, or no key."""
    if not value or not has_own_key():
        return None
    opens, seals = _ring()
    if not is_sealed(value):
        return seals.encrypt(value.encode()).decode() if plain else None
    try:
        seals.decrypt(value.encode())
        return None
    except InvalidToken:
        pass
    try:
        secret = opens.decrypt(value.encode())
    except InvalidToken:
        return None
    return seals.encrypt(secret).decode()


async def rotate_secrets(db: AsyncSession) -> int:
    """Re-seal stored secrets under `SECRET_KEY` at startup; changed row count."""
    if not has_own_key():
        return 0
    changed = 0
    for setting in (await db.exec(select(SystemSetting))).all():
        if setting.secret and (new := resealed(setting.value or "")) is not None:
            setting.value = new
            db.add(setting)
            changed += 1
    for user in (await db.exec(select(User))).all():
        if (new := resealed(user.litellm_key or "")) is not None:
            user.litellm_key = new
            db.add(user)
            changed += 1
    for key in (await db.exec(select(ApiKey))).all():
        if (new := resealed(key.secret or "")) is not None:
            key.secret = new
            db.add(key)
            changed += 1
    for connector in (await db.exec(select(Connector))).all():
        env = {k: str(v) for k, v in (connector.env or {}).items()}
        sealed = {k: resealed(v, plain=True) or v for k, v in env.items()}
        if sealed != env:
            connector.env = sealed
            db.add(connector)
            changed += 1
    if changed:
        await db.commit()
        _cache["values"] = None
    return changed


async def _load(db: AsyncSession) -> dict[str, str]:
    rows = (await db.exec(select(SystemSetting))).all()
    out: dict[str, str] = {}
    for row in rows:
        out[row.key] = decrypt_secret(row.value) if row.secret and row.value else row.value
    return out


async def stored(db: AsyncSession) -> dict[str, str]:
    """Stored values on `db`, bypassing the cache."""
    return await _load(db)


async def all_values(force: bool = False) -> dict[str, str]:
    now = time.monotonic()
    if not force and _cache["values"] is not None and now - _cache["at"] < _TTL:
        return _cache["values"]
    try:
        async with SessionLocal() as db:
            values = await _load(db)
    except Exception as exc:  # noqa: BLE001 — a DB blip must not break model calls
        log.warning("system settings unreadable, falling back to environment: %s", exc)
        return _cache["values"] or {}
    _cache.update(at=now, values=values)
    return values


def invalidate() -> None:
    _cache.update(at=0.0, values=None)


async def litellm_config() -> tuple[str, str]:
    """`(base_url, master_key)`: setting, env, then derived from backend."""
    values = await all_values()
    base = (
        (values.get(LITELLM_BASE_URL) or "").strip()
        # env before derivation: derived public-domain guess must not outrank internal
        or env_settings.litellm_base_url
        or derive_url(
            (values.get(BACKEND_BASE_URL) or "").strip() or env_settings.backend_base_url,
            "litellm",
        )
    )
    key = (values.get(LITELLM_MASTER_KEY) or "").strip() or env_settings.litellm_master_key
    return base, key


@dataclass(frozen=True)
class ToolBackends:
    """Tool addresses in effect; empty = not offered."""

    search: str = ""
    fetch: str = ""
    exec: str = ""
    research: str = ""
    stt: str = ""
    #: vector retrieval; empty → lexical index
    index: str = ""
    #: CPU OCR (ocr-shim); empty → vision model only
    ocr: str = ""

    def get(self, name: str) -> str:
        return getattr(self, name, "")


def derive_url(base: str, feature: str) -> str:
    """Backend address plus feature path; empty if unset."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    return base + _TOOL_PATHS[feature]


async def tools_config() -> ToolBackends:
    """Per-feature addresses: setting, then derived, then env."""
    values = await all_values()
    base = (values.get(BACKEND_BASE_URL) or "").strip() or env_settings.backend_base_url

    def resolve(key: str, feature: str, fallback: str) -> str:
        stored = (values.get(key) or "").strip()
        return stored or derive_url(base, feature) or fallback

    return ToolBackends(
        search=resolve(TOOLS_SEARCH_URL, "search", env_settings.searxng_url),
        fetch=resolve(TOOLS_FETCH_URL, "fetch", env_settings.scraper_url),
        exec=resolve(TOOLS_EXEC_URL, "exec", env_settings.code_interpreter_url),
        research=resolve(TOOLS_RESEARCH_URL, "research", env_settings.deep_research_url),
        stt=resolve(TOOLS_STT_URL, "stt", env_settings.whisper_url),
        index=resolve(TOOLS_INDEX_URL, "index", env_settings.index_url),
        ocr=resolve(TOOLS_OCR_URL, "ocr", env_settings.ocr_url),
    )


async def brand() -> dict[str, str]:
    """UI name and logo URL (empty if none)."""
    values = await all_values()
    logo = values.get(BRAND_LOGO, "")
    return {
        "name": (values.get(BRAND_NAME) or "").strip() or env_settings.brand_name,
        # filename in query: new logo, new URL
        "logo": f"/api/branding/logo?v={logo}" if logo else "",
    }


async def contact_email(db: AsyncSession) -> str:
    """Contact address: stored, else oldest active admin's."""
    values = await all_values()
    stored = (values.get(CONTACT_EMAIL) or "").strip()
    if stored:
        return stored
    row = (
        await db.exec(
            select(User)
            .where(User.role == UserRole.admin, User.status == UserStatus.active)
            .order_by(User.created_at)
        )
    ).first()
    return row.email if row else ""


async def enabled_kinds() -> list[str]:
    """Enabled surfaces; chat always included."""
    values = await all_values()
    raw = values.get(ENABLED_KINDS)
    if raw is None:
        chosen = list(DEFAULT_ENABLED_KINDS)
    else:
        # empty = all off, no default fallback
        chosen = [k.strip() for k in raw.split(",") if k.strip() in OPTIONAL_KINDS]
    return ["chat", *chosen]


@dataclass(frozen=True)
class ModelRoles:
    """Model roles in force; see `MODEL_CHAT`."""

    chat: str
    report: str
    slides: str
    image: str
    audio: str
    video: str
    title: str
    code: str
    code_fallback: str
    self_hosted_thinking: bool
    external_thinking: bool


def model_roles_from(values: dict[str, str]) -> ModelRoles:
    """Stored choice, else env seed."""

    def pick(key: str) -> str:
        if key in values:
            return (values[key] or "").strip()
        return str(getattr(env_settings, MODEL_ROLE_ENV[key]) or "").strip()

    def flag(key: str, env_value: bool) -> bool:
        raw = values.get(key)
        return env_value if raw is None else raw.strip() == "1"

    return ModelRoles(
        chat=pick(MODEL_CHAT),
        report=pick(MODEL_REPORT),
        slides=pick(MODEL_SLIDES),
        image=pick(MODEL_IMAGE),
        audio=pick(MODEL_AUDIO),
        video=pick(MODEL_VIDEO),
        title=pick(MODEL_TITLE),
        code=pick(MODEL_CODE),
        code_fallback=pick(MODEL_CODE_FALLBACK),
        self_hosted_thinking=flag(THINKING_SELF_HOSTED, env_settings.self_hosted_chat_thinking),
        external_thinking=flag(THINKING_EXTERNAL, env_settings.external_chat_thinking),
    )


async def model_roles() -> ModelRoles:
    return model_roles_from(await all_values())


def cached_model_roles() -> ModelRoles:
    """Sync `model_roles()`: last-read values (≤ TTL old), else env."""
    return model_roles_from(_cache["values"] or {})


async def default_monthly_credits() -> int:
    raw = ((await all_values()).get(CREDITS_DEFAULT_MONTHLY) or "").strip()
    return int(raw) if raw.isdigit() else env_settings.default_monthly_credits


async def smtp_config() -> dict[str, str]:
    """Mail settings as plain strings; empty `host` = mail off."""
    values = await all_values()
    return {
        "host": values.get(SMTP_HOST, "") or env_settings.smtp_host,
        "port": values.get(SMTP_PORT, "") or str(env_settings.smtp_port or ""),
        "security": values.get(SMTP_SECURITY, "") or env_settings.smtp_security,
        "username": values.get(SMTP_USERNAME, "") or env_settings.smtp_username,
        "password": values.get(SMTP_PASSWORD, "") or env_settings.smtp_password,
        "sender": values.get(SMTP_FROM, "") or env_settings.smtp_from,
        "baseUrl": values.get(APP_BASE_URL, "") or env_settings.app_base_url,
    }


async def mail_enabled() -> bool:
    config = await smtp_config()
    return bool(config["host"] and config["sender"] and config["baseUrl"])


def parse_domains(raw: str) -> list[str]:
    """`@dankook.ac.kr, example.com` → `["dankook.ac.kr", "example.com"]`."""
    seen: list[str] = []
    for part in re.split(r"[,\s;]+", raw or ""):
        domain = part.strip().lstrip("@").lower()
        if domain and domain not in seen:
            seen.append(domain)
    return seen


@dataclass(frozen=True)
class SignupPolicy:
    mode: str
    #: "database" | "environment"
    mode_source: str
    domains: list[str]
    #: admin's request
    verify_email: bool
    #: effective: needs mail enabled
    verification: bool

    def allows(self, email: str) -> bool:
        if not self.domains:
            return True
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return domain in self.domains


async def signup_policy() -> SignupPolicy:
    """Signup behaviour: mode, domains, mail check."""
    values = await all_values()
    stored_mode = (values.get(SIGNUP_MODE) or "").strip()
    mode = (
        stored_mode if stored_mode in ("open", "approval", "closed") else env_settings.signup_mode
    )
    wants = (values.get(SIGNUP_VERIFY_EMAIL) or "").strip().lower() in ("1", "true", "on", "yes")
    return SignupPolicy(
        mode=mode,
        mode_source="database" if stored_mode else "environment",
        domains=parse_domains(values.get(SIGNUP_DOMAINS, "")),
        verify_email=wants,
        verification=wants and await mail_enabled(),
    )


async def put(db: AsyncSession, key: str, value: str, actor_id: str) -> None:
    row = await db.get(SystemSetting, key)
    is_secret = key in SECRET_KEYS
    stored = encrypt_secret(value) if is_secret and value else value
    if row is None:
        db.add(SystemSetting(key=key, value=stored, secret=is_secret, updated_by=actor_id))
    else:
        row.value = stored
        row.secret = is_secret
        row.updated_at = utcnow()
        row.updated_by = actor_id
        db.add(row)
    # writer must not serve the stale value for another TTL
    _cache["at"] = 0.0


def preview(value: str) -> str:
    """Last four chars of a secret, for display."""
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"
