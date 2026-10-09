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
        page = client.get("/design-templates/doc-internal-draft/preview")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "결재란" in page.text
    # The shared seed around it, not a fragment.
    assert "<!doctype html>" in page.text.lower() or "<html" in page.text.lower()


def test_previews_actually_differ() -> None:
    """Two 서식 never render the same preview bytes."""
    with _client() as client:
        pages = {
            tid: client.get(f"/design-templates/{tid}/preview").text
            for tid in ("doc-internal-draft", "doc-minutes", "doc-comparison", "deck-signal")
        }
    assert len(set(pages.values())) == len(pages)


def test_a_template_without_a_sample_is_a_404_not_a_blank_card() -> None:
    with _client() as client:
        assert client.get("/design-templates/no-such/preview").status_code == 404
        # A retired 서식 has no card either; its documents still render (`rendering`).
        assert client.get("/design-templates/doc-report/preview").status_code == 404


_DECKS = [t.id for t in design_templates.all_templates() if t.kind == "deck"]


@pytest.mark.parametrize("template", _DECKS)
def test_a_deck_sample_is_a_whole_example_deck(template: str) -> None:
    """Cover, an agenda, body slides and a close — not a two-slide teaser."""
    slides = design_templates.sample_slides(design_templates.get(template))
    assert 6 <= len(slides) <= 8, f"{template} has {len(slides)} slides"
    assert 'class="slide cover"' in slides[0]


@pytest.mark.parametrize("template", _DECKS)
def test_the_slide_viewer_gets_one_slide_at_a_time(template: str) -> None:
    slides = design_templates.sample_slides(design_templates.get(template))
    with _client() as client:
        whole = client.get(f"/design-templates/{template}/preview")
        first = client.get(f"/design-templates/{template}/preview?slide=1")
        last = client.get(f"/design-templates/{template}/preview?slide={len(slides)}")
        past = client.get(f"/design-templates/{template}/preview?slide={len(slides) + 1}")
        zero = client.get(f"/design-templates/{template}/preview?slide=0")
    assert whole.status_code == first.status_code == last.status_code == 200
    assert first.text.count('<section class="slide') == 1
    assert slides[0] in first.text and slides[-1] in last.text
    # One slide fills the frame; nothing scrolls under it.
    assert "overflow: hidden" in first.text
    assert first.headers["cache-control"] == "public, max-age=86400"
    assert past.status_code == zero.status_code == 404


def test_a_document_has_no_slides_to_page_through() -> None:
    with _client() as client:
        assert client.get("/design-templates/doc-minutes/preview?slide=1").status_code == 404


def test_the_catalogue_row_carries_the_slide_count() -> None:
    from app.schemas.workspace import DesignTemplateOut

    deck = DesignTemplateOut.of(design_templates.get("deck-briefing")).model_dump(by_alias=True)
    doc = DesignTemplateOut.of(design_templates.get("doc-minutes")).model_dump(by_alias=True)
    assert deck["slideCount"] == len(
        design_templates.sample_slides(design_templates.get("deck-briefing"))
    )
    assert doc["slideCount"] == 0
