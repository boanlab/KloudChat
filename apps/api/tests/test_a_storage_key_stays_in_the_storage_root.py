"""A storage key always names a file under the storage root, never outside it."""

from __future__ import annotations

import pytest

from app.services import files


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "storage_root", lambda: tmp_path)
    return tmp_path


def test_a_written_blob_reads_back_from_under_the_root(root) -> None:
    key = files.write_blob("u1", "f1", "../../보고서.hwpx", b"data")
    assert key == "u1/f1_.._.._보고서.hwpx"
    assert files.read_blob(key) == b"data"
    assert files.blob_path(key).is_relative_to(root)


@pytest.mark.parametrize("key", ["../outside", "u1/../../outside", "/etc/passwd"])
def test_a_key_that_climbs_out_is_refused(root, key) -> None:
    with pytest.raises(ValueError):
        files.blob_path(key)
    with pytest.raises(ValueError):
        files.read_blob(key)


def test_deleting_a_bad_key_only_logs(root, caplog) -> None:
    files.delete_blob("../outside\nforged line")
    assert "forged line" in caplog.text
    assert "\nforged" not in caplog.text
