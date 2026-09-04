"""PDF export through the printer sidecar, which renders the page's own CSS.

Returns `None` when no printer is configured or it fails; the caller falls
back to the structural exporters.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Covers a whole document; the sidecar has its own shorter per-page limit.
_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


async def to_pdf(html: str) -> bytes | None:
    """A finished HTML document as a PDF, or `None` if the printer cannot."""
    base = settings.print_base_url.strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{base}/pdf", json={"html": html})
        response.raise_for_status()
    except Exception:
        # Logged with the traceback: the fallback hides the failure from the result.
        logger.exception("printer unavailable; falling back to the structural PDF")
        return None
    body = response.content
    return body or None
