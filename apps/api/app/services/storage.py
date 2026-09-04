"""Reclaims files of deleted accounts once the volume passes the fill mark.

Uploads live under `file_storage_dir/<user id>/`; the directory outlives the
account row. Living accounts are never touched. Callers: the boot-time loop
and the administrator's button on the usage screen.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.user import AuditEvent, User
from app.services import files as file_service

log = logging.getLogger(__name__)

#: How often the boot-time loop looks at the volume.
_INTERVAL = 30 * 60


@dataclass(slots=True)
class Reclaim:
    """What the sweep found and what it did."""

    fill_before: float
    fill_after: float
    #: Bytes and files under directories whose owner is gone, before the sweep.
    orphan_bytes: int = 0
    orphan_files: int = 0
    freed_bytes: int = 0
    freed_files: int = 0
    removed: list[str] = field(default_factory=list)


def fill(root: Path) -> float:
    usage = shutil.disk_usage(root)
    return (usage.total - usage.free) / usage.total if usage.total else 0.0


async def orphan_directories(db: AsyncSession, root: Path) -> list[Path]:
    """Per-account directories whose account is gone."""
    if not root.exists():
        return []
    directories = [d for d in root.iterdir() if d.is_dir()]
    if not directories:
        return []
    living = set(
        (
            await db.exec(select(User.id).where(col(User.id).in_([d.name for d in directories])))
        ).all()
    )
    return [d for d in directories if d.name not in living]


async def reclaim(
    db: AsyncSession, *, threshold: float | None = None, dry_run: bool = False
) -> Reclaim:
    """Removes orphaned files, oldest first, until the volume is under `threshold`.

    `dry_run` measures without deleting. Below the threshold nothing is removed.
    """
    root = file_service.storage_root()
    limit = settings.storage_reclaim_at if threshold is None else threshold
    before = fill(root)
    result = Reclaim(fill_before=before, fill_after=before)
    orphans = await orphan_directories(db, root)
    candidates: list[tuple[float, Path]] = []
    for directory in orphans:
        for path in directory.rglob("*"):
            if path.is_file():
                stat = path.stat()
                result.orphan_bytes += stat.st_size
                result.orphan_files += 1
                candidates.append((stat.st_mtime, path))
    if dry_run or limit <= 0 or before < limit or not candidates:
        return result
    for _, path in sorted(candidates):
        size = path.stat().st_size
        try:
            path.unlink()
        except OSError as exc:
            log.warning("reclaim could not remove %s: %s", path, exc)
            continue
        result.freed_bytes += size
        result.freed_files += 1
        if fill(root) < limit:
            break
    for directory in orphans:
        # An emptied directory goes with its files; one with files left stays.
        if not any(directory.rglob("*")):
            directory.rmdir()
            result.removed.append(directory.name)
    result.fill_after = fill(root)
    if result.freed_files:
        log.info(
            "reclaim: removed %d orphaned files (%d bytes); volume %.1f%% -> %.1f%%",
            result.freed_files,
            result.freed_bytes,
            before * 100,
            result.fill_after * 100,
        )
        db.add(
            AuditEvent(
                actor_id=None,
                action="storage.reclaim",
                target="파일 저장소",
                detail=f"삭제된 계정의 파일 {result.freed_files}개, {result.freed_bytes:,} B",
                event_metadata={
                    "fillBefore": round(before, 4),
                    "fillAfter": round(result.fill_after, 4),
                    "directoriesRemoved": result.removed,
                },
            )
        )
        await db.commit()
    return result


async def watch() -> None:
    """Boot-time loop; a failed sweep is logged and waits for the next."""
    while True:
        try:
            if settings.storage_reclaim_at > 0:
                async with SessionLocal() as db:
                    await reclaim(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a sweep that fails is logged, not fatal
            log.warning("storage reclaim failed: %s", exc)
        await asyncio.sleep(_INTERVAL)


__all__ = ["Reclaim", "fill", "orphan_directories", "reclaim", "watch"]
