"""Runtime configuration.

`litellm_master_key` never leaves this process: used only by `services/litellm.py`,
exposed by no route, admin routes included.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SignupMode = Literal["open", "approval", "closed"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── app ────────────────────────────────────────────────────────────
    env: Literal["dev", "prod"] = "dev"
    api_prefix: str = "/api"
    #: Service name in the UI. Overridden by the admin screen.
    brand_name: str = "KloudChat"

    # ── mail ───────────────────────────────────────────────────────────
    # Seed values; the admin screen persists to `system_settings` and wins.
    # An empty `smtp_host` disables outbound mail and password reset with it.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    #: Envelope sender. Most relays reject a From they do not own.
    smtp_from: str = ""
    #: Origin the reset link is built from. Never the request Host, which is
    #: attacker-controlled.
    app_base_url: str = ""

    # Explicit even in dev: browsers refuse credentialed requests to a wildcard.
    cors_origins: list[str] = ["http://localhost:5173"]

    # ── database ───────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://kchat:kchat@localhost:5432/kchat"

    # ── auth ───────────────────────────────────────────────────────────
    jwt_secret: str = Field(default="change-me", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 30
    #: Failed sign-ins in a row before an address is locked, and for how long.
    login_max_failures: int = 5
    login_lockout_min: int = 15
    # Window in which a just-rotated refresh token is accepted again without
    # counting as replay (two tabs restoring a session send the same cookie).
    refresh_grace_sec: int = 15
    signup_mode: SignupMode = "approval"
    # Administrator created at startup only when the database has no accounts.
    # Blank: the first account to sign up becomes admin instead.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "관리자"
    # Granted at approval unless the admin sets another number.
    default_monthly_credits: int = 1_000_000
    # Provider USD → credits (100_000 → 1 credit = $0.00001). Used only by
    # `services/models.py`; fine-grained so cheap models do not round onto one floor.
    credits_per_usd: int = 100_000
    # LiteLLM's budget is a backstop above the KloudChat allowance. The two meters
    # reset at different instants, so a backstop on the exact limit would fire first.
    litellm_budget_headroom: float = 0.2

    # argon2id. memory_cost is in KiB. See docs/architecture.md §3.
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65_536
    argon2_parallelism: int = 4

    # ── cookies ────────────────────────────────────────────────────────
    refresh_cookie_name: str = "kchat_refresh"
    # Off in dev for plain-HTTP localhost; on everywhere else.
    cookie_secure: bool = False
    cookie_domain: str | None = None

    # ── downstream ─────────────────────────────────────────────────────
    #: KloudChat-LLM gateway. Model and tool endpoints are derived from it
    #: by appending paths. Overridden by the admin screen.
    backend_base_url: str = ""
    #: HTML→PDF printer sidecar. Empty: PDFs fall back to the structural exporters.
    print_base_url: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_timeout_sec: float = 20.0
    # Admin-screen connection probe; short because someone is waiting on the form.
    litellm_probe_timeout_sec: float = 4.0
    # A tool-using turn on a large local model runs for minutes.
    chat_timeout_sec: float = 900.0
    title_timeout_sec: float = 20.0
    #: Ceiling for the strict-local complexity classifier call; times out to quality.
    auto_routing_classifier_timeout_sec: float = 8.0
    # Per-tool ceiling; `max_tool_hops` bounds the turn.
    tool_timeout_sec: float = 300.0
    # Model↔tool round trips per turn.
    max_tool_hops: int = 8

    # ── files ──────────────────────────────────────────────────────────
    file_storage_dir: str = "/srv/data/files"
    max_upload_mb: int = 200
    # Disk fill (used / total) past which the files of deleted accounts are
    # removed, oldest first, until the volume is back under it. 0 disables.
    storage_reclaim_at: float = 0.8
    #: Characters of file text injected per turn before excerpting.
    file_context_chars: int = 24_000

    # ── tool backends ──────────────────────────────────────────────────
    # Bootstrap values; the admin screen overrides. An empty address drops that
    # tool from the tool list.
    searxng_url: str = ""
    #: Firecrawl-compatible shim in front of Crawl4AI.
    scraper_url: str = ""
    #: Replaced by the gateway's internal key when routed through it. Needed
    #: only when pointing straight at a bare shim.
    scraper_api_key: str = "internal-shim-noauth"
    code_interpreter_url: str = ""
    #: Root of the deep-research MCP endpoint. Actual calls append `/mcp`.
    deep_research_url: str = ""
    #: Vector index for agent knowledge. Empty: retrieval uses only the lexical
    #: scorer in `services/knowledge.py`.
    index_url: str = ""
    #: OpenAI-compatible `/v1/audio/transcriptions`. Empty falls through to
    #: `stt_or_model`; dictation is hidden when neither is set.
    whisper_url: str = ""
    #: Fallback transcription model when `whisper_url` is unset, sent as
    #: chat/completions with an audio part. Microphone audio leaves the network;
    #: "" keeps dictation internal.
    stt_or_model: str = "mistralai/voxtral-small-24b-2507"
    code_interpreter_api_key: str = ""
    # Hits scraped in full; each is a page fetch.
    web_search_results: int = 5
    web_search_scrape: int = 3
    # Names conversations and extracts memories; a small model is intended.
    # Empty falls back to the session's own model.
    title_model: str = "local/qwen3.6-35b"
    #: Conversation model when the user has not chosen one; absent from the
    #: catalogue, the surface falls back to its cheapest. `local/`, not
    #: `strict-local/`: the strict alias is handed no network tool.
    default_chat_model: str = "local/qwen3.6-35b"

    #: Per-surface overrides. Empty falls back to `default_chat_model`.
    default_report_model: str = ""
    default_slides_model: str = ""

    #: Must honour the requested aspect ratio. Not served → cheapest image model.
    default_image_model: str = "google/gemini-2.5-flash-image"

    #: One default per modality of the av surface. Not served → cheapest model
    #: of that modality.
    default_audio_model: str = "openai/gpt-audio-mini"
    default_video_model: str = "google/veo-3.1-lite"

    #: IANA zone for the date given to the model, and nothing else: database
    #: timestamps stay UTC.
    timezone: str = "Asia/Seoul"

    #: MaxMind GeoLite2 City database path. Empty disables region lookup
    #: (`services/geoip.py`). Never a network service: visitor addresses stay
    #: on this instance.
    geoip_database: str = ""

    @property
    def cookie_samesite(self) -> Literal["lax", "none"]:
        # SameSite=None requires Secure; Lax when Secure is off.
        return "none" if self.cookie_secure else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
