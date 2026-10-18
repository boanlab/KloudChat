"""출처 list entries named by page title, not URL slug."""

from __future__ import annotations

from app.services import agent

RESULT = (
    "[1] SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection\n"
    "https://arxiv.org/abs/2303.08896\n2023-03-16 — We propose SelfCheckGPT …"
)


def test_search_titles_are_kept_by_url_and_name_the_entry():
    sources: list[str] = []
    titles: dict[str, str] = {}
    agent._number_sources(RESULT, sources, {"name": "web_search", "arguments": "{}"}, titles)
    url = "https://arxiv.org/abs/2303.08896"
    assert sources == [url]
    label = agent._source_label(url, titles[url])
    assert label == "SelfCheckGPT: Zero-Resource Black-Box Hallucination Detection · arxiv.org"


def test_without_a_title_the_address_still_names_it():
    assert agent._source_label("https://arxiv.org/abs/2508.11823v1") == "arxiv.org · 2508.11823v1"
    # Brackets in a title would break the markdown link.
    assert "[" not in agent._source_label("https://a.org/x", "[PDF] 논문")
