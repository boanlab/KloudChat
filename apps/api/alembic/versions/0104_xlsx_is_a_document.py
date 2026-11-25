"""Sheets (`xlsx`) are listed with the working documents.

`artifact_section.DOCUMENT_KINDS` lacked `xlsx`, so a sheet was filed under `media` and
`/artifacts?open=<sheet>` went to the file box instead of opening it. New rows get the right
section from the model; the sheets made since 0101 are corrected here. `kind` is compared as text
(a Postgres enum is not compared to a literal in a data migration).

Revision ID: 0104
Revises: 0103
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE artifacts SET section = 'document' "
        "WHERE CAST(kind AS TEXT) IN ('xlsx', 'XLSX') AND section <> 'document'"
    ))


def downgrade() -> None:
    pass
