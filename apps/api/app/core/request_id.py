"""`X-Request-ID` on every response, and on the log lines of a failed request."""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

log = logging.getLogger("kchat.request")

HEADER = b"x-request-id"
# A caller's id kept only when it is short and plain; anything else is replaced, not echoed.
_SAFE = re.compile(r"[A-Za-z0-9._-]{1,64}")

current: ContextVar[str] = ContextVar("request_id", default="")


def _incoming(scope) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == HEADER:
            text = value.decode("latin-1")
            return text if _SAFE.fullmatch(text) else ""
    return ""


class RequestIdMiddleware:
    """Pure ASGI, so streamed answers pass through untouched."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request_id = _incoming(scope) or uuid.uuid4().hex
        token = current.set(request_id)
        state = {"started": False}

        async def stamp(message):
            if message.get("type") == "http.response.start":
                state["started"] = True
                headers = [
                    (k, v) for k, v in message.get("headers") or [] if k.lower() != HEADER
                ]
                headers.append((HEADER, request_id.encode()))
                message = {**message, "headers": headers}
                if int(message.get("status") or 0) >= 500:
                    log.warning("%s %s answered %s", scope.get("method"), scope.get("path"),
                                message.get("status"))
            await send(message)

        try:
            await self.app(scope, receive, stamp)
        except Exception:
            log.error("%s %s failed", scope.get("method"), scope.get("path"))
            if not state["started"]:
                # The 500 the outer error handler would send, with the id the page can quote.
                await stamp({"type": "http.response.start", "status": 500,
                             "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": b"Internal Server Error"})
            raise
        finally:
            current.reset(token)


class _Tagged(logging.Filter):
    """Warnings and errors logged while serving a request name that request."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = current.get()
        if request_id and record.levelno >= logging.WARNING and not hasattr(record, "request_id"):
            record.request_id = request_id
            record.msg = f"{record.getMessage()} [request_id={request_id}]"
            record.args = ()
        return True


def tag_log_lines() -> None:
    """The filter on every root handler; records from any logger that propagates pass them."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _Tagged) for f in handler.filters):
            handler.addFilter(_Tagged())
