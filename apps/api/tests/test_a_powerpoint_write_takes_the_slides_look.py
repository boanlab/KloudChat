"""A deck the PowerPoint tool writes takes the theme and slides template `pptx_doc` resolves for
the conversation, once `pptx_doc.deck` takes them."""

from __future__ import annotations

from app.services import pptx_doc
from app.services.tools import pptx as pptx_tools


def test_the_look_reaches_the_deck(monkeypatch):
    seen = {}

    def deck(title, subtitle, slides, closing="", *, tokens=None, template=""):
        seen.update(tokens=tokens, template=template)
        return pptx_doc.blank()

    monkeypatch.setattr(pptx_doc, "deck", deck)
    title, data = pptx_tools._build({"title": "계획", "slides": [{"title": "현황"}]},
                                    ({"accent": "#1F3A68"}, "/x/template.pptx"))
    assert title == "계획" and seen == {"tokens": {"accent": "#1F3A68"},
                                        "template": "/x/template.pptx"}
