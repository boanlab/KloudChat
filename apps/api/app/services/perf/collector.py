"""Per-minute perf loop; advisory-lock holder also reads hosts, GPUs, LiteLLM log."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.db import SessionLocal
from app.services.perf import live, sources, store
from app.services.perf.host import HostReader, Row

log = logging.getLogger(__name__)

#: "kcperf" in ASCII
LOCK_KEY = 0x6B63_7065_7266
#: minutes LiteLLM gets to write a request's log row
MODEL_LOG_LAG_MIN = 3


def _pairs(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in spec.split(","):
        label, sep, path = item.strip().partition("=")
        if sep and label.strip() and path.strip():
            out[label.strip()] = path.strip()
    return out


def _list(spec: str) -> list[str]:
    return [x.strip() for x in spec.split(",") if x.strip()]


class Leader:
    """Advisory lock on a dedicated connection; released when the connection drops."""

    def __init__(self) -> None:
        self.conn = None

    async def held(self) -> bool:
        if not settings.database_url.startswith("postgresql"):
            return True  # single process on SQLite
        import asyncpg

        if self.conn is not None and not self.conn.is_closed():
            try:
                await self.conn.fetchval("SELECT 1", timeout=5)
                return True
            except Exception:  # noqa: BLE001 — a dead connection has let go of the lock
                await self.release()
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
        except Exception as exc:  # noqa: BLE001 — a database blip skips one minute
            log.info("perf lock connection failed: %s", type(exc).__name__)
            return False
        try:
            if await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
                self.conn = conn
                log.info("perf collector: this replica collects")
                return True
        except Exception as exc:  # noqa: BLE001
            log.info("perf lock failed: %s", type(exc).__name__)
        await conn.close()
        return False

    async def release(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None and not conn.is_closed():
            try:
                await conn.close(timeout=5)
            except Exception:  # noqa: BLE001
                conn.terminate()


class Collector:
    def __init__(self) -> None:
        self.host = HostReader(
            settings.perf_host_proc,
            settings.perf_host_cgroup,
            _pairs(settings.perf_disks),
            _list(settings.perf_dns_names),
        )
        self.vllm = sources.VllmReader()
        self.gpu = sources.GpuReader()
        self.leader = Leader()
        self.pruned_at = 0.0

    def app_rows(self) -> list[Row]:
        rows: list[Row] = [
            ("app", name, live.REPLICA, value) for name, value in live.counters.drain().items()
        ]
        try:
            from app.routers import sessions  # late import: circular

            turns = sum(len(signals) for signals in sessions._STOPPING.values())
            rows.append(("app", "turns", live.REPLICA, float(turns)))
        except Exception:  # noqa: BLE001 — the count is a nicety
            pass
        return rows

    async def collect(self, ts: datetime) -> list[Row]:
        rows: list[Row] = []
        if self.host.available():
            try:
                rows += await asyncio.to_thread(self.host.sample)
            except Exception as exc:  # noqa: BLE001 — one bad read skips the host this minute
                log.warning("perf host read failed: %s", exc)
        try:
            rows += await sources.scrape_targets(_list(settings.perf_targets), self.vllm, self.gpu)
        except Exception as exc:  # noqa: BLE001
            log.warning("perf scrape failed: %s", exc)
        return rows

    async def model_minute(self, ts: datetime) -> None:
        """LiteLLM log for one minute a few minutes back, once."""
        if not settings.perf_model_logs:
            return
        minute = ts - timedelta(minutes=MODEL_LOG_LAG_MIN)
        async with SessionLocal() as db:
            if await store.exists(db, minute, "model", "calls", "*"):
                return
        logs = await sources.model_logs(minute, minute + timedelta(minutes=1))
        if logs is None:
            return
        async with SessionLocal() as db:
            await store.write(
                db, minute, sources.model_rows(logs, minute, minute + timedelta(minutes=1))
            )

    async def tick(self, ts: datetime) -> None:
        async with SessionLocal() as db:
            await store.write(db, ts, self.app_rows())
        if not await self.leader.held():
            return
        rows = await self.collect(ts)
        async with SessionLocal() as db:
            await store.write(db, ts, rows)
        try:
            await self.model_minute(ts)
        except Exception as exc:  # noqa: BLE001
            log.warning("perf model log failed: %s", exc)
        async with SessionLocal() as db:
            await store.rollup(db, ts, settings.perf_raw_hours)
            if time.monotonic() - self.pruned_at > 3600:
                await store.prune(db, ts, settings.perf_retention_days)
                self.pruned_at = time.monotonic()


async def run() -> None:
    """Until cancelled; never raises otherwise."""
    collector = Collector()
    try:
        while True:
            # offset so replicas don't race the lock
            now = time.time()
            await asyncio.sleep(60 - now % 60 + 2)
            ts = store.minute(datetime.now(UTC))
            try:
                await collector.tick(ts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the next minute tries again
                log.warning("perf collection failed: %s", exc)
    finally:
        await collector.leader.release()
