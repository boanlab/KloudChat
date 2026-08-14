"""Promoting substantial code out of a transcript into an artifact.

What earns a row is deliberately narrow: a three-line snippet illustrating a
point is part of the answer, and hoisting it out would fill the artifacts
screen with fragments.
"""

from __future__ import annotations

import json
import re

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.workspace import Artifact, ArtifactKind

#: ```lang\n … \n```
_FENCE = re.compile(r"```([A-Za-z0-9_+#.-]*)\n(.*?)```", re.S)

#: Below this a block is illustration, not a deliverable. Either bound qualifies:
#: a dense one-liner config can be short, a sparse script can be under 300 chars.
_MIN_LINES = 8
_MIN_CHARS = 300

#: Fences that are output or prose, not something anyone will run again.
_NOT_CODE = {"", "text", "txt", "output", "console", "log", "md", "markdown", "plain"}

_LANGUAGE_LABEL = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "yml": "yaml",
}


#: `def name(`, `function name(`, `class Name`, `const name =` — enough to name
#: a file after, across the languages a chat actually produces.
_DEFINITION = re.compile(
    r"^\s*(?:async\s+)?(?:def|function|class|interface|type)\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=",
    re.M,
)


def _title_for(content: str, block_start: int, body: str, language: str, index: int) -> str:
    """The nearest heading above the block, else what the code defines.

    Models usually announce a block, and that sentence beats "python 1".
    Failing that, the first thing the code defines is still scannable in a list.
    """
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
    """Stores any new blocks and returns the last artifact's id, or None.

    The id is what the caller shows the panel. Caller commits.
    """
    blocks = find_blocks(content)
    if not blocks:
        return None

    # Regenerating an answer produces the same code again.
    existing = (
        await db.exec(select(Artifact).where(col(Artifact.session_id) == session_id))
    ).all()
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
    """What makes two requested artifacts the same one.

    Keyed on content where there is any, since that is what a regenerated answer
    repeats verbatim. A chart has none — it is series plus a table — so keying
    on `data["content"]` alone would collide every chart on `None`.
    """
    content = data.get("content")
    if isinstance(content, str):
        return (kind, "", content)
    return (kind, title, json.dumps(data, sort_keys=True, ensure_ascii=False))


async def store_requested(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    project_id: str | None,
    requests: list[dict],
) -> str | None:
    """Stores artifacts the model asked for with `create_artifact`.

    Separate from `extract`: extraction guesses at what looks worth keeping,
    this is the model stating it. A request is stored even when short.

    Same de-duplication — regenerating an answer calls the tool again.
    """
    if not requests:
        return None

    existing = (
        await db.exec(select(Artifact).where(col(Artifact.session_id) == session_id))
    ).all()
    seen = {_identity(a.kind.value, a.title, a.data or {}) for a in existing}

    last: Artifact | None = None
    for request in requests:
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
