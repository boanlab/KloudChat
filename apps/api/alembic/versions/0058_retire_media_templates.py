"""Retired image, audio and video 서식: nothing chooses them any more.

No agent, starting point, project default or conversation names one after this. None of
them ever shaped a stored document: an image 서식 only shaped the prompt of the turn that
picked it, an audio or video one only set the composer's chips. So unlike 0055 a
conversation's choice is cleared too; pictures, clips and voices already made keep nothing
of the 서식 and open as before.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with `design_templates.RETIRED_MEDIA`; a migration does not import the app.
RETIRED = (
    "audio-bed",
    "audio-narration",
    "image-architecture",
    "image-banner",
    "image-cover",
    "image-diagram",
    "image-infographic",
    "image-mascot",
    "image-method",
    "image-mockup",
    "image-persona",
    "image-pipeline",
    "image-poster",
    "image-poster-bg",
    "image-scene",
    "image-teaser",
    "video-opening",
    "video-product",
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
    for statement in (
        "UPDATE agents SET render_template_id = NULL WHERE render_template_id IN :retired",
        "UPDATE templates SET render_template_id = '' WHERE render_template_id IN :retired",
        "UPDATE sessions SET render_template_id = NULL WHERE render_template_id IN :retired",
    ):
        bind.execute(sa.text(statement).bindparams(retired))
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
