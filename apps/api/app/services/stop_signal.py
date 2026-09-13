"""Cross-process delivery for the chat stop button.

`sessions._STOPPING` is one process's own `asyncio.Event`s, so the stop button
only reaches a turn streaming on the same process that holds it. With a single
API process that is the whole story; with more than one (`KCHAT_API_REPLICAS`
> 1), the request to stop can land on a different process than the one
running the turn. This broadcasts a Postgres `NOTIFY` so every process gets
the chance to set its own copy of the signal — Postgres is already a required
dependency, so this adds no new service.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import asyncpg
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal

log = logging.getLogger("kchat")

CHANNEL = "kchat_stop"

#: Called with a session id whenever any process (including this one)
#: broadcasts a stop for it.
Listener = Callable[[str], None]


def needed() -> bool:
    """Whether more than one process can be holding this session's signal."""
    return settings.api_replicas > 1


async def broadcast(session_id: str) -> None:
    """Tells every other process a turn on this session should stop.

    Best-effort: the caller sets its own process's signal directly first, so a
    failure here only costs the other processes' copies, not this one's. A no-op
    at the default single replica — no Postgres round trip on the turn's own path.
    """
    if not needed():
        return
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT pg_notify(:channel, :session_id)"), {
                "channel": CHANNEL,
                "session_id": session_id,
            })
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — this process's own signal still fired
        log.warning("stop broadcast failed: %s", type(exc).__name__)


async def listen(on_stop: Listener) -> None:
    """Runs until cancelled, calling `on_stop(session_id)` for every broadcast
    from any process. Reconnects on a dropped connection; never raises, so a
    database outage silences cross-process stop delivery rather than the API.
    """
    # asyncpg speaks the plain protocol; SQLAlchemy's `+asyncpg` suffix is its own marker.
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def _callback(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        on_stop(payload)

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)
            await conn.add_listener(CHANNEL, _callback)
            # `add_listener` delivers on this connection's own reader task; this
            # loop just keeps the connection open and notices if it drops.
            while not conn.is_closed():
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reconnect rather than crash the app
            log.warning("stop-signal listener dropped (%s); reconnecting", type(exc).__name__)
        finally:
            if conn is not None and not conn.is_closed():
                await conn.close()
        # A dropped or never-established connection retries; cancellation (app
        # shutdown) is the only way out.
        await asyncio.sleep(2)
