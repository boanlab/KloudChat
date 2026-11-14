"""Migrations 0078–0080 add `hwp`, `docx` and `pptx` to Postgres's `artifactkind`, outside the
transaction, and touch nothing elsewhere (the kind is a plain string column there)."""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: (revision, its file, the kind it adds)
MIGRATIONS = [
    ("0078", "0078_hwp_artifacts.py", "hwp"),
    ("0079", "0079_docx_artifacts.py", "docx"),
    ("0080", "0080_pptx_artifacts.py", "pptx"),
]


def _load(revision: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m{revision}", _VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.ran: list[tuple[str, bool]] = []
        self.autocommit = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def get_context(self):
        op = self

        @contextlib.contextmanager
        def autocommit_block():
            op.autocommit = True
            yield
            op.autocommit = False

        return SimpleNamespace(autocommit_block=autocommit_block)

    def execute(self, statement: str) -> None:
        self.ran.append((statement, self.autocommit))


@pytest.mark.parametrize(("revision", "filename", "kind"), MIGRATIONS)
def test_postgres_gets_the_value_outside_a_transaction(monkeypatch, revision, filename, kind):
    migration = _load(revision, filename)
    op = _Op("postgresql")
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert op.ran == [(f"ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS '{kind}'", True)]
    migration.downgrade()
    assert len(op.ran) == 1


@pytest.mark.parametrize(("revision", "filename", "kind"), MIGRATIONS)
def test_other_databases_are_left_alone(monkeypatch, revision, filename, kind):
    migration = _load(revision, filename)
    op = _Op("sqlite")
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    assert op.ran == []
    assert (migration.revision, migration.down_revision) == (revision, f"{int(revision) - 1:04d}")
