"""What a listing carries, and what it deliberately leaves behind.

The gallery draws every artifact as a thumbnail and used to be handed every
artifact whole: 385 rows came to 4.0 MB on this instance, 2.8 MB of it the
markup of 69 HTML documents nobody was reading yet. These are the rules that
decide what a card is.
"""

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
    assert card.data["wordCount"] == 900  # what the card actually prints
    assert card.data["sources"] == []
    assert len(card.data["sections"]) == 4
    assert len(card.data["sections"][0]["content"]) == 400
    assert card.data["sections"][0]["heading"] == "0절"


@pytest.mark.parametrize(
    "kind",
    [ArtifactKind.image, ArtifactKind.video, ArtifactKind.audio, ArtifactKind.chart],
)
def test_a_media_card_travels_whole(kind):
    """A `src` and a duration are already card-sized, and the thumbnail is the
    artifact itself — trimming would leave a card with nothing to show."""
    data = {"kind": kind.value, "src": "/api/files/x/content", "durationSec": 8}
    card = ArtifactOut.card(artifact(kind, data))
    assert card.partial is False
    assert card.data == data


def test_the_full_row_is_still_the_full_row():
    """`of()` is what a fetch by id returns, and nothing about it changed."""
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
    """The client's types declare these fields and its renderers read them.

    The first version of this projection dropped keys instead of emptying them,
    and a report card with no `sources` took the whole screen down on
    `sources.length`. A card is the same shape, smaller.
    """
    card = ArtifactOut.card(Artifact(user_id="u", kind=kind, title="t", data=data))
    assert set(card.data) == set(data)
