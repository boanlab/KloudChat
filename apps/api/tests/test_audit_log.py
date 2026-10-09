"""The audit log says what changed, pages through the trail, and labels every action."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import AuditEvent, User, UserRole, UserStatus
from app.routers import admin
from app.routers import usage as usage_router
from app.schemas.admin import SetCreditsRequest, SetRoleRequest, SystemSettingsIn
from app.services import audit as audit_service
from app.services import settings_store

APP = Path(__file__).resolve().parents[1] / "app"
WEB_LABELS = Path(__file__).resolve().parents[3] / "apps/web/src/lib/auditLabels.ts"


# ── diff ───────────────────────────────────────────────────────────────


def test_a_diff_keeps_only_what_moved_and_masks_secrets():
    changes = audit_service.diff(
        {"signup_mode": "approval", "masterKey": "sk-old-1234", "smtp_password": "", "a": ""},
        {"signup_mode": "open", "masterKey": "sk-new-5678", "smtp_password": "hunter2", "a": None},
    )
    assert changes["signup_mode"] == ["approval", "open"]
    assert changes["masterKey"] == [audit_service.MASK, audit_service.MASK]
    assert changes["smtp_password"] == ["", audit_service.MASK]
    # Cleared and unset are the same thing.
    assert "a" not in changes
    assert "sk-" not in repr(changes) and "hunter2" not in repr(changes)


def test_long_values_are_clipped():
    changes = audit_service.diff({"x": "", "ids": []}, {"x": "가" * 500, "ids": list(range(50))})
    assert len(changes["x"][1]) <= 161 and changes["x"][1].endswith("…")
    assert len(changes["ids"][1]) == 21 and changes["ids"][1][-1] == "…(+30)"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Db:
    def __init__(self, user: User | None = None):
        self.user = user
        self.added = []

    async def get(self, model, item_id):
        return self.user

    async def exec(self, statement):
        return _Rows([])

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass

    async def refresh(self, row):
        pass


class _Request:
    headers = {"User-Agent": "tests"}
    client = None


def _admin() -> User:
    return User(id="adm", email="admin@example.com", name="관리자", password_hash="x")


def _audits(db: _Db) -> list[AuditEvent]:
    return [row for row in db.added if isinstance(row, AuditEvent)]


@pytest.mark.asyncio
async def test_a_settings_save_records_its_section_and_masked_values(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    stored = {
        settings_store.SIGNUP_MODE: "approval",
        settings_store.SIGNUP_DOMAINS: "dankook.ac.kr",
        settings_store.SMTP_PASSWORD: "old-secret",
    }

    async def fake_stored(db):
        return dict(stored)

    async def fake_put(db, key, value, actor):
        pass

    async def fake_get_settings(a, db):
        return {}

    monkeypatch.setattr(settings_store, "stored", fake_stored)
    monkeypatch.setattr(settings_store, "put", fake_put)
    monkeypatch.setattr(admin, "get_settings", fake_get_settings)
    db = _Db()
    payload = SystemSettingsIn(
        signup_mode="open",
        signup_domains="dankook.ac.kr",  # unchanged: not recorded
        signup_verify_email="true",
        smtp_password="new-secret",
    )
    await admin.put_settings(payload, _Request(), _admin(), db)
    (event,) = _audits(db)
    assert event.action == "settings.update"
    assert event.target == "시스템 설정"
    changes = event.event_metadata["changes"]
    assert changes["signup_mode"] == ["approval", "open"]
    assert changes["signup_verify_email"] == ["", "true"]
    assert "signup_domains" not in changes
    assert changes["smtp_password"] == [audit_service.MASK, audit_service.MASK]
    assert event.event_metadata["sections"] == ["mail", "signup"]
    assert "secret" not in repr(event.event_metadata) and "secret" not in event.detail


@pytest.mark.asyncio
async def test_a_save_that_moves_nothing_writes_no_event(monkeypatch):
    async def fake_stored(db):
        return {settings_store.SIGNUP_MODE: "open"}

    async def fake_put(db, key, value, actor):
        pass

    async def fake_get_settings(a, db):
        return {}

    monkeypatch.setattr(settings_store, "stored", fake_stored)
    monkeypatch.setattr(settings_store, "put", fake_put)
    monkeypatch.setattr(admin, "get_settings", fake_get_settings)
    db = _Db()
    await admin.put_settings(SystemSettingsIn(signup_mode="open"), _Request(), _admin(), db)
    assert _audits(db) == []


@pytest.mark.asyncio
async def test_role_and_credit_changes_record_old_and_new(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(admin.litellm_service, "sync_budget", nothing)
    person = User(
        id="u1",
        email="hong@example.com",
        name="홍길동",
        password_hash="x",
        role=UserRole.user,
        status=UserStatus.active,
        monthly_credits=1000,
    )
    db = _Db(person)
    await admin.set_role("u1", SetRoleRequest(role=UserRole.admin), _Request(), _admin(), db)
    await admin.set_credits("u1", SetCreditsRequest(monthly_credits=5000), _Request(), _admin(), db)
    role, credits = _audits(db)
    assert role.event_metadata["changes"] == {"role": ["user", "admin"]}
    assert credits.event_metadata["changes"] == {"monthly_credits": [1000, 5000]}


# ── listing ────────────────────────────────────────────────────────────

_DDL = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
        monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
        litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT, allowed_models TEXT, preferences TEXT, created_at DATETIME,
        last_active_at DATETIME, email_verified_at DATETIME
    )
    """,
    """
    CREATE TABLE audit_events (
        id TEXT PRIMARY KEY, at DATETIME, actor_id TEXT, action TEXT, target TEXT, detail TEXT,
        metadata TEXT, ip TEXT, user_agent TEXT, severity TEXT
    )
    """,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
async def trail():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    db = AsyncSession(engine, expire_on_commit=False)
    db.add(User(id="adm", email="admin@example.com", name="관리자", password_hash="x"))
    db.add(User(id="u1", email="hong@example.com", name="홍길동", password_hash="x"))
    # 120 sign-ins by hong an hour apart before T0; 5 policy changes ten days back.
    for i in range(120):
        db.add(
            AuditEvent(
                actor_id="u1",
                action="login",
                target="hong@example.com",
                at=T0 - timedelta(hours=i),
                ip="10.0.0.1",
            )
        )
    for i in range(5):
        db.add(
            AuditEvent(
                actor_id="adm",
                action="governance.update",
                target="정책",
                detail="pii_masking",
                at=T0 - timedelta(days=10, hours=i),
            )
        )
    db.add(
        AuditEvent(
            actor_id=None,
            action="login",
            target="evil@example.com",
            detail="failed",
            severity="warn",
            at=T0 - timedelta(days=20),
        )
    )
    await db.commit()
    yield db
    await db.close()
    await engine.dispose()


async def _list(db, **kw):
    params = {
        "page": 1,
        "size": 50,
        "severity": None,
        "action": None,
        "account": None,
        "q": None,
        "q_actions": None,
        "since": None,
        "until": None,
    }
    params.update(kw)
    return await usage_router.audit_log(_admin(), db, **params)


@pytest.mark.asyncio
async def test_the_trail_comes_a_page_at_a_time_with_a_total(trail):
    first = await _list(trail)
    assert first["total"] == 126 and len(first["items"]) == 50
    assert first["items"][0]["at"] >= first["items"][-1]["at"]
    last = await _list(trail, page=3)
    assert len(last["items"]) == 26
    ids = {r["id"] for r in first["items"]} & {r["id"] for r in last["items"]}
    assert not ids


@pytest.mark.asyncio
async def test_filters_narrow_on_the_server(trail):
    policy = await _list(trail, action="governance.*")
    assert policy["total"] == 5 and policy["items"][0]["actor"] == "admin@example.com"
    by_account = await _list(trail, account="hong")
    assert by_account["total"] == 120
    by_target = await _list(trail, account="evil@")
    assert by_target["total"] == 1 and by_target["items"][0]["actor"] == "시스템"
    window = await _list(trail, since=T0 - timedelta(hours=9, minutes=30), until=T0)
    assert window["total"] == 9
    text = await _list(trail, q="pii_masking")
    assert text["total"] == 5
    # The screen sends the codes whose Korean label matched the search.
    labelled = await _list(trail, q="정책 변경", q_actions="governance.update")
    assert labelled["total"] == 5
    wildcard = await _list(trail, q="%")
    assert wildcard["total"] == 0


# ── labels ─────────────────────────────────────────────────────────────

_WRITES = (
    re.compile(r'action="([a-z_.]+)"'),
    re.compile(r'_audit\(\s*db,\s*request,\s*(?:admin,\s*)?"([a-z_.]+)"'),
    re.compile(r'_audit_policy\(\s*\w+,\s*\w+,\s*"([a-z_.]+)"'),
    re.compile(r'"(pipeline\.(?:approve|reject))"'),
    re.compile(r'"(privacy\.(?:mask|strict)_tool_output)"'),
)


def test_every_written_action_is_known():
    written: set[str] = set()
    for path in (APP / "routers").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in _WRITES:
            written |= set(pattern.findall(text))
    written |= set(_WRITES[0].findall((APP / "services/storage.py").read_text(encoding="utf-8")))
    assert written, "the scan found nothing"
    assert written <= audit_service.ACTIONS, written - audit_service.ACTIONS


def test_every_action_has_a_screen_label():
    if not WEB_LABELS.exists():
        pytest.skip("web sources are not in this checkout")
    text = WEB_LABELS.read_text(encoding="utf-8")
    block = text[text.index("ACTION_LABEL") : text.index("}", text.index("ACTION_LABEL"))]
    labelled = set(re.findall(r"""^\s*'?([a-z_.]+)'?\s*:""", block, re.MULTILINE))
    missing = audit_service.ACTIONS - labelled
    assert not missing, missing
