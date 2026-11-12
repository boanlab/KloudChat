"""Migration 0078 adds `hwp` to Postgres's `artifactkind`, outside the transaction, and
touches nothing elsewhere (the kind is a plain string column there)."""

from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0078_hwp_artifacts.py"
_SPEC = importlib.util.spec_from_file_location("m0078", _PATH)
m0078 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m0078)


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


def test_postgres_gets_the_value_outside_a_transaction(monkeypatch):
    op = _Op("postgresql")
    monkeypatch.setattr(m0078, "op", op)
    m0078.upgrade()
    assert op.ran == [("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'hwp'", True)]
    m0078.downgrade()
    assert len(op.ran) == 1


def test_other_databases_are_left_alone(monkeypatch):
    op = _Op("sqlite")
    monkeypatch.setattr(m0078, "op", op)
    m0078.upgrade()
    assert op.ran == []
    assert (m0078.revision, m0078.down_revision) == ("0078", "0077")
