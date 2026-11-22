"""The chosen model plans and judges: `governance.outline_model_id` is dropped.

Downgrade adds the column back, empty.

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("governance", "outline_model_id")


def downgrade() -> None:
    op.add_column("governance", sa.Column("outline_model_id", sa.String(), nullable=True))
