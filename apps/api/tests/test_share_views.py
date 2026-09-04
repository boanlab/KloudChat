"""Share view records name the reader by account or by address."""

from __future__ import annotations

import pytest

from app.models.workspace import Share, ShareScope, ShareView
from app.routers import shares as shares_router


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """A session with no visits in it; repeat-open folding is checked against a real database."""

    def __init__(self, existing: list[ShareView] | None = None):
        self.rows = list(existing or [])
        self.added: list[object] = []

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        assert table == "share_views", table
        return _Result(sorted(self.rows, key=lambda v: v.last_at, reverse=True))

    def add(self, row):
        self.added.append(row)


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    def __init__(self, ip="203.0.113.9"):
        self.headers = {"X-Forwarded-For": ip} if ip else {}
        self.client = _Client(ip or None)


class _User:
    def __init__(self, uid="user-7", name="남재현", email="namjh@dankook.ac.kr"):
        self.id = uid
        self.name = name
        self.email = email


def _share() -> Share:
    return Share(id="share-1", token="t", owner_id="owner-1", scope=ShareScope.link)


@pytest.mark.anyio
async def test_a_signed_in_reader_is_named():
    db, share = _Db(), _share()

    await shares_router._record_view(db, share, _Request(), _User())

    (row,) = db.added
    assert row.viewer_id == "user-7"
    assert row.viewer_name == "남재현"
    assert row.viewer_email == "namjh@dankook.ac.kr"
    assert row.ip == "203.0.113.9"


@pytest.mark.anyio
async def test_an_anonymous_reader_is_recorded_by_address():
    """An anonymous reader is recorded by address alone."""
    db, share = _Db(), _share()

    await shares_router._record_view(db, share, _Request("198.51.100.4"), None)

    (row,) = db.added
    assert row.viewer_id is None
    assert (row.viewer_name, row.viewer_email) == ("", "")
    assert row.ip == "198.51.100.4"
