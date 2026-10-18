"""One more try for a whole-answer model call whose connection dropped.

A slide or report section is one non-streaming call; when the model server drops it (「peer
closed connection without sending complete message body」), nothing of it
reached the person, so asking once more costs only the wait. The chat path does the same for a
streamed hop and takes the half answer back (`agent.run_turn`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")

#: gateway answers that mean the model server was not there, not that the call was wrong
_GATEWAY = frozenset({502, 503, 504})
#: pause before asking again
PAUSE = 1.0


def dropped(exc: BaseException) -> bool:
    """A connection the model server closed or refused, or a gateway saying it is down; a
    timeout is not one (it already took the whole budget)."""
    if isinstance(exc, httpx.TimeoutException):
        return False
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _GATEWAY


async def once_more(call: Callable[[], Awaitable[T]], what: str = "model call") -> T:
    """`call()`, called again once when its connection dropped."""
    try:
        return await call()
    except httpx.HTTPError as exc:
        if not dropped(exc):
            raise
        log.warning("%s dropped (%s: %s), asking once more", what, type(exc).__name__, exc)
    await asyncio.sleep(PAUSE)
    return await call()
