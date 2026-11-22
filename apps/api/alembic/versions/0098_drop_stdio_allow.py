"""Members no longer register their own commands: the stored allowlist is dropped.

Downgrade restores nothing.

Revision ID: 0098
Revises: 0097
Create Date: 2026-11-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM system_settings WHERE key = 'connectors.stdio_allow'"))


def downgrade() -> None:
    # the allowlist's text is not kept anywhere
    pass
