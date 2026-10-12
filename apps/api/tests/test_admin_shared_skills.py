"""Shipped skills are the administrator's own skills, shared with everyone.

The owner row follows its package until the owner edits it; copies follow until their owner
edits them; checks run by key whether or not anyone added the skill.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from catalog_text_skills import use_text_skills
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import SessionKind
from app.models.user import User, UserRole
from app.models.workspace import Skill, SkillSource, Visibility
from app.routers import sessions
from app.routers import workspace as ws
from app.schemas.workspace import SkillIn
from app.services import check_skills, starter


@pytest.fixture(autouse=True)
def _text_skills(monkeypatch):
    monkeypatch.setattr(starter, "_AGENTS", [])
    use_text_skills(monkeypatch)


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all,
                                tables=[metadata.tables[n] for n in ("users", "skills", "agents")])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _seeded(db):
    admin = User(email="admin@x", name="관리자", password_hash="x", role=UserRole.admin)
    member = User(email="member@x", name="구성원", password_hash="x")
    db.add_all([admin, member])
    await db.commit()
    await starter.seed_catalog(db, admin.id)
    await db.commit()
    return admin, member


async def _row(db, owner_id: str, key: str) -> Skill:
    return (await db.exec(select(Skill).where(
        Skill.owner_id == owner_id, Skill.catalog_key == key))).one()


def _ship(monkeypatch, key: str, **changes) -> None:
    specs = [{**spec, **changes} if spec["key"] == key else spec for spec in starter._SKILLS]
    monkeypatch.setattr(starter, "_SKILLS", specs)


@pytest.mark.asyncio
async def test_every_shipped_skill_is_an_admin_skill_shared_with_everyone(db):
    admin, member = await _seeded(db)
    rows = (await db.exec(select(Skill))).all()

    assert rows and {row.owner_id for row in rows} == {admin.id}
    assert {row.source for row in rows} == {SkillSource.personal}
    assert all(row.package_hash for row in rows)
    published = [row for row in rows if not next(
        s for s in starter._SKILLS if s["key"] == row.catalog_key).get("draft")]
    assert all(row.visibility is Visibility.org for row in published)
    # the store says who shares them
    store = await ws.list_skill_store(member, db)
    citation = next(row for row in store if row.catalog_key == "citation")
    assert citation.owner_name == "관리자" and citation.official


@pytest.mark.asyncio
async def test_a_copy_is_the_members_to_edit_and_names_who_shared_it(db):
    admin, member = await _seeded(db)
    origin = await _row(db, admin.id, "citation")

    copy = await ws.install_skill(origin.id, member, db)

    assert copy.source is SkillSource.workspace and copy.origin_id == origin.id
    listed = next(s for s in await ws.list_skills(member, db) if s.id == copy.id)
    assert listed.origin_owner_name == "관리자"
    edited = await ws.patch_skill(copy.id, SkillIn(name=copy.name, body="내 절차"), member, db)
    assert edited.body == "내 절차"


@pytest.mark.asyncio
async def test_a_new_package_reaches_untouched_rows_and_leaves_edited_ones(db, monkeypatch):
    admin, member = await _seeded(db)
    other = User(email="other@x", name="다른 사람", password_hash="x")
    db.add(other)
    await db.commit()
    citation = await _row(db, admin.id, "citation")
    evidence = await _row(db, admin.id, "evidence")
    kept = await ws.install_skill(citation.id, member, db)
    changed = await ws.install_skill(citation.id, other, db)
    await ws.patch_skill(changed.id, SkillIn(name=changed.name, body="내가 고친 절차"),
                         other, db)
    evidence_copy = await ws.install_skill(evidence.id, member, db)
    # the admin edits one shipped skill like any other
    await ws.patch_skill(evidence.id, SkillIn(name=evidence.name, body="관리자 절차",
                                              visibility=Visibility.org), admin, db)
    await db.commit()

    _ship(monkeypatch, "citation", body="새 절차", version="1.2.0")
    _ship(monkeypatch, "evidence", body="새 근거 절차", version="1.2.0")
    await starter.seed_catalog(db, admin.id)
    await db.commit()

    assert (citation.body, citation.version) == ("새 절차", "1.2.0")
    follower = await db.get(Skill, kept.id)
    assert (follower.body, follower.version) == ("새 절차", "1.2.0")
    assert (await db.get(Skill, changed.id)).body == "내가 고친 절차"
    assert evidence.body == "관리자 절차"
    assert (await db.get(Skill, evidence_copy.id)).body != "새 근거 절차"
    # a second sync changes nothing
    assert await starter.seed_catalog(db, admin.id) == 0
    assert evidence.body == "관리자 절차"


@pytest.mark.asyncio
async def test_rows_synced_before_hashes_follow_and_old_copies_are_linked(db):
    admin = User(email="admin@x", name="관리자", password_hash="x", role=UserRole.admin)
    member = User(email="member@x", name="구성원", password_hash="x")
    db.add_all([admin, member])
    await db.commit()
    stale = dict(name="인용 형식 맞추기", description="옛 설명", when_to_use="옛 시점",
                 body="옛 절차", version="1.0.0", kinds=["chat"], required_tools=[],
                 catalog_key="citation")
    owner_row = Skill(owner_id=admin.id, slug="citation", source=SkillSource.personal,
                      visibility=Visibility.org, **stale)
    orphan = Skill(owner_id=member.id, slug="citation", source=SkillSource.workspace, **stale)
    db.add_all([owner_row, orphan])
    await db.commit()

    await starter.seed_catalog(db, admin.id)
    await db.commit()

    shipped = next(s for s in starter._SKILLS if s["key"] == "citation")
    assert owner_row.body == shipped["body"] and owner_row.package_hash
    assert orphan.origin_id == owner_row.id
    assert orphan.body == shipped["body"]


@pytest.mark.asyncio
async def test_a_template_brings_the_admins_row_not_someones_shared_copy(db):
    admin, member = await _seeded(db)
    origin = await _row(db, admin.id, "citation")
    copy = await db.get(Skill, (await ws.install_skill(origin.id, member, db)).id)
    copy.body, copy.visibility = "내가 고친 절차", Visibility.org
    db.add(copy)
    await db.commit()

    template = SimpleNamespace(skills=("citation",))
    blocks, event = await sessions._template_skills(db, template, SessionKind.report, None)

    assert [s["id"] for s in event["skills"]] == [origin.id]
    assert "내가 고친 절차" not in blocks[0]


def test_check_skills_run_by_key_for_someone_who_added_nothing():
    """An agent's checks resolve from the packages, not from anyone's rows."""
    keys = check_skills.attached(agent_checks=["grounding-check", "gap-check", "pii-mask"])
    assert set(keys) == {"grounding-check", "gap-check", "pii-mask"}


# ── migration 0070 ──────────────────────────────────────────────────────


def _migration():
    path = next((Path(__file__).parents[1] / "alembic" / "versions").glob("0070_*.py"))
    spec = importlib.util.spec_from_file_location("admin_shared_skills_0070", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_turns_built_in_rows_into_the_admins_and_copies():
    migration = _migration()
    assert migration.revision == "0070"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY, role VARCHAR, "
                             "created_at DATETIME)"))
        conn.execute(sa.text("CREATE TABLE skills (id VARCHAR PRIMARY KEY, owner_id VARCHAR, "
                             "catalog_key VARCHAR, source VARCHAR)"))
        conn.execute(sa.text(
            "INSERT INTO users VALUES ('first', 'admin', '2025-01-01'), "
            "('later', 'admin', '2026-01-01'), ('member', 'user', '2024-01-01')"))
        conn.execute(sa.text(
            "INSERT INTO skills VALUES ('owned', 'first', 'citation', 'built_in'), "
            "('seed', 'first', NULL, 'built_in'), ('copy', 'member', 'citation', 'built_in'), "
            "('admin-copy', 'later', 'citation', 'built_in'), "
            "('own', 'member', NULL, 'personal')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = dict(conn.execute(sa.text("SELECT id, source FROM skills")).all())
        assert rows == {"owned": "personal", "seed": "workspace", "copy": "workspace",
                        "admin-copy": "workspace", "own": "personal"}
        assert "package_hash" in {c["name"] for c in sa.inspect(conn).get_columns("skills")}
