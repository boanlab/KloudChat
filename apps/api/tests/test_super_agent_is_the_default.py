"""Super Agent is always offered and is how chat, report and slides conversations start.

No switch: the governance row has no `super_agent_enabled`, the admin API neither takes nor
returns it. The admin's per-surface default (설정 › 시스템 › 모델) may be Super Agent,
Super Agent (strict-local) or a fixed model; unset means Super Agent."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa_core
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from test_super_agent import CATALOGUE
from test_super_agent_turns import _SessionWriteDb

from app.models.chat import ChatSession, RoutingMode, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.models.workspace import Agent as WorkspaceAgent
from app.routers import sessions as sessions_router
from app.schemas.admin import GovernanceIn
from app.schemas.chat import SessionCreate
from app.services import governance, settings_store, start_mode
from app.services import models as model_service

ROUTED = (SessionKind.chat, SessionKind.report, SessionKind.slides)


@pytest.fixture
def world(monkeypatch):
    """Stored settings, governance and catalogue the start reads; tests edit them."""
    state = {
        "values": {},
        "policy": Governance(internal_data_strict_local=True),
        "models": list(CATALOGUE),
    }

    async def all_values(force: bool = False):
        return dict(state["values"])

    async def current(*_args, **_kwargs):
        return state["policy"]

    async def catalogue(force: bool = False):
        return {"models": state["models"], "litellmAvailable": True}

    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(governance, "current", current)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    return state


def _user(**kwargs) -> User:
    return User(email="p@example.test", password_hash="h", name="P", **kwargs)


# ── the switch is gone ──────────────────────────────────────────────────


def test_there_is_no_super_agent_switch_to_store_or_send() -> None:
    assert "super_agent_enabled" not in Governance.model_fields
    assert "super_agent_enabled" not in GovernanceIn.model_fields
    sent = GovernanceIn.model_validate({"superAgentEnabled": False})
    assert "super_agent_enabled" not in sent.model_dump(exclude_unset=True)


@pytest.mark.asyncio
async def test_the_governance_screen_does_not_return_a_switch(world) -> None:
    from app.routers import usage

    class _Db:
        async def get(self, _model, _id):
            return Governance()

    shown = await usage.get_governance(User(email="a@example.test", password_hash="h",
                                            name="A"), _Db())
    assert "superAgentEnabled" not in shown
    assert "superAgentRules" in shown


# ── where a new conversation starts ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ROUTED)
async def test_chat_report_and_slides_start_on_super_agent(world, kind) -> None:
    assert await start_mode.starting_mode(kind, _user()) == (RoutingMode.super, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ROUTED)
async def test_a_strict_project_starts_on_super_strict(world, kind) -> None:
    mode, _ = await start_mode.starting_mode(kind, _user(), project_id="p1")
    assert mode is RoutingMode.super_strict
    world["policy"] = Governance(internal_data_strict_local=False)
    mode, _ = await start_mode.starting_mode(kind, _user(), project_id="p1")
    assert mode is RoutingMode.super


@pytest.mark.asyncio
async def test_other_surfaces_start_on_their_model(world) -> None:
    for kind in (SessionKind.image, SessionKind.av):
        assert await start_mode.starting_mode(kind, _user()) == (RoutingMode.manual, "")


@pytest.mark.asyncio
async def test_a_fixed_or_personal_model_is_kept(world) -> None:
    assert await start_mode.starting_mode(
        SessionKind.chat, _user(), fixed_model="local/qwen3.8-27b"
    ) == (RoutingMode.manual, "local/qwen3.8-27b")
    picked = _user(preferences={"modelByKind": {"report": "local/qwen3.8-27b"}})
    assert await start_mode.starting_mode(SessionKind.report, picked) == (
        RoutingMode.manual, "local/qwen3.8-27b")
    # the pick is per surface: chat still starts on Super Agent
    assert (await start_mode.starting_mode(SessionKind.chat, picked))[0] is RoutingMode.super


@pytest.mark.asyncio
async def test_the_admin_default_decides_per_surface(world) -> None:
    world["values"] = {
        settings_store.ROUTING_CHAT: "super_strict",
        settings_store.ROUTING_REPORT: "manual",
        settings_store.ROUTING_SLIDES: "",
    }
    assert (await start_mode.starting_mode(SessionKind.chat, _user()))[0] is (
        RoutingMode.super_strict)
    assert (await start_mode.starting_mode(SessionKind.report, _user()))[0] is (
        RoutingMode.manual)
    # "" follows chat
    assert (await start_mode.starting_mode(SessionKind.slides, _user()))[0] is (
        RoutingMode.super_strict)


@pytest.mark.asyncio
async def test_allowed_models_that_leave_super_agent_nothing_start_on_a_model(world) -> None:
    only_strict = _user(allowed_models=["strict-local/qwen3.8-flash"])
    # Super Agent may answer on a strict-local model too: it stays the start
    assert (await start_mode.starting_mode(SessionKind.chat, only_strict))[0] is (
        RoutingMode.super)
    nothing = _user(allowed_models=["no/such-model"])
    assert (await start_mode.starting_mode(SessionKind.chat, nothing))[0] is RoutingMode.manual
    world["values"] = {settings_store.ROUTING_CHAT: "super_strict"}
    external_only = _user(allowed_models=["deepseek/deepseek-v4.1-flash"])
    # a strict-local default never widens to the external entry
    assert (await start_mode.starting_mode(SessionKind.chat, external_only))[0] is (
        RoutingMode.manual)


async def _create(monkeypatch, payload: SessionCreate, user: User, agent=None) -> ChatSession:
    async def enabled_kinds():
        return ["chat", "report", "slides", "image"]

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sessions_router.settings_store, "enabled_kinds", enabled_kinds)
    monkeypatch.setattr(sessions_router, "_validate_session_links", nothing)
    monkeypatch.setattr(sessions_router, "_project_render_template", nothing)
    monkeypatch.setattr(sessions_router, "_agent_template_loaded", nothing)
    db = _SessionWriteDb()

    async def get(_model, _id):
        return agent

    db.get = get
    await sessions_router.create_session(payload, user, db)
    (session,) = [row for row in db.added if isinstance(row, ChatSession)]
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ROUTED)
async def test_a_new_conversation_without_a_mode_starts_on_super_agent(
    world, monkeypatch, kind
) -> None:
    session = await _create(monkeypatch, SessionCreate(kind=kind), _user())
    assert (session.routing_mode, session.model) == (RoutingMode.super, "")
    session = await _create(monkeypatch, SessionCreate(kind=kind, project_id="p1"), _user())
    assert session.routing_mode is RoutingMode.super_strict
    session = await _create(
        monkeypatch, SessionCreate(kind=kind, model="local/qwen3.8-27b"), _user())
    assert (session.routing_mode, session.model) == (RoutingMode.manual, "local/qwen3.8-27b")


@pytest.mark.asyncio
async def test_an_agent_keeps_its_own_model_and_one_without_starts_on_super(
    world, monkeypatch
) -> None:
    user = _user()
    fixed = WorkspaceAgent(id="a1", owner_id=user.id, name="A", model="local/qwen3.8-27b")
    session = await _create(monkeypatch, SessionCreate(agent_id="a1"), user, agent=fixed)
    assert (session.routing_mode, session.model) == (RoutingMode.manual, "")
    loose = WorkspaceAgent(id="a2", owner_id=user.id, name="B", model="")
    session = await _create(monkeypatch, SessionCreate(agent_id="a2"), user, agent=loose)
    assert session.routing_mode is RoutingMode.super


@pytest.mark.asyncio
async def test_an_explicit_mode_is_kept(world, monkeypatch) -> None:
    session = await _create(
        monkeypatch, SessionCreate(routing_mode=RoutingMode.manual), _user())
    assert session.routing_mode is RoutingMode.manual
    session = await _create(
        monkeypatch, SessionCreate(kind=SessionKind.image), _user())
    assert session.routing_mode is RoutingMode.manual


# ── the admin's default-model selects ───────────────────────────────────


def test_unset_starts_are_super_agent_and_report_slides_may_follow_chat() -> None:
    roles = settings_store.model_roles_from({})
    assert [roles.start(k) for k in ("chat", "report", "slides")] == ["super"] * 3
    assert roles.surface_default("chat") == "super"
    roles = settings_store.model_roles_from({
        "models.chat": "local/qwen3.8-27b", "routing.chat": "manual",
        "models.report": "", "routing.report": "",
        "routing.slides": "super_strict",
    })
    assert roles.surface_default("chat") == "local/qwen3.8-27b"
    assert roles.surface_default("report") == ""
    assert roles.start("report") == "manual"
    assert roles.surface_default("slides") == "super_strict"
    # an unknown stored value is read as the default
    assert settings_store.model_roles_from({"routing.chat": "bogus"}).start("chat") == "super"


@pytest.mark.asyncio
async def test_the_selects_take_super_agent_entries(world) -> None:
    from app.routers.admin import _check_model_roles, _model_role_settings
    from app.schemas.admin import SystemSettingsIn

    await _check_model_roles(SystemSettingsIn(
        model_chat="super", model_report="super_strict", model_slides="super"))
    # image, audio and video are not routed by Super Agent
    with pytest.raises(HTTPException) as refused:
        await _check_model_roles(SystemSettingsIn(model_image="super"))
    assert refused.value.detail == "model_image_invalid"
    world["values"] = {"routing.report": "super_strict"}
    shown = await _model_role_settings()
    assert (shown["chat"], shown["report"], shown["slides"]) == (
        "super", "super_strict", "super")


@pytest.mark.asyncio
async def test_saving_a_start_keeps_the_model_and_a_model_fixes_the_start(monkeypatch) -> None:
    from test_audit_log import _admin, _audits, _Db, _Request

    from app.routers import admin
    from app.schemas.admin import SystemSettingsIn

    stored = {"models.chat": "local/qwen3.8-flash", "models.report": "local/qwen3.8-27b"}
    written: dict[str, str] = {}

    async def fake_stored(db):
        return dict(stored)

    async def fake_put(db, key, value, actor):
        written[key] = value

    async def fake_get_settings(a, db):
        return {}

    async def accept(_payload):
        return None

    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    monkeypatch.setattr(settings_store, "stored", fake_stored)
    monkeypatch.setattr(settings_store, "put", fake_put)
    monkeypatch.setattr(admin, "get_settings", fake_get_settings)
    monkeypatch.setattr(admin, "_check_model_roles", accept)
    db = _Db()
    await admin.put_settings(SystemSettingsIn(
        model_chat="super_strict", model_report="openai/gpt-x", model_slides=""),
        _Request(), _admin(), db)
    assert written == {
        "routing.chat": "super_strict",
        "models.report": "openai/gpt-x", "routing.report": "manual",
        "models.slides": "", "routing.slides": "",
    }
    (event,) = _audits(db)
    assert event.event_metadata["changes"]["model_chat"] == ["super", "super_strict"]
    assert event.event_metadata["changes"]["model_report"] == ["super", "openai/gpt-x"]


@pytest.mark.asyncio
async def test_the_catalogue_says_how_each_surface_starts(monkeypatch) -> None:
    from app.services import litellm

    values = {"models.chat": "local/qwen3.8-flash", "routing.report": "manual"}

    async def all_values(force: bool = False):
        return dict(values)

    async def model_info():
        return []

    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(litellm, "model_info", model_info)
    monkeypatch.setattr(model_service, "_adapter_entries", lambda: [])
    model_service.invalidate_cache()
    try:
        catalogue = await model_service.list_models(force=True)
    finally:
        model_service.invalidate_cache()
    assert catalogue["defaultRoutingByKind"] == {
        "chat": "super", "report": "manual", "slides": "super"}


# ── migration 0089 ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0089_super_agent_always.py"
    spec = importlib.util.spec_from_file_location("super_agent_always_0089", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(migration, conn, step: str) -> None:
    original = migration.op
    migration.op = Operations(MigrationContext.configure(conn))
    try:
        getattr(migration, step)()
    finally:
        migration.op = original


def test_the_migration_moves_shipped_defaults_to_super_agent_and_drops_the_switch() -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0089", "0088")
    engine = sa_core.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa_core.text(
            "CREATE TABLE governance (id VARCHAR PRIMARY KEY, "
            "super_agent_enabled BOOLEAN NOT NULL DEFAULT 1, super_agent_rules JSON)"))
        conn.execute(sa_core.text("INSERT INTO governance VALUES ('default', 0, '{}')"))
        conn.execute(sa_core.text(
            "CREATE TABLE system_settings (key VARCHAR PRIMARY KEY, value VARCHAR, "
            "secret BOOLEAN, updated_at TIMESTAMP, updated_by VARCHAR)"))
        conn.execute(sa_core.text("CREATE TABLE audit_events (action VARCHAR, metadata JSON)"))
        for key, value in (
            # shipped default, saved by nobody: moves
            ("models.chat", "local/qwen3.8-flash"),
            # changed by an administrator: kept on its model
            ("models.report", "openai/gpt-x"),
            # changed, but no administrator saved it (an environment seed): moves
            ("models.slides", "local/qwen3.6-35b"),
        ):
            conn.execute(sa_core.text(
                "INSERT INTO system_settings VALUES (:k, :v, 0, '2026-11-01', NULL)"),
                {"k": key, "v": value})
        conn.execute(sa_core.text(
            "INSERT INTO audit_events VALUES ('settings.update', :m)"),
            {"m": '{"changes": {"model_report": ["local/qwen3.8-27b", "openai/gpt-x"]}}'})
        _run(migration, conn, "upgrade")
        rows = dict(conn.execute(sa_core.text(
            "SELECT key, value FROM system_settings")).all())
        columns = [c["name"] for c in sa_core.inspect(conn).get_columns("governance")]
        assert "super_agent_enabled" not in columns
        assert rows["routing.report"] == "manual"
        assert "routing.chat" not in rows and "routing.slides" not in rows
        roles = settings_store.model_roles_from(rows)
        assert [roles.start(k) for k in ("chat", "report", "slides")] == [
            "super", "manual", "super"]
        # stored models stay the fallback
        assert rows["models.slides"] == "local/qwen3.6-35b"

        _run(migration, conn, "downgrade")
        rows = dict(conn.execute(sa_core.text(
            "SELECT key, value FROM system_settings")).all())
        columns = [c["name"] for c in sa_core.inspect(conn).get_columns("governance")]
        assert "super_agent_enabled" in columns
        assert not any(key.startswith("routing.") for key in rows)
        assert conn.execute(sa_core.text(
            "SELECT super_agent_enabled FROM governance")).scalar() in (1, True)


def test_a_report_set_to_follow_chat_keeps_following() -> None:
    migration = _migration()
    stored = {"models.report": "", "models.chat": "local/qwen3.8-flash"}
    assert migration.kept_starts(stored, {"model_report"}) == {"routing.report": ""}
    # a shipped value an administrator saved back is the shipped default: moves
    assert migration.kept_starts(
        {"models.chat": "local/qwen3.8-flash"}, {"model_chat"}) == {}
