"""Performance samples for the admin 「성능 모니터링」 page (`services/perf`).

One row per metric, subject and bucket. Raw rows (`step` 60) are rolled into
10-minute rows (`step` 600, mean in `value`, maximum in `peak`) after
`perf_raw_hours`; everything older than `perf_retention_days` is deleted.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer
from sqlmodel import Field, SQLModel


class MetricSample(SQLModel, table=True):
    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_metric_samples_name_ts", "name", "ts"),
        Index("ix_metric_samples_ts", "ts"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
        ),
    )
    #: Start of the bucket, UTC.
    ts: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    #: Bucket length in seconds: 60 raw, 600 rolled up.
    step: int = Field(default=60)
    #: host · container · vllm · gpu · model · app
    source: str = Field(default="")
    name: str = Field(default="")
    #: Component, model, scrape target or replica the value belongs to.
    subject: str = Field(default="")
    value: float = Field(default=0.0)
    #: Maximum within the bucket; equals `value` on raw rows.
    peak: float | None = Field(default=None)


__all__ = ["MetricSample"]
