"""Super Agent: picker entries that pick model and effort per turn.

- `governance.super_agent_enabled`: the entries offered; on for every installation (owner
  decision 2026-11-14: Super Agent is the default), the admin can turn it off.
- `governance.super_agent_rules`: the admin's changes to the built-in class table
  (`super_agent.DEFAULT_RULES`). Old lists an admin changed carry over (the first economy
  model answers simple turns, the first quality model hard ones); lists still at the old
  defaults leave the evaluated table as it is.
- `sessions.routing_mode`: conversations on either Auto lane move to `super`.

Downgrade drops both columns and moves Super Agent conversations back to `auto`.

Revision ID: 0083
Revises: 0082
Create Date: 2026-11-13
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ids(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [v for v in value or [] if isinstance(v, str) and v]


#: the Auto lists a fresh installation got; left as they were, they say nothing about the admin
_OLD_ECONOMY_DEFAULT = ["local/qwen3.8-flash"]
_OLD_QUALITY_DEFAULT = ["deepseek/deepseek-v4.1-flash", "z-ai/glm-5.3-flash"]


def carried_rules(economy, quality) -> dict:
    """Admin table from the old Auto lists an admin changed: economy → simple turns,
    quality → hard ones."""
    rules: dict = {}
    economy_ids, quality_ids = _ids(economy), _ids(quality)
    if economy_ids and economy_ids != _OLD_ECONOMY_DEFAULT:
        rules["simple"] = {"model": economy_ids[0]}
    if quality_ids and quality_ids != _OLD_QUALITY_DEFAULT:
        rules["reasoning"] = {"model": quality_ids[0]}
    return rules


def upgrade() -> None:
    bind = op.get_bind()
    json_type = postgresql.JSONB() if bind.dialect.name == "postgresql" else sa.JSON()
    op.add_column(
        "governance",
        sa.Column("super_agent_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    op.add_column(
        "governance",
        sa.Column("super_agent_rules", json_type, nullable=False,
                  server_default=sa.text("'{}'")),
    )
    governance = sa.table(
        "governance",
        sa.column("id", sa.String),
        sa.column("adaptive_economy_model_ids", sa.JSON),
        sa.column("adaptive_quality_model_ids", sa.JSON),
        sa.column("super_agent_enabled", sa.Boolean),
        sa.column("super_agent_rules", json_type),
    )
    rows = bind.execute(sa.select(
        governance.c.id, governance.c.adaptive_economy_model_ids,
        governance.c.adaptive_quality_model_ids,
    )).all()
    for row_id, economy, quality in rows:
        bind.execute(
            governance.update().where(governance.c.id == row_id).values(
                super_agent_enabled=True,
                super_agent_rules=carried_rules(economy, quality),
            )
        )
    # routing_mode is a plain string column (no enum to cast)
    sessions = sa.table("sessions", sa.column("routing_mode", sa.String))
    bind.execute(
        sessions.update()
        .where(sessions.c.routing_mode.in_(["auto", "auto_quality"]))
        .values(routing_mode="super")
    )


def downgrade() -> None:
    sessions = sa.table("sessions", sa.column("routing_mode", sa.String))
    op.get_bind().execute(
        sessions.update()
        .where(sessions.c.routing_mode.in_(["super", "super_strict"]))
        .values(routing_mode="auto")
    )
    op.drop_column("governance", "super_agent_rules")
    op.drop_column("governance", "super_agent_enabled")
