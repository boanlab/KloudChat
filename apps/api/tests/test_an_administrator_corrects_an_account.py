"""An administrator can correct a name or address, reset a password, and see the whole
catalogue when restricting an account.

Before, the users screen could suspend, delete, re-key and restrict an account but not
fix a typo in its name or get a locked-out person back in; and the restriction picker
listed the administrator's own models rather than the catalogue.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Delete

from app.core.security import verify_password
from app.models.user import RefreshToken, User
from app.routers import admin
from app.schemas.admin import ResetPasswordRequest, UpdateUserRequest


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _Db:
    def __init__(self, user: User, taken: User | None = None):
        self.user = user
        self.taken = taken
        self.statements = []
        self.added = []
        self.commits = 0

    async def get(self, model, item_id):
        return self.user if item_id == self.user.id else None

    async def exec(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Delete):
            return _Rows([])
        return _Rows([self.taken] if self.taken else [])

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


def _person() -> User:
    return User(id="u1", email="hong@example.com", name="홍길동", password_hash="old")


@pytest.mark.asyncio
async def test_a_name_and_an_address_are_corrected_and_audited(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    person = _person()
    db = _Db(person)
    out = await admin.update_user(
        "u1", UpdateUserRequest(name="홍길순", email="Gilsun@Example.com"), _Request(), _admin(), db
    )
    assert out.name == "홍길순" and out.email == "gilsun@example.com"
    assert db.commits == 1
    audit = [row for row in db.added if getattr(row, "action", "") == "user.update"][0]
    assert audit.detail == "name,email" and audit.target == "gilsun@example.com"


@pytest.mark.asyncio
async def test_an_address_already_in_use_is_refused_and_nothing_changes(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    person = _person()
    other = User(id="u2", email="taken@example.com", name="다른", password_hash="x")
    db = _Db(person, taken=other)
    with pytest.raises(admin.HTTPException) as caught:
        await admin.update_user(
            "u1", UpdateUserRequest(email="taken@example.com"), _Request(), _admin(), db
        )
    assert caught.value.status_code == 409 and caught.value.detail == "email_taken"
    assert person.email == "hong@example.com" and db.commits == 0


@pytest.mark.asyncio
async def test_a_field_left_out_is_left_alone():
    person = _person()
    db = _Db(person)
    out = await admin.update_user("u1", UpdateUserRequest(), _Request(), _admin(), db)
    assert out.name == "홍길동" and out.email == "hong@example.com" and db.commits == 0


@pytest.mark.asyncio
async def test_a_password_reset_hashes_the_new_one_and_ends_every_sign_in(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")
    person = _person()
    db = _Db(person)
    await admin.reset_password(
        "u1", ResetPasswordRequest(password="new-secret-12"), _Request(), _admin(), db
    )
    assert person.password_hash != "old" and verify_password("new-secret-12", person.password_hash)
    deletes = [s for s in db.statements if isinstance(s, Delete)]
    assert len(deletes) == 1 and deletes[0].table.name == RefreshToken.__tablename__
    assert "user_id" in str(deletes[0].whereclause)
    assert any(getattr(row, "action", "") == "user.password_reset" for row in db.added)
    assert db.commits == 1


def test_a_short_password_is_refused_before_it_reaches_the_route():
    with pytest.raises(ValueError):
        ResetPasswordRequest(password="short")


@pytest.mark.asyncio
async def test_the_restriction_picker_sees_the_whole_catalogue(monkeypatch):
    async def list_models(force=False):
        return {
            "models": [
                {"id": "local/qwen", "label": "Qwen", "kinds": ["chat"]},
                {"id": "openai/gpt", "kinds": ["chat", "image"]},
            ],
            "litellmAvailable": True,
        }

    monkeypatch.setattr(admin.model_service, "list_models", list_models)
    rows = await admin.catalogue(_admin())
    assert rows == [
        {"id": "local/qwen", "label": "Qwen", "kinds": ["chat"]},
        {"id": "openai/gpt", "label": "openai/gpt", "kinds": ["chat", "image"]},
    ]
