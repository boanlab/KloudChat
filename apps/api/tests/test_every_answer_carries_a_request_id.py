"""Every answer names its request, a 500 included, and so do the error lines it logs."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import request_id

log = logging.getLogger("kchat.test")


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(request_id.RequestIdMiddleware)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        log.error("the store refused")
        raise RuntimeError("boom")

    return app


async def _get(path: str, **headers: str):
    transport = ASGITransport(_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


async def test_an_answer_gets_a_fresh_id():
    first, second = await _get("/ok"), await _get("/ok")
    assert len(first.headers["x-request-id"]) == 32
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_a_plain_id_from_the_proxy_is_kept():
    response = await _get("/ok", **{"X-Request-ID": "edge-42.a_b"})
    assert response.headers["x-request-id"] == "edge-42.a_b"


@pytest.mark.parametrize("sent", ["a b", "x" * 65, "<script>", "aé"])
def test_an_odd_id_is_replaced_not_echoed(sent):
    scope = {"headers": [(b"x-request-id", sent.encode("utf-8"))]}
    assert request_id._incoming(scope) == ""


async def test_a_failure_answers_500_with_the_id_and_logs_it(caplog):
    caplog.handler.addFilter(request_id._Tagged())
    with caplog.at_level(logging.INFO):
        response = await _get("/boom", **{"X-Request-ID": "req-500"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-500"
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("the store refused [request_id=req-500]" in line for line in lines)
    assert any("GET /boom failed [request_id=req-500]" in line for line in lines)


def test_lines_outside_a_request_and_below_warning_are_left_alone():
    tag = request_id._Tagged()
    info = logging.LogRecord("x", logging.INFO, __file__, 1, "fine %s", ("now",), None)
    tag.filter(info)
    assert info.getMessage() == "fine now"
    warning = logging.LogRecord("x", logging.WARNING, __file__, 1, "odd", (), None)
    tag.filter(warning)
    assert warning.getMessage() == "odd"
