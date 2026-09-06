"""How long to wait after a 429 from the model gateway.

LiteLLM's token-per-minute limiter answers with the moment the window resets
(「Limit resets at: 2026-09-06 05:25:06 UTC」). A fixed backoff that ends before
that moment retries into the same wall; waiting for the reset succeeds on the next
call. A 429 that names no reset falls back to the given backoff step.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

#: Never wait longer than this for one retry, whatever the gateway says.
MAX_WAIT_SEC = 90.0

_RESET = re.compile(
    r"resets? at:?\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(?:UTC|Z)?"
)


def retry_delay(
    body: str, headers: dict | None, fallback: float, *, now: datetime | None = None
) -> float:
    """Seconds to sleep before retrying: until the named reset, else `Retry-After`, else
    `fallback`.
    """
    moment = now or datetime.now(UTC)
    match = _RESET.search(body or "")
    if match:
        try:
            reset = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            reset = None
        if reset is not None:
            wait = (reset - moment).total_seconds() + 1.0
            return max(fallback, min(MAX_WAIT_SEC, wait))
    retry_after = (headers or {}).get("retry-after") or (headers or {}).get("Retry-After")
    if retry_after:
        try:
            return max(fallback, min(MAX_WAIT_SEC, float(retry_after)))
        except ValueError:
            pass
    return fallback


__all__ = ["MAX_WAIT_SEC", "retry_delay"]
