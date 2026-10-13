"""Runtime configuration; `litellm_master_key` stays in-process (services/litellm.py only)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SignupMode = Literal["open", "approval", "closed"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["dev", "prod"] = "dev"
    api_prefix: str = "/api"
    brand_name: str = "KloudChat"
    #: Process count (= KCHAT_API_REPLICAS); 1 keeps stop signal in-process.
    api_replicas: int = 1

    # Seeds; admin screen (`system_settings`) wins. Empty smtp_host: no mail, no password reset.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    #: Envelope sender; relays reject unowned From.
    smtp_from: str = ""
    #: Reset-link origin; never the attacker-controlled Host.
    app_base_url: str = ""

    # Explicit even in dev: no credentialed requests to a wildcard.
    cors_origins: list[str] = ["http://localhost:5173"]

    database_url: str = "postgresql+asyncpg://kchat:kchat@localhost:5432/kchat"
    #: Per-process pool; all processes together under Postgres max_connections.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    jwt_secret: str = Field(default="change-me", min_length=8)
    #: Seals secrets at rest. Empty: derived from jwt_secret (rotation breaks stored secrets).
    secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 30
    login_max_failures: int = 5
    login_lockout_min: int = 15
    # Replay grace for a just-rotated refresh token (two tabs, same cookie).
    refresh_grace_sec: int = 15
    signup_mode: SignupMode = "approval"
    # Startup admin only on an empty database; blank: first sign-up becomes admin.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "관리자"
    # Default grant at approval; first-install seed of 「새 계정의 월 크레딧」.
    default_monthly_credits: int = 500_000
    # Provider USD → credits; fine-grained so cheap models don't round to one floor.
    credits_per_usd: int = 100_000
    # LiteLLM budget headroom above the allowance (meters reset at different instants).
    litellm_budget_headroom: float = 0.2

    # argon2id; memory_cost in KiB.
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65_536
    argon2_parallelism: int = 4

    refresh_cookie_name: str = "kchat_refresh"
    # Off only for plain-HTTP localhost dev.
    cookie_secure: bool = False
    cookie_domain: str | None = None

    #: KloudChat-LLM gateway; model/tool endpoints derived. Admin screen overrides.
    backend_base_url: str = ""
    #: HTML→PDF printer sidecar; empty: structural exporters.
    print_base_url: str = ""
    #: `X-Tools-Key` for gateway `/tools/*` only; empty sends nothing.
    tools_gateway_key: str = ""
    litellm_base_url: str = ""
    litellm_master_key: str = ""
    litellm_timeout_sec: float = 20.0
    # Admin connection probe; short (user waiting).
    litellm_probe_timeout_sec: float = 4.0
    # Tool-using turns on large local models run for minutes.
    chat_timeout_sec: float = 900.0
    title_timeout_sec: float = 20.0
    #: 「에이전트로 저장」 distillation ceiling, separate from title's.
    agent_draft_timeout_sec: float = 45.0
    guide_base_url: str = "https://boanlab.github.io/KloudChat"
    #: Strict-local complexity classifier ceiling; timeout → quality.
    auto_routing_classifier_timeout_sec: float = 8.0
    # Per-tool ceiling; max_tool_hops bounds the turn.
    tool_timeout_sec: float = 300.0
    max_tool_hops: int = 8
    chat_stall_sec: int = 180

    file_storage_dir: str = "/srv/data/files"
    max_upload_mb: int = 200
    # Disk fill ratio triggering removal of deleted accounts' files, oldest first; 0 disables.
    storage_reclaim_at: float = 0.8
    file_context_chars: int = 24_000

    # Bootstrap values; admin screen overrides. Empty address drops the tool.
    searxng_url: str = ""
    scraper_url: str = ""
    #: Replaced by gateway key when routed; needed only for a bare shim.
    scraper_api_key: str = "internal-shim-noauth"
    code_interpreter_url: str = ""
    #: Deep-research MCP root; calls append `/mcp`.
    deep_research_url: str = ""
    #: Vector index for agent knowledge; empty: lexical scorer only.
    index_url: str = ""
    #: ocr-shim root; empty: vision model only, if any.
    ocr_url: str = ""
    #: Fallback vision model for unsure pages; only if listed strict-local with vision.
    ocr_vision_model: str = "strict-local/qwen3.8-flash"
    #: Only audio destination; empty: no transcription, no outside fallback.
    whisper_url: str = ""
    code_interpreter_api_key: str = ""
    # First-install seeds; migration 0068 copies to `system_settings`, stored value wins.

    #: `run_code` writer; strict-local uses strict twin. 27B 8/9 right on messy files, Flash 4/8
    code_model: str = "local/qwen3.8-27b"
    code_fallback_model: str = "local/qwen3.8-flash"
    #: Sandbox is networkless in the self-hosted stack, so offered to strict-local turns.
    sandbox_in_boundary: bool = True
    #: Seed `status: draft` skills, admin-private and runnable; off: neither.
    skill_drafts: bool = False
    # Hits scraped in full (one page fetch each).
    web_search_results: int = 5
    web_search_scrape: int = 3
    # Titles, memories, running summary. Empty: session model; strict-local uses its own.
    title_model: str = "local/qwen3.8-flash"
    #: Fallback chat model; `local/`, since strict alias gets no network tool.
    default_chat_model: str = "local/qwen3.8-flash"
    #: Off: Qwen3 templates get `enable_thinking: false`.
    self_hosted_chat_thinking: bool = False
    #: Name fragments that get `enable_thinking: false`; others untouched.
    self_hosted_thinking_models: str = "qwen3.8-flash"
    #: Self-hosted name fragments sent `presence_penalty` (Qwen's own advice against endless
    #: repetition); 27B at the chat temperature loops on lists without it. Empty: none.
    self_hosted_presence_penalty_models: str = "qwen3.8-27b"
    self_hosted_presence_penalty: float = 1.0
    #: Internal-material strict-local default; tests turn it off for fake catalogues.
    internal_data_strict_local_default: bool = True
    #: Fresh governance-row defaults; lists comma-separated.
    adaptive_routing_default: bool = True
    #: Flash: 24/24 Korean requests classified in allowed shapes.
    adaptive_classifier_model_default: str = "strict-local/qwen3.8-flash"
    adaptive_economy_models_default: str = "local/qwen3.8-flash"
    adaptive_quality_default: bool = True
    adaptive_quality_models_default: str = "deepseek/deepseek-v4.1-flash,z-ai/glm-5.3-flash"
    outline_model_default: str = "local/qwen3.8-flash"
    #: External equivalent via OpenRouter `reasoning`; refusing providers remembered.
    external_chat_thinking: bool = False

    #: Per-surface defaults; empty: default_chat_model. Documents keep the larger model.
    default_report_model: str = "local/qwen3.8-27b"
    default_slides_model: str = "local/qwen3.8-27b"

    #: Must honour aspect ratio. Unserved → cheapest image model.
    default_image_model: str = "google/gemini-2.5-flash-image"

    #: Per-modality av defaults. Unserved → cheapest of that modality.
    default_audio_model: str = "openai/gpt-audio-mini"
    default_video_model: str = "google/veo-3.1-lite"

    #: Zone for the model's date only; DB timestamps UTC.
    timezone: str = "Asia/Seoul"

    #: GeoLite2 City path; empty disables. Local file only (addresses stay here).
    geoip_database: str = ""

    perf_monitoring: bool = True
    #: Host /proc and cgroup v2 (read-only mounts); absent: host figures omitted.
    perf_host_proc: str = "/host/proc"
    perf_host_cgroup: str = "/host/cgroup"
    perf_disks: str = "data=/srv/data/files,docker=/"
    #: Resolvable names mapping exactly to containers.
    perf_dns_names: str = "web,db,print,kloudchat-web,kloudchat-db,kloudchat-print"
    #: `/metrics/<name>` source; empty: `<backend>/metrics`.
    perf_metrics_url: str = ""
    perf_targets: str = "vllm-27b,vllm-flashnext,vllm-bgem3,vllm-rerank,vllm-whisper,gpu-1,gpu-2"
    perf_model_logs: bool = True
    #: Minute rows kept, then folded to 10-minute rows.
    perf_raw_hours: int = 48
    perf_retention_days: int = 30
    #: Verdict thresholds (`services/perf/rules.py`); percentages 0-100.
    perf_vllm_waiting_max: float = 2
    perf_kv_cache_max: float = 90
    perf_ttft_p95_max_sec: float = 10
    #: Percent of one core per replica (single-core process).
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
        # SameSite=None requires Secure.
        return "none" if self.cookie_secure else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
