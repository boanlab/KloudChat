"""Host/container usage from read-only /proc and cgroup mounts; no docker socket."""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass, field

_CONTAINER = re.compile(r"docker-([0-9a-f]{64})\.scope|/docker/([0-9a-f]{64})")
# physical interfaces only: bridges/veths duplicate traffic
_VIRTUAL_IF = re.compile(r"^(lo|veth|docker|br-|virbr|cni|flannel|cali|vxlan|tun|tap)")
_WHOLE_DISK = re.compile(
    r"^(sd[a-z]{1,3}|vd[a-z]{1,3}|xvd[a-z]{1,3}|nvme\d{1,3}n\d{1,3}|mmcblk\d{1,3})$"
)
_LOCAL_V4 = re.compile(r"\|-- (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\n\s{1,40}/32 host LOCAL")
_OWN_ID = re.compile(r"/containers/([0-9a-f]{64})/hostname ")


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def cpu_ticks(stat: str) -> tuple[int, int, int] | None:
    """(busy+idle ticks, idle ticks, cpu count) from /proc/stat."""
    total = idle = cpus = 0
    found = False
    for line in stat.splitlines():
        if line.startswith("cpu "):
            ticks = [int(x) for x in line.split()[1:9] if x.isdigit()]
            if len(ticks) < 5:
                return None
            total, idle, found = sum(ticks), ticks[3] + ticks[4], True
        elif line.startswith("cpu") and line[3:4].isdigit():
            cpus += 1
    return (total, idle, max(cpus, 1)) if found else None


def meminfo(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * 1024
    return out


def net_dev(text: str, physical_only: bool = True) -> tuple[int, int]:
    """(received, sent) bytes over interfaces, loopback excluded."""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        name, sep, rest = line.partition(":")
        name = name.strip()
        fields = rest.split()
        if not sep or len(fields) < 9 or name == "lo":
            continue
        if physical_only and _VIRTUAL_IF.match(name):
            continue
        rx += int(fields[0])
        tx += int(fields[8])
    return rx, tx


def diskstats(text: str) -> dict[str, tuple[int, int, int]]:
    """Whole disks: (sectors read, sectors written, I/O ms)."""
    out: dict[str, tuple[int, int, int]] = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 13 and _WHOLE_DISK.match(f[2]):
            out[f[2]] = (int(f[5]), int(f[9]), int(f[12]))
    return out


def keyed(text: str) -> dict[str, int]:
    """key value lines (cpu.stat, memory.stat)."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def io_bytes(text: str) -> tuple[int, int]:
    rb = wb = 0
    for line in text.splitlines():
        for token in line.split()[1:]:
            key, _, value = token.partition("=")
            if value.isdigit():
                if key == "rbytes":
                    rb += int(value)
                elif key == "wbytes":
                    wb += int(value)
    return rb, wb


def cpu_quota(text: str | None) -> float | None:
    """CPUs from cpu.max ("max 100000" → None)."""
    if not text:
        return None
    parts = text.split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > 0:
        return int(parts[0]) / int(parts[1])
    return None


def local_ipv4(fib_trie: str) -> set[str]:
    return {ip for ip in _LOCAL_V4.findall(fib_trie) if not ip.startswith("127.")}


#: (label, fingerprint pattern), first match wins; {own} = this stack's data mount
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("gateway", re.compile(r"\bcaddy run\b")),
    ("litellm", re.compile(r"\blitellm\b[^|]{0,200}--config")),
    ("litellm-db", re.compile(r"^postgres.{0,4000}/litellm/postgres", re.S)),
    ("index-db", re.compile(r"^postgres.{0,4000}/index/postgres", re.S)),
    ("searxng", re.compile(r"^searxng.{0,4000}/services/searxng", re.S)),
    ("searxng-probe", re.compile(r"^searxng")),
    ("valkey", re.compile(r"\bvalkey-server\b")),
    ("ci-redis", re.compile(r"\bredis-server\b")),
    ("ci-minio", re.compile(r"\bminio server\b")),
    ("code-interpreter", re.compile(r"repl_server|\bsrc\.main\b|\bnsjail\b")),
    ("deep-research", re.compile(r"\bmcp-proxy\b")),
    ("print", re.compile(r"\bheadless_shell\b")),
    ("gpu-exporter", re.compile(r"\bexporter\.py\b")),
    ("crawl4ai-shim", re.compile(r"uvicorn app:app.{0,2000}\bchrom(e|ium)\b", re.S)),
    ("whisper-shim", re.compile(r"uvicorn app:app[^|]{0,80}--port 9000")),
    ("search-shim", re.compile(r"uvicorn app:app[^|]{0,80}--no-access-log")),
    ("index-shim", re.compile(r"uvicorn app:app")),
    ("buildkit", re.compile(r"\bbuildkitd\b")),
    ("vllm", re.compile(r"\bvllm\b")),
]


def fingerprint(proc: str, pids: list[str]) -> tuple[str, list[str]]:
    """(cmdline | process names | mount sources, bind-mount sources)."""
    cmd = ""
    names: set[str] = set()
    # once per container (cached)
    for pid in sorted(pids, key=int)[:2000]:
        if not cmd:
            raw = read(f"{proc}/{pid}/cmdline") or ""
            # = args may carry values: excluded
            cmd = " ".join(a for a in raw.split("\0") if a and "=" not in a)[:400]
        comm = read(f"{proc}/{pid}/comm")
        if comm:
            names.add(comm.strip())
    sources: list[str] = []
    for line in (read(f"{proc}/{pids[0]}/mountinfo") or "").splitlines() if pids else []:
        f = line.split()
        # bind/volume sources only
        if (
            len(f) > 4
            and len(f[3]) > 1
            and not f[3].startswith("/..")
            and not f[4].startswith(("/proc", "/sys", "/dev"))
            and f[4] not in ("/", "/etc/hostname", "/etc/hosts", "/etc/resolv.conf")
        ):
            sources.append(f[3])
    base = " ".join(os.path.basename(t) if t.startswith("/") else t for t in cmd.split())
    return f"{base} | {' '.join(sorted(names))} | {' '.join(sources)}", sources


def classify(fp: str, sources: list[str], own_data: str | None) -> str:
    if "uvicorn app.main:app" in fp:
        return "api" if own_data and own_data in sources else "api (다른 인스턴스)"
    if fp.startswith("postgres") and own_data:
        stack = os.path.dirname(own_data.rstrip("/")) + "/"
        if any(s.startswith(stack) for s in sources):
            return "db"
    text = f"{fp} {' '.join(sources)}"
    for label, pattern in RULES:
        if pattern.search(text):
            return label
    if fp.startswith("nginx"):
        return "nginx"
    if fp.startswith("postgres"):
        return "postgres"
    return "container"


@dataclass
class _Prev:
    at: float
    values: dict[str, tuple] = field(default_factory=dict)


Row = tuple[str, str, str, float]  # (source, name, subject, value)


class HostReader:
    """Keeps previous counters to yield interval rates."""

    def __init__(self, proc: str, cgroup: str, disks: dict[str, str], dns_names: list[str]):
        self.proc = proc.rstrip("/")
        self.cgroup = cgroup.rstrip("/")
        self.disks = disks
        self.dns_names = dns_names
        self.prev: _Prev | None = None
        self._names: dict[str, str] = {}

    def available(self) -> bool:
        return read(f"{self.proc}/stat") is not None

    def _own(self) -> tuple[str | None, str | None]:
        """This container's id and data-volume source."""
        info = read("/proc/self/mountinfo") or ""
        own = _OWN_ID.search(info)
        data = None
        for line in info.splitlines():
            f = line.split()
            if len(f) > 4 and f[4] in ("/srv/data/files", "/srv/data"):
                data = f[3]
        return (own.group(1) if own else None), data

    def containers(self) -> dict[str, list[str]]:
        """Container id → host pids."""
        out: dict[str, list[str]] = {}
        own = self._own()[0]
        try:
            entries = os.listdir(self.proc)
        except OSError:
            return out
        for pid in entries:
            if not pid.isdigit():
                continue
            text = read(f"{self.proc}/{pid}/cgroup")
            if not text:
                continue
            match = _CONTAINER.search(text)
            if match:
                out.setdefault(match.group(1) or match.group(2), []).append(pid)
            elif own and text.strip() == "0::/":
                # own processes at the cgroup-namespace root
                out.setdefault(own, []).append(pid)
        return out

    def _cg_dir(self, cid: str) -> str | None:
        for path in (
            f"{self.cgroup}/system.slice/docker-{cid}.scope",
            f"{self.cgroup}/docker/{cid}",
        ):
            if os.path.isdir(path):
                return path
        return None

    def _dns(self) -> dict[str, str]:
        """IPv4 → name for resolvable names."""
        out: dict[str, str] = {}
        for name in self.dns_names:
            try:
                for info in socket.getaddrinfo(name, None, socket.AF_INET):
                    out.setdefault(str(info[4][0]), name)
            except OSError:
                continue
        return out

    def _label_all(self, ids: dict[str, list[str]]) -> dict[str, str]:
        own_id, own_data = self._own()
        # named once per container; DNS only for new ones
        dns = self._dns() if any(cid not in self._names for cid in ids) else {}
        self._names = {cid: name for cid, name in self._names.items() if cid in ids}
        labels: dict[str, str] = {}
        for cid, pids in ids.items():
            if cid in self._names:
                labels[cid] = self._names[cid]
                continue
            fp, sources = fingerprint(self.proc, pids)
            label = classify(fp, sources, own_data)
            ips = local_ipv4(read(f"{self.proc}/{pids[0]}/net/fib_trie") or "")
            named = next((dns[ip] for ip in sorted(ips) if ip in dns), None)
            if named and label not in ("api", "api (다른 인스턴스)"):
                label = named.removeprefix("kloudchat-")
            labels[cid] = label
        # duplicate label: short id disambiguates (api replicas)
        counts: dict[str, int] = {}
        for label in labels.values():
            counts[label] = counts.get(label, 0) + 1
        out = {}
        for cid, label in labels.items():
            self._names[cid] = label
            out[cid] = f"{label} · {cid[:6]}" if counts[label] > 1 else label
            if cid == own_id:
                out[cid] += " (수집)"
        return out

    def sample(self, now: float | None = None) -> list[Row]:
        now = time.time() if now is None else now
        rows: list[Row] = []
        cur: dict[str, tuple] = {}
        prev = self.prev
        dt = (now - prev.at) if prev else 0.0

        def rate(key: str, values: tuple, scale: float = 1.0) -> tuple | None:
            cur[key] = values
            if not prev or dt <= 0 or key not in prev.values:
                return None
            old = prev.values[key]
            diffs = tuple(max(a - b, 0) for a, b in zip(values, old, strict=False))
            return tuple(d * scale / dt for d in diffs)

        stat = cpu_ticks(read(f"{self.proc}/stat") or "")
        cpus = 1
        if stat:
            total, idle, cpus = stat
            cur["cpu"] = (total, idle)
            if prev and "cpu" in prev.values:
                dtotal = total - prev.values["cpu"][0]
                didle = idle - prev.values["cpu"][1]
                if dtotal > 0:
                    rows.append(("host", "cpu_percent", "host", 100.0 * (dtotal - didle) / dtotal))
            rows.append(("host", "cpus", "host", float(cpus)))
        mem = meminfo(read(f"{self.proc}/meminfo") or "")
        mem_total = mem.get("MemTotal", 0)
        if mem_total:
            used = mem_total - mem.get("MemAvailable", mem.get("MemFree", 0))
            rows += [
                ("host", "mem_used_bytes", "host", float(used)),
                ("host", "mem_total_bytes", "host", float(mem_total)),
                ("host", "mem_percent", "host", 100.0 * used / mem_total),
            ]
        load = (read(f"{self.proc}/loadavg") or "").split()
        if load:
            rows.append(("host", "load1", "host", float(load[0])))
        net = read(f"{self.proc}/1/net/dev")
        if net:
            r = rate("net", net_dev(net))
            if r:
                rows += [("host", "net_rx_bps", "host", r[0]), ("host", "net_tx_bps", "host", r[1])]
        for dev, (rs, ws, busy) in diskstats(read(f"{self.proc}/diskstats") or "").items():
            r = rate(f"disk:{dev}", (rs, ws, busy))
            if r:
                rows += [
                    ("host", "disk_read_bps", dev, r[0] * 512),
                    ("host", "disk_write_bps", dev, r[1] * 512),
                    ("host", "disk_busy_percent", dev, min(r[2] / 10.0, 100.0)),
                ]
        for label, path in self.disks.items():
            try:
                st = os.statvfs(path)
            except OSError:
                continue
            size = st.f_blocks * st.f_frsize
            if size:
                free = st.f_bavail * st.f_frsize
                rows += [
                    ("host", "disk_used_percent", label, 100.0 * (size - free) / size),
                    ("host", "disk_free_bytes", label, float(free)),
                ]

        ids = self.containers()
        labels = self._label_all(ids)
        for cid, pids in ids.items():
            cg = self._cg_dir(cid)
            if not cg:
                continue
            subject = labels[cid]
            usage = keyed(read(f"{cg}/cpu.stat") or "").get("usage_usec")
            quota = cpu_quota(read(f"{cg}/cpu.max"))
            if usage is not None:
                r = rate(f"ccpu:{cid}", (usage,))
                if r:
                    # percent of host CPUs and of container quota
                    cores = r[0] / 1e6
                    rows.append(("container", "cpu_percent", subject, 100.0 * cores / cpus))
                    rows.append(("container", "cpu_cores", subject, cores))
                    if quota:
                        rows.append(
                            ("container", "cpu_limit_percent", subject, 100.0 * cores / quota)
                        )
            current = read(f"{cg}/memory.current")
            if current and current.strip().isdigit():
                inactive = keyed(read(f"{cg}/memory.stat") or "").get("inactive_file", 0)
                used = max(int(current) - inactive, 0)
                limit_raw = (read(f"{cg}/memory.max") or "").strip()
                limit = int(limit_raw) if limit_raw.isdigit() else mem_total
                rows.append(("container", "mem_bytes", subject, float(used)))
                if limit:
                    rows.append(("container", "mem_limit_percent", subject, 100.0 * used / limit))
            io = read(f"{cg}/io.stat")
            if io is not None:
                r = rate(f"cio:{cid}", io_bytes(io))
                if r:
                    rows += [
                        ("container", "io_read_bps", subject, r[0]),
                        ("container", "io_write_bps", subject, r[1]),
                    ]
            cnet = read(f"{self.proc}/{pids[0]}/net/dev")
            if cnet:
                r = rate(f"cnet:{cid}", net_dev(cnet, physical_only=False))
                if r:
                    rows += [
                        ("container", "net_rx_bps", subject, r[0]),
                        ("container", "net_tx_bps", subject, r[1]),
                    ]
        self.prev = _Prev(at=now, values=cur)
        return rows
