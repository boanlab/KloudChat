"""Web reports are Markdown only: sections stored as HTML become Markdown.

Reports used to keep a hand-formatted section as `format: "html"` (fonts, colours, sizes,
alignment, line spacing, merged cells). The web report is now one Markdown document; precise
formatting is what the HWP/Word outputs are for. For every `report` artifact with an HTML
section, the current data is first kept as an `artifact_versions` row (「Markdown 전환 전」),
then each HTML section is sanitised and converted with `richtext.to_markdown` (headings,
lists, tables, bold, italic, strikethrough, pictures, diagrams and drawn blocks kept; styling
and cell merges dropped) and the artifact's version moves on by one.

Downgrade changes nothing: the pre-conversion data stays in the version history, from which a
person can restore it.

Revision ID: 0082
Revises: 0081
Create Date: 2026-11-13
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUMMARY = "Markdown 전환 전"

_ARTIFACTS = sa.table(
    "artifacts",
    sa.column("id", sa.String),
    sa.column("kind", sa.String),
    sa.column("data", sa.JSON),
    sa.column("version", sa.Integer),
    sa.column("storage_key", sa.String),
)
_VERSIONS = sa.table(
    "artifact_versions",
    sa.column("id", sa.String),
    sa.column("artifact_id", sa.String),
    sa.column("version", sa.Integer),
    sa.column("data", sa.JSON),
    sa.column("storage_key", sa.String),
    sa.column("summary", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def _has_html(data: object) -> bool:
    return isinstance(data, dict) and any(
        isinstance(section, dict) and section.get("format") == "html"
        for section in data.get("sections") or []
    )


def upgrade() -> None:
    # the converter the API uses for every report save
    from app.services import richtext

    bind = op.get_bind()
    # `artifacts.kind` is a Postgres enum: compared as text
    rows = bind.execute(
        sa.select(
            _ARTIFACTS.c.id, _ARTIFACTS.c.data, _ARTIFACTS.c.version, _ARTIFACTS.c.storage_key
        ).where(sa.cast(_ARTIFACTS.c.kind, sa.String) == "report")
    ).all()
    for row_id, data, version, storage_key in rows:
        if isinstance(data, str):
            data = json.loads(data)
        if not _has_html(data):
            continue
        bind.execute(
            _VERSIONS.insert().values(
                id=uuid.uuid4().hex,
                artifact_id=row_id,
                version=version,
                data=data,
                storage_key=storage_key,
                summary=SUMMARY,
                created_at=datetime.now(UTC),
            )
        )
        converted = {
            **data,
            "sections": [
                richtext.markdown_section(section) if isinstance(section, dict) else section
                for section in data.get("sections") or []
            ],
        }
        bind.execute(
            _ARTIFACTS.update()
            .where(_ARTIFACTS.c.id == row_id)
            .values(data=converted, version=version + 1)
        )


def downgrade() -> None:
    # the HTML stays in the version history
    pass
