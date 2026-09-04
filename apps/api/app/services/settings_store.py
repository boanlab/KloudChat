"""Admin-editable settings: database, then environment, then code default.

Cached in-process for `_TTL` seconds and invalidated on write; other replicas
see a change within the TTL.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings as env_settings
from app.core.db import SessionLocal
from app.models.settings import SystemSetting
from app.models.user import User, UserRole, UserStatus, utcnow

log = logging.getLogger(__name__)

LITELLM_BASE_URL = "litellm.base_url"
LITELLM_MASTER_KEY = "litellm.master_key"

# ── feature integration ─────────────────────────────────────────────────
# One gateway exposes every feature by path; a per-feature URL overrides it.
BACKEND_BASE_URL = "backend.base_url"
TOOLS_SEARCH_URL = "tools.search_url"
TOOLS_FETCH_URL = "tools.fetch_url"
TOOLS_EXEC_URL = "tools.exec_url"
TOOLS_RESEARCH_URL = "tools.research_url"
TOOLS_STT_URL = "tools.stt_url"
TOOLS_INDEX_URL = "tools.index_url"

#: Paths appended to the backend address to derive each feature's address.
_TOOL_PATHS: dict[str, str] = {
    "litellm": "/litellm",
    "search": "/tools/search",
    "fetch": "/tools/fetch",
    "exec": "/tools/exec",
    "research": "/tools/research",
    "stt": "/tools/stt",
    "index": "/tools/index",
}

# ── branding ───────────────────────────────────────────────────────────
#: Service name shown in the UI. Empty falls back to the code default.
BRAND_NAME = "brand.name"
#: Logo filename and MIME type. The file itself lives in the file store.
BRAND_LOGO = "brand.logo"
BRAND_LOGO_MIME = "brand.logo_mime"
#: Where 관리자에게 문의 goes. Empty falls back to the oldest administrator.
CONTACT_EMAIL = "contact.email"

# ── enabled surfaces ───────────────────────────────────────────────────
#: CSV of the enabled surfaces. Chat is never listed: it is always on.
ENABLED_KINDS = "kinds.enabled"

#: Everything except chat. Image and video default to off.
OPTIONAL_KINDS = ("report", "slides", "image", "av")
DEFAULT_ENABLED_KINDS = ("report", "slides")

# ── outgoing mail ──────────────────────────────────────────────────────
SMTP_HOST = "smtp.host"
SMTP_PORT = "smtp.port"
#: "starttls" | "ssl" | "none".
SMTP_SECURITY = "smtp.security"
SMTP_USERNAME = "smtp.username"
SMTP_PASSWORD = "smtp.password"
SMTP_FROM = "smtp.from"
#: Where mailed links point. Not derived from the request Host, which is attacker-controlled.
APP_BASE_URL = "app.base_url"

#: Who may sign up: mode (overrides the `SIGNUP_MODE` env), CSV of allowed
#: mail domains (empty = any), and whether a mailed link must be clicked.
SIGNUP_MODE = "signup.mode"
SIGNUP_DOMAINS = "signup.domains"
SIGNUP_VERIFY_EMAIL = "signup.verify_email"

#: Keys holding secrets: encrypted at rest, never returned by the API — only
#: whether one is set, and its last four characters.
SECRET_KEYS = {LITELLM_MASTER_KEY, SMTP_PASSWORD}

_TTL = 15.0
_cache: dict[str, Any] = {"at": 0.0, "values": None}


def _fernet() -> Fernet:
    """Fernet key derived from `JWT_SECRET`; rotating it makes stored secrets unreadable."""
    digest = hashlib.sha256(env_settings.jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    """Encrypts a secret at rest; also used for per-user LiteLLM keys."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Almost always a rotated JWT_SECRET; empty reads as "not configured".
        log.warning("stored secret could not be decrypted — JWT_SECRET may have changed")
        return ""


async def _load(db: AsyncSession) -> dict[str, str]:
    rows = (await db.exec(select(SystemSetting))).all()
    out: dict[str, str] = {}
    for row in rows:
        out[row.key] = decrypt_secret(row.value) if row.secret and row.value else row.value
    return out


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
    """`(base_url, master_key)`: explicit setting, then environment, then derived
    from the backend address."""
    values = await all_values()
    base = (
        (values.get(LITELLM_BASE_URL) or "").strip()
        # Environment before derivation: the derived address is a guess off the
        # public domain and must not outrank a configured internal one.
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
    """Tool addresses in effect. An empty string means the tool is not offered."""

    search: str = ""
    fetch: str = ""
    exec: str = ""
    research: str = ""
    stt: str = ""
    #: Vector retrieval for agent knowledge; empty falls back to the lexical index.
    index: str = ""

    def get(self, name: str) -> str:
        return getattr(self, name, "")


def derive_url(base: str, feature: str) -> str:
    """Backend address plus the feature path. Empty if the backend is unset."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    return base + _TOOL_PATHS[feature]


async def tools_config() -> ToolBackends:
    """Per-feature addresses: explicit setting, then derived from the backend
    address, then the environment."""
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
    )


async def brand() -> dict[str, str]:
    """Name and logo URL for the UI. The logo is empty when none is set."""
    values = await all_values()
    logo = values.get(BRAND_LOGO, "")
    return {
        "name": (values.get(BRAND_NAME) or "").strip() or env_settings.brand_name,
        # Filename in the query string, so replacing the logo changes the URL.
        "logo": f"/api/branding/logo?v={logo}" if logo else "",
    }


async def contact_email(db: AsyncSession) -> str:
    """Contact address: the stored one, else the oldest active administrator's."""
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
    """The enabled surfaces. Chat is always included."""
    values = await all_values()
    raw = values.get(ENABLED_KINDS)
    if raw is None:
        chosen = list(DEFAULT_ENABLED_KINDS)
    else:
        # An empty string means all off, and must not fall back to the defaults.
        chosen = [k.strip() for k in raw.split(",") if k.strip() in OPTIONAL_KINDS]
    return ["chat", *chosen]


async def smtp_config() -> dict[str, str]:
    """Mail settings in effect, as plain strings. An empty `host` means mail is off."""
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
    #: "database" | "environment".
    mode_source: str
    domains: list[str]
    #: What the administrator asked for.
    verify_email: bool
    #: What actually happens: verification needs mail to be enabled.
    verification: bool

    def allows(self, email: str) -> bool:
        if not self.domains:
            return True
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        return domain in self.domains


async def signup_policy() -> SignupPolicy:
    """How signup behaves: mode, allowed domains, and whether mail is checked."""
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
    # The writing process must not serve the old value for another TTL.
    _cache["at"] = 0.0


def preview(value: str) -> str:
    """Last four characters of a secret, for display."""
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"
