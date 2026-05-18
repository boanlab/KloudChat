#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "rich>=13.7",
#   "httpx>=0.27",
# ]
# ///
"""KloudChat 통합 monitor — GPU 노드 대시보드 + 컨테이너/systemd 로그 스트림.

상단: GPU 노드 라이브 대시보드. .env 의 OLLAMA_URLS / COMFYUI_URLS / WHISPER_URLS
      에서 호스트 추출 → 노드별 GPU + VRAM (torch reserved + 페이지 캐시 분리) +
      Ollama 로드 모델 + ComfyUI 큐 + Whisper 상태. 기본 table (한 줄당 한 호스트),
      --panels 로 상세 박스 뷰.

하단: 라이브 로그 스트림. 컨테이너 (LibreChat / LiteLLM / 두 shim / RAG / SearXNG /
      code-interpreter) + 같은 호스트의 systemd (ollama / comfyui / whisper) 를
      병렬 tail. 각 소스별 grep 필터 + 컬러로 노이즈 제거.

Usage:
    scripts/monitor.py                  # 기본 — 대시보드 + 로그 split
    scripts/monitor.py --dashboard      # 대시보드만 (위 절반 풀스크린)
    scripts/monitor.py --logs           # 로그만 (아래 절반 풀스크린)
    scripts/monitor.py --panels         # 대시보드 박스 뷰
    scripts/monitor.py --interval 5     # 대시보드 refresh 주기 (초, 기본 3)
    scripts/monitor.py --once           # 대시보드 1회 출력 후 종료 (스크립팅용)

uv 가 첫 실행 시 rich + httpx 자동 설치.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"


# ──────────────────────────────────────────────────────────────────────
# .env / 호스트 추출
# ──────────────────────────────────────────────────────────────────────

def env_get(key: str) -> str:
    if not ENV_FILE.is_file():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def collect_hosts() -> list[str]:
    """OLLAMA/COMFYUI/WHISPER_URLS 합집합. host.docker.internal → localhost."""
    hosts: list[str] = []
    seen: set[str] = set()
    for var in ("OLLAMA_URLS", "COMFYUI_URLS", "WHISPER_URLS"):
        for u in env_get(var).split(","):
            m = re.match(r"^\s*https?://([^/:\s]+)", u)
            if not m:
                continue
            h = m.group(1)
            if h == "host.docker.internal":
                h = "localhost"
            if h not in seen:
                seen.add(h)
                hosts.append(h)
    return hosts


# ──────────────────────────────────────────────────────────────────────
# 대시보드 포맷터
# ──────────────────────────────────────────────────────────────────────

def fmt_gib(b: Any) -> str:
    return f"{(b or 0) / 1073741824:.1f}"


def keep_alive(iso: str | None) -> str:
    """Ollama expires_at (ISO 8601) → 'Nm' / 'Nh' / '∞' / 'expired'."""
    if not iso or iso == "null":
        return "-"
    try:
        s = iso.replace("Z", "+00:00")
        m = re.match(r"^(.*?)\.(\d+)([+-]\d{2}:?\d{2})?$", s)
        if m:
            s = f"{m.group(1)}.{m.group(2)[:6]}{m.group(3) or ''}"
        exp = datetime.fromisoformat(s)
    except Exception:
        return "-"
    diff = int((exp - datetime.now(timezone.utc)).total_seconds())
    if diff > 3600 * 24 * 30:
        return "∞"
    if diff > 3600:
        return f"{diff // 3600}h"
    if diff > 60:
        return f"{diff // 60}m"
    if diff > 0:
        return f"{diff}s"
    return "expired"


def vram_bar(torch_pct: int, total_pct: int, width: int = 12) -> Text:
    """2-tone VRAM bar:
      █ (color) = torch_vram_total — 진짜 GPU pressure
      ▒ (dim)   = page cache + 기타 — reclaimable
      ░ (dim)   = MemFree
    GB10 unified memory 에선 페이지 캐시가 nvidia-smi 'used' 에 잡혀 100% 처럼
    보이지만 실제 압박은 torch_pct. 컬러도 torch_pct 기준."""
    torch_pct = max(0, min(100, torch_pct))
    total_pct = max(torch_pct, min(100, total_pct))
    torch_w = torch_pct * width // 100
    total_w = total_pct * width // 100
    cache_w = total_w - torch_w
    empty_w = width - total_w
    color = "green" if torch_pct < 60 else ("yellow" if torch_pct < 85 else "red")
    return (Text("█" * torch_w, style=color)
            + Text("▒" * cache_w, style="dim cyan")
            + Text("░" * empty_w, style="dim"))


def gpu_summary(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """stats → {gpu, torch_used, total_used, total, torch_pct, total_pct}.
    torch_used 는 ComfyUI 의 `torch_vram_total` — 진짜 reserve 된 GPU 메모리.
    total_used 는 nvidia-smi 시각 (페이지 캐시 포함)."""
    if not stats:
        return None
    devs = stats.get("devices") or []
    if not devs:
        return None
    dv = devs[0]
    gpu = re.sub(r"^cuda:\d+\s+", "", dv.get("name", "?"))
    gpu = re.sub(r"\s+:\s+.*$", "", gpu)
    gpu = gpu.replace("NVIDIA ", "")
    total = dv.get("vram_total", 0) or 0
    free = dv.get("vram_free", 0) or 0
    torch_used = dv.get("torch_vram_total", 0) or 0
    total_used = total - free
    return {
        "gpu": gpu,
        "torch_used": torch_used,
        "total_used": total_used,
        "total": total,
        "torch_pct": (torch_used * 100 // total) if total > 0 else 0,
        "total_pct": (total_used * 100 // total) if total > 0 else 0,
    }


# ──────────────────────────────────────────────────────────────────────
# Probe
# ──────────────────────────────────────────────────────────────────────

async def probe_host(client: httpx.AsyncClient, host: str) -> dict[str, Any]:
    """ComfyUI /system_stats + /queue, Ollama /api/ps, Whisper /health 병렬 probe."""
    async def _get(url: str) -> Any:
        try:
            r = await client.get(url, timeout=2.0)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None
    stats, ps, q, wh = await asyncio.gather(
        _get(f"http://{host}:8188/system_stats"),
        _get(f"http://{host}:11434/api/ps"),
        _get(f"http://{host}:8188/queue"),
        _get(f"http://{host}:9000/health"),
    )
    return {"host": host, "stats": stats, "ps": ps, "queue": q, "whisper": wh}


# LiteLLM 등록 모델 캐시. 카탈로그는 setup.sh 가 박을 때만 바뀌므로 1분 단위 리프레시
# 면 충분 — 매 refresh 마다 호출은 낭비.
MODEL_CACHE: dict[str, list[str]] = {}
MODEL_CACHE_AT: float = 0.0
MODEL_REFRESH_SEC = 60.0
LITELLM_URL = "http://localhost:8000"


async def probe_models(client: httpx.AsyncClient) -> dict[str, list[str]] | None:
    """LiteLLM /v1/models → provider 별 그룹. master key 없거나 LiteLLM unreachable
    이면 None. 그룹: openai/anthropic/google (commercial via OR) / ollama (로컬) /
    embed / image (외부 OR image alias)."""
    global MODEL_CACHE_AT
    now = asyncio.get_event_loop().time()
    if MODEL_CACHE and now - MODEL_CACHE_AT < MODEL_REFRESH_SEC:
        return MODEL_CACHE
    key = env_get("LITELLM_MASTER_KEY")
    if not key:
        return None
    try:
        r = await client.get(f"{LITELLM_URL}/v1/models",
                             headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
        r.raise_for_status()
        body = r.json()
    except Exception:
        return None
    # Distinct: /v1/models 는 deployment 단위가 아닌 model_name 단위로 dedup 된 결과.
    groups: dict[str, list[str]] = {
        "openai": [], "anthropic": [], "google": [],
        "ollama": [], "embed": [], "image": [],
    }
    seen: set[str] = set()
    for m in body.get("data") or []:
        name = m.get("id", "")
        if not name or name in seen:
            continue
        seen.add(name)
        if name.startswith("openai/"):
            groups["openai"].append(name.split("/", 1)[1])
        elif name.startswith("anthropic/"):
            groups["anthropic"].append(name.split("/", 1)[1])
        elif name.startswith("google/"):
            groups["google"].append(name.split("/", 1)[1])
        elif name.startswith("ollama/"):
            groups["ollama"].append(name.split("/", 1)[1])
        elif name.startswith("image-"):
            groups["image"].append(name.replace("image-", "", 1))
        else:
            groups["embed"].append(name)
    MODEL_CACHE.clear()
    MODEL_CACHE.update(groups)
    MODEL_CACHE_AT = now
    return MODEL_CACHE


def running_unets(queue: dict[str, Any] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not queue:
        return out
    for job in queue.get("queue_running") or []:
        try:
            pid = str(job[1])
            graph = job[2]
            fname = "?"
            for node in graph.values():
                ct = node.get("class_type", "")
                if ct in ("UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple"):
                    inputs = node.get("inputs") or {}
                    fname = inputs.get("unet_name") or inputs.get("ckpt_name") or "?"
                    break
            out.append((pid[:8], fname))
        except Exception:
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# 대시보드 렌더링
# ──────────────────────────────────────────────────────────────────────

def host_panel(data: dict[str, Any]) -> Panel:
    host = data["host"]
    stats, ps, queue, wh = data["stats"], data["ps"], data["queue"], data["whisper"]

    summ = gpu_summary(stats)
    if summ is not None:
        cache_used = summ["total_used"] - summ["torch_used"]
        gpu_line = Text.assemble(
            ("GPU ", "bold"), summ["gpu"], "  ",
            ("VRAM ", "bold"), vram_bar(summ["torch_pct"], summ["total_pct"], width=20),
            f"  torch {fmt_gib(summ['torch_used'])}G ({summ['torch_pct']}%) ",
            (f"+ cache {fmt_gib(cache_used)}G", "dim cyan"),
            f" / {fmt_gib(summ['total'])}G",
        )
    else:
        gpu_line = Text("(ComfyUI :8188 unreachable)", style="yellow")

    parts: list[Any] = [gpu_line]

    if ps is None:
        parts.append(Text("Ollama   (:11434 unreachable)", style="yellow"))
    else:
        models = ps.get("models") or []
        total = sum((m.get("size_vram") or 0) for m in models)
        parts.append(Text.assemble(
            ("Ollama   ", "bold cyan"),
            f"{len(models)} loaded — {fmt_gib(total)} GiB",
        ))
        if models:
            tbl = Table.grid(padding=(0, 2))
            tbl.add_column(style="white", no_wrap=True)
            tbl.add_column(style="dim", no_wrap=True)
            tbl.add_column(justify="right", style="white")
            for m in models:
                tbl.add_row(
                    "  " + m.get("name", "?"),
                    f"keep {keep_alive(m.get('expires_at'))}",
                    f"{fmt_gib(m.get('size_vram'))} GiB",
                )
            parts.append(tbl)

    if queue is None:
        parts.append(Text("ComfyUI  (:8188 queue unreachable)", style="yellow"))
    else:
        r = len(queue.get("queue_running") or [])
        p = len(queue.get("queue_pending") or [])
        parts.append(Text.assemble(
            ("ComfyUI  ", "bold magenta"),
            f"running={r} pending={p}",
        ))
        for short, fname in running_unets(queue):
            parts.append(Text(f"  [{short}] {fname}", style="dim"))

    if wh:
        parts.append(Text.assemble(
            ("Whisper  ", "bold green"),
            f"model={wh.get('model', '?')}  ",
            f"device={wh.get('device', '?')}  ",
            f"status={wh.get('status', '?')}",
        ))

    return Panel(Group(*parts), title=f"[bold]{host}[/]", border_style="blue")


def render_table(data: list[dict[str, Any]]) -> Table:
    tbl = Table(show_header=True, header_style="bold", expand=True,
                padding=(0, 1), pad_edge=False)
    tbl.add_column("host",    style="cyan",  no_wrap=True, min_width=14)
    tbl.add_column("GPU",     no_wrap=True,  min_width=6)
    tbl.add_column("VRAM",    no_wrap=True,  min_width=20)
    tbl.add_column("Ollama",  no_wrap=True,  min_width=24, overflow="ellipsis")
    tbl.add_column("ComfyUI", no_wrap=True,  min_width=16, overflow="ellipsis")
    tbl.add_column("Whisper", no_wrap=True,  min_width=10)

    for d in data:
        host = d["host"]
        ps, queue, wh = d["ps"], d["queue"], d["whisper"]

        summ = gpu_summary(d["stats"])
        if summ is None:
            gpu, vram_cell = "-", Text("unreachable", style="yellow")
        else:
            gpu = summ["gpu"]
            cache_pct = summ["total_pct"] - summ["torch_pct"]
            vram_cell = Text.assemble(
                vram_bar(summ["torch_pct"], summ["total_pct"], width=10),
                f" {summ['torch_pct']:>3d}% ",
                (f"+{cache_pct}%c", "dim cyan"),
                f" {fmt_gib(summ['torch_used'])}/{fmt_gib(summ['total'])}",
            )

        if ps is None:
            ollama_cell: Text = Text("unreachable", style="yellow")
        else:
            models = sorted(ps.get("models") or [],
                            key=lambda m: -(m.get("size_vram") or 0))
            n = len(models)
            if n == 0:
                ollama_cell = Text("0", style="dim")
            else:
                top = models[0]
                line = (f"{n} · {top.get('name','?')} "
                        f"({fmt_gib(top.get('size_vram'))}G·{keep_alive(top.get('expires_at'))})")
                if n > 1:
                    line += f" +{n-1}"
                ollama_cell = Text(line)

        if queue is None:
            comfy_cell: Text = Text("unreachable", style="yellow")
        else:
            r = len(queue.get("queue_running") or [])
            p = len(queue.get("queue_pending") or [])
            unets = running_unets(queue)
            head = f"{r}/{p}"
            if unets:
                first = re.sub(r"\.(safetensors|gguf|ckpt|pt|bin)$", "", unets[0][1])
                head += f" · {first}"
                if len(unets) > 1:
                    head += f" +{len(unets)-1}"
            comfy_cell = Text(head, style=("magenta" if r > 0 else "dim"))

        if wh:
            short = wh.get("model", "?").replace("large-", "lg-").replace("turbo", "trb")
            wh_cell: Text = Text.assemble(("ok ", "green"), f"· {short}")
        else:
            wh_cell = Text("-", style="dim")

        tbl.add_row(host, gpu, vram_cell, ollama_cell, comfy_cell, wh_cell)

    return tbl


def models_panel(groups: dict[str, list[str]] | None) -> Panel | None:
    """LiteLLM 등록 모델 카탈로그 — provider 별 한 줄. LibreChat 모델 셀렉터에
    노출되는 entries 와 사실상 동치."""
    if not groups or not any(groups.values()):
        return None
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=True, justify="right", style="dim")
    tbl.add_column(no_wrap=False)
    label_style = {
        "openai":    "cyan",
        "anthropic": "magenta",
        "google":    "blue",
        "ollama":    "green",
        "embed":     "yellow",
        "image":     "magenta",
    }
    for prov in ("openai", "anthropic", "google", "ollama", "embed", "image"):
        names = groups.get(prov) or []
        if not names:
            continue
        tbl.add_row(
            Text(prov, style=f"bold {label_style[prov]}"),
            f"({len(names)})",
            "  ".join(names),
        )
    return Panel(tbl, title="[bold]Models (LiteLLM 등록 카탈로그)[/]", border_style="green")


def whisper_shim_panel() -> Panel | None:
    """compose 의 whisper-shim 컨테이너가 있으면 백엔드별 up/inflight."""
    try:
        out = subprocess.run(
            ["docker", "exec", "whisper-shim", "python3", "-c",
             "import urllib.request, json;"
             "r=urllib.request.urlopen('http://localhost:9000/health', timeout=2).read();"
             "print(r.decode())"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return None
        body = json.loads(out.stdout)
    except Exception:
        return None
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(justify="right")
    tbl.add_row("[bold]backend[/]", "[bold]up[/]", "[bold]inflight[/]")
    for b in body.get("backends", []):
        url = b.get("url", "?").replace("http://", "")
        up = "[green]up[/]" if b.get("up") else "[red]down[/]"
        tbl.add_row(url, up, str(b.get("inflight", "?")))
    return Panel(
        tbl,
        title=f"[bold]whisper-shim 라우터[/]  status={body.get('status', '?')}",
        border_style="cyan",
    )


def dashboard_render(
    data: list[dict[str, Any]],
    interval: int,
    *,
    panels: bool,
    models: dict[str, list[str]] | None = None,
) -> Group:
    header = Text.assemble(
        ("KloudChat Monitor", "bold blue"),
        f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"   refresh {interval}s   ",
        (f"mode={'panels' if panels else 'table'}   ", "dim"),
        ("Ctrl+C to quit", "dim"),
    )
    parts: list[Any] = [header]
    if panels:
        parts.extend(host_panel(d) for d in data)
    else:
        parts.append(render_table(data))
    shim = whisper_shim_panel()
    if shim is not None:
        parts.append(shim)
    mods = models_panel(models)
    if mods is not None:
        parts.append(mods)
    return Group(*parts)


# ──────────────────────────────────────────────────────────────────────
# 로그 스트림
# ──────────────────────────────────────────────────────────────────────

# (label, emoji, color, include_regex, exclude_regex|None)
# include 매치 + exclude 미매치 만 화면에 노출. monitor.sh 의 필터 그대로 포팅.
LOG_FILTERS: dict[str, tuple[str, str, str, str | None]] = {
    # 컨테이너 — docker logs -f
    "LibreChat": ("🌐", "blue",
                  r"Login|sendCompletion|onSearchResults|streamAudio|generate_image|tool|run_id|completion|message",
                  r"auth\.json|getUserPluginAuth|Error scraping|Title generation|FIRECRAWL"),
    "LiteLLM":   ("🧠", "green",
                  r"POST|GET|spend|model",
                  r"liveliness|prisma|migration"),
    "ImgShim":   ("🎨", "magenta",
                  r"txt2img|img2img|model=|template=|variants",
                  None),
    "WhShim":    ("🎙", "cyan",
                  r"transcrib|Routing|backends|inflight",
                  None),
    "RAG":       ("📚", "cyan",
                  r"embed|chunk|query|retriev|upload|document|POST|GET",
                  None),
    "SearXNG":   ("🔍", "yellow",
                  r"GET /search|query",
                  None),
    "CodeIntp":  ("💻", "red",
                  r"exec|run|complete|sandbox|repl",
                  r"REPL ready timeout|REPL not ready|Failed to start REPL"),
    # 같은 호스트 systemd
    "Ollama":    ("⚡", "green",
                  r"llm load|loaded|gpu memory|prompt|generate|embedding",
                  None),
    "ComfyUI":   ("🎨", "magenta",
                  r"Prompt executed|got prompt|sampling|VAE|model|loading|VRAM",
                  None),
    "Whisper":   ("🎙", "cyan",
                  r"POST|transcriptions|Loading WhisperModel",
                  None),
}

# 라벨 → docker container name (있으면 docker logs 추적)
CONTAINER_NAME = {
    "LibreChat": "LibreChat",
    "LiteLLM":   "litellm",
    "ImgShim":   "comfyui-shim",
    "WhShim":    "whisper-shim",
    "RAG":       "rag_api",
    "SearXNG":   "searxng",
    "CodeIntp":  "code-interpreter",
}
# 라벨 → systemd unit (있으면 journalctl -fu)
SYSTEMD_UNIT = {
    "Ollama":  "ollama",
    "ComfyUI": "comfyui",
    "Whisper": "whisper",
}

# 로그 라인 버퍼. (ts, label, message). maxlen 만큼 rolling.
LOG_BUFFER: deque[tuple[str, str, str]] = deque(maxlen=1000)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


async def _follow_stream(label: str, cmd: list[str]) -> None:
    """주어진 명령어를 subprocess 로 띄우고 stdout 라인을 LOG_BUFFER 로 흘림.
    include/exclude 필터 + ANSI 제거 + 길이 자르기."""
    emoji, color, inc_re_s, exc_re_s = LOG_FILTERS[label]
    inc_re = re.compile(inc_re_s, re.IGNORECASE)
    exc_re = re.compile(exc_re_s, re.IGNORECASE) if exc_re_s else None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        text = _ANSI_RE.sub("", line.decode(errors="replace")).rstrip()
        if not text:
            continue
        if not inc_re.search(text):
            continue
        if exc_re and exc_re.search(text):
            continue
        if len(text) > 240:
            text = text[:237] + "..."
        ts = datetime.now().strftime("%H:%M:%S")
        LOG_BUFFER.append((ts, label, text))


async def _container_exists(name: str) -> bool:
    try:
        p = await asyncio.create_subprocess_exec(
            "docker", "ps", "--format", "{{.Names}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p.communicate()
        return name in out.decode().splitlines()
    except FileNotFoundError:
        return False


def _has_systemd_unit(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(
            ["systemctl", "list-unit-files", f"{unit}.service", "--no-legend"],
            capture_output=True, text=True, timeout=2,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


async def start_log_streams() -> list[asyncio.Task]:
    """존재하는 컨테이너 + 같은 호스트의 systemd unit 만 골라 tail. 없는 건 silent skip."""
    tasks: list[asyncio.Task] = []
    for label, cname in CONTAINER_NAME.items():
        if await _container_exists(cname):
            tasks.append(asyncio.create_task(
                _follow_stream(label, ["docker", "logs", "-f", "--tail", "0", cname])
            ))
    for label, unit in SYSTEMD_UNIT.items():
        if _has_systemd_unit(unit):
            tasks.append(asyncio.create_task(
                _follow_stream(label, ["journalctl", "-u", unit, "-f", "-n", "0", "--no-pager"])
            ))
    return tasks


def logs_render(max_lines: int) -> Panel:
    """LOG_BUFFER 끝 max_lines 개를 컬러 라인으로 렌더. 비어있으면 hint."""
    if not LOG_BUFFER:
        body = Text("(아직 매칭 라인 없음 — 컨테이너/서비스 활동 대기 중)", style="dim")
    else:
        lines: list[Text] = []
        for ts, label, msg in list(LOG_BUFFER)[-max_lines:]:
            emoji, color, *_ = LOG_FILTERS[label]
            lines.append(Text.assemble(
                (f"{ts} ", color),
                (f"{emoji} {label:<9}", color),
                ("│ ", "dim"),
                msg,
            ))
        body = Group(*lines)
    return Panel(body, title="[bold]Logs[/]", border_style="dim")


# ──────────────────────────────────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────────────────────────────────

def estimate_dashboard_height(n_hosts: int, panels: bool, has_shim_panel: bool,
                               n_model_rows: int) -> int:
    """대시보드 영역에 줄 size — split layout 에서 logs 공간 계산용."""
    header = 1
    if panels:
        per_host = 6  # GPU+Ollama+ComfyUI+Whisper 4 줄 + 패널 테두리 2
        body = per_host * n_hosts
    else:
        body = 4 + n_hosts  # 테이블 헤더 3 + N 데이터 행 + 닫는 줄
    shim = 6 if has_shim_panel else 0
    models = (n_model_rows + 3) if n_model_rows > 0 else 0  # 패널 테두리 2 + 헤더
    return header + body + shim + models + 2


async def main() -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--once", action="store_true", help="대시보드 1회 출력 후 종료")
    p.add_argument("--interval", type=int, default=3, help="대시보드 refresh 주기 (초, 기본 3)")
    p.add_argument("--panels", action="store_true", help="호스트당 큰 패널 (기본은 압축 table)")
    p.add_argument("--dashboard", action="store_true", help="대시보드만 (로그 영역 숨김)")
    p.add_argument("--logs", action="store_true", help="로그만 (대시보드 영역 숨김)")
    args = p.parse_args()

    if args.dashboard and args.logs:
        print("--dashboard 와 --logs 동시 사용 불가", file=sys.stderr)
        return 2

    hosts = collect_hosts()
    if not hosts and not args.logs:
        print("error: .env 의 OLLAMA_URLS / COMFYUI_URLS / WHISPER_URLS 에서 호스트 추출 0건",
              file=sys.stderr)
        return 1

    console = Console()

    # --once: 대시보드 한 프레임만
    if args.once or not console.is_terminal:
        async with httpx.AsyncClient() as client:
            data, mods = await asyncio.gather(
                asyncio.gather(*(probe_host(client, h) for h in hosts)),
                probe_models(client),
            )
        console.print(dashboard_render(list(data), args.interval,
                                       panels=args.panels, models=mods))
        return 0

    log_tasks: list[asyncio.Task] = []
    try:
        if not args.dashboard:
            log_tasks = await start_log_streams()

        if args.logs:
            # 로그만 풀스크린
            with Live(console=console, refresh_per_second=4, screen=True) as live:
                while True:
                    h = console.size.height - 2
                    live.update(logs_render(max_lines=max(5, h)))
                    await asyncio.sleep(0.5)
            return 0

        # 대시보드 단독 OR 합본
        async with httpx.AsyncClient() as client:
            shim_present = whisper_shim_panel() is not None

            last_probe_ts = 0.0
            cached_data: list[dict[str, Any]] = []
            cached_models: dict[str, list[str]] | None = None

            def n_model_rows() -> int:
                if not cached_models:
                    return 0
                return sum(1 for v in cached_models.values() if v)

            def make_layout() -> Layout | Group:
                dash = dashboard_render(cached_data, args.interval,
                                        panels=args.panels, models=cached_models)
                if args.dashboard:
                    return dash
                dash_h = estimate_dashboard_height(
                    len(hosts), args.panels, shim_present, n_model_rows(),
                )
                layout = Layout()
                layout.split_column(
                    Layout(name="dashboard", size=dash_h),
                    Layout(name="logs", ratio=1),
                )
                layout["dashboard"].update(dash)
                term_h = console.size.height
                log_h = max(5, term_h - dash_h - 2)
                layout["logs"].update(logs_render(max_lines=log_h))
                return layout

            with Live(console=console, refresh_per_second=4, screen=True) as live:
                while True:
                    now = asyncio.get_event_loop().time()
                    if now - last_probe_ts >= args.interval or not cached_data:
                        cached_data, cached_models = await asyncio.gather(
                            asyncio.gather(*(probe_host(client, h) for h in hosts)),
                            probe_models(client),
                        )
                        cached_data = list(cached_data)
                        last_probe_ts = now
                    live.update(make_layout())
                    await asyncio.sleep(0.25)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in log_tasks:
            t.cancel()
        if log_tasks:
            await asyncio.gather(*log_tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
