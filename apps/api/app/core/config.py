"""Runtime configuration.

`litellm_master_key` never leaves this process: read here, used by
`services/litellm.py`, exposed by no route, including admin routes.
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
    # Window in which a just-rotated refresh token may be presented again
    # without counting as replay. Two tabs restoring a session send the same
    # cookie; narrow enough that a leaked token is still caught.
    refresh_grace_sec: int = 15
    signup_mode: SignupMode = "approval"
    # Administrator created at startup only when the database has no accounts.
    # Blank: the first account to sign up becomes admin instead.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "관리자"
    # Granted at approval unless the admin sets another number in the same call.
    default_monthly_credits: int = 1_000_000
    # Provider price → credits. Used only by `services/models.py`.
    # 100_000 → 1 credit = $0.00001.
    #
    # Resolution over magnitude: provider prices span four orders of magnitude,
    # and a coarse unit rounds the cheapest models up onto the same floor as
    # models twenty times their price.
    credits_per_usd: int = 100_000
    # Headroom above the KloudChat allowance for the proxy-side budget. KloudChat is
    # the limit users see; LiteLLM's is a backstop against accounting error.
    # The two meters count independently and reset at different instants, so a
    # backstop sitting exactly on the limit would fire first.
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
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_timeout_sec: float = 20.0
    # The admin screen's connection probe. Short: it runs while someone is
    # looking at the form they need to fix.
    litellm_probe_timeout_sec: float = 4.0
    # Long: a tool-using turn on a local 122b runs for minutes.
    chat_timeout_sec: float = 900.0
    title_timeout_sec: float = 20.0
    #: Fail-open-to-quality ceiling for the small strict-local complexity call.
    auto_routing_classifier_timeout_sec: float = 8.0
    # Per-tool ceiling. Deep research and headless rendering take minutes;
    # `max_tool_hops` is what bounds the turn.
    tool_timeout_sec: float = 300.0
    # Model↔tool round trips per turn. Five covers search → read → compute →
    # answer; beyond that a model is usually in a retry loop.
    max_tool_hops: int = 5

    # ── files ──────────────────────────────────────────────────────────
    file_storage_dir: str = "/srv/data/files"
    # Upload ceiling, so one file cannot fill the disk.
    max_upload_mb: int = 200
    #: Characters of file text injected per turn before excerpting.
    file_context_chars: int = 24_000

    # ── tool backends ──────────────────────────────────────────────────
    # All in KloudChat-LLM, addressed from the admin screen. These are a
    # bootstrap for unattended deployments; an empty address drops that tool
    # from the tool list and leaves everything else working.
    searxng_url: str = ""
    #: Firecrawl-compatible shim in front of Crawl4AI.
    scraper_url: str = ""
    #: Replaced by the gateway's internal key when routed through it. Needed
    #: only when pointing straight at a bare shim.
    scraper_api_key: str = "internal-shim-noauth"
    code_interpreter_url: str = ""
    #: Root of the deep-research MCP endpoint. Actual calls append `/mcp`.
    deep_research_url: str = ""
    #: Vector index for agent knowledge. Empty is supported: retrieval then runs
    #: on the lexical scorer in `services/knowledge.py` alone, which needs no
    #: backend at all.
    index_url: str = ""
    #: OpenAI-compatible `/v1/audio/transcriptions`. Empty falls through to
    #: `stt_or_model`; dictation is hidden when neither is set.
    whisper_url: str = ""
    #: Fallback transcription model where a local Whisper cannot run. Sent as
    #: chat/completions with an audio part, since OpenRouter has no
    #: transcription endpoint.
    #:
    #: **Microphone audio leaves the network.** Set to "" to keep dictation
    #: internal, at the cost of having none where Whisper cannot run.
    stt_or_model: str = "mistralai/voxtral-small-24b-2507"
    code_interpreter_api_key: str = ""
    # Hits scraped in full. Each is a page fetch: answer quality against latency.
    web_search_results: int = 5
    web_search_scrape: int = 3
    # Names conversations. Empty falls back to the session's own model, which
    # is correct but wasteful on an expensive one.
    title_model: str = "local/glm-4.7-flash"
    #: Conversation model when the user has not chosen one. Absent from the
    #: catalogue, the surface falls back to its cheapest.
    default_chat_model: str = "local/qwen3.6-35b"

    @property
    def cookie_samesite(self) -> Literal["lax", "none"]:
        # SameSite=None requires Secure; Lax when Secure is off.
        return "none" if self.cookie_secure else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
