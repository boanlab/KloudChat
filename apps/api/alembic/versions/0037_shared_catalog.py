"""Shared catalogue for starter agents and skills: `visibility`, `installs`, `origin_id`.

Copies seeded earlier are left as they are.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Created by 0003 for agents; `create_type=False` so this ADD COLUMN reuses it.
_VISIBILITY = postgresql.ENUM(
    "private", "org", name="agentvisibility", create_type=False
)


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "visibility", _VISIBILITY, nullable=False, server_default="private"
        ),
    )
    op.add_column(
        "skills",
        sa.Column("installs", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("skills", sa.Column("origin_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_skills_origin_id"), "skills", ["origin_id"])

    op.add_column("agents", sa.Column("catalog_key", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("origin_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_agents_origin_id"), "agents", ["origin_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agents_origin_id"), table_name="agents")
    op.drop_column("agents", "origin_id")
    op.drop_column("agents", "catalog_key")

    op.drop_index(op.f("ix_skills_origin_id"), table_name="skills")
    op.drop_column("skills", "origin_id")
    op.drop_column("skills", "installs")
    op.drop_column("skills", "visibility")
