"""Admin perf samples: 60s rows rolled into 600s (mean `value`, max `peak`), then pruned."""

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
    #: bucket start, UTC
    ts: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    #: bucket seconds: 60 raw, 600 rolled up
    step: int = Field(default=60)
    #: host · container · vllm · gpu · model · app
    source: str = Field(default="")
    name: str = Field(default="")
    #: component, model, target or replica
    subject: str = Field(default="")
    value: float = Field(default=0.0)
    #: bucket max; = `value` on raw rows
    peak: float | None = Field(default=None)


__all__ = ["MetricSample"]
