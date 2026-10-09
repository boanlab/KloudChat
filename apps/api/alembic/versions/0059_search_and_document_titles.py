"""Search over conversations and results; report and slides conversations named after their
document.

- `artifacts.search_text`: what a result says as plain words, filled here for every row and
  kept in step by the model on each write.
- `sessions.title_set_by_user`: the person renamed the conversation; no document title
  replaces theirs. Every existing row starts false: there is no record of who typed a title,
  and the backfill below only touches titles that are still the automatic prompt prefix.
- Trigram indexes (`pg_trgm`, a trusted contrib extension since PostgreSQL 13) on message
  text, conversation and result titles and result text, so `ILIKE '%…%'` of three or more
  characters, Korean included, is answered from an index. Where the extension cannot be
  created the indexes are skipped and search still works by scanning the caller's rows.
- Backfill: a report or slides conversation whose title is still the first 40 characters
  of its first request, and whose document has a title, takes the document's title.
  Downgrade drops the columns and indexes but does not restore those titles.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-09
"""

from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept in step with `chat_service.TITLE_CHARS`; a migration does not import the app.
TITLE_CHARS = 40

TRIGRAM_INDEXES = (
    ("ix_messages_content_trgm", "messages", "content"),
    ("ix_sessions_title_trgm", "sessions", "title"),
    ("ix_artifacts_title_trgm", "artifacts", "title"),
    ("ix_artifacts_search_text_trgm", "artifacts", "search_text"),
)

# ── kept in step with `app.services.search_text.artifact_text` ────────

TEXT_LIMIT = 20_000
SKIP_KEYS = frozenset({
    "id", "templateId", "status", "format", "level", "layout", "kind", "language", "design",
    "lint", "research", "diagrams", "factCheck", "marks", "src", "url", "href", "image",
    "logo", "accent", "font", "visualStyle", "theme", "citationStyle", "wordCount",
    "chartType", "storageKey", "sourceFile", "request", "mime", "aspect", "voice", "model",
    "style", "css", "svg", "png", "dataUrl", "thumbnail", "provider", "jobId",
})
_SILENT = frozenset({"style", "script", "svg", "math", "template", "noscript"})


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.size = 0
        self._silent = 0

    def handle_starttag(self, tag, attrs) -> None:  # noqa: ANN001
        if tag in _SILENT:
            self._silent += 1
        elif tag in {"p", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag) -> None:  # noqa: ANN001
        if tag in _SILENT and self._silent:
            self._silent -= 1

    def handle_data(self, data) -> None:  # noqa: ANN001
        if not self._silent and self.size < TEXT_LIMIT:
            self.parts.append(data)
            self.size += len(data)


def _html_text(markup: str) -> str:
    if "<" not in markup:
        return markup
    reader = _Text()
    try:
        reader.feed(markup)
        reader.close()
    except Exception:  # noqa: BLE001
        pass
    return "".join(reader.parts)


def _strings(value: Any, out: list[str], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith(("data:", "http://", "https://")):
            return
        words = " ".join(_html_text(text).split())
        if words:
            out.append(words)
            budget[0] -= len(words) + 1
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in SKIP_KEYS:
                _strings(item, out, budget)
    elif isinstance(value, list):
        for item in value:
            _strings(item, out, budget)


def artifact_text(data: Any) -> str:
    out: list[str] = []
    _strings(data, out, [TEXT_LIMIT])
    return " ".join(out)[:TEXT_LIMIT]


# ── helpers ───────────────────────────────────────────────────────────


def _json(value: Any) -> Any:
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def provisional_title(prompt: str) -> str:
    """`chat_service.provisional_title`: the prompt's first `TITLE_CHARS`, spaces collapsed."""
    return " ".join((prompt or "").split())[:TITLE_CHARS]


def title_backfill(bind) -> list[tuple[str, str]]:  # noqa: ANN001
    """`(session id, new title)` for every conversation the backfill renames. Reads only."""
    candidates = bind.execute(
        sa.text(
            "SELECT s.id, s.title, a.title FROM sessions s JOIN artifacts a "
            "ON a.id = s.artifact_id AND a.user_id = s.user_id "
            "WHERE s.kind IN ('report', 'slides') AND a.kind IN ('report', 'deck', 'html') "
            "AND s.title <> '' AND a.title <> ''"
        )
    ).all()
    renames: list[tuple[str, str]] = []
    batch = 500
    for at in range(0, len(candidates), batch):
        chunk = candidates[at : at + batch]
        firsts: dict[str, str] = {}
        rows = bind.execute(
            sa.text(
                "SELECT session_id, content FROM messages WHERE role = 'user' "
                "AND session_id IN :ids ORDER BY session_id, created_at"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": [row[0] for row in chunk]},
        ).all()
        for session_id, content in rows:
            firsts.setdefault(session_id, content or "")
        for session_id, title, document in chunk:
            new = " ".join(document.split())[:200]
            if (
                session_id in firsts
                and new
                and new != title
                and title == provisional_title(firsts[session_id])
            ):
                renames.append((session_id, new))
    return renames


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    op.add_column(
        "artifacts",
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "sessions",
        sa.Column("title_set_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Fill the text a page at a time; bodies can be large.
    update = sa.text("UPDATE artifacts SET search_text = :text WHERE id = :id")
    last = ""
    while True:
        rows = bind.execute(
            sa.text("SELECT id, data FROM artifacts WHERE id > :last ORDER BY id LIMIT 200"),
            {"last": last},
        ).all()
        if not rows:
            break
        params = [
            {"id": row_id, "text": text}
            for row_id, data in rows
            if (text := artifact_text(_json(data)))
        ]
        if params:
            bind.execute(update, params)
        last = rows[-1][0]

    renames = title_backfill(bind)
    if renames:
        bind.execute(
            sa.text("UPDATE sessions SET title = :title WHERE id = :id"),
            [{"id": session_id, "title": title} for session_id, title in renames],
        )

    if postgres:
        # Trusted since PostgreSQL 13: a database owner may create it without superuser.
        # Where even that is refused, search works without the indexes.
        op.execute(
            "DO $$ BEGIN CREATE EXTENSION IF NOT EXISTS pg_trgm; "
            "EXCEPTION WHEN insufficient_privilege OR undefined_file OR feature_not_supported "
            "THEN RAISE NOTICE 'pg_trgm unavailable; search runs without trigram indexes'; "
            "END $$"
        )
        for name, table, column in TRIGRAM_INDEXES:
            op.execute(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') "
                f"THEN CREATE INDEX IF NOT EXISTS {name} ON {table} "
                f"USING gin ({column} gin_trgm_ops); END IF; END $$"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name, _table, _column in TRIGRAM_INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")
    op.drop_column("sessions", "title_set_by_user")
    op.drop_column("artifacts", "search_text")
