"""Migrations 0101 (nothing to do off Postgres), 0102 (the saved surface list gains `sheet` once)
and 0104 (sheets are listed with the working documents)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _run(prefix: str, conn) -> None:
    path = next((Path(__file__).parents[1] / "alembic" / "versions").glob(f"{prefix}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{prefix}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()


def _settings(conn, value: str | None) -> None:
    conn.execute(sa.text("CREATE TABLE system_settings (key VARCHAR PRIMARY KEY, value VARCHAR)"))
    if value is not None:
        conn.execute(sa.text("INSERT INTO system_settings VALUES ('kinds.enabled', :v)"),
                     {"v": value})


def _saved(conn) -> str | None:
    row = conn.execute(sa.text("SELECT value FROM system_settings")).first()
    return None if row is None else row[0]


def test_0101_leaves_a_database_that_is_not_postgres_alone():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _run("0101", conn)


def test_0102_adds_sheet_to_a_saved_list_once_and_leaves_the_rest():
    for before, after in (
        ("report,slides", "report,slides,sheet"),
        ("report, slides ,image", "report,slides,image,sheet"),
        ("report,sheet,slides", "report,sheet,slides"),
        ("", ""),
        ("  ", "  "),
    ):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            _settings(conn, before)
            _run("0102", conn)
            assert _saved(conn) == after, before
            _run("0102", conn)
            assert _saved(conn) == after, f"a second run changes nothing: {before!r}"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _settings(conn, None)
        _run("0102", conn)
        assert _saved(conn) is None


def test_0104_files_the_sheets_made_so_far_with_the_working_documents():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE artifacts (id VARCHAR PRIMARY KEY, kind VARCHAR, section VARCHAR)"))
        for id_, kind, section in (("s1", "xlsx", "media"), ("s2", "xlsx", ""),
                                   ("s3", "xlsx", "document"), ("d1", "docx", "document"),
                                   ("v1", "video", "media"), ("h1", "html", "html")):
            conn.execute(sa.text("INSERT INTO artifacts VALUES (:i, :k, :s)"),
                         {"i": id_, "k": kind, "s": section})
        _run("0104", conn)
        got = dict(conn.execute(sa.text("SELECT id, section FROM artifacts")).all())
        assert got == {"s1": "document", "s2": "document", "s3": "document",
                       "d1": "document", "v1": "media", "h1": "html"}
        _run("0104", conn)
