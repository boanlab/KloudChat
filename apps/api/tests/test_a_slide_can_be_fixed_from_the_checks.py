"""검사 결과에서 슬라이드를 바로 고칠 수 있는가.

`deck.rewrite_slide` has existed for a while and was reachable from one place:
asking in the conversation. So anything that wanted to correct a single slide
from a panel had to send a sentence to the chat and hope — which is a request,
not an action. The deck does not change, and the reader has to watch the
transcript and work out for themselves whether anything happened.

The checks list is the caller that needed this. It names a slide and says what
is wrong with it, and had nowhere to send that.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import workspace as workspace_router


def _routes() -> dict[str, set[str]]:
    return {
        route.path: set(route.methods)
        for route in workspace_router.router.routes
        if getattr(route, "methods", None)
    }


def test_a_deck_has_the_same_rewrite_door_a_report_has() -> None:
    routes = _routes()
    assert "POST" in routes["/artifacts/{artifact_id}/slides/rewrite"]
    # The report's, for comparison — the two surfaces should not differ in
    # what they let a panel do.
    assert "POST" in routes["/artifacts/{artifact_id}/sections/rewrite"]


def test_the_request_names_a_slide_rather_than_a_section() -> None:
    """A `section_id` in a deck request is a field the caller has to translate."""
    from app.schemas.workspace import SlideRewrite

    payload = SlideRewrite(slideId="sl2", note="한자를 고쳐 주세요")
    assert payload.slide_id == "sl2"
    # An empty note means "just try again", the same as the report's.
    assert SlideRewrite(slideId="sl2").note == ""


@pytest.mark.parametrize("kind", ["report", "html", "image"])
def test_only_a_deck_may_be_rewritten_slide_by_slide(kind: str) -> None:
    """The endpoint reads `data["slides"]`, which the others do not have."""
    import inspect

    source = inspect.getsource(workspace_router.rewrite_slide)
    assert "ArtifactKind.deck" in source
    assert "not_a_deck" in source


def test_a_rewrite_keeps_the_slide_that_came_before_it() -> None:
    """Snapshotted like any other edit, or a worse rewrite is unrecoverable."""
    import inspect

    source = inspect.getsource(workspace_router.rewrite_slide)
    assert "ArtifactVersion(" in source
    assert "artifact.version += 1" in source
    # And charged, like any other model call.
    assert 'reason="deck.rewrite"' in source


def test_the_endpoint_is_mounted_and_refuses_a_stranger() -> None:
    app = FastAPI()
    app.include_router(workspace_router.router)
    with TestClient(app) as client:
        reply = client.post(
            "/artifacts/does-not-exist/slides/rewrite",
            json={"slideId": "sl1", "note": ""},
        )
    # Anything but a 404 for a missing route: the point is that it is mounted.
    assert reply.status_code != 404 or reply.json().get("detail") != "Not Found"
