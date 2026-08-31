"""라우트 자리에 헬퍼가 들어앉지 않았는가.

A private helper written between `@router.get(...)` and the function it was
meant to sit above becomes the endpoint. Nothing complains: the decorator finds
a callable and registers it, FastAPI builds a request model from its
parameters, and `GET /sessions` starts answering 422 asking the browser for a
field called `rows`. The sidebar goes empty and every unit test still passes,
because the helper itself was fine — it was simply also the route.

This is the one shape of that mistake that can be checked without a request:
an endpoint whose name begins with an underscore was never meant to be one.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

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
    usage,
    workspace,
)

#: Read from the routers themselves rather than from `app`: the application
#: wires them up at startup, so at import time it holds one route and this
#: would pass by having nothing to look at.
_ROUTERS = (
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
    usage,
    workspace,
)


def _routes() -> list[APIRoute]:
    return [
        route
        for module in _ROUTERS
        for route in module.router.routes
        if isinstance(route, APIRoute)
    ]


def test_no_private_function_is_serving_a_route() -> None:
    routes = _routes()
    assert len(routes) > 50, f"라우트를 {len(routes)}개밖에 찾지 못했습니다."
    wrong = [
        f"{sorted(route.methods)[0]} {route.path} → {route.endpoint.__name__}"
        for route in routes
        if route.endpoint.__name__.startswith("_")
    ]

    assert not wrong, "헬퍼가 라우트로 등록되었습니다: " + ", ".join(wrong)


def test_the_session_list_is_still_the_session_list() -> None:
    """The one this was written for, named so a failure says where to look."""
    listing = [
        route
        for route in sessions.router.routes
        if isinstance(route, APIRoute) and route.path == "/sessions" and "GET" in route.methods
    ]

    assert listing, "GET /sessions 가 사라졌습니다."
    assert listing[0].endpoint.__name__ == "list_sessions"
