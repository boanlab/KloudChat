from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from app.models.user import UserRole
from app.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, Wire
from app.schemas.workspace import ApiKeyOut


class SystemSettingsIn(Wire):
    """Omitted: unchanged; empty string: clear to env fallback."""

    base_url: str | None = None
    master_key: str | None = None

    brand_name: str | None = None
    #: CSV of surfaces (report,slides,image,av); chat always on
    enabled_kinds: str | None = None

    #: gateway; blank per-feature addresses derived from it
    backend_base_url: str | None = None
    tools_search_url: str | None = None
    tools_fetch_url: str | None = None
    tools_exec_url: str | None = None
    tools_research_url: str | None = None
    tools_stt_url: str | None = None
    tools_index_url: str | None = None
    tools_ocr_url: str | None = None

    #: "1" on, "0" off
    ocr_enabled: str | None = None
    #: "both" | "engine" | "vision"
    ocr_mode: str | None = None
    #: strict-local vision model; "" = first such
    ocr_vision_model: str | None = None
    ocr_max_pages: str | None = None

    #: required; a served chat model
    model_chat: str | None = None
    #: "" follows chat default
    model_report: str | None = None
    model_slides: str | None = None
    #: "" = surface's cheapest
    model_image: str | None = None
    model_audio: str | None = None
    model_video: str | None = None
    #: self-hosted chat model, or "" for session's own
    model_title: str | None = None
    #: `run_code` writer then fallback; self-hosted; "" none
    model_code: str | None = None
    model_code_fallback: str | None = None
    #: "1" think, "0" don't
    thinking_self_hosted: str | None = None
    thinking_external: str | None = None
    default_monthly_credits: str | None = None

    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_security: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    app_base_url: str | None = None

    #: 관리자에게 문의 target; empty: first admin
    contact_email: str | None = None

    #: empty: `SIGNUP_MODE`
    signup_mode: str | None = None
    #: allowed domains, CSV; empty: any
    signup_domains: str | None = None
    #: `on`: mailed-link verification (needs SMTP)
    signup_verify_email: str | None = None


class SmtpTestRequest(Wire):
    """Probe recipient; default: admin's own address."""

    to: str | None = None


class SetRoleRequest(Wire):
    role: UserRole


class UpdateUserRequest(Wire):
    """Admin-set name/email; omitted fields unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    email: EmailStr | None = None


class ReplaceKeyRequest(Wire):
    """Existing LiteLLM virtual key to bind."""

    key: str = Field(min_length=8, max_length=300)


class KloudChatKeyOut(Wire):
    """KloudChat-issued key metadata; never the key."""

    preview: str | None = None
    issued_at: datetime | None = None


class AdminKeysOut(Wire):
    """KloudChat key plus user-issued keys."""

    kloudchat: KloudChatKeyOut | None = None
    named: list[ApiKeyOut] = Field(default_factory=list)


class ResetPasswordRequest(Wire):
    """Admin-set password; ends all sign-ins."""

    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class SetCreditsRequest(Wire):
    monthly_credits: int = Field(ge=0, le=100_000_000)


class ApproveRequest(Wire):
    """Approval and credit assignment in one action."""

    monthly_credits: int | None = Field(default=None, ge=0, le=100_000_000)


class AllowedModelsRequest(Wire):
    """Empty list = whole catalogue."""

    models: list[str] = Field(default_factory=list, max_length=200)


class GovernanceIn(Wire):
    """Instance policy; all optional for single-switch updates."""

    pii_masking: bool | None = None
    external_data_guard: bool | None = None
    allow_user_raw_external: bool | None = None
    privacy_safe_model_ids: list[str] | None = Field(default=None, max_length=20)
    adaptive_routing_enabled: bool | None = None
    adaptive_classifier_model_id: str | None = Field(default=None, max_length=200)
    adaptive_economy_model_ids: list[str] | None = Field(default=None, max_length=3)
    adaptive_quality_enabled: bool | None = None
    adaptive_quality_model_ids: list[str] | None = Field(default=None, max_length=3)
    internal_data_strict_local: bool | None = None
    outline_model_id: str | None = Field(default=None, max_length=200)
    intent_filter: bool | None = None
    blocked_categories: list[str] | None = None
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    #: 0 off; capped at a day (refresh cookie shorter beyond)
    idle_timeout_minutes: int | None = Field(default=None, ge=0, le=1440)
