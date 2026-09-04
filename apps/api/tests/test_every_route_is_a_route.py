"""No endpoint is a private helper that landed under a route decorator."""

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

#: The routers themselves; `app` wires them at startup, not at import.
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
    """`GET /sessions` is served by `list_sessions`."""
    listing = [
        route
        for route in sessions.router.routes
        if isinstance(route, APIRoute) and route.path == "/sessions" and "GET" in route.methods
    ]

    assert listing, "GET /sessions 가 사라졌습니다."
    assert listing[0].endpoint.__name__ == "list_sessions"
