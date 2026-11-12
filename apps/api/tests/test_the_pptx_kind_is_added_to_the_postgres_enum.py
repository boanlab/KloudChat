"""Migration 0080 adds `pptx` to Postgres's `artifactkind`, outside the transaction, and
touches nothing elsewhere (the kind is a plain string column there)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from test_the_hwp_kind_is_added_to_the_postgres_enum import _Op

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0080_pptx_artifacts.py"
_SPEC = importlib.util.spec_from_file_location("m0080", _PATH)
m0080 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m0080)


def test_postgres_gets_the_value_outside_a_transaction(monkeypatch):
    op = _Op("postgresql")
    monkeypatch.setattr(m0080, "op", op)
    m0080.upgrade()
    assert op.ran == [("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'pptx'", True)]
    m0080.downgrade()
    assert len(op.ran) == 1


def test_other_databases_are_left_alone(monkeypatch):
    op = _Op("sqlite")
    monkeypatch.setattr(m0080, "op", op)
    m0080.upgrade()
    assert op.ran == []
    assert (m0080.revision, m0080.down_revision) == ("0080", "0079")
