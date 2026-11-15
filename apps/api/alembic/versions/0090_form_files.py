"""Form files (양식) and the 디자인 지침 field, beside templates and themes for now.

- `files.role`: `form` for a copy saved as a project's or agent's 기본 양식 파일 (not material,
  not in the file box); NULL for every other file.
- `files.form_profile`: what an Office upload offers as a form (`services/form_profile.py`).
- `projects.form_files`, `agents.form_files`: output kind (`pptx`, `docx`, `hwpx`) → file id.
- `projects.design_brief`, `agents.design_brief`: the 디자인 지침 text.
- `sessions.form_files`: output kind → the form attached in this conversation, kept for its
  later turns; `""` for a kind whose saved form the person took off.

Plain columns only; templates and themes are untouched.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
    op.add_column("files", sa.Column("role", sa.String(length=12), nullable=True))
    op.add_column("files", sa.Column("form_profile", json, nullable=True))
    for table in ("projects", "agents"):
        op.add_column(table, sa.Column("form_files", json, nullable=True))
        op.add_column(table, sa.Column("design_brief", sa.Text(), nullable=False,
                                       server_default=""))
    op.add_column("sessions", sa.Column("form_files", json, nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "form_files")
    for table in ("projects", "agents"):
        op.drop_column(table, "design_brief")
        op.drop_column(table, "form_files")
    op.drop_column("files", "form_profile")
    op.drop_column("files", "role")
