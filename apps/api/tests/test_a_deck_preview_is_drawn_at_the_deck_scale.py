"""The gallery draws a deck 서식's sample at the deck's own type scale (`deck_type`), not at
sizes of its own: a sparse slide's body is 22pt, its title 32pt, as in the panel and file."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import workspace as workspace_router
from app.services import deck_preview, deck_type, design_templates

_DECKS = [t for t in design_templates.all_templates() if t.kind == "deck"]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router.router)
    return TestClient(app)


def _declared(html: str, name: str) -> list[str]:
    return re.findall(rf"{re.escape(name)}:\s*([^;]+);", html)


def test_a_sparse_slide_is_drawn_at_22pt_under_a_32pt_title() -> None:
    # 「권고」: three short bullets.
    with _client() as client:
        page = client.get("/design-templates/deck-case/preview?slide=2").text
    assert _declared(page, "--deck-body") == ["22pt"]
    # The slide's own rule wins over `:root`: full step, one-line title.
    assert "--text-scale: 1;" in page
    assert _declared(page, "--deck-title")[-1] == "32pt"
    assert "li { font-size: var(--fs-body)" in page


def test_the_seed_declares_the_one_type_table() -> None:
    declared = deck_preview.type_declarations()
    for name, key in deck_preview._SIZES.items():
        assert f"--deck-{name}: {deck_type.TYPE[key]:g}pt;" in declared
    assert f"--deck-floor: {deck_type.FLOOR_PT}pt;" in declared
    for template in _DECKS:
        assert "{{DECK_TYPE}}" not in template.seed
        assert "--deck-body: 22pt;" in template.seed


#: A size that follows the window, not the slide.
_VIEWPORT_SIZE = re.compile(r"font-size:[^;}]*\d(?:vw|vh|vmin|vmax)", re.I)


def test_no_deck_size_follows_the_window() -> None:
    seed = (design_templates._ROOT / "_deck" / "seed.html").read_text(encoding="utf-8")
    assert not _VIEWPORT_SIZE.search(seed)
    assert "--h2-size" not in seed


@pytest.mark.parametrize("template", _DECKS, ids=lambda t: t.id)
def test_a_deck_face_sets_no_size_of_its_own(template) -> None:
    face = (design_templates._ROOT / template.id / "design.css").read_text(encoding="utf-8")
    for value in re.findall(r"font-size:\s*([^;}]+)", face):
        assert value.strip().startswith("var(--deck-"), f"{template.id}: font-size {value}"


@pytest.mark.parametrize("template", _DECKS, ids=lambda t: t.id)
def test_every_sample_slide_gets_its_step(template) -> None:
    slides = design_templates.sample_slides(template)
    whole = design_templates.preview(template, {})
    for n in range(1, len(slides) + 1):
        assert f".slide:nth-of-type({n}) {{ --text-scale:" in whole
    fits = deck_preview.fits(slides, template.seed)
    # The samples are written to fit at the first step.
    assert all(f.scale == 1.0 for f in fits), [(f.layout, f.scale) for f in fits]


def _bullets(*items: str) -> str:
    lis = "".join(f"<li>{item}</li>" for item in items)
    return f'<section class="slide bullets"><h2>제목</h2><ul>{lis}</ul></section>'


def test_a_crowded_slide_steps_down_and_the_deck_follows_to_18pt() -> None:
    seed = design_templates.get("deck-case").seed
    sparse = _bullets("짧은 항목 하나", "짧은 항목 둘")
    crowded = _bullets(*["한 줄에 다 들어가지 않을 만큼 길게 쓴 설명 문장이 계속 이어진다" * 2] * 6)
    alone = deck_preview.fits([sparse], seed)
    together = deck_preview.fits([sparse, crowded], seed)
    assert alone[0].scale == 1.0
    assert together[1].scale < deck_type.SCALES[1]
    # One step for the deck, not below 18pt on account of one crowded slide.
    assert together[0].scale == deck_type.SCALES[1]


def test_a_table_slide_carries_its_row_size() -> None:
    template = design_templates.get("deck-briefing")
    slides = design_templates.sample_slides(template)
    n = next(i for i, s in enumerate(slides, start=1) if 'class="slide table"' in s)
    page = design_templates.render_slide(template, tokens={}, n=n)
    rows = 5  # header and four rows
    assert f"--deck-table: {deck_type.table_size(rows) * deck_type.K:.2f}pt;" in page
