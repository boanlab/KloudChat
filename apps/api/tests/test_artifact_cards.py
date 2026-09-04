"""`ArtifactOut.card`: the trimmed projection a listing carries per artifact kind."""

from __future__ import annotations

import pytest

from app.models.workspace import Artifact, ArtifactKind
from app.schemas.workspace import ArtifactOut


def artifact(kind: ArtifactKind, data: dict) -> Artifact:
    return Artifact(user_id="u", kind=kind, title="t", data=data)


def test_a_written_document_travels_without_its_markup():
    row = artifact(
        ArtifactKind.html,
        {
            "kind": "html",
            "language": "html",
            "templateId": "deck-editorial",
            "content": "<html>" + "가" * 50_000 + "</html>",
            "blocks": [{"title": "표지", "layout": "cover", "html": "<p>" + "나" * 9_000}],
            "lint": [{"rule": "filler"}],
        },
    )
    card = ArtifactOut.card(row)
    assert card.partial is True
    assert card.data["content"] == ""
    assert card.data["blocks"] == []
    assert card.data["templateId"] == "deck-editorial"


def test_a_deck_card_is_its_slide_titles():
    row = artifact(
        ArtifactKind.deck,
        {
            "kind": "deck",
            "slides": [
                {"id": f"s{i}", "title": f"{i}장", "layout": "bullets", "bullets": ["가" * 300]}
                for i in range(9)
            ],
        },
    )
    card = ArtifactOut.card(row)
    assert card.partial is True
    assert len(card.data["slides"]) == 4
    assert card.data["slides"][0] == {"id": "s0", "title": "0장", "layout": "bullets"}


def test_a_report_card_keeps_the_top_of_the_first_sections():
    row = artifact(
        ArtifactKind.report,
        {
            "kind": "report",
            "wordCount": 900,
            "sources": [{"url": "https://example.test"} for _ in range(20)],
            "sections": [
                {"id": f"s{i}", "heading": f"{i}절", "content": "가" * 2_000} for i in range(9)
            ],
        },
    )
    card = ArtifactOut.card(row)
    assert card.partial is True
    assert card.data["wordCount"] == 900
    assert card.data["sources"] == []
    assert len(card.data["sections"]) == 4
    assert len(card.data["sections"][0]["content"]) == 400
    assert card.data["sections"][0]["heading"] == "0절"


@pytest.mark.parametrize(
    "kind",
    [ArtifactKind.image, ArtifactKind.video, ArtifactKind.audio, ArtifactKind.chart],
)
def test_a_media_card_travels_whole(kind):
    """Media cards carry their full data."""
    data = {"kind": kind.value, "src": "/api/files/x/content", "durationSec": 8}
    card = ArtifactOut.card(artifact(kind, data))
    assert card.partial is False
    assert card.data == data


def test_the_full_row_is_still_the_full_row():
    """`of()` carries the full data with `partial` false."""
    data = {"kind": "html", "content": "<html>x</html>", "blocks": [{"html": "<p>x</p>"}]}
    full = ArtifactOut.of(artifact(ArtifactKind.html, data))
    assert full.partial is False
    assert full.data == data


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (ArtifactKind.html, {"kind": "html", "content": "<p>x</p>", "blocks": [{"html": "x"}]}),
        (ArtifactKind.deck, {"kind": "deck", "slides": [{"title": "가", "bullets": ["나"]}]}),
        (
            ArtifactKind.report,
            {"kind": "report", "sections": [{"heading": "가", "content": "나"}], "sources": [{}]},
        ),
    ],
)
def test_a_card_keeps_every_field_and_only_shrinks_it(kind, data):
    """A card keeps every key of the full data; fields are emptied, never dropped."""
    card = ArtifactOut.card(Artifact(user_id="u", kind=kind, title="t", data=data))
    assert set(card.data) == set(data)
