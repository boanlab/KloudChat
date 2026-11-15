"""One HTTP path for Google and Slack calls: backoff on rate limits and server errors,
`Retry-After` honoured, and a transport tests replace (`use_transport`), so CI never reaches
a provider."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: replaced by tests with an `httpx.MockTransport`
_transport: httpx.AsyncBaseTransport | None = None
#: replaced by tests so a backoff does not wait
_sleep = asyncio.sleep

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRIES = 3
#: longest wait between tries, seconds
_WAIT_CAP = 20.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: Google says a 403 is a rate limit by its reason
_GOOGLE_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


def use_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


class ProviderError(Exception):
    """A provider refused or failed; `code` is a wire-safe reason, never a token."""

    def __init__(self, code: str, status: int = 502, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail[:300]


class AuthExpired(ProviderError):
    """The stored grant no longer works; the person connects again."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("reconnect_required", 401, detail)


def _google_rate_limited(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        errors = (response.json().get("error") or {}).get("errors") or []
    except (ValueError, AttributeError):
        return False
    return any(isinstance(e, dict) and e.get("reason") in _GOOGLE_RATE_REASONS for e in errors)


def _wait(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "")
    if raw.isdigit():
        return min(float(raw), _WAIT_CAP)
    return min(_WAIT_CAP, 0.5 * (2**attempt)) + random.uniform(0, 0.25)


async def request(method: str, url: str, *, token: str | None = None,
                  retries: int = RETRIES, **kwargs: Any) -> httpx.Response:
    """The response after retrying rate limits and server errors; network failures raise
    `ProviderError("unreachable")`."""
    headers = dict(kwargs.pop("headers", None) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(transport=_transport, timeout=TIMEOUT) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            if attempt < retries:
                await _sleep(min(_WAIT_CAP, 0.5 * (2**attempt)))
                continue
            log.info("integration call failed: %s %s: %s", method, url.split("?")[0],
                     type(exc).__name__)
            raise ProviderError("unreachable", 502) from None
        if attempt < retries and (response.status_code in _RETRY_STATUS
                                  or _google_rate_limited(response)):
            await _sleep(_wait(response, attempt))
            continue
        return response
    raise ProviderError("unreachable", 502)  # pragma: no cover - loop always returns


def rate_limited(response: httpx.Response) -> bool:
    return response.status_code == 429 or _google_rate_limited(response)
