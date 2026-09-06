"""Stored secrets are sealed with `SECRET_KEY`, not a key derived from the token signing
secret, and connector credentials are sealed like every other secret.

Before: one `JWT_SECRET` signed access tokens and, through a digest, encrypted every stored
secret, so rotating the signing key destroyed the master key and every user's model key.
Connector environments — the tokens an administrator enters to install an MCP server —
were written to the row in the clear.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.models.settings import SystemSetting
from app.models.user import ApiKey, User
from app.models.workspace import Connector
from app.schemas.workspace import ConnectorOut
from app.services import settings_store
from app.services.tools import catalog

_JWT = "signing-secret-for-tests"
_OWN = "sealing-key-for-tests"


def _derived(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings_store.env_settings, "jwt_secret", _JWT)
    monkeypatch.setattr(settings_store.env_settings, "secret_key", _OWN)


@pytest.fixture
def derived_only(monkeypatch):
    monkeypatch.setattr(settings_store.env_settings, "jwt_secret", _JWT)
    monkeypatch.setattr(settings_store.env_settings, "secret_key", "")


def test_new_secrets_are_sealed_with_the_own_key_and_old_ones_still_open(keys):
    sealed = settings_store.encrypt_secret("sk-live")
    assert _derived(_OWN).decrypt(sealed.encode()) == b"sk-live"
    with pytest.raises(InvalidToken):
        _derived(_JWT).decrypt(sealed.encode())
    # A row written before SECRET_KEY existed.
    legacy = _derived(_JWT).encrypt(b"sk-old").decode()
    assert settings_store.decrypt_secret(legacy) == "sk-old"
    assert settings_store.has_own_key()


def test_without_the_own_key_the_derived_one_is_used_as_before(derived_only):
    sealed = settings_store.encrypt_secret("sk-live")
    assert _derived(_JWT).decrypt(sealed.encode()) == b"sk-live"
    assert settings_store.decrypt_secret(sealed) == "sk-live"
    assert not settings_store.has_own_key()


def test_a_secret_under_a_lost_key_reads_as_unset(keys):
    assert settings_store.decrypt_secret(_derived("somebody-else").encrypt(b"x").decode()) == ""


def test_a_connector_environment_is_sealed_whole_and_opens_whole(keys):
    env = {"GITHUB_TOKEN": "ghp_secret", "USER": "{{USER_ID}}", "URL": "${TOOLS_SEARCH_URL}"}
    sealed = settings_store.encrypt_env(env)
    assert all(settings_store.is_sealed(v) for v in sealed.values())
    assert "ghp_secret" not in str(sealed)
    assert settings_store.decrypt_env(sealed) == env
    # A row written before sealing passes through, so nothing breaks on the way in.
    assert settings_store.decrypt_env({"GITHUB_TOKEN": "ghp_plain"}) == {
        "GITHUB_TOKEN": "ghp_plain"
    }
    assert settings_store.encrypt_env(None) is None
    assert settings_store.decrypt_env(None) == {}


def test_resealing_moves_only_what_is_not_yet_under_the_own_key(keys):
    own = _derived(_OWN)
    legacy = _derived(_JWT).encrypt(b"sk-old").decode()
    moved = settings_store.resealed(legacy)
    assert moved is not None and own.decrypt(moved.encode()) == b"sk-old"
    assert settings_store.resealed(own.encrypt(b"sk-new").decode()) is None
    assert settings_store.resealed("") is None
    # Clear text is sealed only where clear text was ever legitimate (connector rows).
    assert settings_store.resealed("ghp_plain") is None
    plain = settings_store.resealed("ghp_plain", plain=True)
    assert plain is not None and own.decrypt(plain.encode()) == b"ghp_plain"
    # Unreadable rows are left for `decrypt_secret` to report.
    assert settings_store.resealed(_derived("lost").encrypt(b"x").decode()) is None


def test_resealing_does_nothing_without_the_own_key(derived_only):
    assert settings_store.resealed(_derived(_JWT).encrypt(b"x").decode()) is None


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Db:
    def __init__(self, **by_model):
        self.by_model = by_model
        self.added = []
        self.commits = 0

    async def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return _Rows(self.by_model.get(entity.__name__, []))

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_startup_reseals_every_stored_secret_under_the_own_key(keys):
    legacy = _derived(_JWT)
    own = _derived(_OWN)
    setting = SystemSetting(
        key="litellm.master_key", value=legacy.encrypt(b"mk").decode(), secret=True
    )
    plain_setting = SystemSetting(key="tools.search_url", value="http://searx", secret=False)
    user = User(
        email="a@example.com", password_hash="x", litellm_key=legacy.encrypt(b"uk").decode()
    )
    fresh = ApiKey(user_id="u", name="ci", secret=own.encrypt(b"ak").decode())
    connector = Connector(
        owner_id="u", name="GitHub", env={"GITHUB_TOKEN": "ghp_plain", "U": "{{USER_ID}}"}
    )
    db = _Db(
        SystemSetting=[setting, plain_setting], User=[user], ApiKey=[fresh], Connector=[connector]
    )

    assert await settings_store.rotate_secrets(db) == 3

    assert own.decrypt(setting.value.encode()) == b"mk"
    assert plain_setting.value == "http://searx"
    assert own.decrypt(user.litellm_key.encode()) == b"uk"
    assert fresh not in db.added
    assert all(settings_store.is_sealed(v) for v in connector.env.values())
    assert settings_store.decrypt_env(connector.env) == {
        "GITHUB_TOKEN": "ghp_plain",
        "U": "{{USER_ID}}",
    }
    assert db.commits == 1
    # A second start finds nothing to do.
    assert await settings_store.rotate_secrets(db) == 0


@pytest.mark.asyncio
async def test_a_connector_row_never_shows_its_values_and_its_process_sees_them(keys, monkeypatch):
    env = settings_store.encrypt_env({"GITHUB_TOKEN": "ghp_secret", "U": "{{USER_ID}}"})
    connector = Connector(owner_id="u", name="GitHub", env=env)

    out = ConnectorOut.of(connector)
    assert out.env_keys == ["GITHUB_TOKEN"]
    assert "ghp_secret" not in out.model_dump_json()

    async def untouched(text):
        return text

    monkeypatch.setattr(catalog, "resolve_urls", untouched)
    assert await catalog.resolve_env(connector.env) == {
        "GITHUB_TOKEN": "ghp_secret",
        "U": "{{USER_ID}}",
    }
