"""Turn a Plan into concrete cluster mutations.

Each Plan implies three classes of changes:

    (a) per-node *.env overrides for vLLM containers (VLLM_GEMMA26_MAX_LEN,
        VLLM_GEMMA26_GPU_UTIL, …) — picked up by ``docker compose up -d
        --force-recreate <service>``.
    (b) per-node systemd state for ``comfyui.service`` — enable/start or
        stop/disable.
    (c) per-node docker compose service set (stop services whose workloads
        the planner dropped from that node; start services for workloads
        newly assigned).

litellm-config.yaml is owned by ``scripts/gen-litellm-config.sh`` (env-driven,
covers vLLM + OpenRouter + super-agent in one place). The scheduler
intentionally does not write to it — placement decisions land in per-node
``.env`` overrides and container actions, and the shell generator picks the
results up on the next ``setup.sh`` / ``gen-litellm-config.sh`` run.

We produce a *diff* (`ChangePlan`) first; the CLI gates ``--apply`` behind
explicit user confirmation, so dry-runs are the default surface.

Idempotence: applying twice does nothing on the second invocation, which
matters when the operator wants to re-converge after a node reboot. We test
this by re-probing after apply and asserting the diff is empty.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from scheduler.site import SiteConfig
from scheduler.types import (
    Config, NodeSpec, Placement, Plan, Workload,
)


@dataclass(frozen=True)
class NodeAction:
    """One concrete mutation scheduled for a specific node."""

    node_id: str
    host: str
    kind: str           # "env_update" | "container_recreate" | "container_stop"
                        # | "systemd_enable" | "systemd_disable"
    target: str         # container name, systemd unit, env-file path, …
    detail: str         # human-readable summary for the diff output
    command: str = ""   # the shell command we'd run (empty for env_update)


@dataclass(frozen=True)
class ChangePlan:
    """Diff between current cluster state and a target Plan.

    Four buckets:
      - ``env_updates``: per-container vLLM tuning (MAX_LEN, GPU_UTIL) written
        to each remote node's ``.env``.
      - ``orchestrator_env_updates``: ``VLLM_*_URL`` csvs written to the
        orchestrator's local ``.env`` so ``gen-litellm-config.sh`` and the
        local LiteLLM container route to the actually-placed deployments.
      - ``container_actions`` / ``systemd_actions``: lifecycle ops on remote
        nodes.

    ``empty`` is True when nothing needs to change — the convergence check.
    """

    env_updates: tuple[NodeAction, ...] = ()
    orchestrator_env_updates: tuple[NodeAction, ...] = ()
    container_actions: tuple[NodeAction, ...] = ()
    systemd_actions: tuple[NodeAction, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def empty(self) -> bool:
        return not any((self.env_updates, self.orchestrator_env_updates,
                        self.container_actions, self.systemd_actions))


# ──────────────────────────────────────────────────────────────────────
# Diff computation
# ──────────────────────────────────────────────────────────────────────

def _placements_per_node(P: Iterable[Placement]) -> dict[str, list[Placement]]:
    out: dict[str, list[Placement]] = {}
    for p in P:
        out.setdefault(p.node_id, []).append(p)
    return out


def _config_lookup(workloads: Sequence[Workload], wid: str, cname: str) -> Optional[Config]:
    w = next((w for w in workloads if w.id == wid), None)
    if not w:
        return None
    return next((c for c in w.configs if c.name == cname), None)


# Sidecar URL env-var mapping. Workloads without ``env_prefix`` (systemd
# 류 — ComfyUI) still expose a host URL that sidecar shims (comfyui-shim
# 등) probe. Maps workload_id → orchestrator .env var. Empty placements →
# empty csv → ``setup.sh librechat`` stops the corresponding shim.
_SIDECAR_URL_VARS: dict[str, str] = {
    "image-flux": "COMFYUI_URLS",
}


def _orchestrator_url_csvs(
    target: Plan, site: SiteConfig,
) -> dict[str, str]:
    """Aggregate placement → URL csv map for the orchestrator's local ``.env``.

    Two flavours:

    1. **LiteLLM-routed vLLM workloads** — env-var name is ``f"{env_prefix}_URL"``
       (e.g. ``VLLM_GEMMA26_URL``). Read by ``gen-litellm-config.sh`` to populate
       the AUTOGEN block. Requires ``env_prefix`` + ``port`` + ``litellm_model_name``.

    2. **Sidecar workloads** — env-var name comes from ``_SIDECAR_URL_VARS``
       (e.g. ``image-flux`` → ``COMFYUI_URLS``). Read by sidecar shims; empty
       value tells ``setup.sh`` to stop the shim. Requires ``port``.

    URLs dedup'd and sorted for determinism. Empty placement → empty csv (the
    diff step then writes the cleared value), never a stale URL."""
    eligible: dict[str, set[str]] = {}
    var_for: dict[str, str] = {}
    for wid, b in site.workloads.items():
        if b.env_prefix and b.port and b.litellm_model_name:
            var = f"{b.env_prefix}_URL"
        elif wid in _SIDECAR_URL_VARS and b.port:
            var = _SIDECAR_URL_VARS[wid]
        else:
            continue
        var_for[wid] = var
        eligible[var] = set()
    for p in target.placements:
        var = var_for.get(p.workload_id)
        b = site.workloads.get(p.workload_id)
        if var is None or b is None or not b.port:
            continue
        eligible[var].add(f"http://{site.host_no_user(p.node_id)}:{b.port}")
    return {k: ",".join(sorted(v)) for k, v in eligible.items()}


def _read_env_keys(path: str, keys: Iterable[str]) -> dict[str, str]:
    """Return current values of ``keys`` in ``path``. Missing keys map to ''."""
    out = {k: "" for k in keys}
    try:
        with open(path) as f:
            for line in f:
                line = line.rstrip("\n")
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                k, _, v = line.partition("=")
                if k in out:
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def compute_diff(
    *,
    target: Plan,
    current: Sequence[Placement],
    workloads: Sequence[Workload],
    nodes: Sequence[NodeSpec],
    site: SiteConfig,
    local_env_path: str | None = None,
) -> ChangePlan:
    """Build a ChangePlan moving ``current`` → ``target``.

    ``local_env_path`` is the orchestrator's project ``.env`` (where
    ``VLLM_*_URL`` csvs live). When provided, the diff includes any URL csv
    updates needed to align LiteLLM routing with the target placement.
    When None, the orchestrator-env step is skipped (use this from tests
    or read-only callers)."""
    nodes_by_id = {n.node_id: n for n in nodes}
    target_by_node = _placements_per_node(target.placements)
    current_by_node = _placements_per_node(current)

    # Build vllm-workload-id → (container, env_prefix) and systemd-workload-id
    # → unit_name maps off the SiteConfig. Catalog workloads with no binding
    # on this site are silently skipped — site.validate() warns about those.
    vllm_bindings: dict[str, tuple[str, str]] = {}     # wid → (container, env_prefix)
    systemd_bindings: dict[str, str] = {}              # wid → unit_name
    for wid, b in site.workloads.items():
        if b.container and b.env_prefix:
            vllm_bindings[wid] = (b.container, b.env_prefix)
        elif b.systemd_unit:
            systemd_bindings[wid] = b.systemd_unit

    compose_cd = (f"cd {shlex.quote(site.compose.working_dir)} && "
                  f"docker compose -f {shlex.quote(site.compose.vllm_compose_file)}")
    env_target_label = f"{site.compose.working_dir}/{site.compose.env_file}"

    env_updates: list[NodeAction] = []
    container_actions: list[NodeAction] = []
    systemd_actions: list[NodeAction] = []
    notes: list[str] = []

    all_node_ids = sorted(set(target_by_node) | set(current_by_node))
    for nid in all_node_ids:
        node = nodes_by_id.get(nid)
        host = node.hostname if node else site.nodes.get(nid, nid)
        cur_set = {(p.workload_id, p.config_name) for p in current_by_node.get(nid, [])}
        tgt_set = {(p.workload_id, p.config_name) for p in target_by_node.get(nid, [])}

        cur_workloads = {wid for wid, _ in cur_set}
        tgt_workloads = {wid for wid, _ in tgt_set}

        to_stop_vllm = (cur_workloads - tgt_workloads) & set(vllm_bindings)
        to_start_vllm = (tgt_workloads - cur_workloads) & set(vllm_bindings)
        unchanged_workloads = cur_workloads & tgt_workloads & set(vllm_bindings)

        cur_cfgs = {wid: cname for wid, cname in cur_set}
        tgt_cfgs = {wid: cname for wid, cname in tgt_set}
        reconfig = {w for w in unchanged_workloads if cur_cfgs[w] != tgt_cfgs[w]}

        env_lines: list[str] = []
        for wid in sorted(tgt_workloads & set(vllm_bindings)):
            cfg = _config_lookup(workloads, wid, tgt_cfgs[wid])
            if cfg is None:
                continue
            _, prefix = vllm_bindings[wid]
            env_lines.append(f"{prefix}_MAX_LEN={cfg.max_len}")
            # cfg.gpu_util 은 vLLM 의 --gpu-memory-utilization 의미 (physical VRAM
            # fraction) 그대로 — 한 vLLM 이 weights + KV + activation + cudagraph 를
            # 다 담아야 하는 단위라 임의로 rescale 하면 KV/activation 자리 사라져
            # startup 시 _initialize_kv_caches OOM. 노드별 usable_vram cap 의 효과는
            # solver 의 sum(commitments) ≤ planner_vram 제약으로만 발현된다 (여러
            # vLLM 의 합산 reserve 가 ceiling 안에 들도록 가벼운 config 자동 선택).
            env_lines.append(f"{prefix}_GPU_UTIL={cfg.gpu_util:.2f}")
        if env_lines:
            env_updates.append(NodeAction(
                node_id=nid, host=host, kind="env_update",
                target=env_target_label,
                detail=" ; ".join(env_lines),
            ))

        for wid in sorted(to_stop_vllm):
            c, _ = vllm_bindings[wid]
            container_actions.append(NodeAction(
                node_id=nid, host=host, kind="container_stop",
                target=c,
                detail=f"remove {wid} from {nid}",
                command=f"{compose_cd} stop {c} && {compose_cd} rm -f {c}",
            ))
        for wid in sorted(to_start_vllm | reconfig):
            c, _ = vllm_bindings[wid]
            action = "start" if wid in to_start_vllm else "recreate"
            container_actions.append(NodeAction(
                node_id=nid, host=host, kind="container_recreate",
                target=c,
                detail=f"{action} {wid} on {nid} → cfg={tgt_cfgs[wid]}",
                command=f"{compose_cd} up -d --force-recreate {c}",
            ))

        # systemd-managed workloads (currently: image-flux → comfyui.service)
        for wid, unit in systemd_bindings.items():
            cur_has = wid in cur_workloads
            tgt_has = wid in tgt_workloads
            if tgt_has and not cur_has:
                systemd_actions.append(NodeAction(
                    node_id=nid, host=host, kind="systemd_enable",
                    target=unit,
                    detail=f"enable + start {unit} on {nid}",
                    command=f"sudo systemctl enable --now {unit}",
                ))
            elif cur_has and not tgt_has:
                systemd_actions.append(NodeAction(
                    node_id=nid, host=host, kind="systemd_disable",
                    target=unit,
                    detail=f"stop + disable {unit} on {nid}",
                    command=f"sudo systemctl disable --now {unit}",
                ))

    orchestrator_env_updates: list[NodeAction] = []
    if local_env_path:
        desired = _orchestrator_url_csvs(target, site)
        current_values = _read_env_keys(local_env_path, desired.keys())
        changed = {k: v for k, v in desired.items() if v != current_values.get(k, "")}
        if changed:
            detail = " ; ".join(f"{k}={v}" for k, v in sorted(changed.items()))
            orchestrator_env_updates.append(NodeAction(
                node_id="orchestrator", host="localhost",
                kind="orchestrator_env_update",
                target=local_env_path,
                detail=detail,
            ))

    return ChangePlan(
        env_updates=tuple(env_updates),
        orchestrator_env_updates=tuple(orchestrator_env_updates),
        container_actions=tuple(container_actions),
        systemd_actions=tuple(systemd_actions),
        notes=tuple(notes),
    )


# ──────────────────────────────────────────────────────────────────────
# Apply
# ──────────────────────────────────────────────────────────────────────

def render_diff(change: ChangePlan) -> str:
    """Human-readable rendering used by the CLI for confirmation."""
    if change.empty:
        return "Cluster already converged — no changes needed."
    out: list[str] = []
    if change.orchestrator_env_updates:
        out.append("Orchestrator .env (LiteLLM routing URLs):")
        for a in change.orchestrator_env_updates:
            out.append(f"  [{a.target}] {a.detail}")
    if change.env_updates:
        out.append("Env overrides:")
        for a in change.env_updates:
            out.append(f"  [{a.node_id}] {a.detail}")
    if change.container_actions:
        out.append("Container actions:")
        for a in change.container_actions:
            out.append(f"  [{a.node_id}] {a.kind:18s} {a.target:18s} — {a.detail}")
    if change.systemd_actions:
        out.append("Systemd actions:")
        for a in change.systemd_actions:
            out.append(f"  [{a.node_id}] {a.kind:18s} {a.target:18s} — {a.detail}")
    out.append(
        "Note: litellm-config.yaml is regenerated by scripts/gen-litellm-config.sh "
        "after this converges — re-run setup.sh or that script to splice in the "
        "updated deployment list."
    )
    return "\n".join(out)


def _ssh_exec(host: str, command: str, *, timeout: int = 30) -> tuple[int, str, str]:
    """Run a shell command on ``host`` via ssh; return (rc, out, err)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", "LogLevel=ERROR",
             host, command],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def _write_env_overrides(host: str, env_path: str, lines: Iterable[str]) -> tuple[int, str]:
    """Append/update env-var lines in the remote .env file.

    Idempotent: existing keys are overwritten in place, new keys appended.
    """
    cmd_parts: list[str] = []
    cmd_parts.append(f"touch {shlex.quote(env_path)}")
    for line in lines:
        if "=" not in line:
            continue
        key = line.partition("=")[0]
        # remove any existing line then append
        cmd_parts.append(
            f"sed -i '/^{re.escape(key)}=/d' {shlex.quote(env_path)} && "
            f"echo {shlex.quote(line)} >> {shlex.quote(env_path)}"
        )
    cmd = " && ".join(cmd_parts)
    rc, out, err = _ssh_exec(host, cmd)
    return rc, (out or err)


def _write_local_env_overrides(env_path: str, lines: Iterable[str]) -> tuple[int, str]:
    """In-place rewrite of the orchestrator's local .env.

    Same idempotence contract as ``_write_env_overrides`` (overwrite if key
    present, append otherwise) but without ssh — used only for the
    orchestrator's project ``.env`` where ``VLLM_*_URL`` csvs live."""
    try:
        try:
            with open(env_path) as f:
                existing = f.read().splitlines()
        except FileNotFoundError:
            existing = []
        # Build the desired map; preserve the order of ``existing`` for keys
        # already present, append new keys at end.
        updates: dict[str, str] = {}
        for line in lines:
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            updates[k] = v
        seen: set[str] = set()
        out: list[str] = []
        for line in existing:
            stripped = line.lstrip()
            if "=" in line and not stripped.startswith("#"):
                k = line.partition("=")[0]
                if k in updates:
                    out.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            out.append(line)
        for k, v in updates.items():
            if k not in seen:
                out.append(f"{k}={v}")
        with open(env_path, "w") as f:
            f.write("\n".join(out))
            if out and not out[-1].endswith("\n"):
                f.write("\n")
        return 0, ""
    except OSError as e:
        return 1, str(e)


def apply(
    change: ChangePlan,
    *,
    dry_run: bool = True,
    env_path: str | None = None,
    site: SiteConfig | None = None,
) -> list[tuple[NodeAction, str]]:
    """Execute the diff. Returns a per-action (action, outcome) log.

    In dry-run mode we only return what *would* run; in apply mode we ssh
    and execute. The applier is intentionally simple — orchestration
    (parallel ssh, retry on transient failure) can wrap this later.
    """
    log: list[tuple[NodeAction, str]] = []

    if env_path is None:
        if site is None:
            raise ValueError("apply() needs env_path or site argument")
        env_path = f"{site.compose.working_dir}/{site.compose.env_file}"

    # Orchestrator-side .env first — these are local file writes (no ssh),
    # so they're cheap and let any subsequent gen-litellm-config.sh re-run
    # observe the new URL csvs immediately.
    for a in change.orchestrator_env_updates:
        if dry_run:
            log.append((a, f"DRY: would write env to {a.target}"))
            continue
        rc, msg = _write_local_env_overrides(a.target, a.detail.split(" ; "))
        log.append((a, "ok" if rc == 0 else f"FAIL rc={rc}: {msg.strip()}"))

    # Per-node env updates (so container_recreate picks up new vars)
    for a in change.env_updates:
        if dry_run:
            log.append((a, f"DRY: would write env to {a.host}:{env_path}"))
            continue
        rc, msg = _write_env_overrides(a.host, env_path, a.detail.split(" ; "))
        log.append((a, "ok" if rc == 0 else f"FAIL rc={rc}: {msg.strip()}"))

    # systemd actions (stop ComfyUI before container_recreate to free memory)
    for a in (x for x in change.systemd_actions if x.kind == "systemd_disable"):
        log.append((a, _exec_or_dry(a, dry_run)))

    # container stop/recreate
    for a in change.container_actions:
        log.append((a, _exec_or_dry(a, dry_run)))

    # systemd_enable last (start ComfyUI after vLLM containers settle)
    for a in (x for x in change.systemd_actions if x.kind == "systemd_enable"):
        log.append((a, _exec_or_dry(a, dry_run)))

    return log


def _exec_or_dry(action: NodeAction, dry_run: bool) -> str:
    if dry_run:
        return f"DRY: would run on {action.host}: {action.command}"
    rc, out, err = _ssh_exec(action.host, action.command, timeout=600)
    if rc == 0:
        return "ok"
    return f"FAIL rc={rc}: {(out or err).strip()[:200]}"
