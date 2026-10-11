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
    #: This process's own count, matching `KCHAT_API_REPLICAS` in docker-compose.yml.
    #: At 1 (the default), the chat stop button never leaves the process — no
    #: Postgres round trip to broadcast it, no background listener held open.
    #: See `services/stop_signal.py`.
    api_replicas: int = 1

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
    #: Per-process pool: at most `db_pool_size + db_max_overflow` connections each, all
    #: processes together under Postgres's `max_connections`.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── auth ───────────────────────────────────────────────────────────
    jwt_secret: str = Field(default="change-me", min_length=8)
    #: Seals secrets at rest (users' LiteLLM keys, issued API keys, connector credentials,
    #: secret system settings). Empty: a key derived from `jwt_secret` is used, so rotating
    #: the signing key would also make every stored secret unreadable. Set it.
    secret_key: str = ""
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
    default_monthly_credits: int = 500_000
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
    #: Shared secret the gateway requires on `/tools/*` (`TOOLS_GATEWAY_KEY` in
    #: KloudChat-LLM's .env). Sent as `X-Tools-Key`, only to the tool addresses;
    #: see `services/tools_gateway.py`. Empty sends nothing.
    tools_gateway_key: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_timeout_sec: float = 20.0
    # Admin-screen connection probe; short because someone is waiting on the form.
    litellm_probe_timeout_sec: float = 4.0
    # A tool-using turn on a large local model runs for minutes.
    chat_timeout_sec: float = 900.0
    title_timeout_sec: float = 20.0
    #: 「에이전트로 저장」: one distillation call of the person's words (retried once). Its own
    #: ceiling, not the title's: a slow model must not leave the 지시 silently empty.
    agent_draft_timeout_sec: float = 45.0
    #: The published user guide (website/), linked from a how-to answer; empty links none.
    guide_base_url: str = "https://boanlab.github.io/KloudChat"
    #: Ceiling for the strict-local complexity classifier call; times out to quality.
    auto_routing_classifier_timeout_sec: float = 8.0
    # Per-tool ceiling; `max_tool_hops` bounds the turn.
    tool_timeout_sec: float = 300.0
    # Model↔tool round trips per turn.
    max_tool_hops: int = 8
    #: Seconds a streaming model call may go without a chunk before it is given up.
    chat_stall_sec: int = 180

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
    #: OpenAI-compatible `/v1/audio/transcriptions`, the only place audio is sent.
    #: Empty: transcription is unavailable and dictation hidden. There is no
    #: outside-model fallback; a leftover `STT_OR_MODEL` env is ignored.
    whisper_url: str = ""
    code_interpreter_api_key: str = ""
    #: Writes the Python behind `run_code`; the fallback gets one try after it fails.
    #: A strict-local turn uses the `strict-local/` twin of a `local/` id, never another.
    code_model: str = "local/qwen3.8-27b"
    code_fallback_model: str = "local/qwen3.8-flash"
    #: The sandbox runs inside the self-hosted stack with networking off, so its file
    #: tools are offered to strict-local turns too. Off for a sandbox hosted elsewhere.
    sandbox_in_boundary: bool = True
    #: Offers skill packages marked `status: draft` (SKILL.md) for evaluation: seeded
    #: private to the catalogue's administrator, never shared, and runnable by
    #: `run_skill_script`. Off, drafts are neither seeded nor runnable.
    skill_drafts: bool = False
    # Hits scraped in full; each is a page fetch.
    web_search_results: int = 5
    web_search_scrape: int = 3
    # Names conversations and extracts memories; a small model is intended.
    # Empty falls back to the session's own model.
    title_model: str = "local/qwen3.8-27b"
    #: Conversation model when the user has not chosen one; absent from the
    #: catalogue, the surface falls back to its cheapest. `local/`, not
    #: `strict-local/`: the strict alias is handed no network tool.
    default_chat_model: str = "local/qwen3.8-27b"
    #: Whether self-hosted chat models may think before answering. Off: a Qwen3-family
    #: template is told `enable_thinking: false`, so a thinking variant answers at once
    #: instead of spending its budget (and half a minute) on reasoning nobody sees.
    self_hosted_chat_thinking: bool = False
    #: Self-hosted models that think unless told not to (name fragments, comma-separated).
    #: Only these get vLLM's `enable_thinking: false`; a model that never thinks is left
    #: alone, since the extra template variable is a risk for no gain there.
    self_hosted_thinking_models: str = "qwen3.8-flash"
    #: Default of the governance switch that keeps turns carrying internal material on
    #: strict-local models. On in production and on a fresh install; the test
    #: environment turns it off so flows with fake catalogues can be exercised.
    internal_data_strict_local_default: bool = True
    #: Defaults of the routing and outline settings for a fresh governance row; an
    #: administrator's saved choices always win. Lists are comma-separated ids.
    adaptive_routing_default: bool = True
    adaptive_classifier_model_default: str = "strict-local/qwen3.8-27b"
    adaptive_economy_models_default: str = "local/qwen3.8-flash"
    adaptive_quality_default: bool = True
    adaptive_quality_models_default: str = "deepseek/deepseek-v4.1-flash,z-ai/glm-5.3-flash"
    outline_model_default: str = "local/qwen3.8-flash"
    #: The same for external chat models: off sends OpenRouter's `reasoning` switch; a
    #: provider that refuses it is remembered and thinks anyway.
    external_chat_thinking: bool = False

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

    # ── performance monitoring (`services/perf`) ───────────────────────
    #: Off: no samples are collected and the admin page shows none.
    perf_monitoring: bool = True
    #: The host's /proc and cgroup v2, mounted read-only; absent, host and container
    #: figures are left out.
    perf_host_proc: str = "/host/proc"
    perf_host_cgroup: str = "/host/cgroup"
    #: `label=path` pairs whose filesystems are reported (paths inside this container).
    perf_disks: str = "data=/srv/data/files,docker=/"
    #: Names this container can resolve that name a container exactly (else the
    #: process fingerprint names it).
    perf_dns_names: str = "web,db,print,kloudchat-web,kloudchat-db,kloudchat-print"
    #: Where the `/metrics/<name>` targets are read. Empty: `<backend address>/metrics`.
    perf_metrics_url: str = ""
    #: Gateway `/metrics/<name>` targets: `vllm-*` are vLLM servers, `gpu-*` gpu-exporter.
    perf_targets: str = "vllm-27b,vllm-flashnext,vllm-bgem3,vllm-rerank,vllm-whisper,gpu-1,gpu-2"
    #: Per-model latency and errors from LiteLLM's request log (master key).
    perf_model_logs: bool = True
    #: Minute rows kept this long, then folded into 10-minute rows; all gone after
    #: `perf_retention_days`.
    perf_raw_hours: int = 48
    perf_retention_days: int = 30
    #: Verdict thresholds (`services/perf/rules.py`). Percentages are 0-100.
    perf_vllm_waiting_max: float = 2
    perf_kv_cache_max: float = 90
    perf_ttft_p95_max_sec: float = 10
    #: Percent of one CPU core per api replica: one process serves on one core.
    perf_api_cpu_max: float = 80
    perf_mem_max: float = 90
    perf_disk_warn: float = 85
    perf_disk_critical: float = 95
    perf_error_rate_max: float = 5
    perf_gpu_temp_max: float = 85
    perf_gpu_node_mem_max: float = 99
    perf_sustain_min: int = 10
    perf_api_cpu_sustain_min: int = 15

    @property
    def cookie_samesite(self) -> Literal["lax", "none"]:
        # SameSite=None requires Secure; Lax when Secure is off.
        return "none" if self.cookie_secure else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
