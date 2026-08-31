"""서식이 자기 생김새를 보여 준다.

Seventeen 서식 differ almost entirely in CSS, and the gallery card carried a
name and a line — text cannot show CSS, so the gallery read as seventeen
copies of one shape and the differentiation everyone had paid for was
invisible exactly where choosing happens.

Each 서식 now ships `sample.html`, a short body in its own vocabulary, and
`/design-templates/{id}/preview` renders the real seed around it for the card
to shrink. The route is unauthenticated because an iframe `src` cannot carry a
header, and safe because everything it serves ships inside the image.
"""

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
    # In its own vocabulary: a cover/slide wrapper, not a bare paragraph.
    assert "<h1" in shape.sample or 'class="slide' in shape.sample


def test_the_preview_is_the_seed_around_the_sample() -> None:
    with _client() as client:
        page = client.get("/design-templates/doc-notice/preview")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    # The notice's own signature, straight from its sample.
    assert "수신" in page.text
    # And the shared seed around it, not a fragment.
    assert "<!doctype html>" in page.text.lower() or "<html" in page.text.lower()


def test_previews_actually_differ() -> None:
    """The whole point. Two 서식 rendering to the same bytes means the sample
    or the skin is not doing its job."""
    with _client() as client:
        pages = {
            tid: client.get(f"/design-templates/{tid}/preview").text
            for tid in ("doc-notice", "doc-minutes", "doc-brief", "deck-signal")
        }
    assert len(set(pages.values())) == len(pages)


def test_a_template_without_a_sample_is_a_404_not_a_blank_card() -> None:
    with _client() as client:
        assert client.get("/design-templates/no-such/preview").status_code == 404
