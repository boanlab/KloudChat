"""`_card_data` trims artifact bodies for listings: every key kept, pictures and prose dropped."""

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
    """Every section key survives trimming, even when its value is emptied."""
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
    assert trimmed * 10 < whole, f"카드 {trimmed}바이트 / 문서 {whole}바이트"


def test_a_deck_card_carries_no_slide_pictures_either() -> None:
    """Deck cards whitelist slide fields, so `image` never travels."""
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
