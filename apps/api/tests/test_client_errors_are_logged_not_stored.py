"""`POST /client-errors`: anyone may report, briefly and not too often; it reaches the log only."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.routers import client_errors


@pytest.fixture(autouse=True)
def _fresh_counts(monkeypatch):
    monkeypatch.setattr(client_errors, "_seen", {})


async def _post(body, **headers: str):
    app = FastAPI()
    app.include_router(client_errors.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        if isinstance(body, bytes):
            return await client.post("/client-errors", content=body, headers=headers)
        return await client.post("/client-errors", json=body, headers=headers)


async def test_a_signed_out_report_is_logged_on_one_line(caplog):
    with caplog.at_level(logging.WARNING, logger="kchat.client"):
        response = await _post({
            "kind": "error",
            "message": "x is undefined\nforged line",
            "stack": "at a (main.js:1)\nat b (main.js:2)",
            "route": "/s/abc",
            "version": "abc123",
            "requestId": "rid-1",
        })
    assert response.status_code == 204
    (line,) = [r.getMessage() for r in caplog.records]
    assert "\n" not in line
    assert "/s/abc" in line and "abc123" in line and "rid-1" in line
    assert "at a (main.js:1) | at b (main.js:2)" in line
    assert "ip:" in line


async def test_a_signed_in_report_names_the_account_not_the_token(caplog):
    token, _ = create_access_token("user-7", "user")
    with caplog.at_level(logging.WARNING, logger="kchat.client"):
        await _post({"message": "boom"}, Authorization=f"Bearer {token}")
    (line,) = [r.getMessage() for r in caplog.records]
    assert "user:user-7" in line
    assert token not in line


async def test_a_long_body_is_refused():
    response = await _post(b'{"message": "' + b"a" * (client_errors.MAX_BODY + 1) + b'"}',
                           **{"Content-Type": "application/json"})
    assert response.status_code == 413


async def test_a_field_past_its_length_or_junk_is_refused():
    assert (await _post({"message": "a" * 1001})).status_code == 422
    assert (await _post(b"not json")).status_code == 422


async def test_one_caller_is_held_to_its_window():
    for _ in range(client_errors.PER_WINDOW):
        assert (await _post({"message": "again"})).status_code == 204
    assert (await _post({"message": "again"})).status_code == 429
