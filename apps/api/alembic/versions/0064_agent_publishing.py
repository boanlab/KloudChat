"""Published agents (공개 챗봇), phase 1: members of this instance.

A bot is its owner's: conversations on it are `sessions` rows the publisher owns, marked with
the publication, so the ordinary turn applies the publisher's policy, credits and audit. What
a visitor reaches is fixed per version: a hidden copy of the agent and the text of its
knowledge.

- `publications`, `publication_versions`, `publication_visitors`, `publication_counters`
  (per-window counts, upserted), `publication_slots` (turns running, for the concurrency
  limits across replicas), `publication_questions` (questions it could not answer, masked).
- `sessions.publication_id`, `visitor_id`, `publication_version_id`, `shared_at` (sent to the
  maker by the visitor), `viewable` (started under 「대화 열람」 with consent).
- `credit_ledger.publication_id`: a bot's spend, billed to its owner and shown apart.
- `agents.snapshot_of`: a version's hidden copy; `agents.contact`: 담당 부서·연락처, said
  verbatim when the material does not answer.
- `governance.published_concurrency_max`: turns on published bots at once, instance-wide.

No existing row changes; downgrade drops what this adds.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    json = JSONB().with_variant(sa.JSON(), "sqlite")
    ts = sa.DateTime(timezone=True)

    op.create_table(
        "publications",
        sa.Column("id", text(), nullable=False),
        sa.Column("owner_id", text(), nullable=False),
        sa.Column("agent_id", text(), nullable=False, server_default=""),
        sa.Column("slug", text(), nullable=False),
        sa.Column("title", text(), nullable=False, server_default=""),
        sa.Column("description", text(), nullable=False, server_default=""),
        sa.Column("visibility", text(length=20), nullable=False, server_default="internal"),
        sa.Column("status", text(length=20), nullable=False, server_default="draft"),
        sa.Column("pause_reason", text(length=40), nullable=False, server_default=""),
        sa.Column("active_version_id", text(), nullable=True),
        sa.Column("branding", json, nullable=True),
        sa.Column("limits", json, nullable=True),
        sa.Column("features", json, nullable=True),
        sa.Column("show_process", text(length=20), nullable=False, server_default="summary"),
        sa.Column("show_sources", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("conversation_view", text(length=8), nullable=False, server_default="off"),
        sa.Column("consent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", ts, nullable=False),
        sa.Column("updated_at", ts, nullable=False),
        sa.Column("published_at", ts, nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publications_owner_id", "publications", ["owner_id"])
    op.create_index("ix_publications_agent_id", "publications", ["agent_id"])
    op.create_index("ix_publications_slug", "publications", ["slug"], unique=True)

    op.create_table(
        "publication_versions",
        sa.Column("id", text(), nullable=False),
        sa.Column("publication_id", text(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("agent_snapshot_id", text(), nullable=False, server_default=""),
        sa.Column("model", text(), nullable=False, server_default=""),
        sa.Column("note", text(), nullable=False, server_default=""),
        sa.Column("summary", json, nullable=True),
        sa.Column("created_by", text(), nullable=True),
        sa.Column("created_at", ts, nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publication_id", "number", name="ux_publication_versions_number"),
    )
    op.create_index("ix_publication_versions_publication_id", "publication_versions",
                    ["publication_id"])

    op.create_table(
        "publication_visitors",
        sa.Column("id", text(), nullable=False),
        sa.Column("publication_id", text(), nullable=False),
        sa.Column("user_id", text(), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consented_at", ts, nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", ts, nullable=False),
        sa.Column("last_seen_at", ts, nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publication_id", "user_id", name="ux_publication_visitors_user"),
    )
    op.create_index("ix_publication_visitors_publication_id", "publication_visitors",
                    ["publication_id"])
    op.create_index("ix_publication_visitors_user_id", "publication_visitors", ["user_id"])

    op.create_table(
        "publication_counters",
        sa.Column("publication_id", text(), nullable=False),
        sa.Column("window", text(length=20), nullable=False),
        sa.Column("key", text(length=120), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("publication_id", "window", "key"),
    )

    op.create_table(
        "publication_slots",
        sa.Column("id", text(), nullable=False),
        sa.Column("publication_id", text(), nullable=False),
        sa.Column("acquired_at", ts, nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publication_slots_acquired", "publication_slots",
                    ["publication_id", "acquired_at"])

    op.create_table(
        "publication_questions",
        sa.Column("id", text(), nullable=False),
        sa.Column("publication_id", text(), nullable=False),
        sa.Column("version_id", text(), nullable=True),
        sa.Column("text", text(), nullable=False, server_default=""),
        sa.Column("at", ts, nullable=False),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_publication_questions_at", "publication_questions",
                    ["publication_id", "at"])

    op.add_column("sessions", sa.Column("publication_id", text(), nullable=True))
    op.add_column("sessions", sa.Column("visitor_id", text(), nullable=True))
    op.add_column("sessions", sa.Column("publication_version_id", text(), nullable=True))
    op.add_column("sessions", sa.Column("shared_at", ts, nullable=True))
    op.add_column("sessions", sa.Column("viewable", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))
    op.create_index("ix_sessions_publication_id", "sessions", ["publication_id"])
    op.create_index("ix_sessions_visitor_id", "sessions", ["visitor_id"])

    op.add_column("credit_ledger", sa.Column("publication_id", text(), nullable=True))

    op.add_column("agents", sa.Column("snapshot_of", text(), nullable=True))
    op.add_column("agents", sa.Column("contact", text(), nullable=False, server_default=""))
    op.create_index("ix_agents_snapshot_of", "agents", ["snapshot_of"])

    op.add_column("governance", sa.Column("published_concurrency_max", sa.Integer(),
                                          nullable=False, server_default="16"))


def downgrade() -> None:
    op.drop_column("governance", "published_concurrency_max")
    op.drop_index("ix_agents_snapshot_of", table_name="agents")
    op.drop_column("agents", "contact")
    op.drop_column("agents", "snapshot_of")
    op.drop_column("credit_ledger", "publication_id")
    op.drop_index("ix_sessions_visitor_id", table_name="sessions")
    op.drop_index("ix_sessions_publication_id", table_name="sessions")
    for name in ("viewable", "shared_at", "publication_version_id", "visitor_id",
                 "publication_id"):
        op.drop_column("sessions", name)
    op.drop_index("ix_publication_questions_at", table_name="publication_questions")
    op.drop_table("publication_questions")
    op.drop_index("ix_publication_slots_acquired", table_name="publication_slots")
    op.drop_table("publication_slots")
    op.drop_table("publication_counters")
    op.drop_index("ix_publication_visitors_user_id", table_name="publication_visitors")
    op.drop_index("ix_publication_visitors_publication_id", table_name="publication_visitors")
    op.drop_table("publication_visitors")
    op.drop_index("ix_publication_versions_publication_id", table_name="publication_versions")
    op.drop_table("publication_versions")
    op.drop_index("ix_publications_slug", table_name="publications")
    op.drop_index("ix_publications_agent_id", table_name="publications")
    op.drop_index("ix_publications_owner_id", table_name="publications")
    op.drop_table("publications")
