"""Agents carry their process: the official base they run, a retired flag, and their own
pipeline (진행 과정).

- `agents.base_key`: the official agent (catalogue key) whose checks, question card and
  pipeline a person's own agent runs (`services/bases.py`). Backfilled for agents of no
  catalogue entry whose 문서 형식 is the format of an official agent still offered
  (gaejo → gaejo-report, minutes → minutes, comparison → comparison-report).
- `agents.retired`: the catalogue took the agent out (자기소개서·경력기술서 `career-docs`,
  고객 맞춤 제안서 `client-proposal`). Set on the catalogue rows and on every copy of them
  (a copy carries the key; a copy of a copy names its original). Nothing is deleted: the
  rows are hidden from every list and start no new conversation, and the conversations and
  documents made with them still open and export.
- `agents.check_skills`, `agents.check_options`: the check skills the server runs around an
  agent's writing and how strict the trace is, on the row, so a person's own agent holds
  them the way a catalogue agent does (they were read by catalogue key before). Backfilled
  for catalogue rows and their copies from the agent files' `checks` / `check_options`, and
  for the agents given a base above from that base's.
- `agents.bundles`: the 묶음 ticked in the agent form (`services/bundles.py`); the two
  columns above and the agent's own pipeline are compiled from them. Left null for rows made
  before: their view is read off their checks, and the seeder fills the catalogue's.
- `user_pipelines.agent_id`: the agent whose own process a pipeline is (`@self` steps).
- `user_pipelines.kind`: `bundle` for a person's own 묶음, "" for a pipeline.
- Retired 서식 `doc-career-history`, `doc-client-proposal`: cleared from agents that are not
  retired themselves, from starting points and from project defaults, as 0055 did.
  Conversations keep the id: their documents are still drawn in those 서식.

Downgrade drops the columns; the cleared 서식 choices are not restored.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with the `retired = true` agents in `app/agents/*.toml`.
RETIRED_AGENTS = ("career-docs", "client-proposal")
#: Kept in step with `design_templates.RETIRED_KEPT`.
RETIRED_TEMPLATES = ("doc-career-history", "doc-client-proposal")
#: 문서 형식 → the official agent offered for it (`bases.BY_FORMAT`).
#: Kept in step with `checks` / `check_options` in `app/agents/*.toml`.
_FULL = ["gap-check", "grounding-check", "generic-language-killer"]
CHECKS = {
    "gaejo-report": _FULL,
    "document": _FULL,
    "minutes": _FULL,
    "comparison-report": _FULL,
    "data-analysis": ["gap-check", "grounding-check"],
    "slides": [],
    "career-docs": _FULL,
    "client-proposal": _FULL,
}
OPTIONS = {"data-analysis": {"tool_numbers": True}, "career-docs": {"titles": True}}
#: The catalogue rows of a key and every copy of them, or the agents given it as a base.
CHECKS_WHERE = (
    "catalog_key = :key OR origin_id IN (SELECT id FROM agents WHERE catalog_key = :key)"
    " OR (catalog_key IS NULL AND base_key = :key)"
)

BASE_FOR_FORMAT = {
    "gaejo": "gaejo-report",
    "minutes": "minutes",
    "comparison": "comparison-report",
}

#: The rows the data steps touch, so a dry run can count them first.
RETIRE_WHERE = (
    "catalog_key IN :keys OR origin_id IN (SELECT id FROM agents WHERE catalog_key IN :keys)"
)
BASE_WHERE = "catalog_key IS NULL AND base_key IS NULL AND doc_format = :format"
TEMPLATE_WHERE = "render_template_id IN :templates AND retired = false"


def kept_defaults(defaults: object) -> dict | None:
    """A project's surface → 서식 map without retired ids; None when nothing changes."""
    if not isinstance(defaults, dict):
        return None
    kept = {surface: tid for surface, tid in defaults.items() if tid not in RETIRED_TEMPLATES}
    return kept if len(kept) != len(defaults) else None


def upgrade() -> None:
    op.add_column("agents", sa.Column("base_key", sa.String(length=60), nullable=True))
    op.add_column(
        "agents",
        sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    json = JSONB().with_variant(sa.JSON(), "sqlite")
    op.add_column("agents", sa.Column("check_skills", json, nullable=True))
    op.add_column("agents", sa.Column("check_options", json, nullable=True))
    op.add_column("agents", sa.Column("bundles", json, nullable=True))
    op.add_column("user_pipelines", sa.Column("agent_id", sa.String(), nullable=True))
    op.add_column(
        "user_pipelines",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default=""),
    )
    op.create_index("ix_user_pipelines_agent_id", "user_pipelines", ["agent_id"])

    bind = op.get_bind()
    keys = sa.bindparam("keys", value=list(RETIRED_AGENTS), expanding=True)
    bind.execute(sa.text(f"UPDATE agents SET retired = true WHERE {RETIRE_WHERE}").bindparams(keys))
    for format_id, base in BASE_FOR_FORMAT.items():
        bind.execute(
            sa.text(f"UPDATE agents SET base_key = :base WHERE {BASE_WHERE}"),
            {"base": base, "format": format_id},
        )
    agents = sa.table(
        "agents",
        sa.column("check_skills", json),
        sa.column("check_options", json),
    )
    for key, checks in CHECKS.items():
        where = sa.text(CHECKS_WHERE).bindparams(key=key)
        bind.execute(agents.update().where(where).values(
            check_skills=list(checks),
            check_options=OPTIONS[key] if key in OPTIONS else sa.null()))
    templates = sa.bindparam("templates", value=list(RETIRED_TEMPLATES), expanding=True)
    bind.execute(
        sa.text(f"UPDATE agents SET render_template_id = NULL WHERE {TEMPLATE_WHERE}").bindparams(
            templates
        )
    )
    templates = sa.bindparam("templates", value=list(RETIRED_TEMPLATES), expanding=True)
    bind.execute(
        sa.text(
            "UPDATE templates SET render_template_id = '' WHERE render_template_id IN :templates"
        ).bindparams(templates)
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
    op.drop_index("ix_user_pipelines_agent_id", table_name="user_pipelines")
    op.drop_column("user_pipelines", "kind")
    op.drop_column("user_pipelines", "agent_id")
    op.drop_column("agents", "bundles")
    op.drop_column("agents", "check_options")
    op.drop_column("agents", "check_skills")
    op.drop_column("agents", "retired")
    op.drop_column("agents", "base_key")
