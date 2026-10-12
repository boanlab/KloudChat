"""PDF export via the printer sidecar; None on failure (caller falls back)."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: whole document; sidecar has a shorter per-page limit
_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


async def to_pdf(html: str) -> bytes | None:
    """HTML document as PDF, or None."""
    base = settings.print_base_url.strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{base}/pdf", json={"html": html})
        response.raise_for_status()
    except Exception:
        # traceback logged: fallback hides the failure
        logger.exception("printer unavailable; falling back to the structural PDF")
        return None
    body = response.content
    return body or None
