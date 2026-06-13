"""End-to-end memory model: weights + activations + KV + co-residency.

For a workload ``w`` deployed in configuration ``c`` on node ``n``, the
analytic memory cost is

    m(w, c, n)  =  W(w, c)                weight bytes (dtype-resolved)
                +  M_A(w, c, n)           activation buffer (CUDA workspace,
                                          torch.compile graph, …) — empirically
                                          fit via calibration.py
                +  M_KV(w, c, n)          KV residual = u·C_n − W − M_A
                +  Δ_co-resident(n)       penalty when ComfyUI co-tenants n

vLLM's ``--gpu-memory-utilization`` parameter governs the upper bound: it
asks vLLM to claim ``u · C_n`` bytes, out of which weights+activations are
deducted and the remainder becomes KV cache. So for capacity accounting at
the planner level we treat ``u · C_n`` as the *commitment* to that workload
on node n, and the KV residual is what the kv_model consumes downstream.

This module gives the planner the *commitment* value plus the breakdown
into (weight, activation, kv-residual) for diagnostics. The KV residual is
then fed to ``kv_model.breakdown()`` which returns the effective_max_len.
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.types import Config, ModelMetadata, NodeSpec, WorkloadKind


# Co-residency penalty for ComfyUI on GB10 unified-memory hosts. ComfyUI keeps
# FLUX weights resident across requests; nvidia-smi's vram_free attribution
# under-reports the page cache → vLLM's util-based commitment can over-claim
# the unified pool. Empirically calibrated; see calibration.py.
DEFAULT_COMFY_COTENANT_PENALTY: int = 4 * (1024 ** 3)   # 4 GiB

# Co-residency penalty for Whisper systemd. faster-whisper large-v3 with
# int8_float16 keeps weights + CUDA workspace resident across requests
# (~2.5 GiB observed). Lazy-load means the unit is "up" before weights load,
# but we charge the budget conservatively assuming first-transcribe will land.
DEFAULT_WHISPER_COTENANT_PENALTY: int = 3 * (1024 ** 3)   # 3 GiB

# Activation + cudagraph + non-torch overhead buffer when calibration data is
# absent. ~4 GiB covers activation + cudagraph (~1-1.6 GiB) + non-torch for
# gemma-4-26b in the typical case; large non_torch_memory on some nodes pushes
# it higher (fit precisely by calibration). NB: measured gemma-4-26b weights
# ≈ 15.9 GiB GPU / 15.3 disk. Real per-(workload,
# max_len) values fit by calibration; this default protects first-run planning.
DEFAULT_ACTIVATION_BYTES: int = 4 * (1024 ** 3)   # 4 GiB


@dataclass(frozen=True)
class MemoryBreakdown:
    """Per-(w, c, n) byte accounting for one candidate placement.

    Co-residency overhead (ComfyUI / Whisper page-cache + resident weights) is
    NOT included here — it's a fixed per-node deduction taken once via
    ``node_co_resident_reserve()`` and subtracted from ``node.effective_capacity``
    upstream of the LP capacity constraint.
    """

    commitment_bytes: int          # u · C_n  (what vLLM reserves)
    weight_bytes: int              # W(w, c)
    activation_bytes: int          # M_A(w, c, n)
    kv_residual_bytes: int         # commitment - weight - activation
    total_node_charge: int         # what THIS workload contributes to its node


def node_co_resident_reserve(
    *,
    comfyui_co_resident: bool,
    whisper_co_resident: bool,
    comfy_penalty: int = DEFAULT_COMFY_COTENANT_PENALTY,
    whisper_penalty: int = DEFAULT_WHISPER_COTENANT_PENALTY,
) -> int:
    """Fixed per-node memory overhead from co-resident sidecar services.

    Subtract from ``node.effective_capacity`` once per node before placing
    workloads. Independent of which / how-many workloads land on the node.

    Conceptually:
    - ComfyUI keeps FLUX weights resident; unified-memory page cache means
      vLLM's util-based commitment can over-claim. The penalty is a margin
      against that drift, not ComfyUI's own weight bytes (those are in the
      image-flux workload's ``vram_required``).
    - Whisper (faster-whisper int8_float16) keeps large-v3 weights resident
      across requests (~2.5 GiB observed). Lazy load is no excuse — first
      transcribe will resident-pin it.

    Both are fixed per-node overheads regardless of how many vLLM workloads
    share the node — apply once per node, not per workload.
    """
    return (
        (comfy_penalty if comfyui_co_resident else 0)
        + (whisper_penalty if whisper_co_resident else 0)
    )


def commitment(config: Config, node: NodeSpec) -> int:
    """vLLM-reserved bytes for one (config, node) pair.

    ``gpu_util · total_vram`` for vLLM — gpu_util is vLLM's own
    ``--gpu-memory-utilization`` semantics (fraction of physical VRAM the engine
    reserves at startup for weights + KV cache + activations + cudagraphs).
    Planner uses the same value vLLM does, so commitment reflects vLLM's actual
    reserve. Per-node ceiling (``usable_vram_bytes``) is enforced as a separate
    capacity constraint by the solver: ``sum(commitments) ≤ planner_vram_bytes``
    keeps the total reserve under the operator's chosen ceiling without
    rescaling individual configs (which would shrink each below its calibrated
    weight + KV need and break startup).
    For ComfyUI configs (gpu_util==0) we use the static ``vram_required`` since
    ComfyUI doesn't pre-reserve.
    """
    if config.gpu_util > 0:
        # tensor-parallel spans config.tp_size GPUs on the node, each reserving
        # gpu_util of its own VRAM (total_vram_bytes is per-GPU). tp_size=1 ⇒
        # single-GPU reserve, unchanged.
        tp = max(1, config.tp_size)
        return int(config.gpu_util * tp * node.total_vram_bytes)
    return int(config.vram_required)


def activation_estimate(
    model: ModelMetadata,
    config: Config,
    *,
    fit_table: dict | None = None,
) -> int:
    """Activation/workspace buffer estimate.

    ``fit_table`` (optional) maps (model_id, max_len) → fitted bytes from
    ``calibration.py``. When absent we fall back to a conservative default
    — the planner may then over-charge but never under-charge.
    """
    if fit_table is not None:
        key = (model.model_id, int(config.max_len))
        if key in fit_table:
            return int(fit_table[key])
        # nearest-max-len neighbor if exact miss
        same_model = [v for k, v in fit_table.items() if k[0] == model.model_id]
        if same_model:
            return int(max(same_model))   # use the largest seen — conservative
    return DEFAULT_ACTIVATION_BYTES


def breakdown(
    *,
    workload_kind: WorkloadKind,
    model: ModelMetadata,
    config: Config,
    node: NodeSpec,
    activation_fit: dict | None = None,
) -> MemoryBreakdown:
    """Per-(w, c, n) byte accounting for one candidate placement.

    Returns only THIS workload's own commitment. Co-residency overhead is a
    node-level concept — fetch it from ``node_co_resident_reserve()`` and
    deduct from ``node.effective_capacity`` once, not per workload.
    """
    if workload_kind == WorkloadKind.COMFYUI:
        # ComfyUI does not consume KV; commitment IS the charge.
        c = commitment(config, node)
        return MemoryBreakdown(
            commitment_bytes=c,
            weight_bytes=int(config.vram_required),
            activation_bytes=0,
            kv_residual_bytes=0,
            total_node_charge=c,
        )
    c = commitment(config, node)
    # Per-config weight override (e.g. an NVFP4 variant of the same workload)
    # takes precedence over the workload model's default weight bytes.
    W = int(config.weight_bytes or model.weight_bytes)
    A = activation_estimate(model, config, fit_table=activation_fit)
    residual = max(0, c - W - A)
    return MemoryBreakdown(
        commitment_bytes=c,
        weight_bytes=W,
        activation_bytes=A,
        kv_residual_bytes=residual,
        total_node_charge=c,
    )
