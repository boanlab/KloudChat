"""Privacy-aware model routing and structured audit metadata.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _needs_existing_install_policy(has_users: bool, has_policy: bool) -> bool:
    """Existing deployments opt in even when they never created a policy row."""
    return has_users and not has_policy


def upgrade() -> None:
    op.add_column(
        "governance",
        sa.Column("external_data_guard", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "governance",
        sa.Column(
            "allow_user_raw_external", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "governance",
        sa.Column(
            "privacy_safe_model_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # A deployed instance may have users but no governance row (the settings
    # screen was never opened). It is still an existing install and must opt in
    # deliberately. A fresh database has no users, so it keeps no row and the
    # model/server default below remains guard-on when its first row is created.
    bind = op.get_bind()
    has_users = bool(bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM users)")).scalar())
    has_policy = bool(
        bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM governance WHERE id = 'default')")
        ).scalar()
    )
    if _needs_existing_install_policy(has_users, has_policy):
        bind.execute(
            sa.text(
                """
                INSERT INTO governance (
                    id, pii_masking, external_data_guard,
                    allow_user_raw_external, privacy_safe_model_ids,
                    intent_filter, blocked_categories, retention_days,
                    updated_at, updated_by
                ) VALUES (
                    'default', false, false, false, CAST('[]' AS jsonb),
                    false, CAST('[]' AS jsonb), 0, CURRENT_TIMESTAMP, NULL
                )
                """
            )
        )

    # Existing rows received false from the add-column default. Only future
    # policy rows get the secure new-install default.
    op.alter_column("governance", "external_data_guard", server_default=sa.true())
    op.add_column("messages", sa.Column("routing", JSONB(), nullable=True))
    op.add_column("audit_events", sa.Column("metadata", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "metadata")
    op.drop_column("messages", "routing")
    op.drop_column("governance", "privacy_safe_model_ids")
    op.drop_column("governance", "allow_user_raw_external")
    op.drop_column("governance", "external_data_guard")
