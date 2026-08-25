"""The starter agents and skills become one shared catalogue.

They used to be copied into every account at approval, which made the same
procedure N rows: an improvement to one reached nobody, and a workspace of
twenty people held twenty private copies of the same eight skills.

Now the administrator's account holds the originals, shared to the workspace,
and everyone else takes a copy of the ones they actually want. Skills gain the
`visibility` and `installs` columns agents already had, and both gain
`origin_id` so a copy knows where it came from.

Nothing is rewritten. Copies seeded before this stay exactly where they are,
owned and editable by the accounts that hold them — deleting somebody's edited
procedure to tidy a table is not a migration.

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

#: Already created by 0003 for agents. Skills reuse it rather than declare a
#: second two-label type; `create_type=False` keeps this ADD COLUMN from
#: trying to create it again.
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
