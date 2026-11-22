"""`create_artifact` requests stored as Artifact rows.

An answer's inline code blocks are not stored: they stay part of the answer."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind


def _identity(kind: str, title: str, data: dict) -> tuple[str, str, str]:
    """Dedup key: content, else title plus data."""
    content = data.get("content")
    if isinstance(content, str):
        return (kind, "", content)
    return (kind, title, json.dumps(data, sort_keys=True, ensure_ascii=False))


def _mask_text_values(value: Any, masker: Callable[[str], tuple[str, int]]) -> Any:
    """Detached copy with every nested string masked."""
    if isinstance(value, str):
        return masker(value)[0]
    if isinstance(value, dict):
        return {key: _mask_text_values(item, masker) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_text_values(item, masker) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_text_values(item, masker) for item in value)
    return value


async def store_requested(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    project_id: str | None,
    requests: list[dict],
    masker: Callable[[str], tuple[str, int]] | None = None,
) -> str | None:
    """Store `create_artifact` requests, deduped per session. Caller commits."""
    if not requests:
        return None

    # masked at persistence: no protected text in Artifact rows
    safe_requests = (
        [_mask_text_values(request, masker) for request in requests]
        if masker is not None
        else requests
    )

    existing = (await db.exec(select(Artifact).where(col(Artifact.session_id) == session_id))).all()
    seen = {_identity(a.kind.value, a.title, a.data or {}) for a in existing}

    last: Artifact | None = None
    for request in safe_requests:
        identity = _identity(request["kind"], request["title"], request["data"])
        if identity in seen:
            continue
        seen.add(identity)
        artifact = Artifact(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            kind=ArtifactKind(request["kind"]),
            title=request["title"][:200],
            data=request["data"],
        )
        db.add(artifact)
        last = artifact
    return last.id if last is not None else None
