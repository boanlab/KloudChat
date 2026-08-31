"""The exported PDF, drawn by the same engine that drew the screen.

For most of this product's life a PDF was drawn by `report_export` and
`deck_export` — reportlab, placing text at coordinates. That renderer shares no
code with the page view, which draws the document by loading the 서식's own
stylesheet into a shadow root. Two renderers, one document: they agreed only
where somebody had made them agree, and the seed's `@page` rule and print
stylesheet — written, committed, and correct — had never once been used, because
nothing in the image could read CSS.

`page_export` said so plainly: *"the columns and the paper texture belong to the
seed, and the seed needs a browser."* This module is the browser. It hands the
finished HTML to the printer sidecar and gets back a file that looks like the
screen, which is the only definition of fidelity a reader ever applies.

**Absence is not failure.** A deployment that has not added the sidecar — an
upgrade in progress, a smaller install, an architecture the image is not built
for — gets `None` here and falls back to the structural exporters, which still
produce a real PDF. So this returns an optional rather than raising: a missing
printer must cost design, never the download.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Generous, because it covers a whole document: a hundred-page report with
#: embedded pictures is seconds of layout. The sidecar has its own, shorter
#: limit per page load; this one only stops a hung connection from holding a
#: request handler open.
_TIMEOUT = httpx.Timeout(120.0, connect=5.0)


def available() -> bool:
    """Whether a printer is configured at all.

    Callers use it to decide what to offer rather than to decide what to try —
    a configured printer can still be down, which `to_pdf` handles.
    """
    return bool(settings.print_base_url.strip())


async def to_pdf(html: str) -> bytes | None:
    """A finished HTML document as a PDF, or `None` if the printer cannot.

    `None` covers every reason equally — not configured, not running, not
    answering, answered with an error — because the caller's response to all of
    them is the same, and a caller that had to tell them apart would be
    deciding whether the user gets a file.
    """
    base = settings.print_base_url.strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(f"{base}/pdf", json={"html": html})
        response.raise_for_status()
    except Exception:
        # `exception` rather than `warning`: this is the difference between a
        # designed file and a plain one, and it is invisible in the result.
        # Somebody reading logs after "the PDF stopped looking right" needs the
        # reason, not a line saying it happened.
        logger.exception("printer unavailable; falling back to the structural PDF")
        return None
    body = response.content
    # A printer that answers 200 with nothing is a printer that is broken in a
    # way the fallback handles better than an empty download does.
    return body or None
