"""Site-specific deployment bindings.

The scheduler's *algorithm* (memory model, KV model, solvers, evaluator) is
cluster-agnostic. The *bindings* — which container name corresponds to a
workload, which port it listens on, which env-var prefix to write, where
the model checkpoint lives on each node, which directory holds
``docker-compose.vllm.yml`` — are not. We isolate those into a SiteConfig
loaded from YAML, so deploying the scheduler against a new cluster is a
matter of writing a new site file rather than editing Python.

A SiteConfig has three sections:

  ``nodes``         : node_id → ssh ``user@host`` for inventory probes.
  ``workloads``     : workload_id → WorkloadBinding describing the concrete
                      container/service the catalog workload corresponds to.
  ``compose``       : per-node compose working dir, env file, vllm-compose
                      filename, api_base URL template.

The shipped sites are at ``scheduler/sites/`` — ``kloudchat.yaml`` is the
production binding (gitignored), created by copying ``kloudchat.yaml.example``
and filling it in (``load()`` 가 이 .example 을 hint 로 안내). ``example.yaml``
= 다른 클러스터용 범용 참고 예시.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml


_DEFAULT_SITE_FILE = "kloudchat.yaml"


@dataclass(frozen=True)
class WorkloadBinding:
    """How one catalog workload realizes on this site.

    For vLLM workloads (``container`` set), the applier issues
    ``docker compose stop/up`` against the named service in the configured
    compose file. For ComfyUI / native workloads (``systemd_unit`` set), it
    uses ``systemctl`` instead.

    ``model_path`` is the directory the vLLM container mounts (host-side
    absolute path). The metadata loader fetches ``<model_path>/config.json``
    over ssh when the local cache misses.
    """

    container: Optional[str] = None       # docker compose service name
    port: Optional[int] = None            # host-side port (vLLM /v1/, ComfyUI 8188)
    env_prefix: Optional[str] = None      # VLLM_GEMMA26_{MAX_LEN,GPU_UTIL,…}
    model_path: Optional[str] = None      # /var/lib/vllm/models/gemma-4-26b
    systemd_unit: Optional[str] = None    # comfyui.service
    litellm_model_name: Optional[str] = None    # local/gemma-4-26b — only field
                                                # consumed at runtime (applier
                                                # uses it as the gate for
                                                # LiteLLM-routed workloads)


@dataclass(frozen=True)
class ComposeConfig:
    """Where compose files and .env live on every worker node."""

    working_dir: str = "/opt/cluster"
    vllm_compose_file: str = "docker-compose.vllm.yml"
    env_file: str = ".env"               # relative to working_dir


@dataclass(frozen=True)
class SiteConfig:
    """All site-specific configuration in one place."""

    name: str
    nodes: dict[str, str] = field(default_factory=dict)
    workloads: dict[str, WorkloadBinding] = field(default_factory=dict)
    compose: ComposeConfig = field(default_factory=ComposeConfig)
    default_ssh_user: str = "root"
    # node_id → usable_vram_bytes override (omit for nvidia-smi auto-detect).
    # Set on unified-memory nodes to cap planner VRAM below physical so OS / page cache
    # / co-resident services get a guaranteed reserve. See NodeSpec.planner_vram_bytes.
    node_usable_vram: dict[str, int] = field(default_factory=dict)
    # node_id → gpu_tier override (omit to use types.GPU_TIER table lookup).
    # Lets operators bump or demote a specific node in the affinity ranking
    # without code changes (e.g. a particular PRO5000 that should rank
    # alongside the PRO6000s in this cluster). See NodeSpec.gpu_tier.
    node_gpu_tier: dict[str, int] = field(default_factory=dict)

    def host_no_user(self, node_id: str) -> str:
        """Strip the ``user@`` prefix from a node host. Used for the litellm
        ``api_base`` template, which addresses vLLM directly by hostname/IP."""
        h = self.nodes[node_id]
        return h.split("@", 1)[1] if "@" in h else h

    def usable_vram_bytes_for(self, node_id: str) -> Optional[int]:
        """Per-node override of planner-visible VRAM (None = inherit physical)."""
        return self.node_usable_vram.get(node_id)

    def gpu_tier_for(self, node_id: str) -> Optional[int]:
        """Per-node override of GPU affinity tier (None = lookup GPU_TIER table by gpu_class)."""
        return self.node_gpu_tier.get(node_id)

    def vllm_ports(self) -> dict[str, int]:
        """container_name → port, for inventory probes."""
        return {b.container: b.port
                for b in self.workloads.values()
                if b.container and b.port}

    def workload_for_container(self, container_name: str) -> Optional[str]:
        for wid, b in self.workloads.items():
            if b.container == container_name:
                return wid
        return None


# ──────────────────────────────────────────────────────────────────────
# Loading
# ──────────────────────────────────────────────────────────────────────

def _expand_path(p: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(p))))


def load(path: Optional[str | os.PathLike] = None) -> SiteConfig:
    """Load a SiteConfig.

    Resolution order:
      1. Explicit argument.
      2. ``KLOUDCHAT_SCHEDULER_SITE`` environment variable.
      3. ``~/.config/kloudchat-scheduler/site.yaml`` (per-user override).
      4. ``scheduler/sites/kloudchat.yaml`` (deployment-local, gitignored —
         created by copying ``kloudchat.yaml.example`` and filling in the
         placeholders).

    The first existing file wins. ``kloudchat.yaml.example`` is *never* loaded
    directly: it ships with placeholder hosts that won't resolve, so falling
    back to it would mask the missing-config error. Instead we raise a clear
    message telling the operator to copy it.

    Missing keys fall back to dataclass defaults — so a minimal site file
    only needs to override what differs from the dataclass shape.
    """
    candidate_paths: list[Path] = []
    if path:
        candidate_paths.append(_expand_path(path))
    env_path = os.environ.get("KLOUDCHAT_SCHEDULER_SITE")
    if env_path:
        candidate_paths.append(_expand_path(env_path))
    candidate_paths.extend([
        _expand_path("~/.config/kloudchat-scheduler/site.yaml"),
        Path(__file__).parent / "sites" / _DEFAULT_SITE_FILE,
    ])
    for p in candidate_paths:
        if p.is_file():
            with p.open() as f:
                raw = yaml.safe_load(f) or {}
            return _from_dict(raw, source=str(p))
    example = Path(__file__).parent / "sites" / f"{_DEFAULT_SITE_FILE}.example"
    hint = (f"\n  Hint: copy {example} → "
            f"{candidate_paths[-1]} and fill in nodes / default_ssh_user / "
            f"compose.working_dir.") if example.is_file() else ""
    raise FileNotFoundError(
        f"no SiteConfig found; tried {[str(p) for p in candidate_paths]}.{hint}"
    )


def _from_dict(d: dict, *, source: str = "<inline>") -> SiteConfig:
    name = str(d.get("name") or Path(source).stem)
    # 노드 항목 = string (host) 또는 dict ({host, usable_vram_gb}) 둘 다 허용.
    # dict 형 = unified-memory 노드에서 planner ceiling 을 physical 보다 낮추는 용도.
    raw_nodes = d.get("nodes") or {}
    nodes: dict[str, str] = {}
    node_usable_vram: dict[str, int] = {}
    node_gpu_tier: dict[str, int] = {}
    for nid, val in raw_nodes.items():
        if isinstance(val, dict):
            host = val.get("host")
            if not isinstance(host, str) or not host:
                raise ValueError(f"site {source}: node {nid!r} dict form requires 'host'")
            nodes[nid] = host
            ugb = val.get("usable_vram_gb")
            if ugb is not None:
                if not isinstance(ugb, (int, float)) or ugb <= 0:
                    raise ValueError(f"site {source}: node {nid!r} usable_vram_gb must be positive number")
                node_usable_vram[nid] = int(ugb * (1024 ** 3))
            tier = val.get("gpu_tier")
            if tier is not None:
                if not isinstance(tier, int) or tier < 0:
                    raise ValueError(f"site {source}: node {nid!r} gpu_tier must be non-negative int")
                node_gpu_tier[nid] = tier
        else:
            nodes[nid] = str(val)
    # litellm_route / litellm_mode are binding keys the loader ignores: the
    # LiteLLM config is generated by scripts/gen-litellm-config.sh from .env,
    # so only litellm_model_name is consulted here. Dropped silently so site
    # yaml files carrying these keys keep loading.
    _ignored_binding_keys = {"litellm_route", "litellm_mode"}
    workloads = {
        wid: WorkloadBinding(**{k: v for k, v in (b or {}).items()
                                if k not in _ignored_binding_keys})
        for wid, b in (d.get("workloads") or {}).items()
    }
    compose_raw = d.get("compose") or {}
    compose = ComposeConfig(
        working_dir=compose_raw.get("working_dir", "/opt/cluster"),
        vllm_compose_file=compose_raw.get("vllm_compose_file", "docker-compose.vllm.yml"),
        env_file=compose_raw.get("env_file", ".env"),
    )
    default_user = d.get("default_ssh_user") or "root"
    return SiteConfig(
        name=name, nodes=nodes, workloads=workloads,
        compose=compose, default_ssh_user=default_user,
        node_usable_vram=node_usable_vram,
        node_gpu_tier=node_gpu_tier,
    )


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationIssue:
    severity: str       # "error" | "warning"
    message: str


def validate(
    site: SiteConfig,
    workload_ids: Iterable[str],
) -> list[ValidationIssue]:
    """Sanity-check a SiteConfig against the catalog.

    Warns when catalog has workloads with no binding (the planner will be
    unable to place them on this site). Errors on bindings whose
    ``container`` is set without a ``port`` (inventory probe will fail).
    """
    issues: list[ValidationIssue] = []
    catalog_ids = set(workload_ids)
    site_ids = set(site.workloads)
    for missing in catalog_ids - site_ids:
        issues.append(ValidationIssue(
            severity="warning",
            message=f"workload {missing!r} has no binding on site "
                    f"{site.name!r} — planner will not place it",
        ))
    for spurious in site_ids - catalog_ids:
        issues.append(ValidationIssue(
            severity="warning",
            message=f"site {site.name!r} binds {spurious!r} but no such "
                    f"catalog workload exists",
        ))
    for wid, b in site.workloads.items():
        if b.container and not b.port:
            issues.append(ValidationIssue(
                severity="error",
                message=f"binding {wid!r}: container={b.container!r} has no port",
            ))
    return issues
