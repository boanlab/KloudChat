"""User-added starting points for the template gallery.

Same shape as a built-in (kind, group, title, description, fills, prompt) plus
`file_id`: an uploaded form whose extracted text is attached when the template
is picked, so a draft follows the original's shape.

Owned by one user. Sharing a template is an ACL question this table does not
answer.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("group", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fills", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        # The form itself. `SET NULL` rather than cascade: deleting the file
        # should cost the attachment, not the template someone wrote around it.
        sa.Column("file_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The gallery reads "mine, for this surface" on every open.
    op.create_index("ix_templates_owner_id", "templates", ["owner_id"])
    op.create_index("ix_templates_file_id", "templates", ["file_id"])


def downgrade() -> None:
    op.drop_index("ix_templates_file_id", table_name="templates")
    op.drop_index("ix_templates_owner_id", table_name="templates")
    op.drop_table("templates")
