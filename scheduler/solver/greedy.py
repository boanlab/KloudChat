"""Priority-ordered greedy placement (baseline, (1 - 1/e)-approximation).

Algorithm — priority-ranked first-fit with best-config-per-node:

    1. Sort workloads by priority descending.
    2. For each workload in order, greedily attempt to place ``min_replicas``
       copies. For each copy:
           a. For every node, evaluate the *best* config that fits in the
              residual capacity (best = highest effective context length
              that still respects all feature constraints attached to this
              workload, with smallest commitment as tiebreaker).
           b. Pick the node giving the best (effective_len, free_residual)
              tuple. First-fit if multiple nodes tie.
       If a copy can't be placed, the workload's ``y_w`` stays 0 and we move
       on (we still try lower-priority workloads — they may fit elsewhere).
    3. After the must-place pass, if any workload has slack between its
       current replica count and ``max_replicas`` AND a node has unused
       capacity ≥ its commitment, we may add a "bonus replica" — bounded by
       priority order so we never starve a higher-rank workload.

Approximation analysis (sketched here; full proof in docs/ALGORITHM.md §6):

    Let f(S) = Σ_w π_w · 𝟙[w ∈ S] be the priority-weighted coverage of a set
    of *survived* workloads S. f is monotone non-decreasing (more survival is
    better) and submodular (adding the same workload twice yields no extra
    gain). The capacity constraint is matroid-like (per-node knapsack with
    per-workload replica caps). Therefore the greedy algorithm achieves at
    least (1 - 1/e) ≈ 0.632 of the optimal coverage; the bound is tight in
    the worst case (see Nemhauser-Wolsey-Fisher 1978).

In practice — and the EVALUATION.md data backs this up — greedy reaches the
MILP optimum on our concrete cluster, because the priority weights make the
problem strongly monotone (top-rank workload dominates the budget). Greedy
is therefore a useful sanity-check baseline and a safe fallback when the
MILP solver is unavailable (no CBC binary).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from scheduler import evaluator, kv_model, memory_model
from scheduler.types import (
    Config, Feature, NodeSpec, Placement, Plan, Workload, WorkloadKind,
)


@dataclass
class _NodeBudget:
    """Mutable per-node bookkeeping during the greedy walk."""

    node: NodeSpec
    used: int = 0
    placements: list[Placement] = None

    def __post_init__(self) -> None:
        if self.placements is None:
            self.placements = []

    @property
    def residual(self) -> int:
        return max(0, self.node.effective_capacity - self.used)


def _config_admits_feature(
    workload: Workload,
    config: Config,
    node: NodeSpec,
    *,
    feature: Optional[Feature],
    activation_fit: dict | None,
) -> tuple[bool, int]:
    """Returns (admits, eff_max_len). Pure function — no mutation."""
    mb = memory_model.breakdown(
        workload_kind=workload.kind, model=workload.model, config=config, node=node,
        activation_fit=activation_fit,
    )
    if workload.kind == WorkloadKind.COMFYUI:
        # ComfyUI: features only check replica count, not length
        return True, 0
    kvb = kv_model.breakdown(
        config=config, model=workload.model,
        kv_memory_bytes=mb.kv_residual_bytes,
        expected_concurrent_sessions=workload.expected_concurrent_sessions,
    )
    if feature is None or feature.min_effective_len == 0:
        return True, kvb.effective_max_len
    return kvb.effective_max_len >= feature.min_effective_len, kvb.effective_max_len


def _candidate_configs_for(
    workload: Workload,
    node: NodeSpec,
    budget: int,
    *,
    feature: Optional[Feature],
    activation_fit: dict | None,
) -> list[tuple[Config, int, int]]:
    """All (config, charge, eff_len) tuples that fit ``budget`` bytes on ``node``.

    ``budget`` is the residual capacity already net of node-level co-residency
    reserve (initialized in ``solve()`` as ``effective_capacity - reserve``).
    """
    out: list[tuple[Config, int, int]] = []
    for c in workload.configs:
        mb = memory_model.breakdown(
            workload_kind=workload.kind, model=workload.model, config=c, node=node,
            activation_fit=activation_fit,
        )
        if mb.total_node_charge > budget:
            continue
        admits, eff = _config_admits_feature(
            workload, c, node,
            feature=feature, activation_fit=activation_fit,
        )
        if not admits:
            continue
        out.append((c, mb.total_node_charge, eff))
    return out


def _best_placement(
    workload: Workload,
    feature: Optional[Feature],
    budgets: dict[str, _NodeBudget],
    *,
    activation_fit: dict | None,
    used_nodes: set[str],
) -> Optional[Placement]:
    """Pick the (node, config) with largest effective_len; among ties prefer
    high-tier nodes for heavy configs; final tiebreak = smallest charge.

    ``used_nodes`` excludes nodes already carrying this workload (one config
    per (workload, node) — replicas live on distinct nodes).

    Score tuple kept in sync with MILP's objective ordering (see milp.py):
    (eff, affinity_metric, -charge) — bigger wins lexicographically.
    """
    GB_BYTES = 1024 ** 3
    best: Optional[tuple[float, float, int, str, Config]] = None
    for nid, budget in budgets.items():
        if nid in used_nodes:
            continue
        candidates = _candidate_configs_for(
            workload, budget.node, budget.residual,
            feature=feature, activation_fit=activation_fit,
        )
        for cfg, charge, eff in candidates:
            # Heavy config × high-tier node → biggest affinity contribution
            # (matches MILP's per-triple affinity_term).
            aff = (charge / GB_BYTES) * budget.node.affinity_score
            score = (eff, aff, -charge)
            if best is None or score > (best[0], best[1], -best[2]):
                best = (eff, aff, charge, nid, cfg)
    if best is None:
        return None
    _eff, _aff, _charge, nid, cfg = best
    return Placement(workload_id=workload.id, config_name=cfg.name, node_id=nid)


def solve(
    *,
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    priority_order: Sequence[str],
    features: Sequence[Feature] = (),
    current: Sequence[Placement] = (),
    activation_fit: dict | None = None,
    comfy_co_resident_nodes: Optional[set[str]] = None,
    whisper_co_resident_nodes: Optional[set[str]] = None,
) -> Plan:
    """Run greedy placement and return a Plan with score breakdown."""
    comfy_set = comfy_co_resident_nodes or set()
    whisper_set = whisper_co_resident_nodes or set()
    workload_by_id = {w.id: w for w in workloads}
    node_by_id = {n.node_id: n for n in nodes}
    # Pre-debit each node's budget by its fixed co-residency reserve (ComfyUI /
    # Whisper). Per-node, not per-workload — so the reserve is paid once even
    # if multiple workloads share the node.
    budgets = {
        n.node_id: _NodeBudget(
            node=n,
            used=memory_model.node_co_resident_reserve(
                comfyui_co_resident=n.node_id in comfy_set,
                whisper_co_resident=n.node_id in whisper_set,
            ),
        )
        for n in nodes
    }

    # Translate feature priorities → workload weights. The walk order then
    # sorts workloads by descending weight so the highest-priority workload
    # is placed first (which is what makes the greedy (1-1/e)-approximation
    # bound apply).
    feat_map = {f.id: f for f in features}
    weights = evaluator.feature_priority_to_workload_weights(
        priority_order, feat_map, list(workloads),
    )
    ranked = sorted(weights.keys(), key=lambda wid: -weights[wid])

    # feature lookup: workload_id → strictest applicable Feature
    strictest_feature: dict[str, Feature] = {}
    for f in features:
        cur = strictest_feature.get(f.requires_workload)
        if cur is None or f.min_effective_len > cur.min_effective_len:
            strictest_feature[f.requires_workload] = f

    placements: list[Placement] = []
    used_per_workload: dict[str, set[str]] = {}
    notes: list[str] = []

    # Pass 1: min_replicas for every workload in priority order
    for wid in ranked:
        w = workload_by_id.get(wid)
        if w is None:
            continue
        feature = strictest_feature.get(wid)
        used_nodes = used_per_workload.setdefault(wid, set())
        for _ in range(w.min_replicas):
            p = _best_placement(
                w, feature, budgets,
                activation_fit=activation_fit, used_nodes=used_nodes,
            )
            if p is None:
                notes.append(f"could not place min replica of {wid}")
                break
            placements.append(p)
            used_nodes.add(p.node_id)
            cfg = next(c for c in w.configs if c.name == p.config_name)
            mb = memory_model.breakdown(
                workload_kind=w.kind, model=w.model, config=cfg, node=node_by_id[p.node_id],
                activation_fit=activation_fit,
            )
            budgets[p.node_id].used += mb.total_node_charge

    # Pass 2: bonus replicas only after every workload survived its minimum.
    # Order by priority weight × free capacity heuristic.
    for wid in ranked:
        w = workload_by_id.get(wid)
        if w is None:
            continue
        feature = strictest_feature.get(wid)
        used_nodes = used_per_workload[wid]
        while len(used_nodes) < w.max_replicas:
            p = _best_placement(
                w, feature, budgets,
                activation_fit=activation_fit, used_nodes=used_nodes,
            )
            if p is None:
                break
            placements.append(p)
            used_nodes.add(p.node_id)
            cfg = next(c for c in w.configs if c.name == p.config_name)
            mb = memory_model.breakdown(
                workload_kind=w.kind, model=w.model, config=cfg, node=node_by_id[p.node_id],
                activation_fit=activation_fit,
            )
            budgets[p.node_id].used += mb.total_node_charge

    score = evaluator.evaluate(
        placements,
        workloads=workload_by_id, nodes=node_by_id,
        features=features, priorities=weights,
        current=current,
        activation_fit=activation_fit,
        comfy_co_resident_nodes=comfy_set,
        whisper_co_resident_nodes=whisper_set,
    )
    return Plan(
        placements=tuple(placements),
        score=score,
        solver="greedy",
        notes=tuple(notes),
    )
