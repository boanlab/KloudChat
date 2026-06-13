"""Probe-based discovery of compute nodes.

Inventory is intentionally lightweight — we want a NodeSpec list that the
planner can consume, plus enough runtime signal (currently-running workloads,
realized num_gpu_blocks per vLLM) to feed the calibration step.

Probe strategy per node (each step degrades gracefully):

    1. ssh <host> docker ps --format ...    → currently running workloads
    2. ssh <host> 'curl -s :8188/system_stats'
       (ComfyUI exposes vram_free in unified-memory bytes, which is what
        we actually need; nvidia-smi reports [N/A] on GB10)
    3. ssh <host> 'curl -s :800X/metrics'
       (vLLM /metrics gives num_gpu_blocks for KV calibration)

A node that fails every probe step contributes a NodeSpec with capacity 0 and
``alive=False`` so the planner can still exclude it deterministically.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from scheduler.site import SiteConfig
from scheduler.types import GB, NodeSpec


# ComfyUI port is conventionally 8188 across distributions; the site config
# can override via the image-flux binding's ``port`` field.
_DEFAULT_COMFYUI_PORT: int = 8188

# Whisper port: install-whisper.sh default. Same site-binding override pattern.
_DEFAULT_WHISPER_PORT: int = 9000


def _comfyui_port(site: Optional[SiteConfig]) -> int:
    if site is None:
        return _DEFAULT_COMFYUI_PORT
    b = site.workloads.get("image-flux")
    return int(b.port) if b and b.port else _DEFAULT_COMFYUI_PORT


def _whisper_port(_site: Optional[SiteConfig]) -> int:
    # whisper = catalog 에 workload template 없음 → site.workloads override 경로 부재.
    # 호출부 일관성 위해 인자만 받고 무시 (`_` prefix 로 lint 회피).
    return _DEFAULT_WHISPER_PORT


@dataclass(frozen=True)
class RunningWorkload:
    """A single (container or systemd) workload observed live on a node.

    For vLLM containers we also try to grab ``num_gpu_blocks`` so that the
    calibration step has empirical data to fit the analytic model against.
    """

    container_name: str
    num_gpu_blocks: Optional[int] = None
    block_size: Optional[int] = None
    realized_max_len: Optional[int] = None
    realized_gpu_util: Optional[float] = None


@dataclass(frozen=True)
class NodeProbe:
    """Combined probe result. The NodeSpec is the planner-facing summary;
    everything else is for calibration / sensitivity / diagnostics."""

    spec: NodeSpec
    alive: bool
    running_workloads: tuple[RunningWorkload, ...] = field(default_factory=tuple)
    comfyui_running: bool = False
    whisper_running: bool = False
    vram_free_bytes: Optional[int] = None     # from /system_stats
    raw_errors: tuple[str, ...] = field(default_factory=tuple)


def _ssh(host: str, cmd: str, *, timeout: int = 6) -> tuple[int, str, str]:
    """One-shot ssh. Returns (rc, stdout, stderr). Never raises."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             "-o", f"ConnectTimeout={timeout}",
             "-o", "LogLevel=ERROR",
             host, cmd],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as e:
        return 1, "", str(e)


def _parse_metrics_for_kv(metrics_text: str) -> tuple[Optional[int], Optional[int]]:
    """Extract (num_gpu_blocks, block_size) from a vLLM /metrics scrape.

    The relevant line looks like:
        vllm:cache_config_info{... block_size="16" ... num_gpu_blocks="1628" ...} 1.0
    """
    blocks: Optional[int] = None
    bsz: Optional[int] = None
    for line in metrics_text.splitlines():
        if not line.startswith("vllm:cache_config_info"):
            continue
        # parse the brace block of label=value pairs
        try:
            inside = line[line.index("{") + 1: line.rindex("}")]
        except ValueError:
            continue
        for kv in inside.split(","):
            if "=" not in kv:
                continue
            k, _, v = kv.partition("=")
            v = v.strip().strip('"')
            if k == "num_gpu_blocks" and v.isdigit():
                blocks = int(v)
            elif k == "block_size" and v.isdigit():
                bsz = int(v)
        break
    return blocks, bsz


def _probe_vllm(host: str, container: str, port: int) -> RunningWorkload:
    """Hit a single vLLM's /metrics; build a RunningWorkload.

    On scrape failure we still return a RunningWorkload so the caller has a
    record that the container exists — only the calibration fields are absent.
    """
    # realized config (max_len, gpu_util) = docker inspect Args 에서 — /metrics 미기동
    # (로딩 중)이어도 create 시점 인자라 read 가능. applier 가 현재 config 식별 → 수렴 시
    # force-recreate 회피.
    rml, rgu = _probe_vllm_config(host, container)
    rc, out, _ = _ssh(host, f"curl -fsS http://localhost:{port}/metrics")
    if rc != 0 or not out:
        return RunningWorkload(
            container_name=container, realized_max_len=rml, realized_gpu_util=rgu,
        )
    blocks, bsz = _parse_metrics_for_kv(out)
    return RunningWorkload(
        container_name=container,
        num_gpu_blocks=blocks,
        block_size=bsz,
        realized_max_len=rml,
        realized_gpu_util=rgu,
    )


def _probe_vllm_config(host: str, container: str) -> tuple[Optional[int], Optional[float]]:
    """실행 중 컨테이너의 (--max-model-len, --gpu-memory-utilization) 을 docker inspect
    Args 에서 read. config grid 가 같은 max_len 에 gpu_util 만 다른 경우 多(예: 16K@0.45
    vs 16K@0.55) → 둘 다 있어야 config 유일 식별. 로딩 중에도 read 가능(create 시점 고정).
    실패 시 (None, None) → caller 가 '(probed)' 폴백."""
    rc, out, _ = _ssh(host, f"sudo -n docker inspect {container} --format '{{{{json .Args}}}}'")
    if rc != 0 or not out:
        return None, None
    try:
        args = json.loads(out)
    except json.JSONDecodeError:
        return None, None
    max_len: Optional[int] = None
    gpu_util: Optional[float] = None
    for i, a in enumerate(args):
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a == "--max-model-len" and str(nxt).isdigit():
            max_len = int(nxt)
        elif a == "--gpu-memory-utilization":
            try:
                gpu_util = float(nxt)
            except (TypeError, ValueError):
                pass
    return max_len, gpu_util


def _probe_comfyui(host: str, site: Optional[SiteConfig] = None) -> tuple[bool, Optional[int]]:
    """Return (comfyui_running, vram_free_bytes_or_None)."""
    rc, out, _ = _ssh(host, f"curl -fsS http://localhost:{_comfyui_port(site)}/system_stats")
    if rc != 0 or not out:
        return False, None
    try:
        devices = json.loads(out).get("devices") or []
        if devices:
            return True, int(devices[0].get("vram_free") or 0)
    except (json.JSONDecodeError, ValueError):
        pass
    return True, None  # comfyui responded but parsing failed


def _probe_whisper(host: str, site: Optional[SiteConfig] = None) -> bool:
    """True iff whisper systemd unit is responding on /health.

    Note: install-whisper.sh uses lazy model load — /health returns OK before
    the large-v3 weights are pulled into VRAM. We still treat the unit as
    "co-resident" because the model WILL load on first transcribe and stay
    resident (default WHISPER_COMPUTE_TYPE=int8_float16, ~3 GiB).
    """
    rc, _, _ = _ssh(host, f"curl -fsS -o /dev/null http://localhost:{_whisper_port(site)}/health")
    return rc == 0


def _probe_running_containers(host: str) -> set[str]:
    rc, out, _ = _ssh(host, 'docker ps --format "{{.Names}}"')
    if rc != 0:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _probe_gpu_class(host: str, site: Optional[SiteConfig] = None) -> str:
    """Best-effort GPU class string.

    We try /system_stats devices[0].name first (works for GB10 unified memory
    where nvidia-smi returns [N/A]), then fall back to nvidia-smi name.
    """
    rc, out, _ = _ssh(host, f"curl -fsS http://localhost:{_comfyui_port(site)}/system_stats")
    if rc == 0 and out:
        try:
            d = json.loads(out)
            name = ((d.get("devices") or [{}])[0].get("name") or "").lower()
            if "gb10" in name:
                return "gb10"
            if "blackwell" in name and "5000" in name:
                return "pro5000"
            if "blackwell" in name and "6000" in name:
                return "pro6000"
        except (json.JSONDecodeError, ValueError):
            pass
    rc, out, _ = _ssh(host, "nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1")
    return (out or "").strip().lower() or "unknown"


def _probe_gpu_count(host: str) -> int:
    """Number of GPUs on the node (for intra-node tensor-parallel placement).

    ``nvidia-smi -L`` lists one line per GPU. GB10 unified-memory hosts report
    a single device. Falls back to 1 on any probe failure (single-GPU is the
    safe default — a tp>1 config simply won't be admitted)."""
    rc, out, _ = _ssh(host, "nvidia-smi -L 2>/dev/null | grep -c '^GPU'")
    if rc == 0 and out.strip().isdigit():
        return max(1, int(out.strip()))
    return 1


def _probe_total_vram(host: str, site: Optional[SiteConfig] = None) -> int:
    """Total VRAM in bytes. Combines /system_stats and nvidia-smi.

    GB10 unified-mem hosts report total via ComfyUI; discrete-GPU hosts via
    nvidia-smi. Either way we return a single int.
    """
    rc, out, _ = _ssh(host, f"curl -fsS http://localhost:{_comfyui_port(site)}/system_stats")
    if rc == 0 and out:
        try:
            d = json.loads(out)
            devs = d.get("devices") or []
            if devs and devs[0].get("vram_total"):
                return int(devs[0]["vram_total"])
        except (json.JSONDecodeError, ValueError):
            pass
    rc, out, _ = _ssh(host, "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1")
    if rc == 0 and out.strip().isdigit():
        # nvidia-smi reports MiB
        return int(out.strip()) * 1024 * 1024
    # GB10 unified-memory: system RAM is GPU-addressable. /proc/meminfo
    # MemTotal is the right number when both ComfyUI and nvidia-smi fail us.
    rc, out, _ = _ssh(host, "awk '/^MemTotal:/ {print $2}' /proc/meminfo")
    if rc == 0 and out.strip().isdigit():
        return int(out.strip()) * 1024  # kB → B
    return 0


def probe_node(
    node_id: str, host: str, *,
    reserved_bytes: int = 8 * GB,
    site: Optional[SiteConfig] = None,
) -> NodeProbe:
    """One synchronous probe. Returns a NodeProbe with ``alive`` set sensibly.

    We deliberately do NOT raise — a half-dead node still produces a NodeSpec
    that the planner can reason about (capacity 0 effectively removes it).
    """
    errors: list[str] = []
    running = _probe_running_containers(host)
    if not running:
        # may be empty for legitimate reasons, but together with comfy failure
        # we treat the node as down.
        errors.append("docker ps returned nothing")
    comfy_up, vram_free = _probe_comfyui(host, site)
    if not comfy_up:
        errors.append("comfyui /system_stats unreachable")
    whisper_up = _probe_whisper(host, site)
    # whisper 미가용 = 흔한 정상 상태 (WHISPER_URLS 에서 해당 노드 제외 등) → errors 에
    # 미포함 — comfyui 와 달리 GPU class/VRAM 정보 도출도 안 함.
    total_vram = _probe_total_vram(host, site)
    gpu_class = _probe_gpu_class(host, site)
    gpu_count = _probe_gpu_count(host)
    alive = bool(running) or comfy_up or total_vram > 0

    vllm_ports = site.vllm_ports() if site else {}
    workloads = tuple(
        _probe_vllm(host, name, port)
        for name, port in vllm_ports.items()
        if name in running
    )
    usable_override = site.usable_vram_bytes_for(node_id) if site else None
    tier_override = site.gpu_tier_for(node_id) if site else None
    spec = NodeSpec(
        node_id=node_id,
        hostname=host,
        gpu_class=gpu_class,
        total_vram_bytes=total_vram,
        reserved_bytes=reserved_bytes,
        usable_vram_bytes=usable_override,
        gpu_tier_override=tier_override,
        gpu_count=gpu_count,
    )
    return NodeProbe(
        spec=spec,
        alive=alive,
        running_workloads=workloads,
        comfyui_running=comfy_up,
        whisper_running=whisper_up,
        vram_free_bytes=vram_free,
        raw_errors=tuple(errors),
    )


def probe_cluster(
    nodes: dict[str, str] | None = None,
    *,
    site: Optional[SiteConfig] = None,
    **kw,
) -> list[NodeProbe]:
    """Probe every (node_id → host) entry. Returns probes in the input order.

    If ``nodes`` is None, falls back to ``site.nodes`` — typical use is

        probes = inventory.probe_cluster(site=site)

    so the caller doesn't have to extract the node dict by hand.

    For a small cluster (≤ 10 nodes) sequential probing is plenty (each call
    is ssh-bound, ~1-2s). Parallelization is a future optimization.
    """
    if nodes is None:
        nodes = site.nodes if site else {}
    return [probe_node(nid, host, site=site, **kw) for nid, host in nodes.items()]
