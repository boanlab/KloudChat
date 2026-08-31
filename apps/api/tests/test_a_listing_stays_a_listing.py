"""A card carries a card, not a document.

`_card_data` exists because 385 artifacts once came to 4.0 MB. It trims the
body of every kind and keeps every key, so a renderer reading a field on a card
finds it there and empty rather than missing.

Diagrams broke that. When mermaid pictures learned to reach the exported file
they started living on the section as base64 PNGs, and the trimmer — which
removes `content` and rebuilds the rest with `**section` — carried them into
every listing. Measured on one real account afterwards: **384 KB of a 442 KB
first page was diagrams**, 86% of a payload whose entire purpose is to be
small, for pictures no card has ever drawn.

The e2e gallery suite is what caught it, by asserting a byte ceiling. This is
the unit-level version of that ceiling.
"""

from __future__ import annotations

import json

from app.models.workspace import ArtifactKind
from app.schemas.workspace import _card_data

PNG = "data:image/png;base64," + "A" * 20_000


def _report(sections: int = 3) -> dict:
    return {
        "kind": "report",
        "sections": [
            {
                "id": f"s{i}",
                "heading": f"{i}절",
                "level": 1,
                "status": "done",
                "content": "본문 " * 200,
                "diagrams": {f"k{i}": PNG},
                "factCheck": {"status": "done", "claims": []},
            }
            for i in range(sections)
        ],
        "sources": [{"title": "출처", "url": "https://example.com"} for _ in range(20)],
        "citationStyle": "APA",
    }


def test_a_card_does_not_carry_the_pictures() -> None:
    card = _card_data(ArtifactKind.report, _report())
    assert card is not None
    for section in card["sections"]:
        assert section["diagrams"] == {}


def test_the_key_survives_even_though_the_value_does_not() -> None:
    """Every value shrinks and no key disappears.

    A renderer reading `section.diagrams` on a card that has no `diagrams` is
    the failure the first version of this trimmer shipped.
    """
    card = _card_data(ArtifactKind.report, _report())
    assert card is not None
    for section in card["sections"]:
        assert "diagrams" in section
        assert "heading" in section and "status" in section


def test_a_card_is_a_small_fraction_of_the_document() -> None:
    full = _report(sections=6)
    card = _card_data(ArtifactKind.report, full)
    whole = len(json.dumps(full))
    trimmed = len(json.dumps(card))
    # The document here is ~60 KB of picture and prose; the card must be a
    # rounding error beside it, because sixty of these travel together.
    assert trimmed * 10 < whole, f"카드 {trimmed}바이트 / 문서 {whole}바이트"


def test_a_deck_card_carries_no_slide_pictures_either() -> None:
    """Whitelisted rather than subtracted, so a field added to a slide later
    does not silently start travelling."""
    card = _card_data(
        ArtifactKind.deck,
        {
            "kind": "deck",
            "slides": [
                {"id": "s1", "title": "장", "layout": "bullets", "image": PNG}
            ],
        },
    )
    assert card is not None
    assert "image" not in card["slides"][0]
