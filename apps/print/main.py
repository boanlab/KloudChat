"""HTML in, PDF out.

The documents this prints are already finished files: `design_templates.render`
puts the model's blocks inside the 서식's seed, and what comes out is one
self-contained page with its own `@page` rule and its own print stylesheet.
Those rules had been written and never used — every exported PDF was drawn
instead by reportlab, a second renderer that shared no code with the screen and
so agreed with it only by coincidence. This service is what makes the file and
the screen the same document.

**Nothing here reaches the network.** Every request is aborted except the
`about:blank` the page starts on. A seed embeds its pictures as `data:` URIs,
so nothing legitimate needs fetching, and the guarantee is worth more than the
flexibility: the markup is model-authored, and an `<img src="http://…">` in it
would otherwise be a request made from inside the deployment's network by a
process the model chose the address for. Route interception turns that from a
thing to remember into a thing that cannot happen.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel, Field

#: Beyond this a document is not a document. The largest seed is ~22 KB and a
#: report with embedded pictures runs to a few megabytes; 32 MB is far past
#: anything real and still small enough that a runaway artifact cannot exhaust
#: this container's memory before the limit rejects it.
MAX_HTML = 32 * 1024 * 1024

#: A self-contained page with no network has nothing to wait for. This exists
#: only so a pathological document — a CSS animation, a million-row table —
#: fails as a 504 rather than holding the worker forever.
TIMEOUT_MS = 30_000


class Job(BaseModel):
    html: str = Field(max_length=MAX_HTML)


class _Chromium:
    """One browser, started on first use and restarted if it dies.

    Launching costs ~300 ms, which is most of a small document's print time, so
    it is held open. It is also the one piece of state here, and a browser that
    has crashed reports itself closed rather than raising, so every print
    checks before it borrows.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def page_context(self):
        async with self._lock:
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                self._browser = await self._playwright.chromium.launch()
        return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


chromium = _Chromium()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await chromium.close()


app = FastAPI(title="KloudChat printer", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, bool]:
    """Whether a browser can actually be started, not whether the port is open.

    The API falls back to its structural renderer when this service is absent,
    and a printer that answers but cannot launch would send it a 500 on every
    export instead. Launching here is cheap — the browser is the one that stays.
    """
    browser = await chromium.page_context()
    return {"ok": browser.is_connected()}


@app.post("/pdf")
async def pdf(job: Job) -> Response:
    browser = await chromium.page_context()
    # A context per request, so one document cannot leave anything behind for
    # the next — storage, a service worker, a dialog left open.
    context = await browser.new_context()
    try:
        page = await context.new_page()
        await page.route("**/*", lambda route: asyncio.ensure_future(route.abort()))
        page.set_default_timeout(TIMEOUT_MS)
        await page.set_content(job.html, wait_until="load")
        # `prefer_css_page_size` is the whole point: the seed says whether it
        # is A4 portrait with 20mm margins or A4 landscape for slides, and
        # without this Chromium would impose Letter on both.
        out = await page.pdf(print_background=True, prefer_css_page_size=True)
    except asyncio.TimeoutError as err:  # pragma: no cover - pathological input
        raise HTTPException(status_code=504, detail="print_timeout") from err
    finally:
        await context.close()
    return Response(content=out, media_type="application/pdf")
