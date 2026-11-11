"""Granting and revoking admin: audited, never the caller's own, never the last admin."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.user import User, UserRole, UserStatus
from app.routers import admin
from app.schemas.admin import SetRoleRequest


class _Rows:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value


class _Db:
    def __init__(self, user: User, other_admins: int):
        self.user = user
        self.other_admins = other_admins
        self.added = []
        self.commits = 0

    async def get(self, model, item_id):
        return self.user if item_id == self.user.id else None

    async def exec(self, statement):
        return _Rows(self.other_admins)

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
    return User(
        id="adm", email="admin@example.com", name="관리자", password_hash="x", role=UserRole.admin
    )


def _person(role: UserRole) -> User:
    return User(
        id="u1",
        email="hong@example.com",
        name="홍길동",
        password_hash="x",
        role=role,
        status=UserStatus.active,
    )


@pytest.fixture(autouse=True)
def _no_ip(monkeypatch):
    monkeypatch.setattr(admin, "client_ip", lambda request: "127.0.0.1")


@pytest.mark.asyncio
async def test_granting_admin_is_saved_and_lands_in_the_audit_log():
    db = _Db(_person(UserRole.user), other_admins=1)
    out = await admin.set_role("u1", SetRoleRequest(role=UserRole.admin), _Request(), _admin(), db)
    assert out.role == UserRole.admin and db.commits == 1
    audit = [row for row in db.added if getattr(row, "action", "") == "user.role"][0]
    assert audit.target == "hong@example.com" and audit.detail == "role=admin"


@pytest.mark.asyncio
async def test_revoking_another_admin_works_while_one_admin_remains():
    db = _Db(_person(UserRole.admin), other_admins=1)
    out = await admin.set_role("u1", SetRoleRequest(role=UserRole.user), _Request(), _admin(), db)
    assert out.role == UserRole.user and db.commits == 1


@pytest.mark.asyncio
async def test_the_last_active_admin_cannot_be_revoked():
    person = _person(UserRole.admin)
    db = _Db(person, other_admins=0)
    with pytest.raises(HTTPException) as caught:
        await admin.set_role("u1", SetRoleRequest(role=UserRole.user), _Request(), _admin(), db)
    assert caught.value.detail == "last_admin"
    assert person.role == UserRole.admin and db.commits == 0


@pytest.mark.asyncio
async def test_an_admin_cannot_revoke_their_own_admin():
    me = _admin()
    db = _Db(me, other_admins=3)
    with pytest.raises(HTTPException) as caught:
        await admin.set_role("adm", SetRoleRequest(role=UserRole.user), _Request(), me, db)
    assert caught.value.detail == "cannot_demote_self" and db.commits == 0
