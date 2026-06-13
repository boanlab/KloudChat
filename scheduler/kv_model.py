"""KV-cache sizing and effective-context computation.

KV cache is the *first-class* memory consumer in this scheduler — see
``docs/ALGORITHM.md`` §3 for the formal motivation. The analytic per-token
cost is

    M_KV_per_token  =  2 · L_kv · H · d · β_kv     (bytes)

where L_kv is the number of full-attention layers (linear-attention layers
contribute a constant state that we account for separately), H is the
KV-head count, d the per-head dim, and β_kv the KV dtype size in bytes
(FP8 ⇒ 1, BF16 ⇒ 2). The *capacity* of a configuration in tokens is then

    T_KV(w, c, n) = (u(c)·C_n − W_w − M_A(w,c,n)) / M_KV_per_token

i.e. weights and activations come off the top of u·C_n; the residual goes to
KV. ``breakdown()`` returns the ``effective_max_len`` field that enforces
a single sequence of length ℓ_max fits with margin α (for output slack)
under N_conc concurrent sessions:

    ℓ_eff(w, c, n) = min(ℓ_max(c),  T_KV(w, c, n) / (α · N_conc(w)))

A planner that ignores ℓ_eff and only checks ℓ_max produces "ghost plans"
that admit long prompts which vLLM later rejects at runtime — the failure
mode is most visible on KV-tight deployments with high concurrent demand.

References:
    Kwon et al. 2023, "Efficient Memory Management for Large Language Model
    Serving with PagedAttention" (SOSP'23) — the block-allocator vLLM uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from scheduler.types import Config, Dtype, ModelMetadata


# Admission margin: leaves room for output tokens, scheduler overhead, and
# the realized-vs-analytic gap that empirical fitting can't fully close.
# Values < 1.0 would make the planner over-promise; we keep this strictly > 1.
ADMISSION_MARGIN: float = 1.10


def kv_bytes_per_token(model: ModelMetadata, kv_dtype: Dtype) -> int:
    """Bytes of KV cache one token consumes across all KV-bearing layers.

    Multiplied by 2 for the K and V tensors.
    """
    beta = kv_dtype.bytes_per_elem
    return int(round(2 * model.n_layers * model.n_kv_heads * model.head_dim * beta))


def kv_capacity_tokens(kv_memory_bytes: int, model: ModelMetadata, kv_dtype: Dtype) -> int:
    """How many tokens the residual KV budget can hold (floor).

    Args:
        kv_memory_bytes: budget left for KV after weights/activations come off.
    """
    per_tok = kv_bytes_per_token(model, kv_dtype)
    if per_tok <= 0:
        return 0
    return max(0, kv_memory_bytes // per_tok)


@dataclass(frozen=True)
class KVBreakdown:
    """All the byte/token quantities involved at a single (w, c, n) point.

    Includes the scalar ``effective_max_len`` plus the inputs that produced
    it so planner diagnostics, evaluators, and sensitivity reports can show
    *why* a configuration was accepted or rejected without re-deriving the
    math.
    """

    per_token_bytes: int
    capacity_tokens: int
    effective_max_len: int
    bounded_by: str   # "max_len" | "kv_capacity" | "sliding_window"


def breakdown(
    *,
    config: Config,
    model: ModelMetadata,
    kv_memory_bytes: int,
    expected_concurrent_sessions: int = 1,
    admission_margin: float = ADMISSION_MARGIN,
) -> KVBreakdown:
    per_tok = kv_bytes_per_token(model, config.kv_dtype)
    cap = kv_capacity_tokens(kv_memory_bytes, model, config.kv_dtype)
    denom = max(1, expected_concurrent_sessions) * max(1.0, admission_margin)
    kv_bound = int(cap // denom)
    sw_bound = int(model.sliding_window) if model.sliding_window else None
    candidates = [("max_len", config.max_len), ("kv_capacity", kv_bound)]
    if sw_bound is not None:
        candidates.append(("sliding_window", sw_bound))
    bound_name, eff = min(candidates, key=lambda t: t[1]) if candidates else ("max_len", 0)
    return KVBreakdown(
        per_token_bytes=per_tok,
        capacity_tokens=cap,
        effective_max_len=max(0, eff),
        bounded_by=bound_name,
    )
