"""Where an artifact is listed (`artifacts.section`) and which generated files copy one (`files.copy_of`).

- `artifacts.section`: `html`, `code`, `chart`, `diagram`, `document`, `media`, or `snippet`. A
  `snippet` is a code/HTML artifact with no `kind` key in its body; it opens from its conversation
  panel and is listed nowhere.
- `files.copy_of`: the artifact a generated file copies (a chart or diagram picture, or a document
  export), so 만든 파일 leaves it out.

Downgrade drops both columns.

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-15
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENTS = {"report", "deck", "hwp", "docx", "pptx"}
_EXPORTS = re.compile(r"\.(pdf|hwpx?|docx|pptx|md)$", re.I)
_SRC = re.compile(r"/files/([0-9a-f]{32})/content")
_BATCH = 500


def _body(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def section_of(kind: str, data: dict) -> str:
    """As `services/artifact_section.section_of` on the day of this migration, plus the
    `snippet` test only this migration makes."""
    if kind in _DOCUMENTS:
        return "document"
    if kind in ("html", "code"):
        return kind if "kind" in data or not data else "snippet"
    if kind == "chart":
        return "chart"
    if kind == "image":
        if data.get("style") == "figure" or data.get("source"):
            return "diagram"
        if data.get("engine") == "matplotlib" or data.get("style") == "차트":
            return "chart"
    return "media"


def _stem(text: str) -> str:
    """A title or file name for comparison: letters and digits only, first 20."""
    return re.sub(r"[\W_]+", "", text.lower())[:20]


def _update(bind, table: str, column: str, value: str, ids: list[str]) -> None:
    statement = sa.text(f"UPDATE {table} SET {column} = :value WHERE id IN :ids").bindparams(
        sa.bindparam("ids", expanding=True))
    for start in range(0, len(ids), _BATCH):
        bind.execute(statement, {"value": value, "ids": ids[start:start + _BATCH]})


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("section", sa.String(length=16), nullable=False,
                                         server_default=""))
    op.create_index("ix_artifacts_user_section", "artifacts", ["user_id", "section"])
    op.add_column("files", sa.Column("copy_of", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    by_section: dict[str, list[str]] = {}
    pictures: dict[str, str] = {}
    documents: dict[str, list[tuple[str, str]]] = {}
    rows = bind.execute(sa.text(
        "SELECT id, CAST(kind AS TEXT), data, session_id, title FROM artifacts")).all()
    for artifact_id, kind, raw, session_id, title in rows:
        data = _body(raw)
        section = section_of(str(kind), data)
        by_section.setdefault(section, []).append(artifact_id)
        if section in ("chart", "diagram") and str(kind) == "image":
            if found := _SRC.search(str(data.get("src") or "")):
                pictures[found.group(1)] = artifact_id
        if section == "document" and session_id and _stem(title or ""):
            documents.setdefault(session_id, []).append((_stem(title or ""), artifact_id))
    for section, ids in by_section.items():
        _update(bind, "artifacts", "section", section, ids)

    copies: dict[str, list[str]] = {}
    for file_id, artifact_id in pictures.items():
        copies.setdefault(artifact_id, []).append(file_id)
    if documents:
        generated = bind.execute(sa.text(
            "SELECT id, name, session_id FROM files WHERE origin = 'generated' "
            "AND session_id IS NOT NULL AND copy_of IS NULL")).all()
        for file_id, name, session_id in generated:
            if not _EXPORTS.search(name or "") or session_id not in documents:
                continue
            stem = _stem(_EXPORTS.sub("", name))
            for title, artifact_id in documents[session_id]:
                if stem and (title.startswith(stem) or stem.startswith(title)):
                    copies.setdefault(artifact_id, []).append(file_id)
                    break
    for artifact_id, file_ids in copies.items():
        _update(bind, "files", "copy_of", artifact_id, file_ids)


def downgrade() -> None:
    op.drop_column("files", "copy_of")
    op.drop_index("ix_artifacts_user_section", table_name="artifacts")
    op.drop_column("artifacts", "section")
