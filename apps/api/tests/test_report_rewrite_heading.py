from copy import deepcopy

import pytest

from app.services import report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        ("## Findings\n\nOriginal facts.", "Original facts."),
        ("**Findings**\nOriginal facts.", "Original facts."),
        ("Findings\nOriginal facts.", "Original facts."),
        ("Original facts.", "Original facts."),
        ("### Detail\nOriginal facts.", "### Detail\nOriginal facts."),
        ("## Findings\n\n### Detail\nOriginal facts.", "### Detail\nOriginal facts."),
    ],
)
async def test_rewrite_normalises_only_its_own_leading_heading(
    monkeypatch, generated, expected
):
    spent = {"inputTokens": 10, "outputTokens": 20}

    async def complete(*_args, **_kwargs):
        return generated, spent

    monkeypatch.setattr(report, "_complete", complete)
    sections = [
        {"id": "target", "heading": "Findings", "content": "Old findings."},
        {"id": "other", "heading": "Limits", "content": "Original limits."},
    ]
    before = deepcopy(sections)
    body, usage = await report.rewrite_section(
        request="Summarise the supplied facts.",
        heading="Findings",
        sections=sections,
        target_id="target",
        model="fixture-model",
        api_key="fixture-key",
        note="Correct the wording only.",
    )

    assert body == expected
    assert usage == spent
    assert sections == before
    markdown = report.to_markdown("Report", [{**sections[0], "content": body}, sections[1]])
    assert markdown.count("## Findings") == 1
    assert "## Limits\n\nOriginal limits." in markdown
