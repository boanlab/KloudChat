"""`/design-templates/{id}/preview` renders each 서식's `sample.html` inside its real seed."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import workspace as workspace_router
from app.services import design_templates


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "template",
    [t.id for t in design_templates.all_templates() if t.kind in ("document", "deck")],
)
def test_every_document_and_deck_template_has_a_sample(template: str) -> None:
    shape = design_templates.get(template)
    assert shape.sample.strip(), f"{template} has no sample.html"
    # A cover/slide wrapper, not a bare paragraph.
    assert "<h1" in shape.sample or 'class="slide' in shape.sample


def test_the_preview_is_the_seed_around_the_sample() -> None:
    with _client() as client:
        page = client.get("/design-templates/doc-notice/preview")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "수신" in page.text
    # The shared seed around it, not a fragment.
    assert "<!doctype html>" in page.text.lower() or "<html" in page.text.lower()


def test_previews_actually_differ() -> None:
    """Two 서식 never render the same preview bytes."""
    with _client() as client:
        pages = {
            tid: client.get(f"/design-templates/{tid}/preview").text
            for tid in ("doc-notice", "doc-minutes", "doc-brief", "deck-signal")
        }
    assert len(set(pages.values())) == len(pages)


def test_a_template_without_a_sample_is_a_404_not_a_blank_card() -> None:
    with _client() as client:
        assert client.get("/design-templates/no-such/preview").status_code == 404
