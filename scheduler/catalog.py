"""Workload catalog: the canonical list of services the planner schedules.

Each Workload couples
    - a logical identity (``id``, used in priority lists and feature requirements),
    - a launch mechanism (``kind`` = vllm | comfyui),
    - the underlying ModelMetadata (architecture facts driving KV cost), and
    - a *configuration grid* — discrete operating points (max_len × gpu_util)
      that span the trade-off between context length and KV capacity.

The grid is the planner's search space: the MILP picks at most one Config per
(workload, node), and the priority weights determine which workloads must
survive when memory is contended.

Catalog members are *templates*. Instantiation (binding ModelMetadata via
``model_metadata.fetch``) happens lazily because the metadata requires a
node probe on first use; downstream code should call ``build_catalog`` once
per planner invocation.

Features (``FEATURES``) are user-visible capabilities expressed as constraints
on the placement — e.g. "deep-research is on iff some chat-122b deployment
admits ≥ 128K-token effective context". The planner consumes them as hard
constraints (when listed in priority input) or soft objective terms
(otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from scheduler.model_metadata import fetch as fetch_model
from scheduler.site import SiteConfig
from scheduler.types import (
    GB, Config, Dtype, Feature, ModelMetadata, Workload, WorkloadKind,
)


@dataclass(frozen=True)
class WorkloadTemplate:
    """Pre-binding workload definition.

    Catalog templates capture the *archetype* (chat large/small, embedding,
    image generation) and the parameter grid the planner is allowed to
    explore. The concrete model identity (which checkpoint, which container,
    which port) comes from the SiteConfig at build time — so swapping the
    cluster doesn't require editing this file.

    ``model_id`` here is the default checkpoint identity (used as a cache
    key for HF-style metadata). A site may override the on-disk path
    (model_path) without changing the model_id, or override the model_id
    entirely by providing its own catalog template.
    """

    id: str
    kind: WorkloadKind
    model_id: str
    min_replicas: int
    max_replicas: int
    expected_concurrent_sessions: int
    configs: tuple[Config, ...]
    weight_bytes: int = 0   # measured on-disk size override (0 ⇒ analytic estimate)


# ──────────────────────────────────────────────────────────────────────
# Configuration grids
# ──────────────────────────────────────────────────────────────────────

# Measured / spec on-disk weight bytes. Used as on_disk_weight_bytes overrides
# so the planner sizes KV residual against the real footprint instead of the
# analytic estimate (which under-counts MoE shared-expert / router / linear-attn
# projections by ~6-8%).
# gemma-4-26b: NVFP4 MoE (A4B active) ~16.4 GiB. On RTX4090 (no FP4 path) the
# AWQ-int4 build lands in the same ballpark; the scheduler sizes against NVFP4.
W_GEMMA26_NVFP4 = int(16.4 * GB)
# qwen3.5-122b-a10b — NVFP4 deep-research model ~78 GiB.
W_QWEN122B_NVFP4 = int(78.0 * GB)
W_BGE = int(1.1 * GB)
# qwen3-coder-next — FP8 coding-assist model (litellm-only, external clients) ~75 GiB.
W_CODERNEXT_FP8 = int(75.0 * GB)

# Super Agent runs single-stage on the chat model — it handles its own
# tool-calls, so there is no separate small tool-router workload.

# gemma-4-26b is a dense-ish multimodal model (no hybrid-Mamba cudagraph caveat);
# qwen3.5-122b is a hybrid-Mamba MoE — cudagraph capture fails if max_num_seqs
# exceeds the node's available Mamba cache blocks. 128 loads on every measured
# node (48G / 96G / 120G); the prod default 512 fails on a single 48G card.
# Carried on every 122b config so the applier emits a value feasible everywhere.
_MNS_122B = 128

# vLLM chat-gemma4-26b: every (max_len × util) combination the planner may pick.
# Ordering doesn't matter — the solver enumerates all pairs. NVFP4 weights
# ~16.4 GiB leave plenty of KV room, so even a tight 48G card serves long context.
# Multimodal: the served deployment must keep --max-num-batched-tokens ≥ 16384
# for the mm budget (applier/compose responsibility, not a planner grid axis).
_CHAT_GEMMA4_26B_CONFIGS: tuple[Config, ...] = (
    Config(name="16K@0.35", max_len=16 * 1024, gpu_util=0.35, kv_dtype=Dtype.FP8),
    Config(name="16K@0.45", max_len=16 * 1024, gpu_util=0.45, kv_dtype=Dtype.FP8),
    Config(name="32K@0.45", max_len=32 * 1024, gpu_util=0.45, kv_dtype=Dtype.FP8),
    Config(name="32K@0.55", max_len=32 * 1024, gpu_util=0.55, kv_dtype=Dtype.FP8),
    Config(name="32K@0.60", max_len=32 * 1024, gpu_util=0.60, kv_dtype=Dtype.FP8),
    # 64K / 128K — bigger ctx for heavy agent / vision-OCR turns. discrete GPU
    # (PRO6000 류) = 0.80~0.90 push 가능, GB10 unified memory = ComfyUI/Whisper
    # 동거 시 빠듯 → 0.50 보수적 옵션도 병치.
    Config(name="64K@0.60", max_len=64 * 1024, gpu_util=0.60, kv_dtype=Dtype.FP8),
    Config(name="64K@0.70", max_len=64 * 1024, gpu_util=0.70, kv_dtype=Dtype.FP8),
    Config(name="128K@0.50", max_len=128 * 1024, gpu_util=0.50, kv_dtype=Dtype.FP8),
    Config(name="128K@0.70", max_len=128 * 1024, gpu_util=0.70, kv_dtype=Dtype.FP8),
    Config(name="128K@0.85", max_len=128 * 1024, gpu_util=0.85, kv_dtype=Dtype.FP8),
)

_EMBED_CONFIGS: tuple[Config, ...] = (
    Config(name="embed@0.15", max_len=8 * 1024, gpu_util=0.15, kv_dtype=Dtype.BF16),
)

# vLLM chat-122b (Deep Research, served as plain local/qwen3.5:122b). NVFP4
# weights ~78 GiB — needs a high-VRAM card (PRO6000 96G) at tp=1, or tp=2 to span
# two 48G cards. Hybrid-Mamba, so every config carries the feasible max_num_seqs.
# 128K floor: LDR(deep-research) 다단계 컨텍스트 누적 → ≥128K 필요, deep-research
# FEATURE 제약도 "chat-122b 가 ≥128K effective context admit" 요구 (위 FEATURES 참조).
# 64K 시 truncate_to_ctx 가 누적 컨텍스트 절단 → 리서치 손상 → 64K config 미배치.
# KV 풀은 VRAM 으로 결정 (max_len 무관) → 128K 라도 동시성 불변.
_CHAT_122B_CONFIGS: tuple[Config, ...] = (
    Config(name="128K@0.85", max_len=128 * 1024, gpu_util=0.85, kv_dtype=Dtype.FP8, max_num_seqs=_MNS_122B),
    Config(name="128K@0.90", max_len=128 * 1024, gpu_util=0.90, kv_dtype=Dtype.FP8, max_num_seqs=_MNS_122B),
    Config(name="128K@0.92", max_len=128 * 1024, gpu_util=0.92, kv_dtype=Dtype.FP8, max_num_seqs=_MNS_122B),
    # tp=2 — span two 48G cards on a 2-GPU node when no single card fits 78 GiB.
    Config(name="128K@0.90-tp2", max_len=128 * 1024, gpu_util=0.90, kv_dtype=Dtype.FP8,
           max_num_seqs=_MNS_122B, tp_size=2),
)

# qwen3-coder-next (FP8) — coding-assist model for external clients (Claude Code /
# Codex) via LiteLLM only; hidden from the LibreChat UI and excluded from
# auto-created agents. Weight ~75 GiB → needs a high-VRAM card at tp=1 or tp=2
# across two 48G cards. 32K matches the served max-model-len.
_CODERNEXT_CONFIGS: tuple[Config, ...] = (
    Config(name="32K@0.88", max_len=32 * 1024, gpu_util=0.88, kv_dtype=Dtype.FP8),
    Config(name="32K@0.90-tp2", max_len=32 * 1024, gpu_util=0.90, kv_dtype=Dtype.FP8, tp_size=2),
    Config(name="16K@0.85", max_len=16 * 1024, gpu_util=0.85, kv_dtype=Dtype.FP8),
)

# ComfyUI = image(FLUX) + video(LTXV) 를 같은 systemd 서비스 하나로 서빙 — 워크로드도
# 하나(image-flux), 두 capability(image/video feature) 가 공유.
# vram_required (admission floor) 30 GiB = 더 큰 단일 워크플로의 peak. ComfyUI 는
# --normalvram 으로 모델 RAM offload → 요청 시 로드, 큐 직렬 → 한 번에 한 모델셋만 상주
# → peak = max(image ~30G[FLUX Q8 12 + T5-XXL 9 + CLIP/VAE/latent], video ~20G[LTXV 6 +
# 공유 T5-XXL 9 + latent]) = image 쪽. 따라서 video 추가해도 reserve 동일. 가중치+인코더
# 몫까지 포함해야 scheduler 가 ComfyUI 를 vLLM-heavy 노드에 packing 안 함 → GPU 경합 회피.
# comfyui-shim 런타임 admission(3–10 GiB, 단일 in-flight 워킹셋)과는 별개의 long-lived
# reserve.
_COMFYUI_CONFIGS: tuple[Config, ...] = (
    Config(name="image+video", vram_required=30 * GB),
)


# ──────────────────────────────────────────────────────────────────────
# Workload templates
# ──────────────────────────────────────────────────────────────────────

WORKLOAD_TEMPLATES: tuple[WorkloadTemplate, ...] = (
    WorkloadTemplate(
        id="chat-gemma4-26b",
        kind=WorkloadKind.VLLM,
        model_id="google/gemma-4-26b-it",
        min_replicas=1,
        max_replicas=4,
        expected_concurrent_sessions=4,
        configs=_CHAT_GEMMA4_26B_CONFIGS,
        weight_bytes=W_GEMMA26_NVFP4,
    ),
    WorkloadTemplate(
        id="embed-bge-m3",
        kind=WorkloadKind.VLLM,
        model_id="BAAI/bge-m3",
        min_replicas=1,
        # 단일 인스턴스: RAG 호출 = 짧은 forward pass → 한 노드 16 concurrent 까지 충분.
        # 두 인스턴스는 메모리 18 GiB × 2 만 점유 + 다른 워크로드와 충돌.
        max_replicas=1,
        expected_concurrent_sessions=16,
        configs=_EMBED_CONFIGS,
        weight_bytes=W_BGE,
    ),
    WorkloadTemplate(
        id="chat-122b",
        kind=WorkloadKind.VLLM,
        model_id="Qwen/Qwen3.5-122B-A10B-NVFP4",
        min_replicas=1,
        # 단일 인스턴스: Deep Research 전용 long-ctx 모델 → 일반 chat 만큼 트래픽 적음,
        # 78 GiB → 한 노드에 하나만 배치.
        max_replicas=1,
        expected_concurrent_sessions=2,
        configs=_CHAT_122B_CONFIGS,
        weight_bytes=W_QWEN122B_NVFP4,
    ),
    WorkloadTemplate(
        id="coder-next",
        kind=WorkloadKind.VLLM,
        model_id="Qwen/Qwen3-Coder-Next-FP8",
        min_replicas=1,
        # 단일 인스턴스: 외부 코딩 클라이언트(Claude Code / Codex) 전용. LiteLLM 에만
        # 등록 + LibreChat UI / 자동 생성 에이전트에서 제외.
        max_replicas=1,
        expected_concurrent_sessions=4,
        configs=_CODERNEXT_CONFIGS,
        weight_bytes=W_CODERNEXT_FP8,
    ),
    # ComfyUI 서비스 하나 — image(FLUX) + video(LTXV) 둘 다 서빙. image/video feature
    # 가 이 워크로드 공유, vram_required 가 둘의 합산 캐시 peak reserve.
    WorkloadTemplate(
        id="image-flux",
        kind=WorkloadKind.COMFYUI,
        model_id="black-forest-labs/FLUX.1-schnell",
        min_replicas=1,
        max_replicas=1,
        expected_concurrent_sessions=1,
        configs=_COMFYUI_CONFIGS,
    ),
)


# ──────────────────────────────────────────────────────────────────────
# Features
# ──────────────────────────────────────────────────────────────────────

# "deep-research" = 별개 워크로드 아님 → chat-122b 가 충분한 effective context 제공
# 가능 시 켜지는 *capability*. 학술적 형식화는 docs/ALGORITHM.md §4.3.
FEATURES: dict[str, Feature] = {
    # ────────────────────────────────────────────────────────────────────
    # GPU-bound features — these drive placement decisions.
    # ────────────────────────────────────────────────────────────────────

    # LibreChat builtin chat completion (the dominant workload). Super Agent
    # runs single-stage on this model — it handles its own tool-calls, so there
    # is no separate small-router feature.
    "chat":          Feature("chat",          requires_workload="chat-gemma4-26b"),
    # File-upload pipeline (embedding via bge-m3 → pgvector → retrieval).
    "rag":           Feature("rag",           requires_workload="embed-bge-m3"),
    # LibreChat ``agents`` endpoint with multi-step chain / tool loop. The
    # chain capability leans on gemma-4-26b's long context, so we annotate a
    # soft length floor (16K).
    "agents-chain":  Feature("agents-chain",  requires_workload="chat-gemma4-26b",
                             min_effective_len=16 * 1024),
    # Vision OCR (gemma-4-26b is multimodal). Image tokens swell the input
    # length, so we also require a moderate effective context.
    "vision-ocr":    Feature("vision-ocr",    requires_workload="chat-gemma4-26b",
                             min_effective_len=8 * 1024),
    # MCP-mediated deep-research (Local Deep Research over ReAct).
    # Identical placement constraint as the builtin deep-research feature
    # — both surface long-context demand on the same chat-122b deployment.
    # 128K = 현 GB10 클러스터의 122b 운영 지점 (gpu_util 0.85 에서 128K 서빙).
    # LDR 검색 결과 다단계 누적 → ≥128K 필요 — 64K 시 truncate_to_ctx 가 누적
    # 컨텍스트 절단 → 리서치 손상. solver 가 128K admit config 만 고르도록 강제
    # (catalog 의 chat-122b config 는 전부 128K).
    "deep-research": Feature("deep-research", requires_workload="chat-122b",
                             min_effective_len=128 * 1024),
    "mcp-deep-research": Feature("mcp-deep-research", requires_workload="chat-122b",
                                 min_effective_len=128 * 1024),
    # ComfyUI image generation (FLUX-schnell Q8 GGUF).
    "image":         Feature("image",         requires_workload="image-flux"),
    # ComfyUI text-to-video (LTXV) — image 와 같은 ComfyUI 서비스(image-flux 워크로드)
    # 공유. 별도 워크로드 아님 → 같은 노드의 같은 서비스가 모델만 교체해 서빙.
    "video":         Feature("video",         requires_workload="image-flux"),
    # Artifact generation (code / slide / webpage). Super Agent runs single-pass
    # on the chat model — the shim applies the concise/detox artifact directive
    # on the gemma chat path when the artifacts toggle is on. 32K is plenty for
    # a single component/page.
    "artifacts":     Feature("artifacts",     requires_workload="chat-gemma4-26b"),
    # External coding assistants (Claude Code / Codex) hitting qwen3-coder-next
    # via LiteLLM only — hidden from the LibreChat UI dropdown and excluded from
    # auto-created agents. Surfaces the coder-next deployment to the planner.
    "coding":        Feature("coding",        requires_workload="coder-next"),

    # ────────────────────────────────────────────────────────────────────
    # GPU-independent features — listed for prioritization transparency
    # only; they live in non-GPU containers / external APIs and the planner
    # makes no placement decision about them.
    # ────────────────────────────────────────────────────────────────────

    "web-search":       Feature("web-search"),        # searxng + crawl4ai
    "code-interpreter": Feature("code-interpreter"),  # CPU sandbox pod
    "mcp-fetch-url":    Feature("mcp-fetch-url"),     # stdio uvx
    "mcp-time":         Feature("mcp-time"),
    "mcp-usage":        Feature("mcp-usage"),
    "mcp-youtube":      Feature("mcp-youtube"),       # API + whisper fallback
}


# A reasonable usage-frequency-based default priority list. Operators with
# real usage data (LibreChat MongoDB call counts, LiteLLM spend logs) should
# override this on the CLI; this list is what we ship as a sane starting
# point for a fresh cluster.
DEFAULT_PRIORITIES: tuple[str, ...] = (
    "chat",               # bulk of traffic (Super Agent single-stage runs here)
    "rag",                # file upload is heavily used
    "agents-chain",       # multi-step agent runs
    "artifacts",          # code/slide/webpage gen → same gemma chat path (heavily used)
    "image",              # ComfyUI 이미지 생성 (video 도 같은 image-flux 워크로드 공유
                          # — image 배치 시 video 동반. 별도 rank 불필요)
    "deep-research",      # heavy but valued → chat-122b
    "vision-ocr",
    "coding",             # external coding clients → coder-next (litellm-only)
    "mcp-deep-research",
    "web-search",
    "code-interpreter",
    "mcp-fetch-url",
    "mcp-time",
    "mcp-usage",
    "mcp-youtube",
)


# ──────────────────────────────────────────────────────────────────────
# Building (binding to ModelMetadata)
# ──────────────────────────────────────────────────────────────────────

# A minimal stub ModelMetadata for ComfyUI workloads — they don't have a
# transformer config.json. Setting layer/head counts to 0 makes the KV
# formulas evaluate to 0, which is correct (no KV cache).
_COMFY_STUB = ModelMetadata(
    model_id="comfyui-stub",
    n_layers=0, n_kv_heads=0, head_dim=0,
    weight_dtype=Dtype.BF16, weight_bytes=0,
)


def build_catalog(
    templates: Iterable[WorkloadTemplate] = WORKLOAD_TEMPLATES,
    *,
    site: SiteConfig | None = None,
) -> list[Workload]:
    """Bind every template to its ModelMetadata.

    The ``site`` argument supplies model_path + probe host for each
    workload. The probe host is any reachable node in ``site.nodes`` — we
    iterate until one of them responds. With no site we fall back to a
    pure cache lookup (suitable for tests / offline replays where the
    metadata has been pre-populated).

    Missing metadata for a non-ComfyUI workload is fatal — the planner
    can't size KV cache without it.
    """
    bound: list[Workload] = []
    for t in templates:
        if t.kind == WorkloadKind.COMFYUI:
            md = _COMFY_STUB
        else:
            md = _fetch_with_site(t.model_id, t.id, site,
                                  weight_bytes=t.weight_bytes or None)
        bound.append(Workload(
            id=t.id,
            kind=t.kind,
            model=md,
            min_replicas=t.min_replicas,
            max_replicas=t.max_replicas,
            expected_concurrent_sessions=t.expected_concurrent_sessions,
            configs=t.configs,
        ))
    return bound


def _fetch_with_site(model_id: str, workload_id: str, site: SiteConfig | None,
                     *, weight_bytes: int | None = None) -> ModelMetadata:
    """Try each node in turn until metadata fetch succeeds.

    Order matches the ``site.nodes`` insertion order (the operator's mental
    model of "node 1, node 2, …"), so a single failed node doesn't strand
    the planner. ``weight_bytes`` (when known — measured on-disk size) overrides
    the analytic weight estimate.
    """
    if site is None:
        return fetch_model(model_id, on_disk_weight_bytes=weight_bytes)
    binding = site.workloads.get(workload_id)
    model_path = binding.model_path if binding else None
    if not model_path:
        # No path → metadata loader falls back to local cache only.
        return fetch_model(model_id, on_disk_weight_bytes=weight_bytes)
    config_path = f"{model_path.rstrip('/')}/config.json"
    last_exc: Exception | None = None
    for host in site.nodes.values():
        try:
            return fetch_model(model_id, probe_host=host, probe_path=config_path,
                               on_disk_weight_bytes=weight_bytes)
        except FileNotFoundError as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    return fetch_model(model_id, on_disk_weight_bytes=weight_bytes)


def features_for_priorities(priorities: Iterable[str]) -> list[Feature]:
    """Resolve user-supplied priority labels to Feature objects.

    Unknown labels are skipped with no warning — priority lists are user input
    and we don't want a single typo to abort planning. The CLI layer surfaces
    unrecognized labels separately.
    """
    out: list[Feature] = []
    for p in priorities:
        f = FEATURES.get(p)
        if f is not None:
            out.append(f)
    return out
