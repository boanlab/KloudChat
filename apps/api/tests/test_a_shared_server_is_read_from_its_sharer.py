"""A shared MCP server is read by reference: address, shared env and gateway key stay on the
sharer's row, and who may run what follows the sharer's role at the moment of the run."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from test_default_connectors import (  # noqa: F401
    GATEWAY,
    TIME_COMMAND,
    _copies,
    _people,
    _request,
    _server,
    _share,
    db,
)

from app.models.user import User, UserRole, UserStatus
from app.models.workspace import (
    Connector,
    ConnectorTool,
    Transport,
)
from app.routers import admin as admin_router
from app.routers import connectors
from app.schemas.workspace import (
    BulkDelete,
    ConnectorIn,
    ConnectorPatch,
    InstallRequest,
    RequiredEnvIn,
)
from app.services import mcp, netguard, settings_store
from app.services.tools import catalog, registry

PUBLIC = "https://93.184.216.34/mcp"
INTERNAL = "http://10.0.0.5/mcp"


async def _refused(call) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value.status_code, str(refused.value.detail)


async def _third(db) -> User:  # noqa: F811
    other = User(email="c@example.com", name="셋째", password_hash="x", status=UserStatus.active)
    db.add(other)
    await db.commit()
    return other


async def _demote(db, user: User) -> None:  # noqa: F811
    user.role = UserRole.user
    db.add(user)
    await db.commit()
    await catalog.refresh(db)


# ── by reference ───────────────────────────────────────────────────────


async def test_a_reference_follows_the_shared_address_and_copies_nothing(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(
        db, admin, slug="crm", name="CRM", transport=Transport.http, endpoint="http://10.0.0.5/a",
        env={"BASE": "${TOOLS_FETCH_URL}", "SHARED_TOKEN": "admin-secret"},
        required_env=[{"key": "API_TOKEN", "label": "토큰", "secret": True}])
    made = await connectors.install(shared.id, member, db,
                                    InstallRequest(env={"API_TOKEN": "mine"}))
    ref = await db.get(Connector, made.id)
    assert (ref.origin_id, ref.endpoint) == (shared.id, "")
    # only the person's own credential is stored on the reference
    assert settings_store.decrypt_env(ref.env) == {"API_TOKEN": "mine"}
    assert made.env_keys == ["API_TOKEN"] and made.endpoint == ""
    assert "admin-secret" not in made.model_dump_json()

    env = await catalog.connector_env(ref)
    assert env["SHARED_TOKEN"] == "admin-secret" and env["API_TOKEN"] == "mine"
    assert await catalog.effective_endpoint(ref) == "http://10.0.0.5/a"

    # The sharer moves the server and rotates a value: the reference reads the new ones.
    shared.endpoint = "http://10.0.0.5/b"
    shared.env = {**shared.env, "SHARED_TOKEN": settings_store.encrypt_secret("rotated")}
    db.add(shared)
    await db.commit()
    await catalog.refresh(db)
    await db.refresh(ref)
    assert await catalog.effective_endpoint(ref) == "http://10.0.0.5/b"
    assert (await catalog.connector_env(ref))["SHARED_TOKEN"] == "rotated"
    assert (ref.endpoint, settings_store.decrypt_env(ref.env)) == ("", {"API_TOKEN": "mine"})


async def test_a_reference_shows_the_shared_servers_name_but_not_its_values(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin, name="시계", description="시각")
    mine = await connectors.install(shared.id, member, db)
    assert (mine.name, mine.description, mine.official, mine.origin_gone,
            mine.origin_owner_name) == ("시계", "시각", True, False, "관리자")
    await connectors.patch(shared.id, ConnectorPatch(name="새 시계"), admin, db)
    assert (await connectors.list_connectors(member, db))[0].name == "새 시계"
    # 「공식」 is whether the sharer is an administrator now
    await _demote(db, admin)
    assert (await connectors.list_connectors(member, db))[0].official is False


# ── trust follows the sharer ───────────────────────────────────────────


async def test_a_demoted_sharer_stops_a_references_command_at_once(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin)
    made = await connectors.install(shared.id, member, db)
    ref = await db.get(Connector, made.id)
    await catalog.check_runnable(ref, owner_is_admin=False)

    spawned: list[str] = []

    async def call_tool(transport, endpoint, name, arguments, env, **_kwargs):
        spawned.append(endpoint)
        return "12:00"

    monkeypatch.setattr(mcp, "call_tool", call_tool)
    first = next(t for t in await registry.connector_tools(db, member)
                 if t.name.endswith("get_current_time"))
    assert (await first.run({})).content == "12:00" and spawned == [TIME_COMMAND]

    await _demote(db, admin)
    with pytest.raises(catalog.NotRunnable):
        await catalog.check_runnable(ref, owner_is_admin=False)
    # the tool object made before the demotion refuses too: the check runs at each call
    result = await first.run({})
    assert result.failed and "관리자만 등록할 수 있어" in result.content
    assert spawned == [TIME_COMMAND]
    synced = await connectors.sync(ref.id, member, db)
    assert synced.status == "error" and "관리자만" in (synced.error or "")

    # promoted again: it runs again
    admin.role = UserRole.admin
    db.add(admin)
    await db.commit()
    await catalog.refresh(db)
    await catalog.check_runnable(ref, owner_is_admin=False)


async def test_a_members_shared_server_runs_without_shared_env_or_key_and_never_inside(
        db, monkeypatch):  # noqa: F811
    asked = []

    async def list_tools(transport, endpoint, env=None, public_only=False):
        asked.append((endpoint, public_only, dict(env or {})))
        return [{"name": "look"}]

    monkeypatch.setattr(connectors.mcp, "list_tools", list_tools)
    _, bob, _ = await _people(db)
    alice = await _third(db)
    # The sharer is a member who set a public address, a secret and a stray gateway-key wish.
    shared = await _share(
        db, alice, slug="wiki", name="위키", transport=Transport.http, endpoint=PUBLIC,
        env={"SHARED_TOKEN": "alice-secret", "WIKI_URL": "${TOOLS_STT_URL}"},
        tools_key_var="WIKI_URL", tools_key_feature="stt",
        required_env=[{"key": "WIKI_TOKEN", "label": "토큰"}])
    made = await connectors.install(shared.id, bob, db, InstallRequest(env={"WIKI_TOKEN": "bob"}))
    ref = await db.get(Connector, made.id)
    assert made.status == "connected" and made.official is False
    # guarded: public addresses only; nothing of the sharer's env or the gateway key
    assert asked == [(PUBLIC, True, {"WIKI_TOKEN": "bob"})]
    assert await catalog.connector_env(ref) == {"WIKI_TOKEN": "bob"}
    assert await catalog.guarded(ref, owner_is_admin=False)

    # An internal address on the shared row (set before a rule, or by hand) is refused.
    shared.endpoint = INTERNAL
    db.add(shared)
    await db.commit()
    await catalog.refresh(db)
    with pytest.raises(catalog.NotRunnable, match=catalog.INTERNAL_ENDPOINT):
        await catalog.check_runnable(ref, owner_is_admin=False)
    synced = await connectors.sync(ref.id, bob, db)
    assert synced.status == "error" and catalog.INTERNAL_ENDPOINT in (synced.error or "")
    assert len(asked) == 1
    # The sharer's placeholder aimed at a tool address (an internal host) is refused the same way.
    async def resolve(host):
        return ["10.0.0.9"]

    monkeypatch.setattr(netguard, "_system_resolver", resolve)
    shared.endpoint = "${TOOLS_RESEARCH_URL}/mcp"
    db.add(shared)
    await db.commit()
    await catalog.refresh(db)
    with pytest.raises(catalog.NotRunnable):
        await catalog.check_runnable(ref, owner_is_admin=False)

    # A command shared by a member never runs for anyone.
    shared.transport, shared.endpoint = Transport.stdio, TIME_COMMAND
    db.add(shared)
    await db.commit()
    await catalog.refresh(db)
    with pytest.raises(catalog.NotRunnable, match="관리자만 등록할 수 있어"):
        await catalog.check_runnable(ref, owner_is_admin=False)
    # ... even for an administrator holding the reference: the sharer's role decides
    with pytest.raises(catalog.NotRunnable):
        await catalog.check_runnable(ref, owner_is_admin=True)


async def test_a_member_cannot_share_a_command_or_an_internal_address(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    _, member, _ = await _people(db)
    web = await connectors.add_custom(
        ConnectorIn(name="위키", transport=Transport.http, endpoint=PUBLIC), member, db)
    assert (await connectors.patch(web.id, ConnectorPatch(visibility="org"), member, db)
            ).visibility == "org"
    # a command or an internal address in a row of one's own cannot be put into the store
    command = Connector(owner_id=member.id, name="셸", slug="sh", transport=Transport.stdio,
                        endpoint="uvx x")
    inside = Connector(owner_id=member.id, name="사내", slug="in", transport=Transport.http,
                       endpoint=INTERNAL)
    db.add_all([command, inside])
    await db.commit()
    assert await _refused(connectors.patch(
        command.id, ConnectorPatch(visibility="org"), member, db)) == (
        403, "stdio_requires_admin")
    assert await _refused(connectors.patch(
        inside.id, ConnectorPatch(visibility="org"), member, db)) == (
        422, catalog.INTERNAL_ENDPOINT)


# ── a share that ended ─────────────────────────────────────────────────


async def test_a_reference_is_refused_once_the_server_is_no_longer_shared(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin)
    ref = await db.get(Connector, (await connectors.install(shared.id, member, db)).id)
    assert len(await registry.connector_tools(db, member)) == 1  # the write tool starts off

    # withdrawn: private again
    await connectors.patch(shared.id, ConnectorPatch(visibility="private"), admin, db)
    for call in (catalog.check_runnable(ref, owner_is_admin=False),
                 catalog.effective_endpoint(ref), catalog.connector_env(ref)):
        with pytest.raises(catalog.NotRunnable, match=catalog.GONE):
            await call
    assert await registry.connector_tools(db, member) == []
    row = (await connectors.list_connectors(member, db))[0]
    assert row.origin_gone is True and row.endpoint == ""
    synced = await connectors.sync(ref.id, member, db)
    assert synced.status == "error" and catalog.GONE in (synced.error or "")
    # still removable by its owner, and no longer offered
    assert await connectors.get_store(member, db) == []

    # shared again: it runs again (same reference)
    await connectors.patch(shared.id, ConnectorPatch(visibility="org"), admin, db)
    await catalog.check_runnable(ref, owner_is_admin=False)
    assert (await connectors.list_connectors(member, db))[0].origin_gone is False

    # switched off, then deleted
    shared.enabled = False
    db.add(shared)
    await db.commit()
    await catalog.refresh(db)
    with pytest.raises(catalog.NotRunnable, match=catalog.GONE):
        await catalog.check_runnable(ref, owner_is_admin=False)
    shared.enabled = True
    db.add(shared)
    await db.commit()
    await connectors.patch(shared.id, ConnectorPatch(visibility="private"), admin, db)
    await connectors.uninstall(shared.id, admin, db)
    with pytest.raises(catalog.NotRunnable, match=catalog.GONE):
        await catalog.check_runnable(ref, owner_is_admin=False)
    assert (await connectors.list_connectors(member, db))[0].origin_gone is True


# ── what a reference lets its person change ───────────────────────────


async def test_a_reference_takes_only_a_switch_and_the_asked_credentials(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin, slug="weather", name="날씨",
                          required_env=[{"key": "API_TOKEN", "label": "토큰"}])
    mine = await connectors.install(shared.id, member, db, InstallRequest(env={"API_TOKEN": "t"}))
    for body in ({"endpoint": "https://x.example.com"}, {"visibility": "org"},
                 {"by_default": True}, {"listed": False}, {"tools_key_var": "A"},
                 {"required_env": [RequiredEnvIn(key="K")]}, {"name": "내 것"}):
        assert await _refused(connectors.patch(mine.id, ConnectorPatch(**body), member, db)) == (
            422, "catalogue_endpoint"), body
    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(env={"LD_PRELOAD": "/x.so", "API_TOKEN": "u"}), member, db,
    )) == (422, "env_not_allowed:LD_PRELOAD")
    assert (await connectors.patch(mine.id, ConnectorPatch(enabled=False), member, db)
            ).enabled is False
    assert (await connectors.patch(mine.id, ConnectorPatch(env={"API_TOKEN": "u"}), member, db)
            ).env_keys == ["API_TOKEN"]
    # an administrator holding a reference is held to it as well
    admin2 = User(email="a2@example.com", name="관리자2", password_hash="x",
                  role=UserRole.admin, status=UserStatus.active)
    db.add(admin2)
    await db.commit()
    theirs = await connectors.install(shared.id, admin2, db,
                                      InstallRequest(env={"API_TOKEN": "t"}))
    assert await _refused(connectors.patch(
        theirs.id, ConnectorPatch(endpoint="sh -c id"), admin2, db)) == (
        422, "catalogue_endpoint")


async def test_only_an_administrator_sets_the_defaults_the_listing_and_the_gateway_key(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    mine = Connector(owner_id=member.id, name="위키", slug="wiki", transport=Transport.http,
                     endpoint=PUBLIC, env=settings_store.encrypt_env({"W": "${TOOLS_STT_URL}"}))
    db.add(mine)
    await db.commit()
    for body in ({"by_default": True}, {"listed": False}, {"tools_key_var": "W"},
                 {"tools_key_feature": "stt"}):
        assert await _refused(connectors.patch(mine.id, ConnectorPatch(**body), member, db)) == (
            403, "shared_admin_only"), body
    # sharing itself is open to a member (public address), and shows in the store at once
    shared = await connectors.patch(mine.id, ConnectorPatch(visibility="org"), member, db)
    assert shared.visibility == "org" and shared.listed is True and shared.by_default is False
    assert [e["slug"] for e in await connectors.get_store(admin, db)] == ["wiki"]

    # an administrator: the gateway key goes with one variable and one tool
    own = await _share(db, admin, slug="y", name="y", env={"WHISPER_URL": "${TOOLS_STT_URL}"})
    out = await connectors.patch(own.id, ConnectorPatch(
        tools_key_var="WHISPER_URL", tools_key_feature="stt", listed=False), admin, db)
    assert (out.tools_key_var, out.tools_key_feature, out.listed) == ("WHISPER_URL", "stt", False)
    for body in ({"tools_key_var": "NOPE", "tools_key_feature": "stt"},
                 {"tools_key_var": "WHISPER_URL", "tools_key_feature": "ssh"},
                 {"tools_key_var": "WHISPER_URL", "tools_key_feature": ""}):
        refused = await _refused(connectors.patch(own.id, ConnectorPatch(**body), admin, db))
        assert refused[0] == 422
    # a default is installed for everyone, so it may not ask for credentials
    asking = await _share(db, admin, slug="z", name="z",
                          required_env=[{"key": "API_TOKEN", "label": "토큰"}])
    assert await _refused(connectors.patch(
        asking.id, ConnectorPatch(by_default=True), admin, db)) == (422, "catalog_default_env")
    private = Connector(owner_id=admin.id, name="p", slug="p", endpoint="x")
    db.add(private)
    await db.commit()
    assert await _refused(connectors.patch(
        private.id, ConnectorPatch(by_default=True), admin, db)) == (422, "catalog_default_env")
    # withdrawn: no longer a default
    on = await _share(db, admin, slug="d", name="d", by_default=True)
    await connectors.patch(on.id, ConnectorPatch(visibility="private"), admin, db)
    await db.refresh(on)
    assert on.by_default is False and not catalog.provided(await catalog.source(on.id))


# ── removing a shared server ───────────────────────────────────────────


async def test_a_server_others_installed_is_withdrawn_before_it_is_removed(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    other = await _third(db)
    shared = await _share(db, admin)
    assert (await connectors.list_connectors(admin, db))[0].installs == 0
    a = await connectors.install(shared.id, member, db)
    await connectors.install(shared.id, other, db)
    mine = (await connectors.list_connectors(admin, db))[0]
    assert (mine.installs, mine.official, mine.visibility) == (2, True, "org")

    assert await _refused(connectors.uninstall(shared.id, admin, db)) == (
        409, "connector_in_use")
    assert await connectors.uninstall_many(BulkDelete(ids=[shared.id]), admin, db) == {
        "deleted": 0, "kept": 1}
    assert await db.get(Connector, shared.id) is not None
    # one person leaves: still used
    await connectors.uninstall(a.id, member, db)
    assert (await connectors.list_connectors(admin, db))[0].installs == 1
    assert await _refused(connectors.uninstall(shared.id, admin, db)) == (
        409, "connector_in_use")
    # withdrawn first, then removed; the remaining reference is left to say it is gone
    await connectors.patch(shared.id, ConnectorPatch(visibility="private"), admin, db)
    await connectors.uninstall(shared.id, admin, db)
    assert await db.get(Connector, shared.id) is None
    gone = (await connectors.list_connectors(other, db))[0]
    assert gone.origin_gone is True
    # a shared server nobody installed is removed straight away
    lonely = await _share(db, admin, slug="lonely", name="외톨이")
    await connectors.uninstall(lonely.id, admin, db)
    assert await db.get(Connector, lonely.id) is None


# ── the store ──────────────────────────────────────────────────────────


async def test_the_store_leaves_out_ones_own_and_hides_unlisted_from_new_people(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    other = await _third(db)
    visible = await _share(db, admin, slug="seen", name="보임",
                           env={"S": "secret-value"}, required_env=[
                               {"key": "API_TOKEN", "label": "토큰", "hint": "발급",
                                "secret": True}])
    hidden = await _share(db, admin, slug="hid", name="숨김", listed=False)
    await _share(db, other, slug="theirs", name="남의 것", transport=Transport.http,
                          endpoint=PUBLIC)
    await _share(db, member, slug="mine", name="내 것", transport=Transport.http, endpoint=PUBLIC)
    off = await _share(db, admin, slug="off", name="꺼짐", enabled=False)
    await db.refresh(visible)

    store = {e["slug"]: e for e in await connectors.get_store(member, db)}
    # own, unlisted and switched-off ones are not offered
    assert set(store) == {"seen", "theirs"}
    seen = store["seen"]
    assert (seen["official"], seen["installed"], seen["byDefault"], seen["ownerName"]) == (
        True, False, False, "관리자")
    assert store["theirs"]["official"] is False
    assert seen["requiredEnv"] == [
        {"key": "API_TOKEN", "label": "토큰", "hint": "발급", "secret": True}]
    # nothing of the server's own values or address leaves with the listing
    assert not {"env", "endpoint"} & set(seen) and "secret-value" not in str(store)

    # an administrator sees every one, the unlisted marked
    seen_by_admin = {e["slug"]: e for e in await connectors.get_store(other, db)}
    assert set(seen_by_admin) == {"seen", "mine"}
    everything = {e["slug"]: e["listed"] for e in await connectors.get_store(admin, db)}
    assert everything == {"theirs": True, "mine": True}
    admin2 = User(email="a2@example.com", name="관리자2", password_hash="x",
                  role=UserRole.admin, status=UserStatus.active)
    db.add(admin2)
    await db.commit()
    assert {e["slug"]: e["listed"] for e in await connectors.get_store(admin2, db)} == {
        "seen": True, "hid": False, "theirs": True, "mine": True}

    # installed by someone: it stays in their store, marked
    await connectors.install(hidden.id, member, db)
    marked = {e["slug"]: e["installed"] for e in await connectors.get_store(member, db)}
    assert marked == {"seen": False, "hid": True, "theirs": False}
    # a switched-off server cannot be installed; neither can one's own
    assert await _refused(connectors.install(off.id, member, db)) == (404, "unknown_connector")
    assert await _refused(connectors.install(
        (await _copies(db, "mine"))[0].id, member, db)) == (404, "unknown_connector")


# ── deleting a person ──────────────────────────────────────────────────


@pytest.fixture
async def full(monkeypatch):
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    names = ["users", "audit_events", "sessions", "messages", "jobs", "connectors",
             "connector_tools", "connector_credentials", "artifacts", "artifact_versions",
             "projects", "files", "skills", "memories", "agents", "credit_ledger",
             "refresh_tokens", "password_resets", "email_verifications", "push_subscriptions",
             "push_sent"]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            t for n, t in metadata.tables.items() if n in names])
    backends = settings_store.ToolBackends(research=f"{GATEWAY}/tools/research")

    async def tools_config():
        return backends

    async def no_revoke(*_a, **_k):
        return None

    monkeypatch.setattr(settings_store, "tools_config", tools_config)
    monkeypatch.setattr(admin_router.litellm_service, "revoke_key", no_revoke)
    monkeypatch.setattr(admin_router.file_service, "remove_user_files", lambda *_a: 0)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
    catalog.use([])


async def _delete(db, who: User, by: User) -> None:  # noqa: F811
    await admin_router.delete_user(who.id, _request(), by, db)


async def test_a_deleted_administrators_shared_server_passes_to_the_one_who_removes_them(
        full, monkeypatch):
    db = full  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    boss = User(email="b@example.com", name="총괄", password_hash="x", role=UserRole.admin,
                status=UserStatus.active)
    db.add(boss)
    await db.commit()
    used = await _share(db, boss, slug="used", name="쓰는 것")
    unused = await _share(db, boss, slug="unused", name="안 쓰는 것")
    private = Connector(owner_id=boss.id, name="개인", slug="private", endpoint="x")
    db.add(private)
    await db.commit()
    ref = await db.get(Connector, (await connectors.install(used.id, member, db)).id)

    await _delete(db, boss, admin)
    kept = await db.get(Connector, used.id)
    assert kept is not None and kept.owner_id == admin.id
    assert await db.get(Connector, unused.id) is None
    assert await db.get(Connector, private.id) is None
    # the reference runs on, now through the new owner
    await catalog.refresh(db)
    await catalog.check_runnable(ref, owner_is_admin=False)
    assert (await connectors.list_connectors(member, db))[0].origin_owner_name == "관리자"


async def test_a_deleted_members_shared_server_is_removed_and_references_say_it_is_gone(
        full, monkeypatch):
    db = full  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    other = await _third(db)
    shared = await _share(db, member, slug="wiki", name="위키", transport=Transport.http,
                          endpoint=PUBLIC)
    ref = await db.get(Connector, (await connectors.install(shared.id, other, db)).id)
    # the member's own references go with them
    admins_time = await _share(db, admin, slug="time", name="시간")
    await connectors.install(admins_time.id, member, db)

    await _delete(db, member, admin)
    assert await db.get(Connector, shared.id) is None
    assert [c.slug for c in (await db.exec(select(Connector).where(
        Connector.owner_id == member.id))).all()] == []
    await catalog.refresh(db)
    with pytest.raises(catalog.NotRunnable, match=catalog.GONE):
        await catalog.check_runnable(ref, owner_is_admin=False)
    assert (await connectors.list_connectors(other, db))[0].origin_gone is True
    assert (await db.get(Connector, admins_time.id)) is not None
    assert (await db.exec(select(ConnectorTool).where(
        ConnectorTool.connector_id == shared.id))).all() == []


async def test_the_address_check_for_a_member_does_not_need_dns_for_a_literal(monkeypatch):
    async def no_dns(host):
        raise AssertionError("looked up " + host)

    monkeypatch.setattr(netguard, "_system_resolver", no_dns)
    assert await catalog.endpoint_refusal(PUBLIC) is None
    assert await catalog.endpoint_refusal(INTERNAL) == catalog.INTERNAL_ENDPOINT


async def test_the_sharer_sees_the_credentials_its_server_asks_for(db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, _, _ = await _people(db)
    await _share(db, admin, slug="crm", name="CRM", transport=Transport.http, endpoint=PUBLIC,
                 required_env=[{"key": "API_TOKEN", "label": "토큰", "hint": "", "secret": True}])
    [row] = await connectors.list_connectors(admin, db)
    assert row.required_env == [{"key": "API_TOKEN", "label": "토큰", "hint": "", "secret": True}]
