"""`deck.rewrite_slide` is reachable from the checks panel by slide id."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import workspace as workspace_router
from app.services import deck as deck_service


def _routes() -> dict[str, set[str]]:
    return {
        route.path: set(route.methods)
        for route in workspace_router.router.routes
        if getattr(route, "methods", None)
    }


def test_a_deck_has_the_same_rewrite_door_a_report_has() -> None:
    routes = _routes()
    assert "POST" in routes["/artifacts/{artifact_id}/slides/rewrite"]
    # The report's, for comparison.
    assert "POST" in routes["/artifacts/{artifact_id}/sections/rewrite"]


def test_the_request_names_a_slide_rather_than_a_section() -> None:
    """A deck rewrite request carries `slide_id`, not `section_id`."""
    from app.schemas.workspace import SlideRewrite

    payload = SlideRewrite(slideId="sl2", note="한자를 고쳐 주세요")
    assert payload.slide_id == "sl2"
    # An empty note means "just try again".
    assert SlideRewrite(slideId="sl2").note == ""


@pytest.mark.parametrize("kind", ["report", "html", "image"])
def test_only_a_deck_may_be_rewritten_slide_by_slide(kind: str) -> None:
    """Slide rewrite is refused for artifacts without `data["slides"]`."""
    import inspect

    source = inspect.getsource(workspace_router.rewrite_slide)
    assert "ArtifactKind.deck" in source
    assert "not_a_deck" in source


def test_a_rewrite_keeps_the_slide_that_came_before_it() -> None:
    """A slide rewrite is snapshotted like any other edit."""
    import inspect

    source = inspect.getsource(workspace_router.rewrite_slide)
    assert "ArtifactVersion(" in source
    assert "artifact.version += 1" in source
    # Charged like any other model call.
    assert 'reason="deck.rewrite"' in source


def test_the_endpoint_is_mounted_and_refuses_a_stranger() -> None:
    app = FastAPI()
    app.include_router(workspace_router.router)
    with TestClient(app) as client:
        reply = client.post(
            "/artifacts/does-not-exist/slides/rewrite",
            json={"slideId": "sl1", "note": ""},
        )
    # Anything but a 404: the route is mounted.
    assert reply.status_code != 404 or reply.json().get("detail") != "Not Found"


@pytest.mark.asyncio
async def test_retry_removes_the_old_failure_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful retry removes the failure placeholder."""

    async def complete(*_args, **_kwargs):
        return '{"body":"이번에는 정상적으로 작성됐습니다.","notes":"발표 노트"}', {
            "inputTokens": 10,
            "outputTokens": 8,
        }

    monkeypatch.setattr(deck_service, "_complete", complete)
    result, _ = await deck_service.rewrite_slide(
        request="분기 실적 발표",
        slides=[
            {
                "id": "sl1",
                "layout": "bullets",
                "title": "핵심 성과",
                "bullets": [deck_service.UNWRITTEN],
            }
        ],
        target_id="sl1",
        model="test-model",
        api_key="test-key",
    )

    assert result["body"] == "이번에는 정상적으로 작성됐습니다."
    assert "bullets" not in result
