"""``python -m scheduler {inventory,plan,apply,sensitivity,eval}``.

This is the user-facing entrypoint. Each subcommand wires together the
modules in this package; see ``docs/ALGORITHM.md`` for what they compute
and ``docs/EVALUATION.md`` for sample outputs.

Common options:
    --hosts ID=USER@HOST,ID=USER@HOST   Override the node list. Defaults
                                        to whatever the active site config
                                        declares under ``nodes:``.
    --solver greedy|milp                Choose the placement algorithm
                                        (``plan``/``sensitivity`` only).
    --priorities f1,f2,...              Feature priority list, descending.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from scheduler import (
    applier, calibration, catalog, evaluator, inventory,
    sensitivity, site as site_mod,
)
from scheduler.solver import greedy, milp
from scheduler.types import GB, NodeSpec, Placement


# ──────────────────────────────────────────────────────────────────────
# Shared option parsing
# ──────────────────────────────────────────────────────────────────────

# Default priority list = usage-frequency ranking. See catalog.DEFAULT_PRIORITIES
# for the rationale. Operators with real telemetry should override on the CLI.
_DEFAULT_PRIORITIES = ",".join(catalog.DEFAULT_PRIORITIES)


def _parse_hosts(spec: str | None) -> dict[str, str] | None:
    if not spec:
        return None
    return {kv.split("=", 1)[0]: kv.split("=", 1)[1] for kv in spec.split(",") if "=" in kv}


def _parse_priorities(spec: str) -> list[str]:
    return [s for s in spec.split(",") if s]


def _solve(solver: str, **kw):
    if solver == "milp":
        return milp.solve(**kw)
    return greedy.solve(**kw)


def _resolve_comfy_set(probes) -> set[str]:
    return {p.spec.node_id for p in probes if p.comfyui_running}


def _resolve_whisper_set(probes) -> set[str]:
    return {p.spec.node_id for p in probes if p.whisper_running}


# ──────────────────────────────────────────────────────────────────────
# Subcommands
# ──────────────────────────────────────────────────────────────────────

def _site_and_hosts(args) -> tuple[site_mod.SiteConfig, dict[str, str]]:
    """Load SiteConfig (--site or default), apply --hosts override if any,
    then validate against the catalog. Warnings → stderr, errors → SystemExit."""
    site = site_mod.load(getattr(args, "site", None))
    hosts_override = _parse_hosts(getattr(args, "hosts", None))
    if hosts_override:
        # node_usable_vram / node_gpu_tier 는 site config 의 dict-form 노드에서 파싱한
        # per-node override. --hosts 가 동일 node_id 를 그대로 쓰면 (setup.sh 가
        # NODES_VLLM csv 로 같은 id=user@host 패턴 전달) override 가 그대로 적용된다.
        # node_id 가 달라지면 매칭 안 돼 무시 — 운영자가 --hosts 로 노드 ID 를 재정의했단
        # 뜻이라 정상.
        site = site_mod.SiteConfig(
            name=site.name, nodes=hosts_override, workloads=site.workloads,
            compose=site.compose, default_ssh_user=site.default_ssh_user,
            node_usable_vram=site.node_usable_vram,
            node_gpu_tier=site.node_gpu_tier,
        )
    catalog_ids = [t.id for t in catalog.WORKLOAD_TEMPLATES]
    issues = site_mod.validate(site, catalog_ids)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    for w in warnings:
        print(f"warning: {w.message}", file=sys.stderr)
    if errors:
        for e in errors:
            print(f"error: {e.message}", file=sys.stderr)
        raise SystemExit(2)
    return site, site.nodes


def cmd_inventory(args: argparse.Namespace) -> int:
    site, _ = _site_and_hosts(args)
    probes = inventory.probe_cluster(site=site)
    print(f"site: {site.name}")
    print(f"{'node':<6} {'class':<14} {'total':>8} {'free':>8} {'alive':<6} {'comfy':<6} {'whisp':<6} containers")
    for p in probes:
        s = p.spec
        total = f"{s.total_vram_bytes/GB:.1f}G"
        free = f"{(p.vram_free_bytes or 0)/GB:.1f}G" if p.vram_free_bytes else "?"
        ctns = ",".join(rw.container_name for rw in p.running_workloads) or "-"
        print(f"{s.node_id:<6} {s.gpu_class:<14} {total:>8} {free:>8} "
              f"{str(p.alive):<6} {str(p.comfyui_running):<6} {str(p.whisper_running):<6} {ctns}")
        for e in p.raw_errors:
            print(f"  ! {e}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    site, _ = _site_and_hosts(args)
    priorities = _parse_priorities(args.priorities)
    probes = inventory.probe_cluster(site=site)
    nodes = [p.spec for p in probes if p.alive]
    if not nodes:
        print("ERROR: no live nodes after probe", file=sys.stderr)
        return 2
    workloads = catalog.build_catalog(site=site)
    features = catalog.features_for_priorities(priorities)
    fit = calibration.load()
    comfy_set = _resolve_comfy_set(probes)
    whisper_set = _resolve_whisper_set(probes)

    plan = _solve(
        args.solver,
        workloads=workloads, nodes=nodes,
        priority_order=priorities, features=features,
        activation_fit=fit, comfy_co_resident_nodes=comfy_set,
        whisper_co_resident_nodes=whisper_set,
    )
    print(f"=== plan ({args.solver}) ===")
    print(f"score: coverage={plan.score.coverage:.2f}  kv_qos={plan.score.kv_qos:.2f}  "
          f"affinity={plan.score.affinity:.2f}  "
          f"migration={plan.score.migration_cost}  imbalance={plan.score.imbalance:.4f}")
    by_node: dict[str, list] = {}
    for p in plan.placements:
        by_node.setdefault(p.node_id, []).append(p)
    for nid in sorted(by_node):
        for p in by_node[nid]:
            print(f"  [{nid}] {p.workload_id:14s}  cfg={p.config_name}")
    for note in plan.notes:
        print(f"  note: {note}")

    if args.out:
        payload = {
            "solver": plan.solver,
            "score": {
                "coverage": plan.score.coverage,
                "kv_qos": plan.score.kv_qos,
                "affinity": plan.score.affinity,
                "migration_cost": plan.score.migration_cost,
                "imbalance": plan.score.imbalance,
            },
            "placements": [
                {"workload_id": p.workload_id,
                 "config_name": p.config_name,
                 "node_id": p.node_id}
                for p in plan.placements
            ],
            "priorities": priorities,
        }
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nwrote plan to {args.out}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    site, _ = _site_and_hosts(args)
    priorities = _parse_priorities(args.priorities)
    probes = inventory.probe_cluster(site=site)
    nodes = [p.spec for p in probes if p.alive]
    if not nodes:
        print("ERROR: no live nodes after probe", file=sys.stderr)
        return 2
    workloads = catalog.build_catalog(site=site)
    features = catalog.features_for_priorities(priorities)
    fit = calibration.load()
    comfy_set = _resolve_comfy_set(probes)
    whisper_set = _resolve_whisper_set(probes)

    if args.plan:
        with open(args.plan) as f:
            payload = json.load(f)
        plan_placements = [
            Placement(p["workload_id"], p["config_name"], p["node_id"])
            for p in payload["placements"]
        ]
        # Recompute Score against current probe data — the persisted Score
        # reflects the cluster state at plan-write time and may be stale.
        from scheduler.types import Plan
        weights = evaluator.feature_priority_to_workload_weights(
            priorities, {f.id: f for f in features}, workloads,
        )
        score = evaluator.evaluate(
            plan_placements,
            workloads={w.id: w for w in workloads},
            nodes={n.node_id: n for n in nodes},
            features=features, priorities=weights,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
        )
        plan = Plan(placements=tuple(plan_placements), score=score, solver=payload["solver"])
    else:
        plan = _solve(
            args.solver,
            workloads=workloads, nodes=nodes,
            priority_order=priorities, features=features,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
        )

    # Current placement reconstructed from probes (workload presence per node).
    # config_name 은 probe 한 realized_max_len 으로 식별한다 — max_len 이 어떤 config 와
    # 유일 매칭되면 그 이름, 아니면(미serving/모호) '(probed)' 로 둬서 force-recreate 유도.
    # 이게 있어야 수렴 상태 재apply 가 no-op (불필요한 vLLM 재기동/cold-load 리셋 방지).
    workloads_by_id = {w.id: w for w in workloads}

    def _probed_config_name(wid: str, rw) -> str:
        # config grid 가 같은 max_len 에 gpu_util 만 다른 경우가 많아 둘 다로 유일 식별.
        wl = workloads_by_id.get(wid)
        if wl is None or rw.realized_max_len is None or rw.realized_gpu_util is None:
            return "(probed)"
        matches = [c.name for c in wl.configs
                   if c.max_len == rw.realized_max_len
                   and abs(c.gpu_util - rw.realized_gpu_util) < 0.005]
        return matches[0] if len(matches) == 1 else "(probed)"

    current_placements = []
    for p in probes:
        for rw in p.running_workloads:
            wid = site.workload_for_container(rw.container_name)
            if wid:
                current_placements.append(
                    Placement(wid, _probed_config_name(wid, rw), p.spec.node_id)
                )
        # systemd-managed workloads (currently image-flux/ComfyUI) — if probe
        # detected the unit's HTTP endpoint live, count it as currently placed.
        if p.comfyui_running:
            current_placements.append(Placement("image-flux", "flux-q8", p.spec.node_id))

    change = applier.compute_diff(
        target=plan, current=current_placements,
        workloads=workloads, nodes=nodes, site=site,
        local_env_path=args.local_env or None,
    )
    print(applier.render_diff(change))
    if change.empty or args.dry_run:
        return 0
    if not args.yes:
        ans = input("\nApply changes? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted.")
            return 0
    log = applier.apply(change, dry_run=False, site=site)
    for action, status in log:
        print(f"  {action.kind:18s} [{action.node_id}] {action.target}: {status}")
    return 0


def cmd_sensitivity(args: argparse.Namespace) -> int:
    site, _ = _site_and_hosts(args)
    priorities = _parse_priorities(args.priorities)
    probes = inventory.probe_cluster(site=site)
    nodes = [p.spec for p in probes if p.alive]
    workloads = catalog.build_catalog(site=site)
    features = catalog.features_for_priorities(priorities)
    fit = calibration.load()
    comfy_set = _resolve_comfy_set(probes)
    whisper_set = _resolve_whisper_set(probes)

    if args.remove_node:
        diff = sensitivity.what_if_remove_node(
            args.remove_node,
            workloads=workloads, nodes=nodes,
            priority_order=priorities, features=features,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
            solver=args.solver,
        )
        print(f"=== {diff.description} ===")
    elif args.add_node:
        # spec: "id:class:gib[:user@host]" e.g. "255:gb10:122"
        # host omitted → "{default_ssh_user}@{node_id}" verbatim.
        parts = args.add_node.split(":")
        nid, klass, gib = parts[0], parts[1], int(parts[2])
        host = parts[3] if len(parts) > 3 else f"{site.default_ssh_user}@{nid}"
        extra = NodeSpec(
            node_id=nid, hostname=host,
            gpu_class=klass, total_vram_bytes=gib * GB,
        )
        diff = sensitivity.what_if_add_node(
            extra,
            workloads=workloads, nodes=nodes,
            priority_order=priorities, features=features,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
            solver=args.solver,
        )
        print(f"=== {diff.description} ===")
    elif args.shadow_prices:
        prices = sensitivity.capacity_shadow_prices(
            workloads=workloads, nodes=nodes,
            priority_order=priorities, features=features,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
        )
        print("=== LP capacity shadow prices ===")
        for sp in prices:
            tag = "BINDING" if sp.binding else "slack"
            print(f"  [{sp.node_id}] {sp.shadow_price_per_gib:+.3e} obj/GiB  ({tag})")
        return 0
    else:
        print("specify --remove-node, --add-node, or --shadow-prices", file=sys.stderr)
        return 2

    print(f"  Δcoverage = {diff.coverage_delta:+.2f}")
    print(f"  Δkv_qos   = {diff.kv_qos_delta:+.2f}")
    for p in diff.added_placements:
        print(f"  + {p.workload_id} @ {p.node_id} cfg={p.config_name}")
    for p in diff.removed_placements:
        print(f"  - {p.workload_id} @ {p.node_id} cfg={p.config_name}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    site, _ = _site_and_hosts(args)
    priorities = _parse_priorities(args.priorities)
    probes = inventory.probe_cluster(site=site)
    nodes = [p.spec for p in probes if p.alive]
    workloads = catalog.build_catalog(site=site)
    features = catalog.features_for_priorities(priorities)
    fit = calibration.load()
    comfy_set = _resolve_comfy_set(probes)
    whisper_set = _resolve_whisper_set(probes)

    print("solver |  coverage    kv_qos  affinity  migration  imbalance")
    for name, mod in [("greedy", greedy), ("milp", milp)]:
        plan = mod.solve(
            workloads=workloads, nodes=nodes,
            priority_order=priorities, features=features,
            activation_fit=fit, comfy_co_resident_nodes=comfy_set,
            whisper_co_resident_nodes=whisper_set,
        )
        s = plan.score
        print(f"{name:6s} |  {s.coverage:8.2f}  {s.kv_qos:8.2f}  {s.affinity:8.2f}  {s.migration_cost:9d}  {s.imbalance:9.4f}")
    return 0


# ──────────────────────────────────────────────────────────────────────
# CLI scaffolding
# ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("scheduler", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--site", default=None,
                        help="path to site.yaml (default: $KLOUDCHAT_SCHEDULER_SITE or shipped kloudchat.yaml)")
        sp.add_argument("--hosts", default=None,
                        help='override site.nodes: comma-separated "id=user@host" pairs')
        sp.add_argument("--priorities", default=_DEFAULT_PRIORITIES,
                        help="comma-separated feature priority list, descending")
        sp.add_argument("--solver", choices=["greedy", "milp"], default="milp")

    sp = sub.add_parser("inventory", help="probe nodes and print cluster summary")
    sp.add_argument("--site", default=None)
    sp.add_argument("--hosts", default=None)
    sp.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("plan", help="compute a placement plan")
    common(sp)
    sp.add_argument("--out", help="write plan JSON to this path")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("apply", help="diff current vs target plan, optionally apply")
    common(sp)
    sp.add_argument("--plan", help="apply a previously written plan JSON instead of resolving")
    sp.add_argument("--dry-run", action="store_true",
                    help="show diff and exit without applying (no prompt). Without --dry-run and without --yes, prompts before applying.")
    sp.add_argument("-y", "--yes", action="store_true",
                    help="skip confirmation prompt")
    sp.add_argument("--local-env", default=".env",
                    help="orchestrator-side .env path where VLLM_*_URL csvs "
                         "live (default: ./.env). Set to '' to skip local-env "
                         "updates entirely.")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("sensitivity", help="what-if and shadow-price analyses")
    common(sp)
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--remove-node", help="simulate outage of this node id")
    g.add_argument("--add-node",
                   help="simulate adding a node, spec=id:class:gib (e.g. 255:gb10:122)")
    g.add_argument("--shadow-prices", action="store_true",
                   help="LP shadow prices on node capacities")
    sp.set_defaults(func=cmd_sensitivity)

    sp = sub.add_parser("eval", help="compare greedy vs milp on the live cluster")
    common(sp)
    sp.set_defaults(func=cmd_eval)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
