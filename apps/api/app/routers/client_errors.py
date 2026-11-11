"""Errors the browser caught, written to the log; no table, no message content."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field, ValidationError

from app.core import logs
from app.core.deps import client_ip
from app.core.security import decode_access_token
from app.schemas.auth import Wire

log = logging.getLogger("kchat.client")

router = APIRouter(tags=["client-errors"])

#: A report is a message and a cut stack; anything longer is not one.
MAX_BODY = 8 * 1024
#: Reports per caller per window, counted in this process only: with N replicas a caller
#: gets up to N times as many, which is fine for a log line.
PER_WINDOW = 20
WINDOW_SEC = 60.0

_seen: dict[str, deque[float]] = {}


class ClientError(Wire):
    kind: Literal["error", "unhandledrejection", "boundary"] = "error"
    message: str = Field(default="", max_length=1000)
    stack: str = Field(default="", max_length=4000)
    route: str = Field(default="", max_length=300)
    version: str = Field(default="", max_length=64)
    request_id: str = Field(default="", max_length=64)


def _allowed(key: str, now: float) -> bool:
    times = _seen.setdefault(key, deque())
    while times and now - times[0] > WINDOW_SEC:
        times.popleft()
    if len(times) >= PER_WINDOW:
        return False
    times.append(now)
    # Idle callers forgotten, so the table stays as small as the traffic.
    if len(_seen) > 10_000:
        for stale in [k for k, v in _seen.items() if not v or now - v[-1] > WINDOW_SEC]:
            del _seen[stale]
    return True


async def _body(request: Request) -> bytes:
    """At most `MAX_BODY` bytes read, whatever Content-Length claims."""
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BODY:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "report_too_large")
    return data


@router.post("/client-errors", status_code=status.HTTP_204_NO_CONTENT)
async def report_client_error(request: Request) -> Response:
    """Signed in or not: a sign-in page can fail too. The token only names the caller."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    claims = decode_access_token(token) if scheme.lower() == "bearer" and token else None
    who = f"user:{claims.get('sub')}" if claims else f"ip:{client_ip(request)}"
    if not _allowed(who, time.monotonic()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "client_errors_rate_limited")
    try:
        report = ClientError.model_validate(json.loads(await _body(request) or b"{}"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_report") from exc
    stack = " | ".join(line.strip() for line in report.stack.splitlines() if line.strip())
    log.warning(
        "client %s on %s (version %s, %s, request %s): %s :: %s",
        report.kind,
        logs.safe(report.route, 300),
        logs.safe(report.version, 64),
        logs.safe(who, 80),
        logs.safe(report.request_id or "-", 64),
        logs.safe(report.message, 1000),
        logs.safe(stack, 4000),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
