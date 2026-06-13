"""Exact placement via Mixed-Integer Linear Programming.

Solves the PWRP-HGC formulation from ``docs/ALGORITHM.md`` §4 to provable
optimality on the small instances (≤ 8 nodes, ≤ 20 workloads × ≤ 8 configs)
typical of single-cluster LLM serving. CBC dispatches in well under a second
for these sizes; larger clusters can swap in any MIP solver PuLP supports
without changing the model.

Decision variables
------------------
    x_{w,c,n} ∈ {0,1}       — place workload w in config c on node n
    y_w       ∈ {0,1}       — w survives (≥ r_w^min replicas placed)

Hard constraints
----------------
    (1) Per-node single-config-per-workload
            Σ_c x_{w,c,n} ≤ 1                    ∀ w, n

    (2) Replica bounds linking y_w to placements
            r_w^min · y_w  ≤  Σ_{c,n} x_{w,c,n}  ≤  r_w^max · y_w     ∀ w

    (3) Node memory capacity
            Σ_{w,c} m_{w,c,n} · x_{w,c,n}  ≤  C_n^{eff}               ∀ n
        where m includes the ComfyUI co-residency penalty when ComfyUI lives
        on n (we pre-compute m_{w,c,n} per node, so the constraint stays
        linear).

    (4) Feature admission (KV-aware) — for each feature F with
            min_effective_len = ℓ̂ > 0, requires_workload = w*,
        define the *admitting* (w*, c, n) triples
            A(F) = { (w*, c, n) : ℓ_eff(w*, c, n) ≥ ℓ̂ }
        and require
            Σ_{(w*,c,n)∈A(F)} x_{w*,c,n}  ≥  𝟙[F is requested]

        Because ℓ_eff = min(ℓ_max(c), T_KV(w,c,n)/(α·N_conc(w))) is computed
        per (w,c,n) at problem-build time, this remains a *linear* constraint
        — the piecewise-linear (big-M) linearization is unnecessary in our
        instances. ALGORITHM.md §4.3 describes the big-M variant for
        scenarios where N_conc(w) is itself a decision variable.

Objective (two-phase via additive weights)
------------------------------------------
    max  Σ_w π_w · y_w · W^{div}                  (phase 1: diversity)
       + Σ_{w,c,n} π_w · x_{w,c,n} · W^{rep}      (phase 2: replica fill)
       + Σ_{w,c,n} π_w · ρ_{w,c,n} · x_{w,c,n} · W^{kv}
       − W^{mig} · (migration distance)
       − W^{bal} · (variance proxy)

    π_w = 2^{|W|-rank(w)} from the user-supplied priority order.
    ρ_{w,c,n} = min(1, ℓ_eff(w,c,n) / ℓ_target(w))  ∈ [0,1].

    W^{div} ≫ W^{rep} ≫ W^{kv} ≫ W^{mig} ≫ W^{bal}.  The first instance of even
    the lowest-priority workload outweighs any number of extra replicas of
    higher-priority ones, so every distinct capability is brought up (phase 1)
    before leftover capacity is spent on throughput replicas (phase 2).

We model migration as Σ_{(w,c,n)∈P_current^c} x_{w,c,n} (newly-placed
items count) plus an indicator for *removed* items; both representations
are linearizable and yield the same |P △ P_cur| objective.

Imbalance is approximated as the L1 deviation of per-node loads from the
cluster mean — a linearizable surrogate for the std-dev used in
``evaluator.py`` (which only scores final plans, not the LP relaxation).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import pulp

from scheduler import evaluator, kv_model, memory_model
from scheduler.types import (
    Config, Feature, NodeSpec, Placement, Plan, Workload, WorkloadKind,
)


@dataclass(frozen=True)
class _Triple:
    """One (workload, config, node) decision variable's metadata."""

    workload_id: str
    config: Config
    node_id: str
    charge: int          # m_{w,c,n}
    eff_len: int         # ℓ_eff(w,c,n)


def _enumerate_triples(
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    *,
    activation_fit: dict | None,
    node_reserve: dict[str, int],
) -> list[_Triple]:
    """All admissible (w,c,n) triples with their pre-computed charge and ℓ_eff.

    ``node_reserve[nid]`` is the fixed per-node co-residency overhead (ComfyUI
    + Whisper sidecars) deducted from ``effective_capacity`` for admission.
    """
    triples: list[_Triple] = []
    for w in workloads:
        for c in w.configs:
            for n in nodes:
                # Tensor-parallel: a tp_size>1 config needs that many GPUs on
                # the node (intra-node TP). Single-GPU configs/nodes are unaffected.
                if c.tp_size > n.gpu_count:
                    continue
                mb = memory_model.breakdown(
                    workload_kind=w.kind, model=w.model, config=c, node=n,
                    activation_fit=activation_fit,
                )
                if mb.total_node_charge > max(0, n.effective_capacity - node_reserve.get(n.node_id, 0)):
                    continue
                # vLLM: the gpu_util reserve must actually hold weights + activation,
                # else the engine OOMs at startup (a "ghost" config with zero KV).
                # This is the residual>0 admission the measurement campaign exposed:
                # gpu_util·VRAM < weights gets silently admitted otherwise.
                if w.kind == WorkloadKind.VLLM and mb.kv_residual_bytes <= 0:
                    continue
                if w.kind == WorkloadKind.COMFYUI:
                    eff = 0
                else:
                    kvb = kv_model.breakdown(
                        config=c, model=w.model,
                        kv_memory_bytes=mb.kv_residual_bytes,
                        expected_concurrent_sessions=w.expected_concurrent_sessions,
                    )
                    eff = kvb.effective_max_len
                triples.append(_Triple(
                    workload_id=w.id, config=c, node_id=n.node_id,
                    charge=mb.total_node_charge, eff_len=eff,
                ))
    return triples


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
    time_limit_sec: int = 30,
) -> Plan:
    """Build and solve the MILP. Returns a Plan with score and notes."""
    comfy_set = comfy_co_resident_nodes or set()
    whisper_set = whisper_co_resident_nodes or set()
    node_reserve = {
        n.node_id: memory_model.node_co_resident_reserve(
            comfyui_co_resident=n.node_id in comfy_set,
            whisper_co_resident=n.node_id in whisper_set,
        )
        for n in nodes
    }
    workload_by_id = {w.id: w for w in workloads}
    node_by_id = {n.node_id: n for n in nodes}

    feat_map = {f.id: f for f in features}
    weights = evaluator.feature_priority_to_workload_weights(
        priority_order, feat_map, list(workloads),
    )
    # KV-QoS reward target = each constrained workload's LARGEST configured context
    # (ceiling), so the reward keeps rising with context and the solver spends slack
    # VRAM on it. min_effective_len stays the HARD admission floor (constraint (4));
    # this is only the soft reward target. Coverage phases (W_div ≫ W_rep ≫ W_kv)
    # dominate, so context grows only from genuine slack, never by under-serving.
    target_by_workload: dict[str, int] = {}
    for f in features:
        if f.min_effective_len > 0:
            w = workload_by_id.get(f.requires_workload)
            ceiling = max((c.max_len for c in w.configs), default=0) if w else 0
            target_by_workload[f.requires_workload] = max(
                target_by_workload.get(f.requires_workload, 0),
                ceiling,
            )

    triples = _enumerate_triples(
        workloads, nodes,
        activation_fit=activation_fit,
        node_reserve=node_reserve,
    )

    prob = pulp.LpProblem("PWRP_HGC", pulp.LpMaximize)
    x = {
        (t.workload_id, t.config.name, t.node_id): pulp.LpVariable(
            f"x_{t.workload_id}_{t.config.name}_{t.node_id}", cat=pulp.LpBinary
        )
        for t in triples
    }
    y = {w.id: pulp.LpVariable(f"y_{w.id}", cat=pulp.LpBinary) for w in workloads}

    # (1) per-node single-config-per-workload
    for w in workloads:
        for n in nodes:
            relevant = [x[(w.id, c.name, n.node_id)]
                        for c in w.configs
                        if (w.id, c.name, n.node_id) in x]
            if relevant:
                prob += pulp.lpSum(relevant) <= 1, f"single_cfg_{w.id}_{n.node_id}"

    # (2) replica bounds linked to y_w
    for w in workloads:
        placed = [x[(w.id, c.name, n.node_id)]
                  for c in w.configs for n in nodes
                  if (w.id, c.name, n.node_id) in x]
        if placed:
            prob += pulp.lpSum(placed) >= w.min_replicas * y[w.id], f"min_rep_{w.id}"
            prob += pulp.lpSum(placed) <= w.max_replicas * y[w.id], f"max_rep_{w.id}"
        else:
            # no admissible triple ⇒ y must be 0
            prob += y[w.id] == 0, f"unplaceable_{w.id}"

    # (3) per-node capacity
    triples_by_node: dict[str, list[_Triple]] = {}
    for t in triples:
        triples_by_node.setdefault(t.node_id, []).append(t)
    for nid, ts in triples_by_node.items():
        cap = max(0, node_by_id[nid].effective_capacity - node_reserve.get(nid, 0))
        prob += pulp.lpSum(t.charge * x[(t.workload_id, t.config.name, t.node_id)]
                           for t in ts) <= cap, f"cap_{nid}"

    # (4) feature admission (KV-aware)
    for f in features:
        wid = f.requires_workload
        if wid not in workload_by_id:
            continue
        admitting = [x[(t.workload_id, t.config.name, t.node_id)]
                     for t in triples
                     if t.workload_id == wid and t.eff_len >= max(1, f.min_effective_len)]
        if not admitting:
            # No triple can satisfy this feature's length requirement — skip
            # adding the constraint; coverage of the requiring workload is left
            # to the replica/coverage constraints (feature stays uncovered).
            continue
        if f.min_effective_len > 0:
            prob += pulp.lpSum(admitting) >= 1, f"feature_{f.id}"

    # Objective: lexicographic via additive weights.
    #
    # Two-phase placement via additive weight separation (single solve):
    #   Phase 1 — diversity: place one replica of each workload, in priority
    #             order, as far as capacity allows  (W_div · Σ_w π_w·y_w).
    #   Phase 2 — replica fill: spend leftover capacity on extra replicas of the
    #             highest-priority workloads  (W_rep · Σ_placements π_w·x).
    # W_div ≫ W_rep, so the first instance of even the lowest-priority workload
    # outweighs any number of extra replicas of higher-priority ones — every
    # distinct capability is brought up before any throughput replica. Both stay
    # ≫ kv_qos so a missing/under-served workload is never traded for extra ctx.
    #
    # Tie-breakers below the two coverage phases (all ≪ W_rep), in order:
    #   kv_qos (per-placement ctx-upgrade reward) ≫ migration ≫ {affinity, balance}.
    W_div = 1e9          # phase 1: distinct-workload coverage (priority-ordered)
    W_rep = 1e3          # phase 2: extra-replica fill
    W_kv = 10            # ctx-upgrade reward (per placement)
    W_mig = 1e-2         # penalty for moving a running workload
    W_aff = 1e-3         # GPU-tier affinity reward
    W_bal = 1e-3         # load-balance nudge

    # phase 1: each distinct active workload, priority-weighted. y_w is gated by
    # admissibility + replica bounds; π_w = 2^{N-rank} ⇒ strict priority order.
    diversity_term = pulp.lpSum(weights[w.id] * y[w.id] for w in workloads)
    # phase 2: every placement (first + replicas), priority-weighted. The first
    # replica is also counted here, but diversity dominance (W_div) makes that
    # contribution negligible — this term only decides how leftover capacity is
    # filled once each workload has its first instance.
    coverage_term = pulp.lpSum(
        weights[t.workload_id] * x[(t.workload_id, t.config.name, t.node_id)]
        for t in triples
    )

    kv_term_parts = []
    for t in triples:
        target = target_by_workload.get(t.workload_id, 0)
        if target <= 0:
            ratio = 1.0
        else:
            ratio = min(1.0, t.eff_len / float(target))
        kv_term_parts.append(weights[t.workload_id] * ratio
                             * x[(t.workload_id, t.config.name, t.node_id)])
    kv_qos_term = pulp.lpSum(kv_term_parts)

    # affinity: heavy configs prefer high-tier nodes. Per-triple weight is
    # (charge_in_GB × node.affinity_score), so a heavy chat-122b config gives
    # a much bigger bonus on a PRO6000 than on a GB10, while a light
    # embed-bge-m3 gets a small bonus everywhere — i.e. the choice mostly
    # affects placements that actually load the GPU. See types.NodeSpec
    # .affinity_score for the tier-dominant + VRAM-tiebreaker scoring.
    GB_BYTES = 1024 ** 3
    affinity_term = pulp.lpSum(
        (t.charge / GB_BYTES) * node_by_id[t.node_id].affinity_score
        * x[(t.workload_id, t.config.name, t.node_id)]
        for t in triples
    )

    # migration cost: |new \ current| + |current \ new| — count placements added
    # (var=1 for keys not in current) and removed (1-var for keys in current).
    current_keys = {(p.workload_id, p.config_name, p.node_id) for p in current}
    new_placement_indicator = []
    for key, var in x.items():
        new_placement_indicator.append(var if key not in current_keys else (1 - var))
    migration_term = pulp.lpSum(new_placement_indicator) if new_placement_indicator else 0

    # balance: L1 deviation of node loads from mean
    if len(nodes) >= 2:
        loads = {
            n.node_id: pulp.lpSum(
                t.charge * x[(t.workload_id, t.config.name, t.node_id)]
                for t in triples_by_node.get(n.node_id, [])
            )
            for n in nodes
        }
        mean_load = pulp.lpSum(loads.values()) / len(nodes)
        deviations = []
        for nid, ld in loads.items():
            dev = pulp.LpVariable(f"dev_{nid}", lowBound=0)
            prob += dev >= ld - mean_load
            prob += dev >= mean_load - ld
            deviations.append(dev)
        balance_term = pulp.lpSum(deviations)
    else:
        balance_term = 0

    prob += (W_div * diversity_term
             + W_rep * coverage_term
             + W_kv * kv_qos_term
             + W_aff * affinity_term
             - W_mig * migration_term
             - W_bal * balance_term)

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit_sec)
    status = prob.solve(solver)

    notes: list[str] = []
    if status != pulp.LpStatusOptimal:
        notes.append(f"CBC status: {pulp.LpStatus[status]}")

    placements: list[Placement] = []
    for (wid, cname, nid), var in x.items():
        if pulp.value(var) >= 0.5:
            placements.append(Placement(wid, cname, nid))

    score = evaluator.evaluate(
        placements,
        workloads=workload_by_id, nodes=node_by_id,
        features=features, priorities=weights,
        current=current, activation_fit=activation_fit,
        comfy_co_resident_nodes=comfy_set,
        whisper_co_resident_nodes=whisper_set,
    )
    return Plan(
        placements=tuple(placements),
        score=score,
        solver="milp",
        notes=tuple(notes),
    )
