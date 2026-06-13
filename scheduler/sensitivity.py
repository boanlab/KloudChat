"""What-if and bottleneck analysis for placement plans.

Two flavors of question this module answers:

    1. *What-if* — "what does the plan look like if I add another node?
       remove this one?". Implemented by re-running the solver with
       perturbed inputs and diffing the resulting plans.

    2. *Bottleneck* — "which workload is right at the edge of placement?
       which node is most constrained?". Implemented by inspecting the LP
       relaxation of the MILP — non-integral x_{w,c,n} after relaxation
       identifies workloads whose feasibility hinges on tight constraints;
       dual values (shadow prices) on capacity constraints identify the
       nodes whose extra GiB would lift coverage the most.

For small instances (our cluster size), what-if is essentially free — each
solve takes < 1 s. The bottleneck analysis uses CBC's LP relaxation, which
PuLP exposes via a parameter override on PULP_CBC_CMD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pulp

from scheduler import evaluator, memory_model
from scheduler.solver import greedy, milp
from scheduler.types import (
    Feature, NodeSpec, Placement, Plan, Workload,
)


# ──────────────────────────────────────────────────────────────────────
# What-if
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WhatIfDiff:
    """Difference between baseline plan and a perturbed plan."""

    baseline: Plan
    perturbed: Plan
    added_placements: tuple[Placement, ...]
    removed_placements: tuple[Placement, ...]
    coverage_delta: float
    kv_qos_delta: float
    description: str


def _diff_plans(p_base: Plan, p_perturbed: Plan, description: str) -> WhatIfDiff:
    base = set(p_base.placements)
    new = set(p_perturbed.placements)
    return WhatIfDiff(
        baseline=p_base,
        perturbed=p_perturbed,
        added_placements=tuple(sorted(new - base, key=lambda p: (p.node_id, p.workload_id))),
        removed_placements=tuple(sorted(base - new, key=lambda p: (p.node_id, p.workload_id))),
        coverage_delta=p_perturbed.score.coverage - p_base.score.coverage,
        kv_qos_delta=p_perturbed.score.kv_qos - p_base.score.kv_qos,
        description=description,
    )


def what_if_add_node(
    extra_node: NodeSpec,
    *,
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    priority_order: Sequence[str],
    features: Sequence[Feature],
    activation_fit: dict | None,
    comfy_co_resident_nodes: set[str] | None,
    whisper_co_resident_nodes: set[str] | None = None,
    solver: str = "milp",
) -> WhatIfDiff:
    """Re-solve with one additional node; report the delta."""
    solve = milp.solve if solver == "milp" else greedy.solve
    base_plan = solve(
        workloads=workloads, nodes=nodes,
        priority_order=priority_order, features=features,
        activation_fit=activation_fit,
        comfy_co_resident_nodes=comfy_co_resident_nodes,
        whisper_co_resident_nodes=whisper_co_resident_nodes,
    )
    perturbed_plan = solve(
        workloads=workloads, nodes=list(nodes) + [extra_node],
        priority_order=priority_order, features=features,
        activation_fit=activation_fit,
        comfy_co_resident_nodes=comfy_co_resident_nodes,
        whisper_co_resident_nodes=whisper_co_resident_nodes,
    )
    return _diff_plans(
        base_plan, perturbed_plan,
        description=f"add node {extra_node.node_id} ({extra_node.gpu_class}, "
                    f"{extra_node.total_vram_bytes/(1024**3):.0f} GiB)",
    )


def what_if_remove_node(
    node_id: str,
    *,
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    priority_order: Sequence[str],
    features: Sequence[Feature],
    activation_fit: dict | None,
    comfy_co_resident_nodes: set[str] | None,
    whisper_co_resident_nodes: set[str] | None = None,
    solver: str = "milp",
) -> WhatIfDiff:
    """Re-solve with one node removed (simulates outage/decom)."""
    solve = milp.solve if solver == "milp" else greedy.solve
    base_plan = solve(
        workloads=workloads, nodes=nodes,
        priority_order=priority_order, features=features,
        activation_fit=activation_fit,
        comfy_co_resident_nodes=comfy_co_resident_nodes,
        whisper_co_resident_nodes=whisper_co_resident_nodes,
    )
    surviving = [n for n in nodes if n.node_id != node_id]
    surviving_comfy = (comfy_co_resident_nodes or set()) - {node_id}
    surviving_whisper = (whisper_co_resident_nodes or set()) - {node_id}
    perturbed_plan = solve(
        workloads=workloads, nodes=surviving,
        priority_order=priority_order, features=features,
        activation_fit=activation_fit,
        comfy_co_resident_nodes=surviving_comfy,
        whisper_co_resident_nodes=surviving_whisper,
    )
    return _diff_plans(
        base_plan, perturbed_plan,
        description=f"remove node {node_id} (simulated outage)",
    )


# ──────────────────────────────────────────────────────────────────────
# Bottleneck (LP relaxation shadow prices)
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CapacityShadowPrice:
    """Per-node value of one extra byte of effective capacity.

    Positive values identify nodes where adding memory would let the planner
    cover more (or higher-priority) workloads. Reported in *objective units
    per GiB* so the magnitude is comparable to the priority weights.
    """

    node_id: str
    shadow_price_per_gib: float
    binding: bool       # True iff the capacity constraint is tight at optimum


def capacity_shadow_prices(
    *,
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    priority_order: Sequence[str],
    features: Sequence[Feature],
    activation_fit: dict | None = None,
    comfy_co_resident_nodes: set[str] | None = None,
    whisper_co_resident_nodes: set[str] | None = None,
) -> list[CapacityShadowPrice]:
    """Solve the *LP relaxation* of the MILP; return dual values on capacity rows.

    PuLP loses the dual values when ``cat=LpBinary`` is enforced, so we rebuild
    the model with ``cat=LpContinuous`` here. The relaxed solution is not
    feasible as a placement, but the dual values it produces are the correct
    bottleneck signal — see Wolsey 1998 §4.2 for the theory.
    """
    from scheduler.solver.milp import _enumerate_triples

    comfy_set = comfy_co_resident_nodes or set()
    whisper_set = whisper_co_resident_nodes or set()
    node_reserve = {
        n.node_id: memory_model.node_co_resident_reserve(
            comfyui_co_resident=n.node_id in comfy_set,
            whisper_co_resident=n.node_id in whisper_set,
        )
        for n in nodes
    }
    node_by_id = {n.node_id: n for n in nodes}
    feat_map = {f.id: f for f in features}
    weights = evaluator.feature_priority_to_workload_weights(
        priority_order, feat_map, list(workloads),
    )
    target_by_workload: dict[str, int] = {}
    for f in features:
        if f.min_effective_len > 0:
            target_by_workload[f.requires_workload] = max(
                target_by_workload.get(f.requires_workload, 0), f.min_effective_len
            )

    triples = _enumerate_triples(
        workloads, nodes, activation_fit=activation_fit,
        node_reserve=node_reserve,
    )
    prob = pulp.LpProblem("PWRP_HGC_LP", pulp.LpMaximize)
    x = {
        (t.workload_id, t.config.name, t.node_id): pulp.LpVariable(
            f"x_{t.workload_id}_{t.config.name}_{t.node_id}",
            lowBound=0, upBound=1, cat=pulp.LpContinuous,
        )
        for t in triples
    }
    y = {w.id: pulp.LpVariable(f"y_{w.id}", lowBound=0, upBound=1,
                               cat=pulp.LpContinuous)
         for w in workloads}

    for w in workloads:
        for n in nodes:
            relevant = [x[(w.id, c.name, n.node_id)] for c in w.configs
                        if (w.id, c.name, n.node_id) in x]
            if relevant:
                prob += pulp.lpSum(relevant) <= 1
    for w in workloads:
        placed = [x[(w.id, c.name, n.node_id)] for c in w.configs for n in nodes
                  if (w.id, c.name, n.node_id) in x]
        if placed:
            prob += pulp.lpSum(placed) >= w.min_replicas * y[w.id]
            prob += pulp.lpSum(placed) <= w.max_replicas * y[w.id]

    cap_constraints: dict[str, pulp.LpConstraint] = {}
    triples_by_node: dict[str, list] = {}
    for t in triples:
        triples_by_node.setdefault(t.node_id, []).append(t)
    for nid, ts in triples_by_node.items():
        cap = max(0, node_by_id[nid].effective_capacity - node_reserve.get(nid, 0))
        c = pulp.LpConstraint(
            e=pulp.lpSum(t.charge * x[(t.workload_id, t.config.name, t.node_id)] for t in ts) - cap,
            sense=pulp.LpConstraintLE,
            name=f"cap_{nid}",
            rhs=0,
        )
        prob += c
        cap_constraints[nid] = prob.constraints[f"cap_{nid}"]

    prob += (1e6 * pulp.lpSum(weights[w.id] * y[w.id] for w in workloads)
             + pulp.lpSum(weights[t.workload_id]
                          * (1.0 if target_by_workload.get(t.workload_id, 0) <= 0
                             else min(1.0, t.eff_len / float(target_by_workload[t.workload_id])))
                          * x[(t.workload_id, t.config.name, t.node_id)]
                          for t in triples))

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    out: list[CapacityShadowPrice] = []
    for nid, constr in cap_constraints.items():
        # CBC + PuLP store dual values on constraint.pi
        pi = constr.pi
        # LP relaxation: binding iff slack ≈ 0
        slack = constr.slack
        binding = slack is not None and abs(slack) < 1e-3
        out.append(CapacityShadowPrice(
            node_id=nid,
            shadow_price_per_gib=float(pi or 0.0) * (1024 ** 3),
            binding=binding,
        ))
    return out
