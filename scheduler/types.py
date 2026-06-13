"""Core dataclasses for the placement scheduler.

The placement problem is formalized as Priority-Weighted Replica Placement under
Heterogeneous GPU Constraints (PWRP-HGC). See docs/ALGORITHM.md for the full
formalization, NP-hardness proof, and approximation analysis.

Notation (cross-referenced with docs/ALGORITHM.md):
    n ∈ N      — compute nodes (heterogeneous: GPU class, total VRAM)
    w ∈ W      — workloads (chat-gemma4-26b, embed-bge-m3, …)
    c ∈ C_w    — configurations of workload w (e.g. max_len × util grid)
    π_w        — priority weight of workload w  (= 2^{|W|-rank(w)})
    r_w^{min}  — minimum replica count for w (1 unless HA required)
    r_w^{max}  — maximum replica count for w (cap usually = |N|)

Memory accounting (with KV cache as first-class citizen):

    m_{w,c,n} = W_w(c,n) + M_KV_{w,c,n} + M_A_{w,c,n} + Δ_{co-resident}(n)

where W_w(c,n) is dtype-resolved weight bytes, M_KV is the KV-cache memory
derived from the analytic formula 2·L·H·d·β·T_KV (T_KV is the realized
num_gpu_blocks × block_size, fit via calibration), and Δ accounts for the
ComfyUI-vs-vLLM co-residency penalty observed empirically on unified-memory
hosts (page cache attribution skews vram_free downward).

All byte quantities use ``int`` (no float drift in capacity arithmetic). All
token quantities use ``int``. Capacity comparisons therefore yield reproducible
plans across runs (a property we exploit in evaluation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


GB: int = 1024 ** 3


# ──────────────────────────────────────────────────────────────────────
# GPU tier ranking — used by the placement affinity score
# ──────────────────────────────────────────────────────────────────────

# Higher tier = preferred for heavy workloads. Ordering reflects per-card
# inference throughput (compute, mem bandwidth, scheduler interconnect) NOT
# raw VRAM — GB10's unified memory is slower than PRO6000's GDDR despite the
# larger capacity, so it ranks lowest. Per-site override is allowed in
# scheduler/site.py (node yaml may set `gpu_tier:` to bump or demote a specific
# node — e.g. a special-purpose PRO5000 that should rank higher in a given
# cluster). Unknown gpu_class → tier 0 (least preferred).
GPU_TIER: dict[str, int] = {
    "pro6000": 5,   # PRO6000
    "pro5000": 4,   # PRO5000
    "rtx5090":     3,   # RTX5090
    "rtx4090":           2,   # RTX4090 — emitted by lib.sh detect_gpu_class
    "gb10":               1,   # GB10
}
DEFAULT_GPU_TIER: int = 0


class Dtype(str, Enum):
    """Numerical dtypes relevant to weight & KV cache sizing.

    BF16/FP16: 2 bytes/elem · FP8: 1 byte/elem · NVFP4: 0.5 byte/elem.
    """

    BF16 = "bf16"
    FP16 = "fp16"
    FP8 = "fp8"
    NVFP4 = "nvfp4"

    @property
    def bytes_per_elem(self) -> float:
        return {"bf16": 2, "fp16": 2, "fp8": 1, "nvfp4": 0.5}[self.value]


class WorkloadKind(str, Enum):
    """How the workload is launched (drives applier strategy)."""

    VLLM = "vllm"          # docker compose service in docker-compose.vllm.yml
    COMFYUI = "comfyui"    # systemd unit on the node (native python)


@dataclass(frozen=True)
class ModelMetadata:
    """HuggingFace-style transformer architecture metadata.

    Needed for the analytic KV-cache sizing formula. Source: the model's
    ``config.json`` (fetched via inventory probe; cached locally).
    """

    model_id: str            # e.g. "google/gemma-4-26b-it"
    n_layers: int            # KV-bearing (full-attention) layer count
    n_kv_heads: int          # GQA: distinct from n_attention_heads
    head_dim: int
    weight_dtype: Dtype
    weight_bytes: int        # checkpoint on-disk (≈ in-memory) size
    sliding_window: Optional[int] = None  # if set, KV is capped at this length
    is_hybrid_mamba: bool = False  # hybrid linear/full attention (qwen3.5-122b-a10b): cudagraph capture needs max_num_seqs ≤ available Mamba cache blocks — configs set a feasible max_num_seqs (see catalog)


@dataclass(frozen=True)
class Config:
    """One operating point of a workload.

    For vLLM workloads, (max_len, gpu_util, kv_dtype) span the configuration
    grid we enumerate. For ComfyUI, ``vram_required`` is the static admission
    threshold (no KV; ComfyUI streams diffusion weights and discards).
    """

    name: str                          # human label, e.g. "16K@0.45"
    max_len: int = 0                   # 0 ⇒ N/A (non-vLLM)
    gpu_util: float = 0.0              # vLLM --gpu-memory-utilization
    kv_dtype: Dtype = Dtype.FP8
    vram_required: int = 0             # non-vLLM admission floor (bytes)
    weight_bytes: int = 0              # per-config weight override (0 ⇒ use workload model's); e.g. an alternate-quant variant of the same workload
    max_num_seqs: int = 0              # vLLM --max-num-seqs (0 ⇒ engine default); hybrid-Mamba models need this ≤ available Mamba cache blocks
    tp_size: int = 1                   # tensor-parallel degree (GPUs spanned on one node); 1 ⇒ single-GPU


@dataclass(frozen=True)
class Workload:
    """A logically-distinct service we schedule across nodes."""

    id: str                            # "chat-gemma4-26b", "embed-bge-m3", …
    kind: WorkloadKind
    model: ModelMetadata
    min_replicas: int = 1
    max_replicas: int = 4
    expected_concurrent_sessions: int = 1
    configs: tuple[Config, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Feature:
    """A user-visible capability that imposes constraints on the placement.

    Two flavors:

      *GPU-bound* features set ``requires_workload`` to a workload ID and
      optionally a context-length floor. These contribute to ``y_w`` weights
      and to constraint (C4) in the MILP.

      *GPU-independent* features (web-search, code-interpreter run in their
      own non-GPU containers, MCP tools that hit external APIs, …) leave
      ``requires_workload = None``. They appear in the catalog so users can
      include them in the priority list for documentation purposes — the
      planner skips them silently because no placement decision involves
      them. Tracking them keeps the priority list a single source of truth
      for "what the user expects to be available."

    Examples:
        deep-research → at least one chat-122b deployment with effective
                        context length ≥ 128K tokens.
        rag           → at least one embed-bge-m3 deployment alive.
        web-search    → GPU-independent (searxng + crawl4ai).
    """

    id: str
    requires_workload: Optional[str] = None
    min_effective_len: int = 0    # token; 0 ⇒ no context constraint
    min_replicas: int = 1


@dataclass(frozen=True)
class NodeSpec:
    """Discovered/declared compute node capacity.

    ``total_vram_bytes`` is the physical ceiling (auto-detected via nvidia-smi).
    The planner's *view* of usable VRAM is ``planner_vram_bytes`` — by default
    ``total - reserved`` (small page cache / OS margin), but the site config may
    override it via per-node ``usable_vram_gb``. Setting ``usable_vram_bytes``
    explicitly is how unified-memory nodes (where vLLM's util fraction would
    otherwise over-claim the shared pool) cap planner-allocated VRAM below
    physical — discrete-VRAM nodes typically omit it and inherit auto-detect.
    Commitment and capacity arithmetic both go through ``planner_vram_bytes``
    so configs scale consistently against whichever ceiling is in effect.
    """

    node_id: str                  # short identifier, e.g. last IPv4 octet
    hostname: str                 # ssh-reachable host (IP or DNS name)
    gpu_class: str                # "gb10", "pro5000", …
    total_vram_bytes: int                            # physical, PER GPU (inventory reports one card)
    reserved_bytes: int = 8 * GB                     # OS + page cache margin (used when usable_vram_bytes is None)
    usable_vram_bytes: Optional[int] = None          # explicit planner ceiling — overrides total-reserved when set
    gpu_tier_override: Optional[int] = None          # per-node tier override (site yaml); None → lookup GPU_TIER[gpu_class]
    gpu_count: int = 1                               # GPUs on this node (for intra-node tensor-parallel); 1 ⇒ single-GPU

    @property
    def planner_vram_bytes(self) -> int:
        """Single source of truth for planner / commitment / capacity arithmetic.

        For a multi-GPU node (``gpu_count`` > 1) the planner ceiling is the
        aggregate across cards — a TP config can span them. Single-GPU nodes
        (the common case) are unchanged. An explicit ``usable_vram_bytes`` site
        override always wins (and is assumed to already be the aggregate)."""
        if self.usable_vram_bytes is not None:
            return self.usable_vram_bytes
        return self.gpu_count * self.total_vram_bytes - self.reserved_bytes

    @property
    def effective_capacity(self) -> int:
        return self.planner_vram_bytes

    @property
    def gpu_tier(self) -> int:
        """Affinity tier (higher = preferred for heavy workloads). Override
        wins; otherwise lookup GPU_TIER table by gpu_class (case-insensitive,
        unknown → DEFAULT_GPU_TIER = 0)."""
        if self.gpu_tier_override is not None:
            return self.gpu_tier_override
        return GPU_TIER.get(self.gpu_class.lower(), DEFAULT_GPU_TIER)

    @property
    def affinity_score(self) -> float:
        """Per-node score used in the placement objective's affinity term.

        Tier dominates (integer × 1.0), usable VRAM acts as a tiebreaker
        (0.01 per GB). PRO6000 (tier 5, 96 GB) → 5.96; GB10 (tier 1, 128 GB)
        → 2.28 — but tier dominance still puts every PRO6000 above GB10
        regardless of capacity. The tiebreaker only separates same-tier nodes
        whose capacities differ.
        """
        return float(self.gpu_tier) + 0.01 * (self.planner_vram_bytes / GB)


@dataclass(frozen=True)
class Placement:
    """One scheduled (workload, config, node) decision."""

    workload_id: str
    config_name: str
    node_id: str


@dataclass(frozen=True)
class Score:
    """Multi-objective score breakdown (lexicographic).

    Used for tie-breaking and for cross-solver comparison in EVALUATION.md.
    Smaller migration / imbalance is better, encoded as negative contributions
    when summed into a single scalar.
    """

    coverage: float                # Σ π_w · y_w  (priority-weighted survival)
    kv_qos: float                  # Σ π_w · min(1, ℓ_eff / ℓ_target)
    migration_cost: int            # |P_target Δ P_current|
    imbalance: float               # std-dev of per-node memory loads
    affinity: float = 0.0          # Σ (charge_GB · node.affinity_score) — tier-aware preference (optional/defaulted field).


@dataclass(frozen=True)
class Plan:
    """Complete output of a solver: placements + score + justification."""

    placements: tuple[Placement, ...]
    score: Score
    solver: str                    # "greedy" | "milp"
    notes: tuple[str, ...] = field(default_factory=tuple)
