"""Performance monitoring: readers, sustained-overload rules, admin routes and the migration."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.deps import require_admin
from app.models.chat import ChatSession, PipelineRun
from app.models.metrics import MetricSample
from app.models.user import User, UserRole
from app.routers import perf as perf_router
from app.services.perf import host, live, prom, rules, sources, store

FIXTURES = Path(__file__).parent / "fixtures" / "perf"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# ── Prometheus text and vLLM ────────────────────────────────────────────


def test_vllm_text_is_read_into_gauges_and_histograms():
    samples = prom.parse((FIXTURES / "vllm_122b.prom").read_text())
    assert prom.total(samples, "vllm:num_requests_running") == 0.0
    ttft = prom.buckets(samples, "vllm:time_to_first_token_seconds")
    assert ttft[float("inf")] == 6385.0 and len(ttft) > 10
    # The fixture's cumulative histogram has a median between its bounds.
    assert 0 < prom.quantile(ttft, 0.5) < 60


def test_a_quantile_comes_from_the_change_between_two_scrapes():
    before = {0.5: 10.0, 1.0: 10.0, 5.0: 10.0, float("inf"): 10.0}
    after = {0.5: 10.0, 1.0: 12.0, 5.0: 30.0, float("inf"): 30.0}
    window = prom.delta(after, before)
    assert window == {0.5: 0.0, 1.0: 2.0, 5.0: 20.0, float("inf"): 20.0}
    assert prom.quantile(window, 0.95) == pytest.approx(1.0 + 4.0 * (19 - 2) / 18)
    assert prom.quantile(prom.delta(before, before), 0.5) is None
    # A restarted server counts from zero: the new counts stand.
    assert prom.delta(before, after) == before


def _vllm(running: float, waiting: float, kv: float, gen: float, ttft: dict[str, float]) -> str:
    lines = [
        f'vllm:num_requests_running{{engine="0",model_name="local/qwen3.8-27b"}} {running}',
        f'vllm:num_requests_waiting{{engine="0",model_name="local/qwen3.8-27b"}} {waiting}',
        f'vllm:kv_cache_usage_perc{{engine="0",model_name="local/qwen3.8-27b"}} {kv}',
        f'vllm:generation_tokens_total{{engine="0",model_name="local/qwen3.8-27b"}} {gen}',
        'vllm:num_preemptions_total{engine="0",model_name="local/qwen3.8-27b"} 3',
    ]
    for le, count in ttft.items():
        lines.append(
            f'vllm:time_to_first_token_seconds_bucket{{engine="0",le="{le}",'
            f'model_name="local/qwen3.8-27b"}} {count}'
        )
    return "\n".join(lines) + "\n"


def test_the_vllm_reader_reports_rates_and_latency_from_the_second_scrape_on():
    reader = sources.VllmReader()
    first = reader.rows("vllm-27b", _vllm(4, 0, 0.5, 1000, {"1.0": 5, "+Inf": 5}), now=0)
    assert ("vllm", "kv_cache_percent", "local/qwen3.8-27b", 50.0) in first
    assert not any(r[1] == "ttft_p95" for r in first)
    second = reader.rows("vllm-27b", _vllm(9, 3, 0.95, 7000, {"1.0": 5, "+Inf": 15}), now=60)
    by = {r[1]: r[3] for r in second}
    assert by["running"] == 9 and by["waiting"] == 3 and by["gen_tokens_per_s"] == 100
    assert by["preemptions"] == 0 and by["ttft_p95"] == 1.0
    # Unreachable: the model it served is reported down.
    assert reader.rows("vllm-27b", None) == [("vllm", "up", "local/qwen3.8-27b", 0.0)]


def test_the_gpu_reader_reads_the_exporter():
    text = "\n".join(
        [
            'kloudchat_node_info{hostname="spark-254"} 1',
            'kloudchat_gpu_utilization_percent{gpu="0",name="NVIDIA GB10"} 87',
            'kloudchat_gpu_temperature_celsius{gpu="0",name="NVIDIA GB10"} 66',
            'kloudchat_gpu_power_watts{gpu="0",name="NVIDIA GB10"} 81.5',
            "kloudchat_node_memory_total_bytes 1000",
            "kloudchat_node_memory_available_bytes 250",
            'kloudchat_node_cpu_seconds_total{mode="all"} 100',
            'kloudchat_node_cpu_seconds_total{mode="idle"} 80',
        ]
    )
    reader = sources.GpuReader()
    rows = {r[1]: r for r in reader.rows("gpu-1", text)}
    assert rows["gpu_util"] == ("gpu", "gpu_util", "gpu-1 · spark-254", 87.0)
    assert rows["node_mem_percent"][3] == 75.0 and rows["gpu_power"][3] == 81.5
    later = text.replace('mode="all"} 100', 'mode="all"} 200').replace(
        'mode="idle"} 80', 'mode="idle"} 130'
    )
    assert {r[1]: r[3] for r in reader.rows("gpu-1", later)}["node_cpu_percent"] == 50.0
    assert reader.rows("gpu-1", None) == [("gpu", "up", "gpu-1 · spark-254", 0.0)]


def test_the_model_log_becomes_latency_errors_and_a_minute_marker():
    start = NOW
    logs = [
        {
            "model_group": "local/qwen3.8-27b",
            "status": "success",
            "startTime": "2026-10-09T12:00:01+00:00",
            "endTime": "2026-10-09T12:00:05+00:00",
            "completionStartTime": "2026-10-09T12:00:02+00:00",
            "request_duration_ms": 4000,
            "completion_tokens": 100,
        },
        {
            "model_group": "local/qwen3.8-27b",
            "status": "failure",
            "startTime": "2026-10-09T12:00:30Z",
            "endTime": "2026-10-09T12:00:31Z",
            "completion_tokens": 0,
        },
        # Started in the next minute: left for that minute.
        {
            "model_group": "local/qwen3.8-27b",
            "status": "success",
            "startTime": "2026-10-09T12:01:00+00:00",
            "endTime": "2026-10-09T12:01:09+00:00",
        },
    ]
    rows = {
        (r[1], r[2]): r[3] for r in sources.model_rows(logs, start, start + timedelta(minutes=1))
    }
    assert rows[("calls", "*")] == 2 and rows[("calls", "local/qwen3.8-27b")] == 2
    assert rows[("errors", "local/qwen3.8-27b")] == 1
    assert rows[("latency_p95", "local/qwen3.8-27b")] == 4.0
    assert rows[("ttft_p50", "local/qwen3.8-27b")] == 1.0
    assert rows[("out_tokens", "local/qwen3.8-27b")] == 100
    assert sources.model_rows([], start, start + timedelta(minutes=1)) == [
        ("model", "calls", "*", 0.0)
    ]


# ── host and cgroups ────────────────────────────────────────────────────


def test_proc_parsers():
    stat = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\ncpu1 1 1 1 1\nintr 5\n"
    assert host.cpu_ticks(stat) == (1000, 800, 2)
    net = (
        "Inter-|   Receive\n face |bytes\n"
        "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
        "  eth0: 100 1 0 0 0 0 0 0 50 1 0 0 0 0 0 0\n"
        "veth1a: 700 1 0 0 0 0 0 0 700 1 0 0 0 0 0 0\n"
    )
    assert host.net_dev(net) == (100, 50)
    assert host.net_dev(net, physical_only=False) == (800, 750)
    disks = host.diskstats(
        "   8       0 sda 10 0 200 0 5 0 300 0 0 40 0\n"
        "   8       1 sda1 10 0 200 0 5 0 300 0 0 40 0\n"
        " 259       0 nvme0n1 1 0 2 0 3 0 4 0 0 5 0\n"
        "   7       0 loop0 1 0 2 0 3 0 4 0 0 5 0\n"
    )
    assert disks == {"sda": (200, 300, 40), "nvme0n1": (2, 4, 5)}
    assert host.io_bytes("8:0 rbytes=10 wbytes=20 rios=1\n259:0 rbytes=1 wbytes=2\n") == (11, 22)
    assert host.cpu_quota("max 100000") is None and host.cpu_quota("200000 100000") == 2.0
    trie = (
        "Main:\n  +-- 0.0.0.0/0\n     |-- 172.19.0.4\n        /32 host LOCAL\n"
        "     |-- 127.0.0.1\n        /32 host LOCAL\n"
    )
    assert host.local_ipv4(trie) == {"172.19.0.4"}


@pytest.mark.parametrize(
    ("fp", "sources_", "label"),
    [
        (
            "python3.12 uvicorn app.main:app --host 0.0.0.0 | python3.12 |",
            ["/home/k/data/files"],
            "api",
        ),
        (
            "python3.12 uvicorn app.main:app | python3.12 |",
            ["/var/lib/docker/volumes/x/_data"],
            "api (다른 인스턴스)",
        ),
        ("postgres | postgres |", ["/home/k/data/postgres"], "db"),
        ("postgres | postgres |", ["/home/k-llm/data/litellm/postgres"], "litellm-db"),
        ("caddy run --config /etc/caddy/Caddyfile | caddy |", [], "gateway"),
        ("python3.13 litellm --config /app/config.yaml | python3.13 |", [], "litellm"),
        ("python3.11 uvicorn app:app --host 0.0.0.0 --port 9000 | uvicorn |", [], "whisper-shim"),
        ("python3.11 uvicorn app:app --port 8080 --no-access-log | uvicorn |", [], "search-shim"),
        ("python3.11 uvicorn app:app --port 8080 | chrome uvicorn |", [], "crawl4ai-shim"),
        # A test runner's browser is not the fetch shim.
        ("node cli.js test | chrome node |", [], "container"),
        ("| headless_shell node uvicorn |", [], "print"),
        ("nginx: master process | nginx |", [], "nginx"),
        ("mystery | x |", [], "container"),
    ],
)
def test_containers_are_named_from_their_processes(fp, sources_, label):
    assert host.classify(fp, sources_, "/home/k/data/files") == label


def _fake_host(root: Path, *, ticks: tuple[int, int], usage: int, net: int) -> None:
    proc, cg = root / "proc", root / "cgroup"
    (proc / "1" / "net").mkdir(parents=True, exist_ok=True)
    (proc / "4242" / "net").mkdir(parents=True, exist_ok=True)
    busy, idle = ticks
    (proc / "stat").write_text(
        f"cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1\ncpu1 1\ncpu2 1\ncpu3 1\n"
    )
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 400 kB\n")
    (proc / "loadavg").write_text("1.50 1.0 0.5 1/100 4242\n")
    header = "Inter-|\n face |\n"
    (proc / "1" / "net" / "dev").write_text(
        header + f"  eth0: {net} 0 0 0 0 0 0 0 {net // 2} 0 0 0 0 0 0 0\n"
    )
    (proc / "diskstats").write_text("   8       0 sda 1 0 100 0 1 0 200 0 0 10 0\n")
    cid = "a" * 64
    (proc / "4242" / "cgroup").write_text(f"0::/../docker-{cid}.scope\n")
    (proc / "4242" / "cmdline").write_text("caddy\0run\0--config\0/etc/caddy/Caddyfile\0")
    (proc / "4242" / "comm").write_text("caddy\n")
    (proc / "4242" / "net" / "dev").write_text(
        header + f"  eth0: {net} 0 0 0 0 0 0 0 {net} 0 0 0 0 0 0 0\n"
    )
    scope = cg / "system.slice" / f"docker-{cid}.scope"
    scope.mkdir(parents=True, exist_ok=True)
    (scope / "cpu.stat").write_text(f"usage_usec {usage}\n")
    (scope / "cpu.max").write_text("max 100000\n")
    (scope / "memory.current").write_text("600000\n")
    (scope / "memory.stat").write_text("inactive_file 100000\n")
    (scope / "memory.max").write_text("1000000\n")
    (scope / "io.stat").write_text("8:0 rbytes=0 wbytes=0\n")


def test_the_host_reader_turns_two_readings_into_rates(tmp_path):
    _fake_host(tmp_path, ticks=(100, 900), usage=0, net=0)
    reader = host.HostReader(
        str(tmp_path / "proc"), str(tmp_path / "cgroup"), {"data": str(tmp_path)}, []
    )
    first = reader.sample(now=1000.0)
    assert ("host", "mem_percent", "host", 60.0) in first
    assert any(r[:3] == ("host", "disk_used_percent", "data") for r in first)
    assert not any(r[1] == "cpu_percent" for r in first)
    _fake_host(tmp_path, ticks=(400, 1600), usage=30_000_000, net=6000)
    rows = {(r[0], r[1], r[2]): r[3] for r in reader.sample(now=1060.0)}
    assert rows[("host", "cpu_percent", "host")] == 30.0
    assert rows[("host", "net_rx_bps", "host")] == 100.0
    assert rows[("container", "cpu_cores", "gateway")] == 0.5
    assert rows[("container", "cpu_percent", "gateway")] == 12.5  # of 4 CPUs
    assert rows[("container", "mem_bytes", "gateway")] == 500000
    assert rows[("container", "mem_limit_percent", "gateway")] == 50.0
    assert rows[("container", "net_tx_bps", "gateway")] == 100.0


def test_without_the_host_mount_nothing_is_read(tmp_path):
    reader = host.HostReader(str(tmp_path / "none"), str(tmp_path / "none"), {}, [])
    assert not reader.available()


# ── rules ───────────────────────────────────────────────────────────────


def _points(values: list[float], minutes_ago_first: int) -> list[tuple[datetime, float]]:
    start = NOW - timedelta(minutes=minutes_ago_first)
    return [(start + timedelta(minutes=i), v) for i, v in enumerate(values)]


def test_a_queue_that_lasts_is_gpu_saturation_but_a_spike_is_not():
    th = rules.Thresholds()
    lasting = {("vllm", "waiting", "local/qwen3.5-122b"): _points([5] * 12, 11)}
    verdicts = rules.evaluate(lasting, NOW, th, NOW)
    assert [v.code for v in verdicts] == ["gpu_saturated"]
    assert verdicts[0].level == "critical" and "GPU 포화" in verdicts[0].title
    assert verdicts[0].evidence[0]["windowMin"] == 10 and verdicts[0].evidence[0]["latest"] == 5
    assert rules.overall(verdicts) == "critical"
    spike = {("vllm", "waiting", "local/qwen3.5-122b"): _points([0] * 9 + [9, 9, 0], 11)}
    assert rules.evaluate(spike, NOW, th, NOW) == []
    too_few = {("vllm", "waiting", "m"): _points([9, 9], 1)}
    assert rules.evaluate(too_few, NOW, th, NOW) == []


def test_api_cpu_disk_errors_and_memory():
    th = rules.Thresholds()
    series = {
        ("container", "cpu_cores", "api · f9ff31"): _points([0.85] * 16, 15),
        ("container", "cpu_cores", "api (다른 인스턴스) · ad8a76"): _points([1.0] * 16, 15),
        ("host", "disk_used_percent", "data"): _points([96.0], 0),
        ("model", "calls", "z-ai/glm-5.3"): _points([10, 10, 10], 2),
        ("model", "errors", "z-ai/glm-5.3"): _points([1, 1, 2], 2),
        ("host", "mem_percent", "host"): _points([95.0] * 11, 10),
    }
    found = {v.code: v for v in rules.evaluate(series, NOW, th, NOW)}
    assert set(found) == {"api_cpu", "disk_full", "model_errors", "host_memory"}
    assert "api 복제본 추가 검토" in found["api_cpu"].title and found["api_cpu"].level == "warn"
    assert "f9ff31" in found["api_cpu"].title
    assert found["disk_full"].level == "critical"
    assert "13.3%" in found["model_errors"].detail
    assert all(v.action for v in found.values())


def test_kv_cache_with_preemption_is_critical_and_a_stalled_collector_is_named():
    th = rules.Thresholds()
    series = {
        ("vllm", "kv_cache_percent", "m"): _points([95.0] * 11, 10),
        ("vllm", "preemptions", "m"): _points([0, 2, 0], 3),
    }
    verdicts = rules.evaluate(series, NOW, th, NOW - timedelta(minutes=20))
    codes = {v.code: v.level for v in verdicts}
    assert codes == {"kv_cache_full": "critical", "collector_stalled": "warn"}


def test_a_gb10_resting_at_vllms_reservation_is_quiet_but_beyond_it_warns():
    th = rules.Thresholds()
    resting = {("gpu", "node_mem_percent", "gpu-2 · gx10-ffb4"): _points([97.2] * 11, 10)}
    assert rules.evaluate(resting, NOW, th, NOW) == []
    full = {("gpu", "node_mem_percent", "gpu-2 · gx10-ffb4"): _points([99.4] * 11, 10)}
    verdicts = rules.evaluate(full, NOW, th, NOW)
    assert [v.code for v in verdicts] == ["gpu_node_memory"]
    assert verdicts[0].evidence[0]["threshold"] == 99 and "gx10-ffb4" in verdicts[0].title


# ── live counters ───────────────────────────────────────────────────────


async def test_streams_are_counted_until_their_last_byte():
    counters = live.Counters()
    live_counters, live.counters = live.counters, counters
    try:
        seen: list[int] = []

        async def app(scope, receive, send):
            kind = b"text/event-stream" if scope["path"] == "/s" else b"application/json"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200 if kind != b"x" else 500,
                    "headers": [(b"content-type", kind)],
                }
            )
            seen.append(counters.streams)
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def send(_message):
            pass

        middleware = live.LoadMiddleware(app)
        await middleware({"type": "http", "path": "/s"}, None, send)
        await middleware({"type": "http", "path": "/j"}, None, send)
        assert seen == [1, 0] and counters.streams == 0
        drained = counters.drain()
        assert (
            drained["streams_peak"] == 1 and drained["requests"] == 1 and "http_p95_ms" in drained
        )
        assert counters.drain()["requests"] == 0
    finally:
        live.counters = live_counters


# ── storage and routes ─────────────────────────────────────────────────


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = [
        metadata.tables[m.__tablename__] for m in (User, ChatSession, PipelineRun, MetricSample)
    ]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


def _admin() -> User:
    return User(
        id="adm", email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin
    )


async def test_every_perf_route_is_admin_only():
    for route in perf_router.router.routes:
        calls = {d.call for d in route.dependant.dependencies}
        assert require_admin in calls, route.path
    member = User(id="m", email="m@example.com", name="회원", password_hash="x")
    with pytest.raises(HTTPException) as caught:
        await require_admin(member)
    assert caught.value.status_code == 403


async def test_the_overview_reads_back_verdicts_tables_and_charts(db):
    now = store.minute(datetime.now(UTC))
    for i in range(12):
        at = now - timedelta(minutes=11 - i)
        await store.write(
            db,
            at,
            [
                ("host", "cpu_percent", "host", 40.0 + i),
                ("host", "mem_percent", "host", 50.0),
                ("container", "cpu_cores", "api · f9ff31", 0.3),
                ("container", "cpu_percent", "api · f9ff31", 3.0),
                ("container", "mem_bytes", "api · f9ff31", 4e8),
                ("vllm", "up", "local/qwen3.5-122b", 1.0),
                ("vllm", "waiting", "local/qwen3.5-122b", 6.0),
                ("vllm", "ttft_p95", "local/qwen3.5-122b", 2.0 + i),
                ("app", "streams", "api · f9ff31", float(i)),
                ("model", "calls", "local/qwen3.5-122b", 5.0),
                ("model", "errors", "local/qwen3.5-122b", 0.0),
                ("model", "latency_p95", "local/qwen3.5-122b", 9.0),
            ],
        )
    db.add(
        PipelineRun(
            session_id="s",
            user_id="u",
            pipeline="report",
            stages=[
                {"stage": "outline", "status": "done", "ms": 12000},
                {"stage": "write", "status": "done", "ms": 60000},
                {"stage": "check", "status": "skipped", "ms": 0},
            ],
        )
    )
    await db.commit()

    out = await perf_router.overview(_admin(), db, range="1h")
    assert out["status"] == "critical"
    assert out["verdicts"][0]["code"] == "gpu_saturated"
    assert out["host"]["cpuPercent"] == 51.0 and out["host"]["cpuPeak"] == 51.0
    assert out["components"][0]["name"] == "api · f9ff31"
    assert out["components"][0]["memPeak"] == 4e8
    model = out["models"][0]
    assert model["model"] == "local/qwen3.5-122b" and model["calls"] == 60 and model["errors"] == 0
    assert out["vllm"][0]["waitingPeak"] == 6.0 and out["vllm"][0]["up"]
    assert out["replicas"][0]["streams"] == 11
    assert {r["stage"] for r in out["pipeline"]} == {"outline", "write"}
    overlay = out["charts"][0]
    assert overlay["key"] == "sessions_latency"
    assert {s["axis"] for s in overlay["series"]} == {"left", "right"}

    found = await perf_router.series(_admin(), db, range="1h", names="waiting", source="vllm")
    assert found["series"][0]["subject"] == "local/qwen3.5-122b"
    text = await perf_router.export(_admin(), db)
    assert 'kloudchat_perf_vllm_waiting{subject="local/qwen3.5-122b"} 6' in text


async def test_old_minutes_fold_into_ten_minute_rows_with_counts_summed(db):
    old = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    for i in range(20):
        await store.write(
            db,
            old + timedelta(minutes=i),
            [
                ("model", "calls", "m", 2.0),
                ("host", "cpu_percent", "host", float(i)),
            ],
        )
    folded = await store.rollup(db, old + timedelta(hours=50), raw_hours=48)
    assert folded == 40
    rows = (await db.exec(select(MetricSample).order_by(MetricSample.name, MetricSample.ts))).all()
    assert [(r.name, r.step, r.value, r.peak) for r in rows] == [
        ("calls", 600, 20.0, 2.0),
        ("calls", 600, 20.0, 2.0),
        ("cpu_percent", 600, 4.5, 9.0),
        ("cpu_percent", 600, 14.5, 19.0),
    ]
    await store.prune(db, old + timedelta(days=31), retention_days=30)
    assert (await db.exec(select(func.count()).select_from(MetricSample))).one() == 0


def test_the_migration_creates_and_drops_metric_samples_on_sqlite():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0056_metric_samples.py"
    spec = importlib.util.spec_from_file_location("metric_samples_0056", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0056", "0055")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("metric_samples")}
        assert {c.name for c in MetricSample.__table__.columns} == columns
        indexes = {i["name"] for i in sa.inspect(conn).get_indexes("metric_samples")}
        assert {"ix_metric_samples_name_ts", "ix_metric_samples_ts"} <= indexes
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "metric_samples" not in sa.inspect(conn).get_table_names()
