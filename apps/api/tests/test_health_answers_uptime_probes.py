"""`/api/health` answers HEAD as well as GET (uptime probes), and the boot warning about the
LiteLLM key is given only when no key is set anywhere."""

from __future__ import annotations

import logging

from httpx import ASGITransport, AsyncClient

from app import main
from app.services import settings_store


async def test_head_and_get_both_answer(monkeypatch):
    async def up(*_args, **_kwargs):
        return True

    monkeypatch.setattr(main.litellm, "health", up)
    async with AsyncClient(transport=ASGITransport(main.app), base_url="http://test") as client:
        got = await client.get("/api/health")
        head = await client.head("/api/health")
    assert got.status_code == 200 and got.json() == {"status": "ok", "litellm": "ok"}
    assert head.status_code == 200


async def test_the_key_warning_counts_an_admin_saved_key(monkeypatch, caplog):
    monkeypatch.setattr(main.settings, "litellm_master_key", "")
    saved = {"key": "sk-saved"}

    async def config():
        return "http://litellm.invalid", saved["key"]

    monkeypatch.setattr(settings_store, "litellm_config", config)
    with caplog.at_level(logging.INFO, logger=main.log.name):
        await main._warn_without_litellm_key()
        saved["key"] = ""
        await main._warn_without_litellm_key()
    lines = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert lines[0][0] == logging.INFO and "saved in admin settings" in lines[0][1]
    assert lines[1][0] == logging.WARNING and "none saved" in lines[1][1]
    assert "sk-saved" not in caplog.text
