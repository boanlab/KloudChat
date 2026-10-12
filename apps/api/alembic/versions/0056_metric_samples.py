"""Performance samples for the admin 「성능 모니터링」 page.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("source", text(), nullable=False, server_default=""),
        sa.Column("name", text(), nullable=False, server_default=""),
        sa.Column("subject", text(), nullable=False, server_default=""),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("peak", sa.Float(), nullable=True),
    )
    op.create_index("ix_metric_samples_name_ts", "metric_samples", ["name", "ts"])
    op.create_index("ix_metric_samples_ts", "metric_samples", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_metric_samples_ts", table_name="metric_samples")
    op.drop_index("ix_metric_samples_name_ts", table_name="metric_samples")
    op.drop_table("metric_samples")
