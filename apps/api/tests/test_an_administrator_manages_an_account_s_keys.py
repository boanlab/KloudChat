"""An administrator sees every key an account holds, revokes the ones the person issued,
and can bind a key they already hold as the account's KloudChat key."""

from __future__ import annotations

import pytest
from sqlalchemy import Select

from app.models.user import ApiKey, User, UserStatus
from app.routers import admin
from app.schemas.admin import ReplaceKeyRequest
from app.services import settings_store


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _Db:
    def __init__(self, user: User, keys: list[ApiKey] | None = None):
        self.user = user
        self.keys = keys or []
        self.added = []
        self.commits = 0

    async def get(self, model, item_id):
        if model is User:
            return self.user if item_id == self.user.id else None
        return next((k for k in self.keys if k.id == item_id), None)

    async def exec(self, statement):
        assert isinstance(statement, Select)
        return _Rows([k for k in self.keys if k.revoked_at is None])

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        pass


class _Request:
    headers = {"User-Agent": "tests"}
    client = None


def _admin() -> User:
    return User(id="adm", email="admin@example.com", name="관리자", password_hash="x")


def _person(**extra) -> User:
    return User(id="u1", email="hong@example.com", name="홍길동", password_hash="x", **extra)


@pytest.mark.asyncio
async def test_the_list_shows_kloudchat_s_key_as_a_preview_and_the_live_named_keys(monkeypatch):
    monkeypatch.setattr(settings_store.env_settings, "secret_key", "k" * 16)
    person = _person(
        litellm_key=settings_store.encrypt_secret("sk-kloud"), litellm_key_preview="…loud"
    )
    live = ApiKey(id="k1", user_id="u1", name="CI", secret="enc", preview="…1234")
    gone = ApiKey(id="k2", user_id="u1", name="old", secret="enc", preview="…9999")
    gone.revoked_at = gone.created_at
    out = await admin.list_user_keys("u1", _admin(), _Db(person, [live, gone]))
    assert out.kloudchat is not None and out.kloudchat.preview == "…loud"
    assert [k.name for k in out.named] == ["CI"]
    assert all(k.secret is None for k in out.named)

    bare = await admin.list_user_keys("u1", _admin(), _Db(_person(), []))
    assert bare.kloudchat is None and bare.named == []


@pytest.mark.asyncio
async def test_a_named_key_is_revoked_on_the_proxy_and_kept_as_a_revoked_row(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    monkeypatch.setattr(settings_store.env_settings, "secret_key", "k" * 16)
    deleted: list[str] = []

    async def delete_key(secret):
        deleted.append(secret)
        return True

    monkeypatch.setattr(admin.litellm_service, "delete_key", delete_key)
    key = ApiKey(
        id="k1",
        user_id="u1",
        name="CI",
        secret=settings_store.encrypt_secret("sk-ci"),
        preview="…k-ci",
    )
    db = _Db(_person(), [key])
    await admin.revoke_user_key("u1", "k1", _Request(), _admin(), db)
    assert deleted == ["sk-ci"] and key.revoked_at is not None and db.commits == 1
    assert any(getattr(row, "action", "") == "key.revoke" for row in db.added)

    with pytest.raises(admin.HTTPException) as caught:
        await admin.revoke_user_key("u1", "k1", _Request(), _admin(), db)
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_a_key_the_proxy_does_not_know_is_refused_and_a_known_one_is_bound(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    monkeypatch.setattr(settings_store.env_settings, "secret_key", "k" * 16)
    calls: list[str] = []

    async def health(quick=False):
        return True

    async def key_spend(secret):
        return {"spend": 0.0, "maxBudget": 1.0} if secret == "sk-known-key" else None

    async def revoke_key(user):
        calls.append("revoke")
        user.litellm_key = None
        return True

    async def sync_budget(user):
        calls.append("budget")
        return True

    async def set_key_blocked(user, blocked):
        calls.append(f"blocked={blocked}")
        return True

    for name, fn in (
        ("health", health),
        ("key_spend", key_spend),
        ("revoke_key", revoke_key),
        ("sync_budget", sync_budget),
        ("set_key_blocked", set_key_blocked),
    ):
        monkeypatch.setattr(admin.litellm_service, name, fn)

    person = _person(litellm_key=settings_store.encrypt_secret("sk-old"))
    db = _Db(person)
    with pytest.raises(admin.HTTPException) as caught:
        await admin.replace_litellm_key(
            "u1", ReplaceKeyRequest(key="sk-unknown-1"), _Request(), _admin(), db
        )
    assert caught.value.status_code == 400 and calls == []

    out = await admin.replace_litellm_key(
        "u1", ReplaceKeyRequest(key="sk-known-key"), _Request(), _admin(), db
    )
    assert settings_store.decrypt_secret(person.litellm_key) == "sk-known-key"
    assert out.litellm_key_preview == "…-key"
    assert calls == ["revoke", "budget"] and db.commits == 1

    suspended = _person(status=UserStatus.suspended)
    calls.clear()
    await admin.replace_litellm_key(
        "u1", ReplaceKeyRequest(key="sk-known-key"), _Request(), _admin(), _Db(suspended)
    )
    assert calls == ["revoke", "budget", "blocked=True"]
