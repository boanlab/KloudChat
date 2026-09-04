"""Any administrator may delete a 공용 템플릿; private templates stay their owner's."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.user import User, UserRole, UserStatus
from app.models.workspace import Template
from app.routers import workspace as ws


def _user(user_id: str, role: UserRole = UserRole.user) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="hash",
        name=user_id,
        role=role,
        status=UserStatus.active,
    )


class _Db:
    def __init__(self, row: Template | None) -> None:
        self.row = row

    async def get(self, _model, item_id):
        return self.row if self.row is not None and self.row.id == item_id else None


def _template(owner: str, *, shared: bool) -> Template:
    return Template(
        id="t-1", owner_id=owner, kind="report", title="기관 공문", prompt="", shared=shared
    )


@pytest.mark.asyncio
async def test_an_administrator_can_take_down_a_shared_template_somebody_else_registered():
    db = _Db(_template("admin-1", shared=True))

    row = await ws._own_or_shared(db, _user("admin-2", UserRole.admin), "t-1")

    assert row.id == "t-1"


@pytest.mark.asyncio
async def test_an_ordinary_account_still_cannot_touch_a_shared_template():
    db = _Db(_template("admin-1", shared=True))

    with pytest.raises(HTTPException) as refused:
        await ws._own_or_shared(db, _user("someone"), "t-1")

    assert refused.value.status_code == 404


@pytest.mark.asyncio
async def test_a_private_template_is_nobodys_business_but_its_owners():
    """A private template cannot be deleted by an administrator."""
    db = _Db(_template("someone", shared=False))

    with pytest.raises(HTTPException):
        await ws._own_or_shared(db, _user("admin-2", UserRole.admin), "t-1")


@pytest.mark.asyncio
async def test_the_owner_still_reaches_their_own():
    db = _Db(_template("mine", shared=False))

    assert (await ws._own_or_shared(db, _user("mine"), "t-1")).id == "t-1"
