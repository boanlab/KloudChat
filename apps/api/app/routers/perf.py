"""Admin 「성능 모니터링」: verdicts, current figures, peaks and charts from
`metric_samples` (`services/perf`), and a Prometheus export of the latest values."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlmodel import select

from app.core.config import settings
from app.core.deps import AdminUser, DbSession
from app.models.chat import PipelineRun
from app.services.perf import rules, store
from app.services.perf.store import RANGES, Key, Point

router = APIRouter(prefix="/admin/perf", tags=["admin"])

RangeName = Query("1h", pattern=r"^(1h|24h|7d|30d)$")

_PROM_NAME = re.compile(r"[^a-zA-Z0-9_]")


def thresholds() -> rules.Thresholds:
    return rules.Thresholds(
        waiting_max=settings.perf_vllm_waiting_max,
        kv_cache_max=settings.perf_kv_cache_max,
        ttft_p95_max_sec=settings.perf_ttft_p95_max_sec,
        api_cpu_max=settings.perf_api_cpu_max,
        mem_max=settings.perf_mem_max,
        disk_warn=settings.perf_disk_warn,
        disk_critical=settings.perf_disk_critical,
        error_rate_max=settings.perf_error_rate_max,
        gpu_temp_max=settings.perf_gpu_temp_max,
        gpu_node_mem_max=settings.perf_gpu_node_mem_max,
        sustain_min=settings.perf_sustain_min,
        api_cpu_sustain_min=settings.perf_api_cpu_sustain_min,
    )


def _points(points: list[Point]) -> list[list[float]]:
    return [[at * 1000, round(mean, 4), round(peak, 4)] for at, mean, peak in points]


def _sum_series(groups: list[list[Point]]) -> list[Point]:
    """Bucket-wise sum of several series (replicas, models)."""
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for points in groups:
        for at, mean, peak in points:
            acc[at][0] += mean
            acc[at][1] += peak
    return [(at, v[0], v[1]) for at, v in sorted(acc.items())]


def _top(
    series: dict[Key, list[Point]], name: str, n: int, source: str | None = None
) -> list[tuple[str, list[Point]]]:
    found = [
        (k[2], pts)
        for k, pts in series.items()
        if k[1] == name and (source is None or k[0] == source) and pts
    ]
    found.sort(key=lambda item: -sum(p[1] for p in item[1]) / len(item[1]))
    return found[:n]


def _chart(
    key: str, title: str, unit: str, lines: list[tuple[str, list[Point]]], axis: str = "left"
) -> dict:
    return {
        "key": key,
        "title": title,
        "unit": unit,
        "series": [{"name": name, "axis": axis, "points": _points(pts)} for name, pts in lines],
    }


def _peak(series: dict[Key, list[Point]], key: Key) -> float | None:
    points = series.get(key)
    return max(p[2] for p in points) if points else None


def _mean(series: dict[Key, list[Point]], key: Key) -> float | None:
    points = series.get(key)
    return sum(p[1] for p in points) / len(points) if points else None


def _total(series: dict[Key, list[Point]], key: Key) -> float:
    return sum(p[1] for p in series.get(key, []))


async def _pipeline_stages(db, since: datetime) -> list[dict]:
    rows = (
        await db.exec(
            select(PipelineRun.pipeline, PipelineRun.stages)
            .where(PipelineRun.created_at >= since)
            .order_by(PipelineRun.created_at.desc())
            .limit(3000)
        )
    ).all()
    durations: dict[str, list[float]] = defaultdict(list)
    for _pipeline, stages in rows:
        for stage in stages or []:
            if (
                isinstance(stage, dict)
                and stage.get("status") in ("done", "ok", "passed")
                and isinstance(stage.get("ms"), int | float)
                and stage["ms"] > 0
            ):
                durations[str(stage.get("stage") or "?")[:60]].append(stage["ms"] / 1000)
    out = []
    for stage, values in durations.items():
        values.sort()
        out.append(
            {
                "stage": stage,
                "runs": len(values),
                "p50": values[len(values) // 2],
                "p95": values[min(len(values) - 1, int(0.95 * len(values)))],
                "max": values[-1],
            }
        )
    out.sort(key=lambda r: -r["p95"])
    return out[:20]


@router.get("/overview")
async def overview(admin: AdminUser, db: DbSession, range: str = RangeName):  # noqa: A002
    now = datetime.now(UTC)
    seconds, step = RANGES[range]
    start = now - timedelta(seconds=seconds)

    last = await store.last_sample_at(db)
    recent = await store.recent(db, since=now - timedelta(minutes=45), names=rules.NAMES)
    verdicts = rules.evaluate(recent, now, thresholds(), last)
    cur = await store.latest(db, since=now - timedelta(minutes=5))
    # Only the newest minute of each source: a renamed or stopped container drops out.
    newest: dict[str, datetime] = {}
    for (source, _, _), (at, _) in cur.items():
        newest[source] = max(newest.get(source, at), at)
    cur = {k: v for k, v in cur.items() if v[0] >= newest[k[0]] - timedelta(seconds=90)}

    def now_of(source: str, name: str, subject: str) -> float | None:
        found = cur.get((source, name, subject))
        return found[1] if found else None

    charted = await store.series(
        db,
        start=start,
        end=now,
        step=step,
        names=[
            "cpu_percent",
            "mem_percent",
            "mem_bytes",
            "net_rx_bps",
            "net_tx_bps",
            "disk_busy_percent",
            "streams",
            "turns",
            "requests",
            "ttft_p95",
            "e2e_p95",
            "latency_p95",
            "calls",
            "waiting",
            "running",
            "kv_cache_percent",
            "gpu_util",
            "node_mem_percent",
            "gpu_temp",
        ],
    )
    # One bucket over the whole range: its peak is the range's peak.
    whole = await store.series(
        db,
        start=start,
        end=now,
        step=seconds + step,
        names=[
            "cpu_percent",
            "cpu_cores",
            "mem_bytes",
            "mem_limit_percent",
            "cpu_limit_percent",
            "waiting",
            "running",
            "kv_cache_percent",
            "ttft_p95",
            "e2e_p95",
            "preemptions",
            "requests",
            "calls",
            "errors",
            "out_tokens",
            "latency_p50",
            "latency_p95",
            "ttft_p50",
            "gpu_util",
            "gpu_temp",
            "gpu_power",
            "node_mem_percent",
            "streams",
            "turns",
            "errors_5xx",
            "http_p95_ms",
        ],
    )
    whole = {k: [(0, *_fold(pts, k[1] in store.SUM_NAMES))] for k, pts in whole.items()}

    host = {
        "cpuPercent": now_of("host", "cpu_percent", "host"),
        "cpus": now_of("host", "cpus", "host"),
        "memPercent": now_of("host", "mem_percent", "host"),
        "memUsed": now_of("host", "mem_used_bytes", "host"),
        "memTotal": now_of("host", "mem_total_bytes", "host"),
        "load1": now_of("host", "load1", "host"),
        "netRx": now_of("host", "net_rx_bps", "host"),
        "netTx": now_of("host", "net_tx_bps", "host"),
        "cpuPeak": _peak(charted, ("host", "cpu_percent", "host")),
        "memPeak": _peak(charted, ("host", "mem_percent", "host")),
        "disks": [
            {"label": k[2], "usedPercent": v[1], "free": now_of("host", "disk_free_bytes", k[2])}
            for k, v in sorted(cur.items())
            if k[:2] == ("host", "disk_used_percent")
        ],
        "devices": [
            {
                "device": k[2],
                "busyPercent": v[1],
                "read": now_of("host", "disk_read_bps", k[2]),
                "write": now_of("host", "disk_write_bps", k[2]),
            }
            for k, v in sorted(cur.items())
            if k[:2] == ("host", "disk_busy_percent")
        ],
    }

    subjects = sorted({k[2] for k in cur if k[0] == "container"})
    components = []
    for subject in subjects:

        def c(name: str, subject: str = subject) -> float | None:
            return now_of("container", name, subject)

        components.append(
            {
                "name": subject,
                "cpuPercent": c("cpu_percent"),
                "cpuCores": c("cpu_cores"),
                "cpuLimitPercent": c("cpu_limit_percent"),
                "cpuCoresPeak": _peak(whole, ("container", "cpu_cores", subject)),
                "memBytes": c("mem_bytes"),
                "memLimitPercent": c("mem_limit_percent"),
                "memPeak": _peak(whole, ("container", "mem_bytes", subject)),
                "netRx": c("net_rx_bps"),
                "netTx": c("net_tx_bps"),
                "ioRead": c("io_read_bps"),
                "ioWrite": c("io_write_bps"),
            }
        )
    components.sort(key=lambda r: -(r["cpuCores"] or 0))

    models = []
    for key in whole:
        if key[0] != "model" or key[1] != "calls" or key[2] == "*":
            continue
        calls = _total(whole, key)
        errors = _total(whole, ("model", "errors", key[2]))
        models.append(
            {
                "model": key[2],
                "calls": calls,
                "errors": errors,
                "errorRate": 100 * errors / calls if calls else 0.0,
                "latencyP50": _mean(whole, ("model", "latency_p50", key[2])),
                "latencyP95": _mean(whole, ("model", "latency_p95", key[2])),
                "latencyP95Peak": _peak(whole, ("model", "latency_p95", key[2])),
                "ttftP50": _mean(whole, ("model", "ttft_p50", key[2])),
                "outTokens": _total(whole, ("model", "out_tokens", key[2])),
            }
        )
    models.sort(key=lambda r: -r["calls"])

    vllm = []
    for key in sorted(k for k in cur if k[:2] == ("vllm", "up")):
        m = key[2]
        vllm.append(
            {
                "model": m,
                "up": bool(cur[key][1]),
                "running": now_of("vllm", "running", m),
                "waiting": now_of("vllm", "waiting", m),
                "kvCache": now_of("vllm", "kv_cache_percent", m),
                "ttftP95": now_of("vllm", "ttft_p95", m),
                "e2eP95": now_of("vllm", "e2e_p95", m),
                "genTps": now_of("vllm", "gen_tokens_per_s", m),
                "waitingPeak": _peak(whole, ("vllm", "waiting", m)),
                "runningPeak": _peak(whole, ("vllm", "running", m)),
                "kvPeak": _peak(whole, ("vllm", "kv_cache_percent", m)),
                "ttftP95Peak": _peak(whole, ("vllm", "ttft_p95", m)),
                "preemptions": _total(whole, ("vllm", "preemptions", m)),
                "requests": _total(whole, ("vllm", "requests", m)),
            }
        )

    gpus = []
    for key in sorted(k for k in cur if k[:2] == ("gpu", "up")):
        g = key[2]
        gpus.append(
            {
                "node": g,
                "up": bool(cur[key][1]),
                "util": now_of("gpu", "gpu_util", g),
                "memPercent": now_of("gpu", "gpu_mem_percent", g),
                "temp": now_of("gpu", "gpu_temp", g),
                "power": now_of("gpu", "gpu_power", g),
                "nodeMemPercent": now_of("gpu", "node_mem_percent", g),
                "nodeCpuPercent": now_of("gpu", "node_cpu_percent", g),
                "utilPeak": _peak(whole, ("gpu", "gpu_util", g)),
            }
        )

    replicas = []
    for key in sorted(k for k in cur if k[:2] == ("app", "streams")):
        r = key[2]
        replicas.append(
            {
                "name": r,
                "streams": cur[key][1],
                "turns": now_of("app", "turns", r),
                "requests": now_of("app", "requests", r),
                "errors": now_of("app", "errors_5xx", r),
                "httpP95Ms": now_of("app", "http_p95_ms", r),
                "streamsPeak": _peak(whole, ("app", "streams", r)),
            }
        )

    sessions = _sum_series([pts for k, pts in charted.items() if k[:2] == ("app", "streams")])
    turns = _sum_series([pts for k, pts in charted.items() if k[:2] == ("app", "turns")])
    latency_lines = [(f"TTFT p95 · {m}", pts) for m, pts in _top(charted, "ttft_p95", 2, "vllm")]
    latency_lines += [
        (f"응답 p95 · {m}", pts) for m, pts in _top(charted, "latency_p95", 2, "model")
    ]
    charts = [
        {
            "key": "sessions_latency",
            "title": "세션 수 vs 모델 지연",
            "unit": "",
            "series": [
                {
                    "name": "동시 스트림",
                    "axis": "left",
                    "unit": "count",
                    "points": _points(sessions),
                },
                {"name": "진행 중 턴", "axis": "left", "unit": "count", "points": _points(turns)},
            ]
            + [
                {"name": n, "axis": "right", "unit": "s", "points": _points(p)}
                for n, p in latency_lines
            ],
        },
        _chart(
            "host",
            "호스트 CPU · 메모리",
            "%",
            [
                ("CPU", charted.get(("host", "cpu_percent", "host"), [])),
                ("메모리", charted.get(("host", "mem_percent", "host"), [])),
            ],
        ),
        _chart(
            "components_cpu",
            "컴포넌트 CPU (호스트 대비)",
            "%",
            _top(charted, "cpu_percent", 6, "container"),
        ),
        _chart(
            "components_mem", "컴포넌트 메모리", "bytes", _top(charted, "mem_bytes", 6, "container")
        ),
        _chart("vllm_queue", "모델 서버 대기 요청", "count", _top(charted, "waiting", 6, "vllm")),
        _chart("vllm_kv", "KV 캐시 사용률", "%", _top(charted, "kv_cache_percent", 6, "vllm")),
        _chart("gpu_util", "GPU 사용률", "%", _top(charted, "gpu_util", 4, "gpu")),
        _chart(
            "net",
            "호스트 네트워크",
            "bps",
            [
                ("수신", charted.get(("host", "net_rx_bps", "host"), [])),
                ("송신", charted.get(("host", "net_tx_bps", "host"), [])),
            ],
        ),
        _chart("disk", "디스크 사용 시간", "%", _top(charted, "disk_busy_percent", 4, "host")),
    ]

    th = thresholds()
    return {
        "generatedAt": now.isoformat(),
        "range": range,
        "stepSec": step,
        "status": rules.overall(verdicts),
        "verdicts": [v.as_dict() for v in verdicts],
        "collector": {
            "enabled": settings.perf_monitoring,
            "lastSampleAt": last.isoformat() if last else None,
            "hostAvailable": any(k[0] == "host" for k in cur),
            "modelLogAt": (
                m.isoformat() if (m := await store.last_sample_at(db, "model")) else None
            ),
            "targets": settings.perf_targets.split(","),
        },
        "host": host,
        "components": components,
        "models": models,
        "vllm": vllm,
        "gpus": gpus,
        "replicas": replicas,
        "pipeline": await _pipeline_stages(db, start),
        "charts": charts,
        "thresholds": {
            "waitingMax": th.waiting_max,
            "kvCacheMax": th.kv_cache_max,
            "ttftP95MaxSec": th.ttft_p95_max_sec,
            "apiCpuMax": th.api_cpu_max,
            "memMax": th.mem_max,
            "diskWarn": th.disk_warn,
            "diskCritical": th.disk_critical,
            "errorRateMax": th.error_rate_max,
            "gpuTempMax": th.gpu_temp_max,
            "gpuNodeMemMax": th.gpu_node_mem_max,
            "sustainMin": th.sustain_min,
            "apiCpuSustainMin": th.api_cpu_sustain_min,
        },
    }


def _fold(points: list[Point], counts: bool) -> tuple[float, float]:
    """Several buckets into one: sum for counts, else mean of means; max of peaks."""
    total = sum(p[1] for p in points)
    return (total if counts else total / len(points)), max(p[2] for p in points)


@router.get("/series")
async def series(
    admin: AdminUser,
    db: DbSession,
    range: str = RangeName,  # noqa: A002
    names: str = Query(..., max_length=500),
    source: str | None = Query(None, max_length=40),
):
    """Any stored series by name, bucketed for the range: {source, name, subject, points}."""
    now = datetime.now(UTC)
    seconds, step = RANGES[range]
    wanted = [n.strip() for n in names.split(",") if n.strip()][:20]
    found = await store.series(
        db, start=now - timedelta(seconds=seconds), end=now, step=step, names=wanted, source=source
    )
    return {
        "range": range,
        "stepSec": step,
        "series": [
            {"source": k[0], "name": k[1], "subject": k[2], "points": _points(v)}
            for k, v in sorted(found.items())
        ],
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def export(admin: AdminUser, db: DbSession) -> str:
    """The latest value of every series in the Prometheus text format (for Grafana)."""
    lines: list[str] = []
    for (source, name, subject), (at, value) in sorted(
        (await store.latest(db, since=datetime.now(UTC) - timedelta(minutes=5))).items()
    ):
        metric = _PROM_NAME.sub("_", f"kloudchat_perf_{source}_{name}")
        label = subject.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        lines.append(f'{metric}{{subject="{label}"}} {value:g} {int(at.timestamp() * 1000)}')
    return "\n".join(lines) + "\n"
