"""Network readers: GPU node /metrics via the KloudChat-LLM gateway, LiteLLM request log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings
from app.services import settings_store
from app.services.perf import prom
from app.services.perf.host import Row

log = logging.getLogger(__name__)

HEADER = "X-Tools-Key"
#: vLLM histograms as quantiles: (family, short name)
_HISTOGRAMS = (
    ("vllm:time_to_first_token_seconds", "ttft"),
    ("vllm:e2e_request_latency_seconds", "e2e"),
    ("vllm:request_queue_time_seconds", "queue"),
)


async def metrics_base() -> str:
    """`perf_metrics_url`, else `<backend>/metrics`, else ""."""
    if settings.perf_metrics_url.strip():
        return settings.perf_metrics_url.strip().rstrip("/")
    values = await settings_store.all_values()
    base = (values.get(settings_store.BACKEND_BASE_URL) or "").strip() or settings.backend_base_url
    return f"{base.strip().rstrip('/')}/metrics" if base.strip() else ""


async def scrape(client: httpx.AsyncClient, base: str, target: str) -> str | None:
    """One target's text, or None unless 200."""
    key = settings.tools_gateway_key.strip()
    try:
        response = await client.get(
            f"{base}/{target}", headers={HEADER: key} if key else {}, follow_redirects=False
        )
    except httpx.HTTPError as exc:
        log.info("perf scrape %s failed: %s", target, type(exc).__name__)
        return None
    if response.status_code != 200:
        return None
    return response.text[:4_000_000]


class VllmReader:
    """Gauges as read; counters/histograms as deltas since last scrape."""

    def __init__(self) -> None:
        self.prev: dict[str, tuple[float, dict]] = {}
        self.model_of: dict[str, str] = {}

    def rows(self, target: str, text: str | None, now: float | None = None) -> list[Row]:
        now = time.time() if now is None else now
        if text is None:
            return [("vllm", "up", self.model_of.get(target, target), 0.0)]
        samples = prom.parse(text)
        model = next((s.labels["model_name"] for s in samples if "model_name" in s.labels), target)
        self.model_of[target] = model
        out: list[Row] = [("vllm", "up", model, 1.0)]
        for name, metric in (
            ("running", "vllm:num_requests_running"),
            ("waiting", "vllm:num_requests_waiting"),
        ):
            value = prom.total(samples, metric)
            if value is not None:
                out.append(("vllm", name, model, value))
        kv = [
            s.value
            for s in samples
            if s.name in ("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc")
        ]
        if kv:
            out.append(("vllm", "kv_cache_percent", model, 100.0 * max(kv)))

        state = {
            "preempt": prom.total(samples, "vllm:num_preemptions_total"),
            "gen": prom.total(samples, "vllm:generation_tokens_total"),
            "prompt": prom.total(samples, "vllm:prompt_tokens_total"),
            "done": prom.total(samples, "vllm:e2e_request_latency_seconds_count"),
            **{short: prom.buckets(samples, family) for family, short in _HISTOGRAMS},
        }
        prev = self.prev.get(target)
        self.prev[target] = (now, state)
        if not prev or now <= prev[0]:
            return out
        dt = now - prev[0]
        old = prev[1]

        def change(key: str) -> float | None:
            if state[key] is None or old.get(key) is None:
                return None
            diff = state[key] - old[key]
            return state[key] if diff < 0 else diff  # counter reset: restarted server

        if (v := change("preempt")) is not None:
            out.append(("vllm", "preemptions", model, v))
        if (v := change("done")) is not None:
            out.append(("vllm", "requests", model, v))
        if (v := change("gen")) is not None:
            out.append(("vllm", "gen_tokens_per_s", model, v / dt))
        if (v := change("prompt")) is not None:
            out.append(("vllm", "prompt_tokens_per_s", model, v / dt))
        for _, short in _HISTOGRAMS:
            window = prom.delta(state[short], old.get(short))
            for q, label in ((0.5, "p50"), (0.95, "p95")):
                value = prom.quantile(window, q)
                if value is not None:
                    out.append(("vllm", f"{short}_{label}", model, value))
        return out


class GpuReader:
    def __init__(self) -> None:
        self.prev: dict[str, tuple[float, float]] = {}
        self.subject_of: dict[str, str] = {}

    def rows(self, target: str, text: str | None) -> list[Row]:
        if text is None:
            return [("gpu", "up", self.subject_of.get(target, target), 0.0)]
        samples = prom.parse(text)
        host = next(
            (s.labels.get("hostname", "") for s in samples if s.name == "kloudchat_node_info"), ""
        )
        subject = f"{target} · {host}" if host else target
        self.subject_of[target] = subject
        out: list[Row] = [("gpu", "up", subject, 1.0)]

        def values(name: str) -> list[float]:
            return [s.value for s in samples if s.name == name]

        if util := values("kloudchat_gpu_utilization_percent"):
            out.append(("gpu", "gpu_util", subject, sum(util) / len(util)))
        used, size = (
            values("kloudchat_gpu_memory_used_bytes"),
            values("kloudchat_gpu_memory_total_bytes"),
        )
        if used and size and sum(size) > 0:
            out.append(("gpu", "gpu_mem_percent", subject, 100.0 * sum(used) / sum(size)))
        if temp := values("kloudchat_gpu_temperature_celsius"):
            out.append(("gpu", "gpu_temp", subject, max(temp)))
        if power := values("kloudchat_gpu_power_watts"):
            out.append(("gpu", "gpu_power", subject, sum(power)))
        mem_total = prom.total(samples, "kloudchat_node_memory_total_bytes")
        mem_free = prom.total(samples, "kloudchat_node_memory_available_bytes")
        if mem_total and mem_free is not None:
            # GB10 unified memory = model memory
            out.append(
                ("gpu", "node_mem_percent", subject, 100.0 * (mem_total - mem_free) / mem_total)
            )
        load = prom.total(samples, "kloudchat_node_load1")
        if load is not None:
            out.append(("gpu", "load1", subject, load))
        cpu_all = next(
            (
                s.value
                for s in samples
                if s.name == "kloudchat_node_cpu_seconds_total" and s.labels.get("mode") == "all"
            ),
            None,
        )
        cpu_idle = next(
            (
                s.value
                for s in samples
                if s.name == "kloudchat_node_cpu_seconds_total" and s.labels.get("mode") == "idle"
            ),
            None,
        )
        if cpu_all is not None and cpu_idle is not None:
            prev = self.prev.get(target)
            self.prev[target] = (cpu_all, cpu_idle)
            if prev and cpu_all > prev[0]:
                busy = (cpu_all - prev[0]) - (cpu_idle - prev[1])
                out.append(
                    (
                        "gpu",
                        "node_cpu_percent",
                        subject,
                        max(0.0, 100.0 * busy / (cpu_all - prev[0])),
                    )
                )
        return out


async def scrape_targets(targets: list[str], vllm: VllmReader, gpu: GpuReader) -> list[Row]:
    base = await metrics_base()
    if not base or not targets:
        return []
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
        texts = await asyncio.gather(*(scrape(client, base, t) for t in targets))
    rows: list[Row] = []
    for target, text in zip(targets, texts, strict=True):
        if target.startswith("gpu-"):
            rows += gpu.rows(target, text)
        else:
            rows += vllm.rows(target, text)
    return rows


def _when(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def model_rows(logs: list[dict], start: datetime, end: datetime) -> list[Row]:
    """Per-model stats in [start, end); a "*" `calls` row marks the minute read."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in logs:
        began = _when(row.get("startTime"))
        if began is None or not (start <= began < end):
            continue
        model = str(row.get("model_group") or row.get("model") or "?")[:120]
        groups[model].append(row)
    out: list[Row] = [("model", "calls", "*", float(sum(len(v) for v in groups.values())))]
    for model, rows in groups.items():
        latency: list[float] = []
        first: list[float] = []
        errors = tokens = 0
        for row in rows:
            if row.get("status") not in (None, "", "success"):
                errors += 1
            began, ended = _when(row.get("startTime")), _when(row.get("endTime"))
            ms = row.get("request_duration_ms")
            if isinstance(ms, int | float) and ms >= 0:
                latency.append(ms / 1000)
            elif began and ended:
                latency.append(max((ended - began).total_seconds(), 0.0))
            token_at = _when(row.get("completionStartTime"))
            if began and token_at and token_at >= began:
                first.append((token_at - began).total_seconds())
            tokens += int(row.get("completion_tokens") or 0)
        out += [
            ("model", "calls", model, float(len(rows))),
            ("model", "errors", model, float(errors)),
            ("model", "out_tokens", model, float(tokens)),
        ]
        if latency:
            out += [
                ("model", "latency_p50", model, _pct(latency, 0.5)),
                ("model", "latency_p95", model, _pct(latency, 0.95)),
            ]
        if first:
            out += [
                ("model", "ttft_p50", model, _pct(first, 0.5)),
                ("model", "ttft_p95", model, _pct(first, 0.95)),
            ]
    return out


async def model_logs(start: datetime, end: datetime, max_pages: int = 30) -> list[dict] | None:
    """LiteLLM spend log rows in [start, end), or None; no prompts/responses."""
    base, key = await settings_store.litellm_config()
    if not base or not key:
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    rows: list[dict] = []
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=httpx.Timeout(15.0, connect=4.0),
    ) as client:
        for page in range(1, max_pages + 1):
            try:
                response = await client.get(
                    "/spend/logs/v2",
                    params={
                        "start_date": start.strftime(fmt),
                        # inclusive to the second; `model_rows` drops rows past `end`
                        "end_date": (end + timedelta(seconds=1)).strftime(fmt),
                        "page": page,
                        "page_size": 100,
                        "sort_by": "startTime",
                        "sort_order": "asc",
                    },
                )
            except httpx.HTTPError as exc:
                log.info("perf model log unreadable: %s", type(exc).__name__)
                return None if page == 1 else rows
            if response.status_code != 200:
                log.info("perf model log answered %s", response.status_code)
                return None if page == 1 else rows
            body = response.json()
            data = body.get("data") if isinstance(body, dict) else body
            if not isinstance(data, list):
                return rows
            rows += [r for r in data if isinstance(r, dict)]
            total_pages = body.get("total_pages") if isinstance(body, dict) else None
            if len(data) < 100 or (isinstance(total_pages, int) and page >= total_pages):
                break
    return rows
