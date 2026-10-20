"""Metric tile labels: whole, or cut between words; the PDF tile wraps them."""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.services import deck, deck_export


def test_an_english_metric_label_is_kept_whole_or_cut_between_words():
    # the round-11 T8 slide: 「Local accuracy a」
    metrics = deck._clean_metrics([["64%", "Local accuracy above 20 pages"],
                                   ["91%", "Cloud accuracy above 20 pages"]])
    assert metrics == [["64%", "Local accuracy above 20 pages"],
                       ["91%", "Cloud accuracy above 20 pages"]]
    long = deck._clean_metrics([["64%", "Local model accuracy on documents above twenty pages"]])
    assert long == [["64%", "Local model accuracy"]]
    # Korean labels keep their 16-character room
    assert deck._clean_metrics([["32%", "오탐 감소"]]) == [["32%", "오탐 감소"]]
    assert deck._clean_metrics([["3건", "가나다라마바사아자차카타파하가나다라"]])[0][1] == (
        "가나다라마바사아자차카타파하가나")


def test_the_pdf_metric_tile_wraps_a_label_inside_its_tile(monkeypatch):
    drawn: list[tuple[float, str, float]] = []
    original = canvas.Canvas.drawString

    def spy(self, x, y, text, *args, **kwargs):
        drawn.append((x, text, self._fontsize, self._fontname))
        return original(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(canvas.Canvas, "drawString", spy)
    labels = ["Local accuracy above 20 pages", "Cloud accuracy above 20 pages",
              "Local cost per 1,000 docs", "Cloud cost per 1,000 docs"]
    slides = [{"title": "Gap", "layout": "metrics",
               "metrics": [[f"{n}%", label]
                           for n, label in zip((64, 91, 4, 38), labels, strict=True)]}]
    assert deck_export.to_pdf("Deck", slides).startswith(b"%PDF")
    lines = [(x, text, size, font) for x, text, size, font in drawn
             if any(text and text in label for label in labels)]
    assert " ".join(text for _, text, _, _ in lines).split() == " ".join(labels).split()
    starts = sorted({x for x, *_ in lines})
    span = starts[1] - starts[0] - 24
    for _, text, size, font in lines:
        assert pdfmetrics.stringWidth(text, font, size) <= span - 14
