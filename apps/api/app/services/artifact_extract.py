"""Stores substantial code blocks and model-requested artifacts from a transcript as Artifact rows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind

#: ```lang\n … \n```
_FENCE = re.compile(r"```([A-Za-z0-9_+#.-]*)\n(.*?)```", re.S)

#: A block qualifies by meeting either bound.
_MIN_LINES = 8
_MIN_CHARS = 300

#: Fences that hold output or prose, not code.
_NOT_CODE = {"", "text", "txt", "output", "console", "log", "md", "markdown", "plain"}

_LANGUAGE_LABEL = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "yml": "yaml",
}


#: `def name(`, `function name(`, `class Name`, `const name =`.
_DEFINITION = re.compile(
    r"^\s*(?:async\s+)?(?:def|function|class|interface|type)\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=",
    re.M,
)


def _title_for(content: str, block_start: int, body: str, language: str, index: int) -> str:
    """Nearest heading above the block, else the first definition, else `"<lang> <n>"`."""
    before = content[:block_start].rstrip().splitlines()
    for line in reversed(before[-4:]):
        line = line.strip()
        if heading := re.match(r"^#{1,6}\s+(.{2,60})$", line):
            return heading.group(1).strip()
        if bold := re.match(r"^\*\*(.{2,60})\*\*:?$", line):
            return bold.group(1).strip()
    if defined := _DEFINITION.search(body):
        name = defined.group(1) or defined.group(2)
        return f"{name}()" if defined.group(1) else name
    return f"{language or '코드'} {index}"


def find_blocks(content: str) -> list[dict]:
    """Blocks in `content` worth keeping, in order."""
    out: list[dict] = []
    for match in _FENCE.finditer(content):
        raw_lang = (match.group(1) or "").lower()
        body = match.group(2).strip("\n")
        if raw_lang in _NOT_CODE:
            continue
        if len(body.splitlines()) < _MIN_LINES and len(body) < _MIN_CHARS:
            continue
        language = _LANGUAGE_LABEL.get(raw_lang, raw_lang)
        out.append(
            {
                "kind": ArtifactKind.html if language == "html" else ArtifactKind.code,
                "title": _title_for(content, match.start(), body, language, len(out) + 1),
                "data": {"content": body, "language": language},
            }
        )
    return out


async def extract(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    project_id: str | None,
    content: str,
) -> str | None:
    """Stores any new blocks and returns the last artifact's id, or None. Caller commits."""
    blocks = find_blocks(content)
    if not blocks:
        return None

    # Skip content already stored for this session (regenerated answers).
    existing = (await db.exec(select(Artifact).where(col(Artifact.session_id) == session_id))).all()
    seen = {(a.data or {}).get("content") for a in existing}

    last: Artifact | None = None
    for block in blocks:
        if block["data"]["content"] in seen:
            continue
        seen.add(block["data"]["content"])
        artifact = Artifact(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            kind=block["kind"],
            title=block["title"][:200],
            data=block["data"],
        )
        db.add(artifact)
        last = artifact
    return last.id if last is not None else None


def _identity(kind: str, title: str, data: dict) -> tuple[str, str, str]:
    """De-duplication key: content when present, else title plus the full data payload."""
    content = data.get("content")
    if isinstance(content, str):
        return (kind, "", content)
    return (kind, title, json.dumps(data, sort_keys=True, ensure_ascii=False))


def _mask_text_values(value: Any, masker: Callable[[str], tuple[str, int]]) -> Any:
    """Detached copy of `value` with every nested string masked."""
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
    """Stores artifacts requested via `create_artifact`, de-duplicated per session. Caller commits.
    """
    if not requests:
        return None

    # Masked at the persistence boundary: the in-turn tool request stays raw,
    # but no protected text may reach an Artifact row.
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
