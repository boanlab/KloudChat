"""Opaque keyset cursors for `…/page` lists: `{items, total, next}`, `next` null on the last."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

from fastapi import HTTPException, Query

#: default and ceiling for `limit`
PAGE = 50
PAGE_MAX = 200

#: shared query params
Limit = Query(PAGE, ge=1, le=PAGE_MAX)
Cursor = Query(None, max_length=300)


def _bad() -> HTTPException:
    return HTTPException(status_code=422, detail="bad_cursor")


def stamp(value: datetime) -> str:
    """UTC ISO; a naive value (SQLite) is already UTC."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def encode(*parts: str) -> str:
    return base64.urlsafe_b64encode("|".join(parts).encode()).decode().rstrip("=")


def decode(cursor: str, fields: int) -> list[str]:
    """`fields` parts back from `encode`; 422 `bad_cursor` for anything else."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, ValueError):
        raise _bad() from None
    parts = raw.split("|")
    if len(parts) != fields or not all(parts):
        raise _bad()
    return parts


def when(text: str) -> datetime:
    """A cursor's timestamp; 422 when it is not one."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise _bad() from None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def after(rows: list, limit: int, key) -> tuple[list, str | None]:
    """Rows fetched with `limit + 1`: the page, and the next cursor from its last row."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    return page, encode(*key(page[-1]))


__all__ = ["PAGE", "PAGE_MAX", "Cursor", "Limit", "after", "decode", "encode", "stamp", "when"]
