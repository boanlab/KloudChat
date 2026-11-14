"""One version move at a time for a document whose body is JSON (the web report, the web
deck): a hand save, a restore and a chat revision each move the document only from the
version they read (`advance`), so the second of two writers is told instead of overwriting
the first. When a chat revision loses that race, the parts it changed are put onto the newer
version if nobody else touched them (`merge`); otherwise its result is kept apart, as a draft
beside the document (`draft`).

The office kinds (HWP, Word, PowerPoint) keep one file per version and move the same way in
`office_doc.save`.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import utcnow
from app.models.workspace import Artifact, ArtifactVersion

#: what a revision draft kept apart is called: 「분기 보고서 (수정안)」
DRAFT_SUFFIX = " (수정안)"
#: keys every writer derives from the parts again; never a conflict of their own
DERIVED = frozenset({"lint", "wordCount", "marks"})
#: the title travels with the body for `merge`
_TITLE = "__title__"


async def advance(db: AsyncSession, artifact: Artifact, expected: int, summary: str) -> bool:
    """Move `artifact` from `expected` to the next version in one conditional UPDATE, and keep
    the superseded body as a version row; False (nothing changed) when another writer moved
    it first. The check is the UPDATE itself (`WHERE id = … AND version = expected`): of two
    writers that both read `expected`, exactly one matches the row."""
    moved = await db.exec(
        update(Artifact)
        .where(col(Artifact.id) == artifact.id, col(Artifact.version) == expected)
        .values(version=expected + 1)
        .execution_options(synchronize_session=False)
    )
    if moved.rowcount != 1:
        return False
    db.add(
        ArtifactVersion(
            artifact_id=artifact.id,
            version=expected,
            data=artifact.data,
            storage_key=artifact.storage_key,
            summary=summary[:200],
        )
    )
    artifact.version = expected + 1
    return True


async def body_at(db: AsyncSession, artifact: Artifact, version: int) -> dict | None:
    """The document's body as it stood at `version` (the current one, or a kept version)."""
    if version == artifact.version:
        return dict(artifact.data or {})
    row = (
        await db.exec(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.version == version
            )
        )
    ).first()
    return dict(row.data) if row is not None and isinstance(row.data, dict) else None


def _ids(parts: list) -> dict[str, dict] | None:
    """Parts by id; None when one has no id or two share one (nothing to line up by)."""
    out: dict[str, dict] = {}
    for part in parts:
        if not isinstance(part, dict) or not part.get("id"):
            return None
        key = str(part["id"])
        if key in out:
            return None
        out[key] = part
    return out


def _parts(base: list, ours: list, theirs: list) -> list | None:
    """Our changed, added and removed parts put onto `theirs`; None when both sides changed,
    removed or moved the same part (or both reordered the document)."""
    b, o, t = _ids(base), _ids(ours), _ids(theirs)
    if b is None or o is None or t is None:
        return None
    changed = {i for i in o if i in b and o[i] != b[i]}
    added = [i for i in o if i not in b]
    removed = {i for i in b if i not in o}
    their_removed = {i for i in b if i not in t}
    their_added = {i for i in t if i not in b}
    touched = {i for i in t if i in b and t[i] != b[i]} | their_removed
    if (changed | removed) & touched or set(added) & their_added:
        return None
    common = [i for i in b if i in o and i in t]
    base_order = common
    our_order = [i for i in o if i in common]
    their_order = [i for i in t if i in common]
    if our_order != base_order:
        if their_order != base_order or their_added or their_removed:
            return None
        # we moved parts, they only rewrote others: our order, their words where they wrote
        return [copy.deepcopy(o[i] if i in changed or i in added else t[i]) for i in o]
    out = [copy.deepcopy(o[i] if i in changed else t[i]) for i in t if i not in removed]
    sequence = list(o)
    for i in added:
        at = sequence.index(i)
        before = next((p for p in reversed(sequence[:at])
                       if any(str(x.get("id")) == p for x in out)), None)
        place = 0 if before is None else next(
            n for n, x in enumerate(out) if str(x.get("id")) == before) + 1
        out.insert(place, copy.deepcopy(o[i]))
    return out


def merge(base: dict, ours: dict, theirs: dict, *, parts: str, title: tuple[str, str, str]
          ) -> tuple[dict, str] | None:
    """`ours` (a revision of `base`) put onto `theirs` (a later version of `base`): the parts
    under `parts` (`sections`, `slides`) by id, every other key and the title as a whole;
    `(body, title)`, or None when both changed the same thing. Derived keys come from
    `theirs` and are the caller's to recompute."""
    merged_parts = _parts(list(base.get(parts) or []), list(ours.get(parts) or []),
                          list(theirs.get(parts) or []))
    if merged_parts is None:
        return None
    b = {**base, _TITLE: title[0]}
    o = {**ours, _TITLE: title[1]}
    t = {**theirs, _TITLE: title[2]}
    out: dict[str, Any] = {}
    for key in {*b, *o, *t} - {parts}:
        if key in DERIVED:
            if key in t:
                out[key] = t[key]
            continue
        bv, ov, tv = b.get(key), o.get(key), t.get(key)
        if ov != bv and tv != bv and ov != tv:
            return None
        value = ov if ov != bv else tv
        if value is not None or key in t:
            out[key] = value
    out[parts] = merged_parts
    return {k: v for k, v in out.items() if k != _TITLE}, str(out.get(_TITLE) or title[2])


async def draft(db: AsyncSession, of: Artifact, *, title: str, data: dict) -> Artifact:
    """A revision kept apart beside `of`: its own document in the same conversation, so the
    person can open it, compare and take what they want; `of` is left as they saved it."""
    kept = Artifact(
        user_id=of.user_id,
        session_id=of.session_id,
        project_id=of.project_id,
        kind=of.kind,
        title=(title.removesuffix(DRAFT_SUFFIX) + DRAFT_SUFFIX)[:200],
        data=data,
        updated_at=utcnow(),
    )
    db.add(kept)
    await db.flush()
    return kept
