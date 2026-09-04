"""Every 서식 is told the seed's layout vocabulary; its own rules come first."""

from __future__ import annotations

from app.services import design_templates as templates
from app.services import page

#: Seed-styled elements a plain answer never uses; enough to catch a `<p>`-only vocabulary.
_STANDS_UP = ("<table>", "<dl>", "<blockquote>", 'class="note"')


def test_every_writing_template_is_told_what_its_typesetting_stands_up() -> None:
    rows = [t for t in templates.all_templates() if t.kind in ("deck", "document")]
    assert rows, "서식이 하나도 실리지 않았습니다."
    for row in rows:
        guide = page._guide(row)
        missing = [name for name in _STANDS_UP if name not in guide]
        assert not missing, f"{row.id} 안내에 {missing} 가 없습니다."


def test_a_deck_is_not_told_about_the_document_seed() -> None:
    """`seed_from` decides which vocabulary a 서식 is told about."""
    deck = templates.get("deck-briefing")
    assert "한 장에 담는 양" in deck.markup
    document = templates.get("doc-report")
    assert "한 절에 담는 양" in document.markup


def test_a_templates_own_rules_come_first() -> None:
    """A 서식's own instructions precede the seed vocabulary."""
    row = templates.get("deck-proposal")
    guide = page._guide(row)
    assert guide.index(row.instructions[:40]) < guide.index("이 조판이 세워 주는 마크업")
