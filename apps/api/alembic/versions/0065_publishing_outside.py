"""Published agents open to people without an account: by link, or listed on the public page.

Widening a bot's audience beyond members waits for an administrator, and so does a new
version on a bot already open outside; the approved version answers meanwhile.

- `publications`: the requested audience and its review (`requested_visibility`,
  `review_state`, `review_note`, `reviewed_at`, `reviewed_by`), the version waiting for review
  (`pending_version_id`), and `attention_at`/`owner_seen_at` for the owner's badge.
- `publication_versions.approved_at`/`approved_by`: approved for people without an account.
- `publication_visitors.anon_id`: a visitor without an account, by their signed cookie's id;
  `blocked_at`.

No existing row changes: every bot stays open to members only. Downgrade closes the outside
audiences (every bot back to members only) and drops what this adds.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    text = sqlmodel.sql.sqltypes.AutoString
    ts = sa.DateTime(timezone=True)

    op.add_column("publications", sa.Column("requested_visibility", text(length=20),
                                            nullable=False, server_default=""))
    op.add_column("publications", sa.Column("review_state", text(length=20), nullable=False,
                                            server_default=""))
    op.add_column("publications", sa.Column("review_note", text(), nullable=False,
                                            server_default=""))
    op.add_column("publications", sa.Column("reviewed_at", ts, nullable=True))
    op.add_column("publications", sa.Column("reviewed_by", text(), nullable=True))
    op.add_column("publications", sa.Column("pending_version_id", text(), nullable=True))
    op.add_column("publications", sa.Column("attention_at", ts, nullable=True))
    op.add_column("publications", sa.Column("owner_seen_at", ts, nullable=True))
    op.create_index("ix_publications_review_state", "publications", ["review_state"])

    op.add_column("publication_versions", sa.Column("approved_at", ts, nullable=True))
    op.add_column("publication_versions", sa.Column("approved_by", text(), nullable=True))

    op.add_column("publication_visitors", sa.Column("anon_id", text(length=64), nullable=True))
    op.add_column("publication_visitors", sa.Column("blocked_at", ts, nullable=True))
    op.create_index("ux_publication_visitors_anon", "publication_visitors",
                    ["publication_id", "anon_id"], unique=True)


def downgrade() -> None:
    # The code before this revision serves members only, and members only on `internal`.
    op.execute("UPDATE publications SET visibility = 'internal'")
    op.drop_index("ux_publication_visitors_anon", table_name="publication_visitors")
    op.drop_column("publication_visitors", "blocked_at")
    op.drop_column("publication_visitors", "anon_id")
    op.drop_column("publication_versions", "approved_by")
    op.drop_column("publication_versions", "approved_at")
    op.drop_index("ix_publications_review_state", table_name="publications")
    for name in ("owner_seen_at", "attention_at", "pending_version_id", "reviewed_by",
                 "reviewed_at", "review_note", "review_state", "requested_visibility"):
        op.drop_column("publications", name)
