"""Documents an agent can search.

Two columns on `files` rather than a table of its own: an agent's knowledge is a
file, with the same extraction, blob storage and token count. `agent_id` says
whose shelf it sits on; `source_url` records text read from a page rather than
uploaded, which is a snapshot and not a subscription.

Retrieval is lexical in-process, with an optional vector index in the model
stack — see `services/knowledge.py`.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files", sa.Column("agent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "files", sa.Column("source_url", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    # Deleting an agent takes its shelf with it: knowledge attached to nothing is
    # a row nobody can reach and nobody can delete.
    op.create_foreign_key(
        "fk_files_agent_id", "files", "agents", ["agent_id"], ["id"], ondelete="CASCADE"
    )
    # The search reads "this agent's sources" on every tool call.
    op.create_index("ix_files_agent_id", "files", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_files_agent_id", table_name="files")
    op.drop_constraint("fk_files_agent_id", "files", type_="foreignkey")
    op.drop_column("files", "source_url")
    op.drop_column("files", "agent_id")
