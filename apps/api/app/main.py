"""KloudChat API entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import SessionLocal
from app.routers import (
    admin,
    auth,
    bot_api,
    branding,
    connectors,
    issues,
    jobs,
    keys,
    llm,
    models,
    perf,
    pipelines,
    publications,
    runs,
    search,
    sessions,
    shares,
    skill_files,
    transcriptions,
    usage,
    user_pipelines,
    user_templates,
    workspace,
)
from app.services import (
    api_key_usage,
    bootstrap,
    connector_defaults,
    litellm,
    ocr_jobs,
    publishing,
    settings_store,
    starter,
    stop_signal,
    storage,
)
from app.services.perf import collector as perf_collector
from app.services.perf import live as perf_live

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kchat")


async def _provide_defaults() -> None:
    try:
        await connector_defaults.provide_at_startup(SessionLocal)
    except Exception as exc:  # noqa: BLE001 — the next start or admin save retries
        log.warning("default connectors not installed at startup: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("KloudChat api starting (env=%s, signup_mode=%s)", settings.env, settings.signup_mode)
    if not settings.litellm_master_key:
        log.warning("LITELLM_MASTER_KEY is unset — model routes will report unavailable")
    try:
        await bootstrap.seed_admin()
    except Exception as exc:  # noqa: BLE001 — a failed seed must not block startup
        log.warning("bootstrap admin not created: %s", exc)
    if not settings_store.has_own_key():
        log.warning(
            "SECRET_KEY is unset — stored secrets are sealed with a key derived from "
            "JWT_SECRET; set KCHAT_SECRET_KEY"
        )
    else:
        try:
            async with SessionLocal() as db:
                moved = await settings_store.rotate_secrets(db)
            if moved:
                # count not logged: CodeQL treats it as secret material
                log.info("stored secrets re-sealed under SECRET_KEY")
        except Exception as exc:  # noqa: BLE001 — old rows still open with the old key
            log.warning("stored secrets were not re-sealed: %s", exc)
    # catalogue renames/hidden flags applied at deploy
    try:
        await starter.sync_catalog_at_startup(SessionLocal)
    except Exception as exc:  # noqa: BLE001 — the owner's next sign-in syncs it
        log.warning("catalogue not synced at startup: %s", exc)
    # 기본 제공 copies anyone lacks; background: servers may be slow to list tools
    defaults = asyncio.create_task(_provide_defaults())
    sweeper = asyncio.create_task(storage.watch())
    # published bots: retention, pausing unaffordable bots
    bots = asyncio.create_task(publishing.watch())
    reader = asyncio.create_task(ocr_jobs.watch())
    key_usage = asyncio.create_task(api_key_usage.watch())
    # cross-replica stop delivery; no extra connection for a single replica
    stop_listener = (
        asyncio.create_task(stop_signal.listen(sessions.fire_stop_locally))
        if stop_signal.needed()
        else None
    )
    # one replica also samples host and GPUs
    perf_task = asyncio.create_task(perf_collector.run()) if settings.perf_monitoring else None
    yield
    defaults.cancel()
    sweeper.cancel()
    bots.cancel()
    reader.cancel()
    key_usage.cancel()
    if stop_listener is not None:
        stop_listener.cancel()
    if perf_task is not None:
        perf_task.cancel()


app = FastAPI(
    title="KloudChat API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.env == "dev" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # required for the refresh cookie
    allow_methods=["*"],
    allow_headers=["*"],
)
# per-replica open streams and request durations
app.add_middleware(perf_live.LoadMiddleware)

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(branding.router, prefix=settings.api_prefix)
# no API prefix: base URL for external tools
app.include_router(llm.router)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(models.router, prefix=settings.api_prefix)
app.include_router(sessions.router, prefix=settings.api_prefix)
app.include_router(search.router, prefix=settings.api_prefix)
app.include_router(runs.router, prefix=settings.api_prefix)
app.include_router(pipelines.router, prefix=settings.api_prefix)
app.include_router(user_pipelines.router, prefix=settings.api_prefix)
app.include_router(user_templates.router, prefix=settings.api_prefix)
app.include_router(workspace.router, prefix=settings.api_prefix)
app.include_router(skill_files.router, prefix=settings.api_prefix)
app.include_router(connectors.router, prefix=settings.api_prefix)
app.include_router(connectors.admin_router, prefix=settings.api_prefix)
app.include_router(jobs.router, prefix=settings.api_prefix)
app.include_router(usage.router, prefix=settings.api_prefix)
app.include_router(usage.me_router, prefix=settings.api_prefix)
app.include_router(keys.router, prefix=settings.api_prefix)
app.include_router(transcriptions.router, prefix=settings.api_prefix)
app.include_router(shares.router, prefix=settings.api_prefix)
app.include_router(publications.router, prefix=settings.api_prefix)
# published bots: API, MCP, frame policy
app.include_router(bot_api.router, prefix=settings.api_prefix)
app.include_router(issues.router, prefix=settings.api_prefix)
app.include_router(perf.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health")
async def health():
    """KloudChat and LiteLLM status reported separately."""
    return {
        "status": "ok",
        "litellm": "ok" if await litellm.health() else "unavailable",
    }
