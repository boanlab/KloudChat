"""매 서식이 조판의 어휘를 모델에게 알려 주는가.

The gap this closes: eight of seventeen 서식 named their layouts in
`template.toml` — `split`, `compare`, `figure`, `ask` — and their
`instructions.md` never said what to build one out of. The seed's stylesheet
stood up two columns, a large claim, a definition list and a real table, and
the model, handed a layout name and nothing else, wrote `<p>` and `<ul>`. The
design was in the file and unused, which is exactly what a deck that looks
plain looks like from the inside.

So the vocabulary lives beside the seed it describes, and every 서식 drawn on
that seed is told about it. A 서식 that describes its own layouts still goes
first and still wins; this is the floor under the ones that describe none.
"""

from __future__ import annotations

from app.services import design_templates as templates
from app.services import page

#: Elements the seeds actually style, and the ones a plain answer never uses.
#: Not the whole vocabulary — enough that a 서식 told only about `<p>` fails.
_STANDS_UP = ("<table>", "<dl>", "<blockquote>", 'class="note"')


def test_every_writing_template_is_told_what_its_typesetting_stands_up() -> None:
    rows = [t for t in templates.all_templates() if t.kind in ("deck", "document")]
    assert rows, "서식이 하나도 실리지 않았습니다."
    for row in rows:
        guide = page._guide(row)
        missing = [name for name in _STANDS_UP if name not in guide]
        assert not missing, f"{row.id} 안내에 {missing} 가 없습니다."


def test_a_deck_is_not_told_about_the_document_seed() -> None:
    """`seed_from` decides the vocabulary, as it decides the stylesheet.

    A deck told to reach for `<hr>` between paragraphs is being described a
    page it is not on.
    """
    deck = templates.get("deck-briefing")
    assert "한 장에 담는 양" in deck.markup
    document = templates.get("doc-report")
    assert "한 절에 담는 양" in document.markup


def test_a_templates_own_rules_come_first() -> None:
    """The 서식 is the specific thing; the seed's vocabulary is the floor.

    Read the other way round, a 서식's own rule about its own layout would
    arrive after a general sentence contradicting it.
    """
    row = templates.get("deck-proposal")
    guide = page._guide(row)
    assert guide.index(row.instructions[:40]) < guide.index("이 조판이 세워 주는 마크업")
