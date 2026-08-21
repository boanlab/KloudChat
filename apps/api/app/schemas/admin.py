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


class SmtpTestRequest(Wire):
    """Where the probe message goes. Defaults to the administrator's own address —
    testing a mail server by mailing someone else is how test mail reaches users."""

    to: str | None = None


class SetRoleRequest(Wire):
    role: UserRole


class SetCreditsRequest(Wire):
    monthly_credits: int = Field(ge=0, le=100_000_000)


class ApproveRequest(Wire):
    """Approval and the credit assignment are one action — an active user with a
    zero allowance can log in but cannot do anything, which reads as a bug.
    """

    monthly_credits: int | None = Field(default=None, ge=0, le=100_000_000)


class AllowedModelsRequest(Wire):
    """Empty list = the whole catalogue, which is what every account starts with."""

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
    #: Empty string clears it, like the classifier above.
    outline_model_id: str | None = Field(default=None, max_length=200)
    intent_filter: bool | None = None
    blocked_categories: list[str] | None = None
    #: 0 keeps everything. Anything above it clears bodies older than that.
    retention_days: int | None = Field(default=None, ge=0, le=3650)
