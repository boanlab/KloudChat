from __future__ import annotations

from pydantic import Field

from app.models.user import UserRole
from app.schemas.auth import Wire


class SystemSettingsIn(Wire):
    """Omitted = leave as is. Empty string = clear and fall back to the environment."""

    base_url: str | None = None
    master_key: str | None = None

    #: Service name shown in the UI. An empty string reverts to the default.
    brand_name: str | None = None
    #: CSV of the surfaces to enable (report,slides,image,av). Chat is always on.
    enabled_kinds: str | None = None

    # ── feature integration ────────────────────────────────────────────
    #: Backend gateway address. Per-feature addresses left blank are derived
    #: from this by appending a path.
    backend_base_url: str | None = None
    tools_search_url: str | None = None
    tools_fetch_url: str | None = None
    tools_exec_url: str | None = None
    tools_research_url: str | None = None
    tools_stt_url: str | None = None
    tools_index_url: str | None = None

    # ── outgoing mail ──────────────────────────────────────────────────
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_security: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    app_base_url: str | None = None

    #: Where 관리자에게 문의 goes. Empty falls back to the first administrator.
    contact_email: str | None = None

    # ── who may sign up ────────────────────────────────────────────────
    #: `open` / `approval` / `closed`; empty falls back to `SIGNUP_MODE`.
    signup_mode: str | None = None
    #: Mail domains allowed to register, comma-separated; empty allows any.
    signup_domains: str | None = None
    #: `on` asks new accounts to click a mailed link first (needs SMTP).
    signup_verify_email: str | None = None


class SmtpTestRequest(Wire):
    """Recipient of the probe message; defaults to the administrator's own address."""

    to: str | None = None


class SetRoleRequest(Wire):
    role: UserRole


class SetCreditsRequest(Wire):
    monthly_credits: int = Field(ge=0, le=100_000_000)


class ApproveRequest(Wire):
    """Approval and credit assignment in one action."""

    monthly_credits: int | None = Field(default=None, ge=0, le=100_000_000)


class AllowedModelsRequest(Wire):
    """Empty list = the whole catalogue (the default)."""

    models: list[str] = Field(default_factory=list, max_length=200)


class GovernanceIn(Wire):
    """Instance policy. Every field optional so a screen can send one switch."""

    pii_masking: bool | None = None
    external_data_guard: bool | None = None
    allow_user_raw_external: bool | None = None
    privacy_safe_model_ids: list[str] | None = Field(default=None, max_length=20)
    adaptive_routing_enabled: bool | None = None
    adaptive_classifier_model_id: str | None = Field(default=None, max_length=200)
    adaptive_economy_model_ids: list[str] | None = Field(default=None, max_length=3)
    adaptive_quality_enabled: bool | None = None
    adaptive_quality_model_ids: list[str] | None = Field(default=None, max_length=3)
    #: Empty string clears it, like the classifier above.
    outline_model_id: str | None = Field(default=None, max_length=200)
    intent_filter: bool | None = None
    blocked_categories: list[str] | None = None
    #: 0 keeps everything.
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    #: 0 is off. Capped at a day; beyond that the refresh cookie lifetime is shorter.
    idle_timeout_minutes: int | None = Field(default=None, ge=0, le=1440)
