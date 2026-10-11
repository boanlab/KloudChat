from copy import deepcopy

import pytest

from app.services import report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("heading", "generated", "expected"),
    [
        ("Findings", "## Findings\n\nOriginal facts.", "Original facts."),
        ("Findings", "**Findings**\nOriginal facts.", "Original facts."),
        ("Findings", "Findings\nOriginal facts.", "Original facts."),
        ("Findings", "Original facts.", "Original facts."),
        ("Findings", "### Detail\nOriginal facts.", "### Detail\nOriginal facts."),
        ("Findings", "## Findings\n\n### Detail\nOriginal facts.", "### Detail\nOriginal facts."),
        ("Findings", "# 1. Findings\n\nOriginal facts.", "Original facts."),
        ("Findings", "## 1) Findings\nOriginal facts.", "Original facts."),
        ("Findings", "## **1. Findings**\nOriginal facts.", "Original facts."),
        ("Findings", "**1) Findings**\nOriginal facts.", "Original facts."),
        ("비교 분석", "\n\n## 1. 비교 분석\n\n비교 본문.", "비교 본문."),
        ("비교 분석", "## 1. 비교 분석", ""),
        ("1. Findings", "## 1. Findings\nOriginal facts.", "Original facts."),
        ("Findings", "## 1. Limits\nOriginal facts.", "## 1. Limits\nOriginal facts."),
        ("Findings", "### 1. Detail\nOriginal facts.", "### 1. Detail\nOriginal facts."),
        (
            "Findings",
            "## 1. Findings\n\n### 1) Detail\nOriginal facts.",
            "### 1) Detail\nOriginal facts.",
        ),
        (
            "Findings",
            "Original facts.\n\n## 1. Findings\nMore facts.",
            "Original facts.\n\n## 1. Findings\nMore facts.",
        ),
        ("Findings", "1. Findings\n2. Limits", "1. Findings\n2. Limits"),
        ("Findings", "1) Findings\n2) Limits", "1) Findings\n2) Limits"),
        ("Findings", "1. **Findings**\n2. **Limits**", "1. **Findings**\n2. **Limits**"),
        ("Findings", "## 1.1 Findings\nOriginal facts.", "## 1.1 Findings\nOriginal facts."),
        ("Findings", "## 1.Findings\nOriginal facts.", "## 1.Findings\nOriginal facts."),
        ("Findings", "## 1. Findings summary\nFacts.", "## 1. Findings summary\nFacts."),
        ("1. Findings", "## 2. Findings\nFacts.", "## 2. Findings\nFacts."),
    ],
)
async def test_rewrite_normalises_only_its_own_leading_heading(
    monkeypatch, heading, generated, expected
):
    spent = {"inputTokens": 10, "outputTokens": 20}

    async def complete(*_args, **_kwargs):
        return generated, spent

    monkeypatch.setattr(report, "_complete", complete)
    sections = [
        {"id": "target", "heading": heading, "content": "Old findings."},
        {"id": "other", "heading": "Limits", "content": "Original limits."},
    ]
    before = deepcopy(sections)
    body, usage = await report.rewrite_section(
        request="Summarise the supplied facts.",
        heading=heading,
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
    assert markdown.count(f"## {heading}") == 1
    assert "## Limits\n\nOriginal limits." in markdown


_WITH_TABLE = ("국내 동향은 다음과 같다.\n\n**표 1. 국내 연구 비교**\n\n"
               "| 연구 | 방법 |\n|---|---|\n| 가 | NLI |\n| 나 | QA |\n\n"
               "표 1에서 보듯 방법이 갈린다.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("generated", "note", "expected"),
    [
        # Research lane R-09: the rewrite dropped 표 1 and kept the sentence that names it.
        ("국내 동향을 새로 정리했다.\n\n표 1에서 보듯 두 갈래다.", "",
         "국내 동향을 새로 정리했다.\n\n표 1에서 보듯 두 갈래다.\n\n**표 1. 국내 연구 비교**\n\n"
         "| 연구 | 방법 |\n|---|---|\n| 가 | NLI |\n| 나 | QA |"),
        ("**표 1. 국내 연구 비교**\n\n새 설명.", "",
         "**표 1. 국내 연구 비교**\n\n| 연구 | 방법 |\n|---|---|\n| 가 | NLI |\n| 나 | QA |"
         "\n\n새 설명."),
        ("표 없이 줄글로만 정리했다.", "표는 빼고 줄글로", "표 없이 줄글로만 정리했다."),
    ],
)
async def test_a_rewrite_keeps_the_section_s_table(monkeypatch, generated, note, expected):
    async def complete(*_args, **_kwargs):
        return generated, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    sections = [{"id": "t", "heading": "국내 동향", "content": _WITH_TABLE}]
    body, _ = await report.rewrite_section(
        request="연구계획서", heading="국내 동향", sections=sections, target_id="t",
        model="m", api_key="k", note=note)
    assert body == expected
