"""Google and Slack connections are gone: their tables and stored settings are dropped.

Dropped, children before parents: `slack_threads`, `slack_bot_channels`, `slack_workspaces`,
`integration_imports`, `integration_actions`, `integration_accounts` (their indexes go with
them), and the `integrations.*` rows of `system_settings`. Files already copied in stay as
ordinary uploads. Downgrade restores nothing; the data and the sealed tokens are gone.

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

TABLES = (
    "slack_threads",
    "slack_bot_channels",
    "slack_workspaces",
    "integration_imports",
    "integration_actions",
    "integration_accounts",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table}"'))
    op.execute(sa.text("DELETE FROM system_settings WHERE key LIKE 'integrations.%'"))


def downgrade() -> None:
    # The dropped tables and settings are not restored.
    pass
