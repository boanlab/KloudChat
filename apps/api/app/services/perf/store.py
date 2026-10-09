"""Writing, rolling up and reading `metric_samples`."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, cast, delete, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.metrics import MetricSample
from app.services.perf.host import Row

ROLLUP_STEP = 600

#: Per-minute counts: rolled up and bucketed as sums, not means.
SUM_NAMES = frozenset({"calls", "errors", "out_tokens", "requests", "preemptions", "errors_5xx"})

#: Range → (seconds, display bucket in seconds).
RANGES: dict[str, tuple[int, int]] = {
    "1h": (3600, 60),
    "24h": (86400, 600),
    "7d": (7 * 86400, 3600),
    "30d": (30 * 86400, 4 * 3600),
}

Key = tuple[str, str, str]  # (source, name, subject)
Point = tuple[int, float, float]  # (epoch seconds, mean, peak)


def minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


def as_utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


async def write(db: AsyncSession, ts: datetime, rows: Iterable[Row]) -> int:
    count = 0
    for source, name, subject, value in rows:
        if value != value:  # NaN
            continue
        db.add(
            MetricSample(
                ts=ts,
                step=60,
                source=source,
                name=name,
                subject=subject[:200],
                value=float(value),
                peak=float(value),
            )
        )
        count += 1
    await db.commit()
    return count


async def exists(db: AsyncSession, ts: datetime, source: str, name: str, subject: str) -> bool:
    found = await db.exec(
        select(MetricSample.id)
        .where(
            MetricSample.ts == ts,
            MetricSample.source == source,
            MetricSample.name == name,
            MetricSample.subject == subject,
        )
        .limit(1)
    )
    return found.first() is not None


async def rollup(db: AsyncSession, now: datetime, raw_hours: int, max_hours: int = 3) -> int:
    """Folds raw rows older than `raw_hours` into 10-minute rows, an hour at a time."""
    cutoff = now - timedelta(hours=raw_hours)
    folded = 0
    for _ in range(max_hours):
        oldest = (
            await db.exec(
                select(func.min(MetricSample.ts)).where(
                    MetricSample.step == 60, MetricSample.ts < cutoff
                )
            )
        ).first()
        if oldest is None:
            break
        start = as_utc(oldest).replace(minute=0, second=0, microsecond=0)
        end = min(start + timedelta(hours=1), cutoff)
        rows = (
            await db.exec(
                select(MetricSample).where(
                    MetricSample.step == 60, MetricSample.ts >= start, MetricSample.ts < end
                )
            )
        ).all()
        groups: dict[tuple, list[MetricSample]] = defaultdict(list)
        for row in rows:
            at = as_utc(row.ts)
            bucket = at - timedelta(
                minutes=at.minute % 10, seconds=at.second, microseconds=at.microsecond
            )
            groups[(bucket, row.source, row.name, row.subject)].append(row)
        for (bucket, source, name, subject), members in groups.items():
            total = sum(m.value for m in members)
            db.add(
                MetricSample(
                    ts=bucket,
                    step=ROLLUP_STEP,
                    source=source,
                    name=name,
                    subject=subject,
                    value=total if name in SUM_NAMES else total / len(members),
                    peak=max((m.peak if m.peak is not None else m.value) for m in members),
                )
            )
        await db.exec(
            delete(MetricSample)
            .where(MetricSample.step == 60, MetricSample.ts >= start, MetricSample.ts < end)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        folded += len(rows)
    return folded


async def prune(db: AsyncSession, now: datetime, retention_days: int) -> None:
    await db.exec(
        delete(MetricSample)
        .where(MetricSample.ts < now - timedelta(days=retention_days))
        .execution_options(synchronize_session=False)
    )
    await db.commit()


def _bucket(db: AsyncSession, step: int):
    if db.bind.dialect.name == "sqlite":
        epoch = cast(func.strftime("%s", MetricSample.ts), Integer)
    else:
        epoch = func.floor(func.extract("epoch", MetricSample.ts))
    return cast(func.floor(epoch / step), Integer) * step


async def series(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    step: int,
    names: Iterable[str],
    source: str | None = None,
    subjects: Iterable[str] | None = None,
) -> dict[Key, list[Point]]:
    """Mean and peak per display bucket, raw and rolled rows together; counts
    (`SUM_NAMES`) are summed instead of averaged."""
    bucket = _bucket(db, step).label("bucket")
    query = select(
        MetricSample.source,
        MetricSample.name,
        MetricSample.subject,
        bucket,
        func.avg(MetricSample.value),
        func.max(func.coalesce(MetricSample.peak, MetricSample.value)),
        func.sum(MetricSample.value),
    ).where(MetricSample.ts >= start, MetricSample.ts < end, MetricSample.name.in_(list(names)))
    if source:
        query = query.where(MetricSample.source == source)
    if subjects is not None:
        query = query.where(MetricSample.subject.in_(list(subjects)))
    query = query.group_by(MetricSample.source, MetricSample.name, MetricSample.subject, bucket)
    out: dict[Key, list[Point]] = defaultdict(list)
    for src, name, subject, at, mean, peak, total in (await db.exec(query)).all():
        value = float(total) if name in SUM_NAMES else float(mean)
        out[(src, name, subject)].append((int(at), value, float(peak)))
    for points in out.values():
        points.sort()
    return dict(out)


async def latest(
    db: AsyncSession, *, since: datetime, source: str | None = None
) -> dict[Key, tuple[datetime, float]]:
    """The newest raw value of every series written after `since`."""
    query = select(
        MetricSample.ts,
        MetricSample.source,
        MetricSample.name,
        MetricSample.subject,
        MetricSample.value,
    ).where(MetricSample.ts >= since, MetricSample.step == 60)
    if source:
        query = query.where(MetricSample.source == source)
    out: dict[Key, tuple[datetime, float]] = {}
    for ts, src, name, subject, value in (await db.exec(query.order_by(MetricSample.ts))).all():
        out[(src, name, subject)] = (as_utc(ts), float(value))
    return out


async def recent(
    db: AsyncSession, *, since: datetime, names: Iterable[str]
) -> dict[Key, list[tuple[datetime, float]]]:
    """Raw points of the named series after `since`, oldest first (for the rules)."""
    query = (
        select(
            MetricSample.ts,
            MetricSample.source,
            MetricSample.name,
            MetricSample.subject,
            MetricSample.value,
        )
        .where(
            MetricSample.ts >= since,
            MetricSample.step == 60,
            MetricSample.name.in_(list(names)),
        )
        .order_by(MetricSample.ts)
    )
    out: dict[Key, list[tuple[datetime, float]]] = defaultdict(list)
    for ts, src, name, subject, value in (await db.exec(query)).all():
        out[(src, name, subject)].append((as_utc(ts), float(value)))
    return dict(out)


async def last_sample_at(db: AsyncSession, source: str | None = None) -> datetime | None:
    query = select(func.max(MetricSample.ts))
    if source:
        query = query.where(MetricSample.source == source)
    found = (await db.exec(query)).first()
    return as_utc(found) if found else None
