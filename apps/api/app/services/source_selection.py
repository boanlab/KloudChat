"""Which project sources a conversation's turns may use (지식 서가's per-source switches).

Stored as the switched-off file ids (`SourceSelection.off`), so all sources are on until the
person turns one off and a source added later starts on. Context assembly
(`workspace_context._project_knowledge`) and the `search_knowledge` shelf
(`sessions._knowledge_shelf`) both drop the switched-off files through `keep`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.shelf import SourceSelection
from app.models.user import utcnow

T = TypeVar("T")

#: ids kept per conversation; a project holds far fewer sources
MAX_OFF = 2000


async def excluded(db: AsyncSession, session_id: str | None) -> set[str]:
    """File ids switched off for this conversation (empty: every source on)."""
    if not session_id:
        return set()
    row = await db.get(SourceSelection, session_id)
    if row is None or not isinstance(row.off, list):
        return set()
    return {str(item) for item in row.off if item}


def keep(files: Iterable[T], off: set[str]) -> list[T]:
    """`files` without the switched-off ones (rows with an `id`)."""
    if not off:
        return list(files)
    return [f for f in files if str(getattr(f, "id", "")) not in off]


async def store(db: AsyncSession, session_id: str, user_id: str, off: Iterable[Any]) -> list[str]:
    """Replace the conversation's switched-off ids; the caller commits."""
    ids = list(dict.fromkeys(str(item) for item in off if item))[:MAX_OFF]
    row = await db.get(SourceSelection, session_id)
    if row is None:
        row = SourceSelection(session_id=session_id, user_id=user_id)
    row.off = ids
    row.updated_at = utcnow()
    db.add(row)
    return ids


__all__ = ["MAX_OFF", "excluded", "keep", "store"]
