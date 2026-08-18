"""One look, shared by every surface a project produces.

Before this, the deck accent was picked by the model out of a fixed palette and
the exporters chose their own fonts, so a report, its deck and its cover image
came out of the same project looking like three unrelated documents.

`design_systems` holds four renderer tokens plus a short block of prose for the
model. `projects.design_system_id` is nullable and null by default: an existing
project keeps the previous behaviour exactly.

`SET NULL` on delete rather than cascade — removing a look should cost the
projects their look, not the projects.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_systems",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("tokens", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("image_style", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("craft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_systems_owner_id", "design_systems", ["owner_id"])
    # Every picker open reads the shared set.
    op.create_index(
        "ix_design_systems_shared",
        "design_systems",
        ["shared"],
        postgresql_where=sa.text("shared"),
    )
    op.add_column(
        "projects",
        sa.Column("design_system_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_design_system_id",
        "projects",
        "design_systems",
        ["design_system_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _backfill_starter_designs()


#: The same three looks `services/starter._DESIGNS` gives a new account.
#:
#: Duplicated here rather than imported because a migration has to keep working
#: after that list is edited: it describes the database at revision 0020, not
#: whatever the application ships today.
#:
#: This runs once. `starter.seed` only reaches its design block for an account
#: with no agents — a fresh signup — so an existing account would otherwise
#: never see one of these, and an account that deletes all three keeps them
#: deleted. Both halves are deliberate.
_STARTER_DESIGNS = (
    (
        "기본",
        "지금까지의 기본값 그대로. 색과 서체만 고정합니다.",
        '{"accent": "#5b5bd6", "ink": "#1a1a1a", "muted": "#666666", "font": "gothic"}',
        "",
        "clean uncluttered composition, generous whitespace",
        '["restraint"]',
    ),
    (
        "문서용 명조",
        "보고서와 공문에 맞춘 먹빛 명조. 인쇄해도 읽힙니다.",
        '{"accent": "#334155", "ink": "#111827", "muted": "#6b7280", "font": "serif"}',
        "제목은 명사구로 쓴다. 한 문장에 한 사실만 담고, 수식어를 덜어낸다.",
        "muted documentary photography, low saturation, natural light",
        '["restraint", "typography"]',
    ),
    (
        "발표용 청록",
        "슬라이드와 표지 이미지를 같은 청록으로 묶습니다.",
        '{"accent": "#0f766e", "ink": "#0f172a", "muted": "#64748b", "font": "gothic"}',
        "청중이 소리 내어 읽을 문장으로 쓴다. 한 장에 주장 하나.",
        "bold high-contrast graphic, large negative space, teal accent",
        '["restraint"]',
    ),
)


def _backfill_starter_designs() -> None:
    """Gives every existing account the same three looks a new one gets."""
    for name, description, tokens, body, image_style, craft in _STARTER_DESIGNS:
        op.execute(
            sa.text(
                """
                INSERT INTO design_systems
                    (id, owner_id, name, description, tokens, body, image_style,
                     craft, shared, created_at, updated_at)
                SELECT replace(gen_random_uuid()::text, '-', ''), u.id,
                       :name, :description, cast(:tokens AS jsonb), :body,
                       :image_style, cast(:craft AS jsonb), false, now(), now()
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM design_systems d
                    WHERE d.owner_id = u.id AND d.name = :name
                )
                """
            ).bindparams(
                name=name,
                description=description,
                tokens=tokens,
                body=body,
                image_style=image_style,
                craft=craft,
            )
        )


def downgrade() -> None:
    op.drop_constraint("fk_projects_design_system_id", "projects", type_="foreignkey")
    op.drop_column("projects", "design_system_id")
    op.drop_index("ix_design_systems_shared", table_name="design_systems")
    op.drop_index("ix_design_systems_owner_id", table_name="design_systems")
    op.drop_table("design_systems")
