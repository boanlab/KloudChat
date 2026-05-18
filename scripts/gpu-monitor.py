#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "rich>=13.7",
#   "httpx>=0.27",
# ]
# ///
"""KloudChat GPU 노드 라이브 모니터 (TUI).

.env 의 OLLAMA_URLS / COMFYUI_URLS / WHISPER_URLS 에서 유니크 호스트 추출 →
노드별로 다음을 한 패널에 표시:
  - GPU 이름 + VRAM 사용/총량 + 컬러 게이지 (ComfyUI /system_stats)
  - Ollama 로드 모델 + per-model VRAM + keep-alive 남은 시간 (/api/ps)
  - ComfyUI 큐 (running/pending) + 실행 중 워크플로의 UNet 파일명
  - Whisper systemd /health

추가로 같은 호스트에서 docker exec 가능하면 whisper-shim 라우터 패널 (백엔드별
up/down + inflight 카운터).

Usage:
    scripts/gpu-monitor.py [--once] [--interval N]

uv 가 첫 실행 시 rich + httpx 자동 설치.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"


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


def fmt_gib(b: Any) -> str:
    return f"{(b or 0) / 1073741824:.1f}"


def keep_alive(iso: str | None) -> str:
    """Ollama expires_at (ISO 8601) → 'Nm' / 'Nh' / '∞' / 'expired'."""
    if not iso or iso == "null":
        return "-"
    try:
        s = iso.replace("Z", "+00:00")
        # cap fractional seconds at 6 digits (Python's fromisoformat 한계)
        m = re.match(r"^(.*?)\.(\d+)([+-]\d{2}:?\d{2})?$", s)
        if m:
            s = f"{m.group(1)}.{m.group(2)[:6]}{m.group(3) or ''}"
        exp = datetime.fromisoformat(s)
    except Exception:
        return "-"
    diff = int((exp - datetime.now(timezone.utc)).total_seconds())
    if diff > 3600 * 24 * 30:  # 30일 초과 → KEEP_ALIVE=-1 로 간주
        return "∞"
    if diff > 3600:
        return f"{diff // 3600}h"
    if diff > 60:
        return f"{diff // 60}m"
    if diff > 0:
        return f"{diff}s"
    return "expired"


def vram_bar(pct: int, width: int = 28) -> Text:
    """채움 █ / 빈칸 ░ + 사용량 구간별 컬러."""
    pct = max(0, min(100, pct))
    filled = pct * width // 100
    color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
    return Text("█" * filled, style=color) + Text("░" * (width - filled), style="dim")


async def probe_host(client: httpx.AsyncClient, host: str) -> dict[str, Any]:
    """ComfyUI /system_stats + /queue, Ollama /api/ps, Whisper /health 병렬 probe.
    실패한 endpoint 는 None — 패널 렌더 시 unreachable 표시."""
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


def running_unets(queue: dict[str, Any] | None) -> list[tuple[str, str]]:
    """queue_running 의 각 잡에서 UNETLoader/UnetLoaderGGUF/CheckpointLoaderSimple
    노드의 unet_name / ckpt_name 추출. 반환: [(prompt_id 앞 8자, 파일명), ...]."""
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


def host_panel(data: dict[str, Any]) -> Panel:
    host = data["host"]
    stats, ps, queue, wh = data["stats"], data["ps"], data["queue"], data["whisper"]

    # ── 헤더: GPU 이름 + VRAM bar
    if stats and (devs := stats.get("devices")):
        dv = devs[0]
        gpu = re.sub(r"^cuda:\d+\s+", "", dv.get("name", "?"))
        gpu = re.sub(r"\s+:\s+.*$", "", gpu)
        vram_total = dv.get("vram_total", 0) or 0
        vram_free = dv.get("vram_free", 0) or 0
        used = vram_total - vram_free
        pct = (used * 100 // vram_total) if vram_total > 0 else 0
        gpu_line = Text.assemble(
            ("GPU ", "bold"), gpu, "  ",
            ("VRAM ", "bold"), f"{fmt_gib(used)} / {fmt_gib(vram_total)} GiB  ",
            vram_bar(pct), f"  {pct}%",
        )
    else:
        gpu_line = Text("(ComfyUI :8188 unreachable)", style="yellow")

    parts: list[Any] = [gpu_line]

    # ── Ollama 로드 모델
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

    # ── ComfyUI 큐
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

    # ── Whisper systemd
    if wh:
        parts.append(Text.assemble(
            ("Whisper  ", "bold green"),
            f"model={wh.get('model', '?')}  ",
            f"device={wh.get('device', '?')}  ",
            f"status={wh.get('status', '?')}",
        ))

    return Panel(Group(*parts), title=f"[bold]{host}[/]", border_style="blue")


def whisper_shim_panel() -> Panel | None:
    """같은 호스트에 compose 의 whisper-shim 컨테이너가 있으면 백엔드별 up/inflight."""
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


def render(data: list[dict[str, Any]], interval: int) -> Group:
    header = Text.assemble(
        ("KloudChat GPU Monitor", "bold blue"),
        f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"   refresh {interval}s   ",
        ("Ctrl+C to quit", "dim"),
    )
    parts: list[Any] = [header]
    parts.extend(host_panel(d) for d in data)
    shim = whisper_shim_panel()
    if shim is not None:
        parts.append(shim)
    return Group(*parts)


async def main() -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--once", action="store_true", help="1회 출력 후 종료 (TUI 비활성)")
    p.add_argument("--interval", type=int, default=3, help="refresh 주기 초 (기본 3)")
    args = p.parse_args()

    hosts = collect_hosts()
    if not hosts:
        print("error: .env 의 OLLAMA_URLS / COMFYUI_URLS / WHISPER_URLS 에서 호스트 추출 0건",
              file=sys.stderr)
        return 1

    console = Console()
    async with httpx.AsyncClient() as client:
        if args.once or not console.is_terminal:
            data = await asyncio.gather(*(probe_host(client, h) for h in hosts))
            console.print(render(list(data), args.interval))
            return 0
        try:
            with Live(console=console, refresh_per_second=4, screen=True) as live:
                while True:
                    data = await asyncio.gather(*(probe_host(client, h) for h in hosts))
                    live.update(render(list(data), args.interval))
                    await asyncio.sleep(args.interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
