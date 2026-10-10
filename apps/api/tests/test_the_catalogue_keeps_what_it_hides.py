"""Hidden is not gone: a shipped skill, 서식, theme or connector entry marked unlisted leaves
the lists people pick from, and keeps working everywhere it is already used — by name, in a
bundle, in an old conversation, in an export. Administrators still see it, marked.

Also the step-1 catalogue itself: what is shown, 마무리하기's HWPX default for the 공문
family, and 엑셀 정리 keeping duplicate rows unless asked (migration 0063 for the rows)."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import SessionKind
from app.models.user import AuditEvent, User, UserRole
from app.models.workspace import (
    Agent,
    Connector,
    ConnectorCatalogEntry,
    ConnectorTool,
    DesignSystem,
    Skill,
    StoredFile,
)
from app.routers import connectors
from app.routers import sessions as sessions_router
from app.routers import workspace as ws
from app.schemas.workspace import DesignSystemOut
from app.services import (
    bundles,
    deck_export,
    design_templates,
    skill_packages,
    starter,
)
from app.services.tools import catalog

SHOWN_SKILLS = {"grounding-check", "gap-check", "calc-check", "date-calc", "quote-calc",
                "xlsx-clean", "pdf-table"}
SHOWN_DOCUMENTS = {"doc-internal-draft", "doc-event-plan", "doc-result-report",
                   "doc-gaejo-report", "doc-comparison", "doc-minutes"}
HIDDEN_DECKS = {"deck-case", "deck-defense", "deck-editorial", "deck-lecture", "deck-proposal",
                "deck-signal"}
SKILLS_DIR = Path(skill_packages.__file__).resolve().parent.parent / "skills"


# ── what is shown ──────────────────────────────────────────────────────


def test_seven_skills_are_shown_and_the_rest_kept_as_parts():
    packages = skill_packages.packages()
    assert {k for k, p in packages.items() if p.listed} == SHOWN_SKILLS
    assert not any(packages[k].draft for k in SHOWN_SKILLS)
    hidden = set(packages) - SHOWN_SKILLS
    # Nothing is deleted: the drafts stay drafts, the two published ones stay published.
    assert {"text-fit", "generic-language-killer", "copy-editing", "release-notes"} <= hidden
    assert {k for k in hidden if not packages[k].draft} == {"text-fit"}
    for key in hidden:
        assert packages[key].body and packages[key].description


def test_an_unlisted_skill_runs_by_name_and_in_bundles(monkeypatch):
    monkeypatch.setattr(settings, "skill_drafts", False)
    # Unlike a draft, unlisted does not gate run_skill_script.
    assert skill_packages.find("text-fit").key == "text-fit"
    assert skill_packages.find("글자 수 맞춤").key == "text-fit"
    assert "text-fit" in {p.key for p in skill_packages.scripted()}
    assert not skill_packages.listed("text-fit") and skill_packages.listed("xlsx-clean")
    # A person's own skill (no package) is listed.
    assert skill_packages.listed(None) and skill_packages.listed("my-own")
    # 마무리하기 still runs the unlisted 상투어 check.
    made = bundles.compile("report", [{"bundle": "finish", "items": {}}], bundles.shipped())
    assert "generic-language-killer" in made.check_skills
    # Editors that name server parts still offer it.
    from app.services import skill_specs

    assert "text-fit" in json.dumps(skill_specs.catalogue(), ensure_ascii=False)


def test_the_documents_and_one_deck_are_shown_the_other_decks_kept():
    listed = {t.id for t in design_templates.listed()}
    offered = {t.id for t in design_templates.offered()}
    assert {t for t in listed if t.startswith("doc-")} == SHOWN_DOCUMENTS
    assert {t for t in listed if t.startswith("deck-")} == {"deck-briefing"}
    assert HIDDEN_DECKS <= offered and not HIDDEN_DECKS & listed
    for template_id in HIDDEN_DECKS:
        # Still chosen by id: an old conversation or agent keeps it.
        assert design_templates.choosable(template_id).id == template_id
        assert sessions_router._resolved_template_id(template_id, SessionKind.slides) \
            == template_id
    # A new template starts only from a shown one.
    from app.services import user_templates

    starts = {s["id"] for s in user_templates.catalogue()["starts"]}
    assert starts == SHOWN_DOCUMENTS


@pytest.mark.asyncio
async def test_the_template_list_marks_the_hidden_decks():
    rows = await ws.list_design_templates(user=None, surface="slides")
    marks = {row.id: row.listed for row in rows}
    assert marks["deck-briefing"] is True
    assert {k for k, v in marks.items() if not v} == HIDDEN_DECKS


@pytest.mark.parametrize("template_id", sorted(HIDDEN_DECKS))
def test_a_deck_in_a_hidden_template_still_exports(template_id):
    from pptx import Presentation

    template = design_templates.get(template_id)
    slides = [{"layout": "cover", "title": "점검 결과", "bullets": ["시설팀"]},
              {"layout": "bullets", "title": "점검은 12건이다", "bullets": ["완료 10건"]}]
    data = deck_export.to_pptx("점검", slides, template=template.pptx_template)
    assert data[:2] == b"PK"
    assert len(Presentation(io.BytesIO(data)).slides) == 2


def test_the_presentation_theme_is_hidden_and_kept():
    assert [d["name"] for d in starter._DESIGNS if d.get("listed") is not False] == [
        "기본", "문서용 명조"]
    row = DesignSystem(owner_id="u", name="발표용 청록", tokens={"accent": "#0f766e"})
    assert DesignSystemOut.of(row, owner_id="u").listed is False
    assert DesignSystemOut.of(DesignSystem(owner_id="u", name="기본", tokens={}),
                             owner_id="u").listed


# ── skills: the store and a person's own list ──────────────────────────


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
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (
                User, AuditEvent, Agent, Skill, DesignSystem, StoredFile,
                ConnectorCatalogEntry, Connector, ConnectorTool)])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
    catalog.use([])


@pytest.mark.asyncio
async def test_the_store_hides_unlisted_skills_from_members_and_marks_them_for_admins(db):
    owner = User(email="owner@example.com", name="Owner", password_hash="x", role=UserRole.admin)
    other_admin = User(email="admin2@example.com", name="Admin", password_hash="x",
                       role=UserRole.admin)
    member = User(email="member@example.com", name="Member", password_hash="x")
    db.add_all([owner, other_admin, member])
    await db.commit()
    await starter.seed_designs(db, owner.id)
    await starter.sync_catalog(db, owner)
    await db.commit()
    seen = {row.catalog_key: row.listed for row in await ws.list_skill_store(member, db)}
    assert set(seen) == SHOWN_SKILLS and all(seen.values())
    marked = {row.catalog_key: row.listed for row in await ws.list_skill_store(other_admin, db)}
    assert marked["text-fit"] is False and SHOWN_SKILLS <= set(marked)
    # A copy taken earlier stays the person's and works; its row says it is hidden.
    text_fit = (await db.exec(select(Skill).where(Skill.catalog_key == "text-fit",
                                                  Skill.owner_id == owner.id))).one()
    copy = await ws._install_skill(db, member, text_fit)
    await db.commit()
    mine = {row.id: row for row in await ws.list_skills(member, db)}
    assert mine[copy.id].listed is False and mine[copy.id].scripts
    owned = {row.catalog_key: row.listed for row in await ws.list_skills(owner, db)}
    assert owned["text-fit"] is False and owned["xlsx-clean"] is True


# ── connectors ─────────────────────────────────────────────────────────


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_an_unlisted_connector_entry_is_kept_for_those_who_installed_it(db, monkeypatch):
    async def no_server(*_a, **_k):
        raise RuntimeError("offline")

    monkeypatch.setattr(connectors.mcp, "list_tools", no_server)
    admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
    early, late = (User(email=f"{n}@example.com", name=n, password_hash="x")
                   for n in ("early", "late"))
    db.add_all([admin, early, late])
    await db.commit()
    entry = connectors.CatalogEntryIn(slug="clock", name="시계", transport="stdio",
                                      endpoint="uvx mcp-server-time")
    await connectors.admin_add_entry(entry, admin, db, _request())
    await connectors.install("clock", early, db)
    row = (await connectors.admin_catalog(admin, db))["entries"][0]
    assert row["listed"] is True
    out = await connectors.admin_edit_entry(row["id"], entry.model_copy(update={"listed": False}),
                                            admin, db, _request())
    assert out["entries"][0]["listed"] is False
    # Not offered to someone new; still there, marked installed, for whoever has it.
    assert "clock" not in [e["slug"] for e in await connectors.get_catalog(late, db)]
    assert [e["installed"] for e in await connectors.get_catalog(early, db)
            if e["slug"] == "clock"] == [True]
    # Installable by slug all the same (unlike a disabled entry).
    assert (await connectors.install("clock", late, db)).slug == "clock"


def _migration():
    path = next((Path(__file__).parents[1] / "alembic" / "versions").glob("0063_*.py"))
    spec = importlib.util.spec_from_file_location("catalogue_listed_0063", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0063_hides_youtube_turns_deep_research_off_and_goes_back():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0063", "0062")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE connector_catalog (id VARCHAR PRIMARY KEY, slug VARCHAR,"
            " description VARCHAR, enabled BOOLEAN NOT NULL)"))
        conn.execute(sa.text("CREATE TABLE connectors (id VARCHAR PRIMARY KEY, slug VARCHAR,"
                             " enabled BOOLEAN NOT NULL)"))
        conn.execute(sa.text(
            "INSERT INTO connector_catalog VALUES ('1', 'time', :time, 1),"
            " ('2', 'youtube', 'y', 1), ('3', 'deep-research', 'd', 1), ('4', 'crm', 'c', 1)"),
            {"time": migration.TIME_BEFORE})
        conn.execute(sa.text("INSERT INTO connectors VALUES ('i1', 'deep-research', 1),"
                             " ('i2', 'youtube', 1)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = {r[0]: tuple(r[1:]) for r in conn.execute(sa.text(
            "SELECT slug, enabled, listed, description FROM connector_catalog"))}
        assert rows == {"time": (1, 1, migration.TIME_AFTER), "youtube": (1, 0, "y"),
                        "deep-research": (0, 1, "d"), "crm": (1, 1, "c")}
        assert "지어내" not in migration.TIME_AFTER
        # Installed connectors are rows of their own: untouched.
        assert set(conn.execute(sa.text("SELECT slug, enabled FROM connectors"))) == {
            ("deep-research", 1), ("youtube", 1)}
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        rows = {r[0]: tuple(r[1:]) for r in conn.execute(sa.text(
            "SELECT slug, enabled, description FROM connector_catalog"))}
        assert rows["time"] == (1, migration.TIME_BEFORE) and rows["deep-research"][0] == 1
        assert "listed" not in {c["name"] for c in sa.inspect(conn).get_columns(
            "connector_catalog")}


# ── 마무리하기 for the 공문 family ──────────────────────────────────────


@pytest.mark.parametrize(("doc_format", "made"), [
    ("internal_draft", "hwpx"), ("official", "hwpx"), ("event_plan", "hwpx"),
    ("result_report", "hwpx"), ("gaejo", "hwpx"), ("minutes", "docx"), ("comparison", "docx"),
    (None, "docx"),
])
def test_finishing_exports_the_official_family_as_hwpx(doc_format, made):
    shipped = bundles.shipped()
    picked = [{"bundle": "finish", "items": {"export": True}}]
    steps = bundles.compile("report", picked, shipped, doc_format=doc_format).steps
    assert [s["settings"] for s in steps if s["kind"] == "export"] == [{"formats": [made]}]
    assert bundles.view(picked, shipped, doc_format=doc_format)[0]["items"]["export"] == made
    # The person's pick wins.
    mine = [{"bundle": "finish", "items": {"export": "pdf"}}]
    steps = bundles.compile("report", mine, shipped, doc_format=doc_format).steps
    assert [s["settings"] for s in steps if s["kind"] == "export"] == [{"formats": ["pdf"]}]


def test_the_shipped_agents_still_do_not_export_on_their_own():
    for spec in starter.offered_agents(drafts=True):
        assert not any(s["kind"] == "export" for s in spec["steps"] or []), spec["key"]


# ── 엑셀 정리 keeps duplicates unless asked ────────────────────────────


def _clean(tmp_path, **options) -> dict:
    pytest.importorskip("openpyxl")
    (tmp_path / "거래.csv").write_text(
        "전표일,거래처,금액\n2026-09-01,가나상사,12000\n2026-09-01,가나상사,12000\n"
        "2026-09-02,다라상사,5000\n", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SKILLS_DIR / "xlsx-clean" / "scripts" / "clean.py"),
         json.dumps({"file": "거래.csv", "recalc": False, **options}, ensure_ascii=False)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads(done.stdout.strip().splitlines()[-1])["sheets"][0]


def test_xlsx_clean_marks_identical_rows_and_keeps_them_by_default(tmp_path):
    sheet = _clean(tmp_path)
    assert sheet["rows_out"] == 3 and "duplicate" not in sheet["removed"]
    assert sheet["suspected_duplicates"] == 1


def test_xlsx_clean_removes_identical_rows_only_when_asked(tmp_path):
    sheet = _clean(tmp_path, dedupe=True)
    assert sheet["rows_out"] == 2 and sheet["removed"]["duplicate"] == 1
    assert "suspected_duplicates" not in sheet


def test_an_unknown_id_is_still_refused():
    with pytest.raises(HTTPException):
        sessions_router._resolved_template_id("deck-nope", SessionKind.slides)
