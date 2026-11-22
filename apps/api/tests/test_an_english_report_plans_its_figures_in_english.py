"""An English report plans and draws its figures in English: caption and box labels."""

from __future__ import annotations

from app.services import diagrams, report

REQUEST = ("Write a one-page monthly email marketing report for October 2026 in English for our "
           "Head of Marketing. Sections: summary, what worked, actions for November.")
DRAFT = ("## Summary\nOpen rate rose to 31.5% from 28.9% while the click rate fell to 3.1%.\n"
         "## What Worked\nThe question-style subject line won the October 14 test by 4.2 points.\n"
         "## Actions for November\nCheck the segment before every send, then adopt the tested "
         "subject style, then review unsubscribes weekly.\n")


async def test_an_english_report_asks_for_english_figures(monkeypatch):
    seen: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return DRAFT, {"inputTokens": 1, "outputTokens": 1}

    async def planning(**kwargs):
        seen.append(kwargs.get("language", "ko"))
        return [], {"inputTokens": 0, "outputTokens": 0}

    monkeypatch.setattr(report, "_complete", complete)
    monkeypatch.setattr(diagrams, "plan", planning)
    [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        approved_plan={"title": "October 2026 Email Marketing Report",
                       "sections": ["Summary", "What Worked", "Actions for November"]},
    )]
    assert seen and set(seen) == {"en"}
