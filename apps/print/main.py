"""HTML in, PDF out: prints the self-contained documents `design_templates.render` produces.

**Nothing here reaches the network.** Every request is aborted except the
`about:blank` the page starts on; seeds embed pictures as `data:` URIs. The
markup is model-authored, so an `<img src="http://…">` in it must not become
a request from inside the deployment.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, async_playwright
from pydantic import BaseModel, Field

#: Well past any real document; bounds this container's memory.
MAX_HTML = 32 * 1024 * 1024

#: A pathological document (CSS animation, million-row table) fails as 504
#: rather than holding the worker.
TIMEOUT_MS = 30_000


class Job(BaseModel):
    html: str = Field(max_length=MAX_HTML)


#: A one-slide figure deck is tens of kilobytes; this bounds a hostile one.
MAX_PPTX_B64 = 8 * 1024 * 1024


class SlideJob(BaseModel):
    #: The .pptx, base64-encoded; its first slide is rendered.
    pptx: str = Field(max_length=MAX_PPTX_B64)
    #: Output resolution; 192 dpi draws a 10-inch figure 1920 px wide.
    dpi: int = Field(default=192, ge=72, le=300)


#: LibreOffice runs one conversion at a time: its user profile is not shared safely.
_office = asyncio.Lock()


class _Chromium:
    """One browser, started on first use (~300 ms) and relaunched if it dies."""

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

    async def new_context(self) -> BrowserContext:
        """Isolated context; relaunches once if Chromium died since `is_connected`."""
        async with self._lock:
            for attempt in range(2):
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                if self._browser is None or not self._browser.is_connected():
                    self._browser = await self._playwright.chromium.launch()
                try:
                    return await self._browser.new_context()
                except PlaywrightError:
                    if attempt:
                        raise
                    try:
                        await self._browser.close()
                    except PlaywrightError:
                        pass
                    self._browser = None
            raise RuntimeError("unreachable")

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
    """Whether a browser can be started, not merely whether the port is open."""
    browser = await chromium.page_context()
    return {"ok": browser.is_connected()}


@app.post("/pdf")
async def pdf(job: Job) -> Response:
    # A context per request: no storage, service worker or dialog survives.
    context = await chromium.new_context()
    try:
        page = await context.new_page()
        await page.route("**/*", lambda route: asyncio.ensure_future(route.abort()))
        page.set_default_timeout(TIMEOUT_MS)
        await page.set_content(job.html, wait_until="load")
        # The seed's `@page` rule sets the paper; without this Chromium uses Letter.
        out = await page.pdf(print_background=True, prefer_css_page_size=True)
    except asyncio.TimeoutError as err:  # pragma: no cover - pathological input
        raise HTTPException(status_code=504, detail="print_timeout") from err
    finally:
        await context.close()
    return Response(content=out, media_type="application/pdf")


async def _run(*command: str, timeout: float) -> None:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        raise HTTPException(status_code=504, detail="render_timeout") from exc
    if process.returncode:
        raise HTTPException(status_code=422, detail=f"render_failed: {err.decode()[-300:]}")


@app.post("/pptx/png")
async def pptx_png(job: SlideJob) -> Response:
    """The first slide of a .pptx as a PNG, via LibreOffice (PDF) and poppler.

    Nothing is fetched: the slide embeds its pictures, and the container has no route out."""
    try:
        blob = base64.b64decode(job.pptx, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="bad_pptx") from exc
    work = Path(tempfile.mkdtemp(prefix="slide-"))
    try:
        source = work / "figure.pptx"
        source.write_bytes(blob)
        async with _office:
            await _run(
                "soffice", f"-env:UserInstallation=file://{work}/profile", "--headless",
                "--norestore", "--convert-to", "pdf", "--outdir", str(work), str(source),
                timeout=60,
            )
        await _run(
            "pdftoppm", "-r", str(job.dpi), "-f", "1", "-l", "1", "-png", "-singlefile",
            str(work / "figure.pdf"), str(work / "figure"),
            timeout=30,
        )
        return Response(content=(work / "figure.png").read_bytes(), media_type="image/png")
    finally:
        shutil.rmtree(work, ignore_errors=True)
