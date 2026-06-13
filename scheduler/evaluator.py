"""Plan scoring — coverage, KV-QoS, migration cost, load imbalance.

The four metrics correspond directly to the lexicographic objective in
``docs/ALGORITHM.md`` §4. Each one is a deterministic function of the Plan,
node specs, workload catalog, and (for migration) the current placement, so
that scores are reproducible across solvers and trivially comparable.

Notation:
    P              — set of placements in the candidate plan
    y_w            — indicator: workload w has ≥ r_w^min replicas in P
    π_w            — priority weight of w (2^(|W|-rank(w)) when ranks given)
    ℓ_eff(p)       — effective context length of placement p
    ℓ_target(w)    — feature-derived target context length for w
    load(n, P)     — Σ memory charges on node n under P
    P_curr         — currently realized placement (for migration distance)
"""

from __future__ import annotations

import statistics
from typing import Iterable, Sequence

from scheduler import kv_model, memory_model
from scheduler.types import (
    Feature, NodeSpec, Placement, Score, Workload, WorkloadKind,
)


def priority_weights(workload_ids_in_priority_order: Sequence[str]) -> dict[str, float]:
    """π_w = 2^{|W|-rank(w)}: lexicographic via additive weights.

    The top workload's weight strictly exceeds the sum of all lower weights,
    so coverage at one rank can never be sacrificed for any gain at a lower
    rank — this gives the lexicographic preference for free, without needing
    a multi-stage solver.
    """
    n = len(workload_ids_in_priority_order)
    return {wid: float(1 << (n - rank))
            for rank, wid in enumerate(workload_ids_in_priority_order)}


def feature_priority_to_workload_weights(
    feature_priority_order: Sequence[str],
    features: dict[str, Feature],
    workloads: Sequence[Workload],
) -> dict[str, float]:
    """Convert user-facing feature priorities to workload-level π_w.

    The user-supplied priority order is read as a *usage-frequency ranking*:
    features the user expects to invoke more often go first, less-frequent
    ones later. Survival under capacity contention follows that ranking.

    GPU-independent features (``requires_workload is None``) appear in the
    priority list for documentation but contribute no workload weight —
    they're satisfied by their own non-GPU container regardless of placement.

    Multiple features can resolve to the same workload (``chat``,
    ``agents-chain`` and ``artifacts`` all require ``chat-gemma4-26b``); that
    workload inherits the *highest* rank among its requiring features (i.e.
    maximum priority weight), preserving lex dominance. Workloads not referenced
    by any
    prioritized feature get a tiny baseline weight so they still survive
    when capacity allows.
    """
    weights_by_feature = priority_weights(list(feature_priority_order))
    wl_ids = {w.id for w in workloads}
    out: dict[str, float] = {}
    for fid in feature_priority_order:
        f = features.get(fid)
        if f is None or f.requires_workload is None:
            continue
        if f.requires_workload not in wl_ids:
            continue
        w = weights_by_feature[fid]
        prev = out.get(f.requires_workload, 0.0)
        if w > prev:
            out[f.requires_workload] = w
    baseline = (min(out.values()) / 16.0) if out else 1.0
    for w in workloads:
        out.setdefault(w.id, baseline)
    return out


def _placement_per_workload(P: Iterable[Placement]) -> dict[str, list[Placement]]:
    out: dict[str, list[Placement]] = {}
    for p in P:
        out.setdefault(p.workload_id, []).append(p)
    return out


def coverage(
    P: Sequence[Placement],
    workloads: dict[str, Workload],
    priorities: dict[str, float],
) -> float:
    """Σ_w π_w · 𝟙[#replicas(w) ≥ r_w^min].

    Workloads with zero priority (i.e. not in the user's list) contribute
    a fixed small weight so the planner still tries to keep them alive when
    it doesn't cost anything; that weight is below the smallest π_w in
    ``priorities`` so it never preempts a prioritized survival.
    """
    by_w = _placement_per_workload(P)
    score = 0.0
    base = min(priorities.values(), default=1.0) / 16.0   # bonus < smallest π
    for wid, w in workloads.items():
        replicas = len(by_w.get(wid, []))
        alive = replicas >= w.min_replicas
        weight = priorities.get(wid, base)
        if alive:
            score += weight
    return score


def kv_qos(
    P: Sequence[Placement],
    workloads: dict[str, Workload],
    nodes: dict[str, NodeSpec],
    *,
    features: Sequence[Feature] = (),
    activation_fit: dict | None = None,
    comfy_co_resident_nodes: set[str] | None = None,
    whisper_co_resident_nodes: set[str] | None = None,
    priorities: dict[str, float] | None = None,
) -> float:
    """Σ_p π_{w(p)} · min(1, ℓ_eff(p) / ℓ_target(w(p))).

    ℓ_target is each context-constrained workload's LARGEST configured context
    (the ceiling) — so the solver spends spare VRAM on context instead of leaving
    it idle, maximizing effective length with whatever capacity coverage leaves
    free. A workload with no context feature scores 1.0 (unconstrained). The hard
    minimum stays ``Feature.min_effective_len`` (enforced as a solver feasibility
    constraint, separate from this objective); here min_effective_len > 0 only
    flags which workloads opt into context-maximization. Because KV-QoS sits below
    coverage (diversity ≫ replicas) in the lex order, this never evicts or shrinks
    another workload below its minimum — it only consumes genuine slack.
    """
    # kv_residual_bytes is independent of co-residency reserve (per-node
    # capacity deduction happens upstream; KV is workload-internal).
    # Sets are accepted only so the signature stays symmetric with the solvers.
    _ = comfy_co_resident_nodes
    _ = whisper_co_resident_nodes
    priorities = priorities or {}
    target_by_workload: dict[str, int] = {}
    for f in features:
        if f.min_effective_len > 0:
            w = workloads.get(f.requires_workload)
            ceiling = max((c.max_len for c in w.configs), default=0) if w else 0
            target_by_workload[f.requires_workload] = max(
                target_by_workload.get(f.requires_workload, 0),
                ceiling,
            )

    total = 0.0
    for p in P:
        w = workloads[p.workload_id]
        if w.kind == WorkloadKind.COMFYUI:
            total += priorities.get(w.id, 0.0)
            continue
        node = nodes[p.node_id]
        cfg = next(c for c in w.configs if c.name == p.config_name)
        mb = memory_model.breakdown(
            workload_kind=w.kind, model=w.model, config=cfg, node=node,
            activation_fit=activation_fit,
        )
        kvb = kv_model.breakdown(
            config=cfg, model=w.model,
            kv_memory_bytes=mb.kv_residual_bytes,
            expected_concurrent_sessions=w.expected_concurrent_sessions,
        )
        target = target_by_workload.get(w.id, 0)
        if target <= 0:
            ratio = 1.0
        else:
            ratio = min(1.0, kvb.effective_max_len / float(target))
        total += priorities.get(w.id, 0.0) * ratio
    return total


def migration_cost(P: Sequence[Placement], P_current: Sequence[Placement]) -> int:
    """Symmetric difference |P △ P_current| — number of (start | stop | recreate)
    actions implied by moving from current to target.

    A placement is keyed by (workload_id, node_id, config_name); changing any
    field counts as one stop + one start.
    """
    def key(p: Placement) -> tuple[str, str, str]:
        return (p.workload_id, p.node_id, p.config_name)
    a, b = {key(p) for p in P}, {key(p) for p in P_current}
    return len(a ^ b)


def imbalance(
    P: Sequence[Placement],
    workloads: dict[str, Workload],
    nodes: dict[str, NodeSpec],
    *,
    activation_fit: dict | None = None,
    comfy_co_resident_nodes: set[str] | None = None,
    whisper_co_resident_nodes: set[str] | None = None,
) -> float:
    """Std-dev of per-node memory load fractions.

    Lower is better (better-balanced cluster). Bounded by [0, 0.5] for our
    [0, 1] load fractions.
    """
    loads: dict[str, int] = {n: 0 for n in nodes}
    comfy_set = comfy_co_resident_nodes or set()
    whisper_set = whisper_co_resident_nodes or set()
    # Per-node co-residency reserve counted once, not per workload — same
    # accounting as the LP capacity constraint.
    for nid in nodes:
        loads[nid] = memory_model.node_co_resident_reserve(
            comfyui_co_resident=nid in comfy_set,
            whisper_co_resident=nid in whisper_set,
        )
    for p in P:
        w = workloads[p.workload_id]
        cfg = next(c for c in w.configs if c.name == p.config_name)
        mb = memory_model.breakdown(
            workload_kind=w.kind, model=w.model, config=cfg, node=nodes[p.node_id],
            activation_fit=activation_fit,
        )
        loads[p.node_id] += mb.total_node_charge
    fractions = [
        loads[nid] / max(1, nodes[nid].effective_capacity) for nid in nodes
    ]
    if len(fractions) <= 1:
        return 0.0
    return statistics.pstdev(fractions)


def affinity(
    P: Sequence[Placement],
    workloads: dict[str, Workload],
    nodes: dict[str, NodeSpec],
    *,
    activation_fit: dict | None = None,
) -> float:
    """Σ (charge_GB · node.affinity_score) over all placements.

    Mirrors the per-triple affinity_term in milp.py. Higher = heavier configs
    landed on higher-tier nodes (PRO6000 > PRO5000 > RTX5090 > RTX4090 > GB10).
    Used by cross-solver/cross-plan comparison to keep score reporting
    consistent with what the MILP optimized for. See types.NodeSpec
    .affinity_score for the tier-dominant + VRAM-tiebreaker scoring.
    """
    GB_BYTES = 1024 ** 3
    total = 0.0
    for p in P:
        w = workloads.get(p.workload_id)
        n = nodes.get(p.node_id)
        if w is None or n is None:
            continue
        cfg = next((c for c in w.configs if c.name == p.config_name), None)
        if cfg is None:
            continue
        mb = memory_model.breakdown(
            workload_kind=w.kind, model=w.model, config=cfg, node=n,
            activation_fit=activation_fit,
        )
        total += (mb.total_node_charge / GB_BYTES) * n.affinity_score
    return total


def evaluate(
    plan_placements: Sequence[Placement],
    *,
    workloads: dict[str, Workload],
    nodes: dict[str, NodeSpec],
    features: Sequence[Feature],
    priorities: dict[str, float],
    current: Sequence[Placement] = (),
    activation_fit: dict | None = None,
    comfy_co_resident_nodes: set[str] | None = None,
    whisper_co_resident_nodes: set[str] | None = None,
) -> Score:
    """Compute all five metrics for a candidate plan."""
    return Score(
        coverage=coverage(plan_placements, workloads, priorities),
        kv_qos=kv_qos(
            plan_placements, workloads, nodes,
            features=features, activation_fit=activation_fit,
            comfy_co_resident_nodes=comfy_co_resident_nodes,
            whisper_co_resident_nodes=whisper_co_resident_nodes,
            priorities=priorities,
        ),
        migration_cost=migration_cost(plan_placements, current),
        imbalance=imbalance(
            plan_placements, workloads, nodes,
            activation_fit=activation_fit,
            comfy_co_resident_nodes=comfy_co_resident_nodes,
            whisper_co_resident_nodes=whisper_co_resident_nodes,
        ),
        affinity=affinity(
            plan_placements, workloads, nodes,
            activation_fit=activation_fit,
        ),
    )


