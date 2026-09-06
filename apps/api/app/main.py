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
    branding,
    connectors,
    jobs,
    keys,
    llm,
    models,
    sessions,
    shares,
    transcriptions,
    usage,
    workspace,
)
from app.services import bootstrap, litellm, settings_store, storage

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kchat")


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
                # The count stays out of the log: CodeQL reads any value out of
                # `rotate_secrets` as secret material.
                log.info("stored secrets re-sealed under SECRET_KEY")
        except Exception as exc:  # noqa: BLE001 — old rows still open with the old key
            log.warning("stored secrets were not re-sealed: %s", exc)
    # Reclaims deleted accounts' files when the disk fills; see services/storage.py.
    sweeper = asyncio.create_task(storage.watch())
    yield
    sweeper.cancel()


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

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(branding.router, prefix=settings.api_prefix)
# Mounted at /llm without the API prefix: the base URL external tools point at.
app.include_router(llm.router)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(models.router, prefix=settings.api_prefix)
app.include_router(sessions.router, prefix=settings.api_prefix)
app.include_router(workspace.router, prefix=settings.api_prefix)
app.include_router(connectors.router, prefix=settings.api_prefix)
app.include_router(jobs.router, prefix=settings.api_prefix)
app.include_router(usage.router, prefix=settings.api_prefix)
app.include_router(usage.me_router, prefix=settings.api_prefix)
app.include_router(keys.router, prefix=settings.api_prefix)
app.include_router(transcriptions.router, prefix=settings.api_prefix)
app.include_router(shares.router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health")
async def health():
    """Reports KloudChat and LiteLLM separately so the UI can say which is down."""
    return {
        "status": "ok",
        "litellm": "ok" if await litellm.health() else "unavailable",
    }
