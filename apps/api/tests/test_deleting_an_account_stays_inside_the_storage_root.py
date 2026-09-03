"""Removing an account's files never reaches outside the upload directory.

The admin delete passes an id that came off the URL. A real id names one
directory under the storage root; anything else — a path that climbs out, a
nested path, the root itself — is left alone and reported as zero bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings
from app.services import files


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    storage = tmp_path / "uploads"
    monkeypatch.setattr(settings, "file_storage_dir", str(storage))
    return storage


def _seed(directory: Path, size: int = 10) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a.bin").write_bytes(b"x" * size)


def test_a_real_account_directory_is_removed_and_measured(root: Path) -> None:
    _seed(root / "u1", 10)
    (root / "u1" / "deep").mkdir()
    (root / "u1" / "deep" / "b.bin").write_bytes(b"y" * 5)
    assert files.remove_user_files("u1") == 15
    assert not (root / "u1").exists()


@pytest.mark.parametrize("user_id", ["..", "../outside", "u1/deep", ".", "", "/"])
def test_anything_that_is_not_one_directory_under_the_root_is_left_alone(
    root: Path, tmp_path: Path, user_id: str
) -> None:
    _seed(tmp_path / "outside")
    _seed(root / "u1")
    (root / "u1" / "deep").mkdir()
    assert files.remove_user_files(user_id) == 0
    assert (tmp_path / "outside" / "a.bin").exists()
    assert (root / "u1" / "a.bin").exists()
    assert (root / "u1" / "deep").is_dir()
    assert root.is_dir()


def test_a_missing_directory_is_zero_bytes(root: Path) -> None:
    root.mkdir(parents=True)
    assert files.remove_user_files("nobody") == 0
