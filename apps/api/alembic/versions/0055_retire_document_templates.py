"""Retired document 서식: no agent, starting point or project default chooses them any more.

Documents and conversations that name one keep the id; `design_templates.rendering` draws
those in the shared seed. Only the choices are cleared, so saving an agent, a starting point
or a project does not fail on an id the catalogue no longer has.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with `design_templates.RETIRED`; a migration does not import the app.
RETIRED = (
    "doc-brief",
    "doc-case",
    "doc-incident",
    "doc-lab",
    "doc-notice",
    "doc-project-brief",
    "doc-proposal",
    "doc-report",
    "doc-survey",
    "doc-term-paper",
)


def kept_defaults(defaults: object) -> dict | None:
    """A project's surface → 서식 map without retired ids; None when nothing changes."""
    if not isinstance(defaults, dict):
        return None
    kept = {surface: tid for surface, tid in defaults.items() if tid not in RETIRED}
    return kept if len(kept) != len(defaults) else None


def upgrade() -> None:
    bind = op.get_bind()
    retired = sa.bindparam("retired", value=list(RETIRED), expanding=True)
    bind.execute(
        sa.text(
            "UPDATE agents SET render_template_id = NULL WHERE render_template_id IN :retired"
        ).bindparams(retired)
    )
    bind.execute(
        sa.text(
            "UPDATE templates SET render_template_id = '' WHERE render_template_id IN :retired"
        ).bindparams(retired)
    )
    projects = sa.table(
        "projects",
        sa.column("id", sa.String()),
        sa.column("render_templates", JSONB().with_variant(sa.JSON(), "sqlite")),
    )
    rows = bind.execute(
        sa.select(projects.c.id, projects.c.render_templates).where(
            projects.c.render_templates.is_not(None)
        )
    ).fetchall()
    for row_id, defaults in rows:
        kept = kept_defaults(defaults)
        if kept is not None:
            bind.execute(
                projects.update().where(projects.c.id == row_id).values(render_templates=kept)
            )


def downgrade() -> None:
    # The cleared choices are not restored; the 서식 they named are gone.
    pass
