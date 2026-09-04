"""디스크가 차면 지운 계정의 파일부터, 오래된 것부터 거둔다."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.services import storage


class _Db:
    def __init__(self, living: list[str]) -> None:
        self.living = living
        self.added: list = []
        self.commits = 0

    async def exec(self, _query):
        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _Rows(list(self.living))

    def add(self, row) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


def _seed(root: Path) -> None:
    (root / "alive").mkdir()
    (root / "alive" / "keep.bin").write_bytes(b"x" * 100)
    (root / "gone").mkdir()
    old = root / "gone" / "old.bin"
    old.write_bytes(b"x" * 100)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (root / "gone" / "new.bin").write_bytes(b"x" * 100)


@pytest.mark.asyncio
async def test_orphans_go_oldest_first_and_only_past_the_mark(monkeypatch, tmp_path) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(storage.file_service, "storage_root", lambda: tmp_path)
    db = _Db(living=["alive"])

    # Under the mark: measured, nothing removed.
    monkeypatch.setattr(storage, "fill", lambda _root: 0.5)
    result = await storage.reclaim(db, threshold=0.8)
    assert result.orphan_files == 2 and result.freed_files == 0
    assert (tmp_path / "gone" / "old.bin").exists()

    # Past the mark: oldest orphan first, stop once under the mark; live accounts untouched.
    fills = iter([0.9, 0.7, 0.7, 0.7])
    monkeypatch.setattr(storage, "fill", lambda _root: next(fills))
    result = await storage.reclaim(db, threshold=0.8)
    assert result.freed_files == 1
    assert not (tmp_path / "gone" / "old.bin").exists()
    assert (tmp_path / "gone" / "new.bin").exists()
    assert (tmp_path / "alive" / "keep.bin").exists()
    assert db.added and db.added[0].action == "storage.reclaim"


@pytest.mark.asyncio
async def test_a_manual_sweep_takes_every_orphan_and_the_empty_directory(
    monkeypatch, tmp_path
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(storage.file_service, "storage_root", lambda: tmp_path)
    monkeypatch.setattr(storage, "fill", lambda _root: 0.3)
    result = await storage.reclaim(_Db(living=["alive"]), threshold=1e-9)
    assert result.freed_files == 2 and result.removed == ["gone"]
    assert not (tmp_path / "gone").exists() and (tmp_path / "alive" / "keep.bin").exists()
