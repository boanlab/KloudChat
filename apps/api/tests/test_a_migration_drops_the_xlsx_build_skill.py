"""Migration 0103: the shipped skill 엑셀 파일 만들기 goes; rows, copies and references are
cleaned, edits kept."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    path = next((Path(__file__).parents[1] / "alembic" / "versions").glob("0103_*.py"))
    spec = importlib.util.spec_from_file_location("drop_xlsx_build_0103", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill(conn, id, owner, key, *, origin=None, body="본문", package_hash=None,
           scripts=None, name="엑셀 파일 만들기"):
    conn.execute(sa.text(
        "INSERT INTO skills (id, owner_id, catalog_key, origin_id, package_hash, name, "
        "description, when_to_use, body, version, kinds, required_tools, server_fields, "
        "script_files) VALUES (:i, :o, :k, :g, :h, :n, 'd', 'w', :b, '1.0.0', :ki, :rt, :sf, :sc)"
    ), {"i": id, "o": owner, "k": key, "g": origin, "h": package_hash, "n": name, "b": body,
        "ki": json.dumps(["chat"]), "rt": json.dumps([]),
        "sf": json.dumps({"stage": "post", "entry": "note"}) if key else None,
        "sc": json.dumps(scripts) if scripts else None})


def _hash(m, conn, id):
    row = conn.execute(sa.text("SELECT * FROM skills WHERE id = :i"), {"i": id}).one()._asdict()
    for field in ("kinds", "required_tools"):
        row[field] = json.loads(row[field])
    return m._hash(SimpleNamespace(**row))


def _before(conn) -> None:
    for ddl in (
        "CREATE TABLE users (id VARCHAR PRIMARY KEY, role VARCHAR, created_at DATETIME)",
        "CREATE TABLE skills (id VARCHAR PRIMARY KEY, owner_id VARCHAR, catalog_key VARCHAR, "
        "origin_id VARCHAR, package_hash VARCHAR, name VARCHAR, description VARCHAR, "
        "when_to_use VARCHAR, body VARCHAR, version VARCHAR, kinds JSON, required_tools JSON, "
        "server_fields JSON, script_files JSON)",
        "CREATE TABLE agents (id VARCHAR PRIMARY KEY, skill_ids JSON, active_skill_ids JSON, "
        "check_skills JSON)",
        "CREATE TABLE projects (id VARCHAR PRIMARY KEY, skill_ids JSON)",
        "CREATE TABLE sessions (id VARCHAR PRIMARY KEY, skill_ids JSON)",
        "CREATE TABLE user_templates (id VARCHAR PRIMARY KEY, check_skills JSON)",
    ):
        conn.execute(sa.text(ddl))
    for uid, role, at in (("admin", "admin", "2026-01-01 00:00:00"),
                          ("ann", "user", "2026-01-02 00:00:00"),
                          ("bob", "user", "2026-01-03 00:00:00")):
        conn.execute(sa.text("INSERT INTO users VALUES (:i, :r, :a)"),
                     {"i": uid, "r": role, "a": at})


def _run(conn) -> None:
    module = _migration()
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()


def _ids(conn) -> set[str]:
    return {r[0] for r in conn.execute(sa.text("SELECT id FROM skills"))}


def test_a_catalogue_row_and_untouched_copies_go_and_edited_copies_stay(caplog):
    m = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _skill(conn, "root", "admin", "xlsx-build")
        conn.execute(sa.text("UPDATE skills SET package_hash = :h WHERE id = 'root'"),
                     {"h": _hash(m, conn, "root")})
        _skill(conn, "same", "ann", "xlsx-build", origin="root")
        _skill(conn, "edited", "bob", "xlsx-build", origin="root", body="내가 고친 본문")
        _skill(conn, "mine", "ann", None, name="내 스킬")
        _skill(conn, "clean-root", "admin", "xlsx-clean", name="엑셀 정리")
        _skill(conn, "kept-key", "admin", "date-calc", name="날짜 계산")
        conn.execute(sa.text("UPDATE skills SET package_hash = :h WHERE id = 'other-root'"),
                     {"h": _hash(m, conn, "clean-root")})
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _run(conn)
        assert _ids(conn) == {"edited", "mine", "kept-key", "clean-root"}
        row = conn.execute(sa.text(
            "SELECT catalog_key, origin_id, package_hash, server_fields FROM skills "
            "WHERE id = 'edited'")).one()
        # the person's own now: no shipped identity, no origin, no shipped-script fields
        assert tuple(row) == (None, None, None, None)
        # a skill that is not the removed one is not touched
        assert conn.execute(sa.text(
            "SELECT catalog_key FROM skills WHERE id = 'kept-key'")).scalar() == "date-calc"
    assert "3 skill rows carry the removed key: 2 deleted, 1 kept" in caplog.text


def test_a_skill_the_administrator_edited_stays_with_its_copies():
    m = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _skill(conn, "root", "admin", "xlsx-build", name="엑셀 파일 만들기")
        conn.execute(sa.text("UPDATE skills SET package_hash = :h WHERE id = 'root'"),
                     {"h": _hash(m, conn, "root")})
        conn.execute(sa.text("UPDATE skills SET body = '관리자가 고침' WHERE id = 'root'"))
        _skill(conn, "copy", "ann", "xlsx-build", origin="root", body="관리자가 고침",
               name="엑셀 파일 만들기")
        _run(conn)
        assert _ids(conn) == {"root", "copy"}
        rows = conn.execute(sa.text(
            "SELECT id, catalog_key, origin_id FROM skills ORDER BY id")).all()
        # copies keep pointing at the administrator's skill, which is no longer shipped
        assert [tuple(r) for r in rows] == [("copy", None, "root"), ("root", None, None)]


def test_a_copy_with_scripts_of_its_own_stays_and_an_orphan_without_origin_stays():
    m = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _skill(conn, "root", "admin", "xlsx-build", name="엑셀 파일 만들기")
        conn.execute(sa.text("UPDATE skills SET package_hash = :h WHERE id = 'root'"),
                     {"h": _hash(m, conn, "root")})
        _skill(conn, "scripted", "ann", "xlsx-build", origin="root", name="엑셀 파일 만들기",
               scripts=[{"name": "x.py", "content": "print(1)"}])
        _skill(conn, "lost", "bob", "xlsx-build", origin="gone", name="엑셀 파일 만들기")
        _run(conn)
        assert _ids(conn) == {"scripted", "lost"}


def test_a_deleted_skill_leaves_no_reference_behind():
    m = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _skill(conn, "root", "admin", "xlsx-build", name="엑셀 파일 만들기")
        conn.execute(sa.text("UPDATE skills SET package_hash = :h WHERE id = 'root'"),
                     {"h": _hash(m, conn, "root")})
        _skill(conn, "copy", "ann", "xlsx-build", origin="root", name="엑셀 파일 만들기")
        _skill(conn, "mine", "ann", None, name="내 스킬")
        j = json.dumps
        conn.execute(sa.text("INSERT INTO agents VALUES ('a1', :s, :a, :c)"),
                     {"s": j(["copy", "mine"]), "a": j(["root"]),
                      "c": j(["grounding-check", "xlsx-build", "pii-mask"])})
        conn.execute(sa.text("INSERT INTO agents VALUES ('a2', :s, NULL, NULL)"),
                     {"s": j(["root"])})
        conn.execute(sa.text("INSERT INTO agents VALUES ('a3', NULL, NULL, :c)"),
                     {"c": j(["answer-hold"])})
        conn.execute(sa.text("INSERT INTO projects VALUES ('p1', :s)"), {"s": j(["copy", "mine"])})
        conn.execute(sa.text("INSERT INTO sessions VALUES ('s1', :s)"), {"s": j(["root", "copy"])})
        conn.execute(sa.text("INSERT INTO sessions VALUES ('s2', NULL)"))
        conn.execute(sa.text("INSERT INTO user_templates VALUES ('t1', :c)"),
                     {"c": j(["xlsx-build", "grounding-check"])})
        _run(conn)

        def get(sql):
            raw = conn.execute(sa.text(sql)).scalar()
            return json.loads(raw) if raw is not None else None

        assert get("SELECT skill_ids FROM agents WHERE id = 'a1'") == ["mine"]
        assert get("SELECT active_skill_ids FROM agents WHERE id = 'a1'") == []
        assert get("SELECT check_skills FROM agents WHERE id = 'a1'") == ["grounding-check",
                                                                        "pii-mask"]
        # an allowlist that held only the removed skill stays an allowlist, now empty
        assert get("SELECT skill_ids FROM agents WHERE id = 'a2'") == []
        assert get("SELECT check_skills FROM agents WHERE id = 'a3'") == ["answer-hold"]
        assert get("SELECT skill_ids FROM projects WHERE id = 'p1'") == ["mine"]
        assert get("SELECT skill_ids FROM sessions WHERE id = 's1'") == []
        assert get("SELECT skill_ids FROM sessions WHERE id = 's2'") is None
        assert get("SELECT check_skills FROM user_templates WHERE id = 't1'") == [
            "grounding-check"]
        # every id a row still holds names a skill that exists
        live = _ids(conn)
        for table, column in (("agents", "skill_ids"), ("agents", "active_skill_ids"),
                              ("projects", "skill_ids"), ("sessions", "skill_ids")):
            for (held,) in conn.execute(sa.text(f"SELECT {column} FROM {table}")):
                assert set(json.loads(held or "[]")) <= live


def test_a_database_without_those_skills_is_left_alone():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _skill(conn, "other", "admin", "date-calc", name="날짜 계산")
        _run(conn)
        assert _ids(conn) == {"other"}
