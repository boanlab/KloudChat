"""Cross-process chat stop via Postgres NOTIFY (multiple API replicas)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable

import asyncpg
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal

log = logging.getLogger("kchat")

CHANNEL = "kchat_stop"

#: sender id; own NOTIFY echo ignored (signals already fired; would stop the next turn)
_ORIGIN = uuid.uuid4().hex

#: callback for stops broadcast by other processes
Listener = Callable[[str], None]


def payload_for(session_id: str) -> str:
    return f"{_ORIGIN}:{session_id}"


def session_from(payload: str) -> str | None:
    """Session named by a broadcast, or None if self-sent."""
    origin, sep, session_id = payload.partition(":")
    if not sep:
        # bare session id, no sender
        return origin or None
    if origin == _ORIGIN:
        return None
    return session_id or None


def needed() -> bool:
    """Whether multiple processes may hold this session's signal."""
    return settings.api_replicas > 1


async def broadcast(session_id: str) -> None:
    """Best-effort broadcast of a stop; no-op at a single replica."""
    if not needed():
        return
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT pg_notify(:channel, :payload)"), {
                "channel": CHANNEL,
                "payload": payload_for(session_id),
            })
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — this process's own signal still fired
        log.warning("stop broadcast failed: %s", type(exc).__name__)


async def listen(on_stop: Listener) -> None:
    """Listen for stop broadcasts until cancelled; reconnects, never raises."""
    # asyncpg DSN: strip SQLAlchemy's +asyncpg
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def _callback(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        session_id = session_from(payload)
        if session_id is not None:
            on_stop(session_id)

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.add_listener(CHANNEL, _callback)
            # listener runs on the connection's reader task; loop only keeps it alive
            while not conn.is_closed():
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect rather than crash the app
            log.warning("stop-signal listener dropped (%s); reconnecting", type(exc).__name__)
        finally:
            if conn is not None and not conn.is_closed():
                await conn.close()
        # retry on drop; only cancellation exits
        await asyncio.sleep(2)
