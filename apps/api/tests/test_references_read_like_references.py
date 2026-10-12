"""References: one work cited once, its year and venue kept, its link live in DOCX."""

import io
import zipfile

import pytest

from app.services import report_export, research, settings_store

_HITS = [
    {"title": "Generating Scientific Claims for Zero-Shot Scientific Fact Checking",
     "url": "http://arxiv.org/abs/2203.12990", "snippet": "scientific claim generation fact"},
    {"title": "Generating scientific claims for zero-shot scientific fact checking",
     "url": "https://aclanthology.org/2022.acl-long.175/", "snippet": "scientific claim fact"},
    {"title": "Survey on factuality in large language models",
     "url": "https://dl.acm.org/doi/abs/10.1145/3742420", "snippet": "factuality survey fact",
     "authors": ["Cunxiang Wang", "Xiaoze Liu", "Yuanhao Yue", "Xiangru Tang"],
     "published": "2024-05-01"},
    {"title": "FELM: Benchmarking Factuality Evaluation of",
     "url": "https://proceedings.neurips.cc/paper_files/paper/2023/file/x-Paper.pdf",
     "snippet": "factuality evaluation benchmark fact"},
]


@pytest.mark.asyncio
async def test_a_work_found_twice_is_one_source_with_its_year_and_venue(monkeypatch):
    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def plan(*_args, **_kwargs):
        return ["scientific fact checking factuality"], {"inputTokens": 0, "outputTokens": 0}

    async def gather(*_args, **_kwargs):
        return list(_HITS)

    async def scrape(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(research, "_plan", plan)
    monkeypatch.setattr(research, "_gather", gather)
    monkeypatch.setattr(research, "relevance", lambda *_a: 1.0)
    monkeypatch.setattr(research, "scrape", scrape)
    found = await research.run("과학 요약 사실 검증 연구", scholarly=True)
    titles = [s["title"].lower() for s in found.sources]
    assert titles.count("generating scientific claims for zero-shot scientific fact checking") == 1
    claims, survey, felm = found.sources
    assert (claims["year"], claims["publisher"]) == ("2022", "arXiv")
    assert survey["author"] == "Cunxiang Wang, Xiaoze Liu, Yuanhao Yue 외"
    assert (survey["year"], survey["doi"]) == ("2024", "10.1145/3742420")
    assert survey["publisher"] == "ACM"
    assert (felm["year"], felm["publisher"]) == ("2023", "NeurIPS")
    assert report_export.citation_text(survey, "APA") == (
        "Cunxiang Wang, Xiaoze Liu, Yuanhao Yue 외. (2024). Survey on factuality in large "
        "language models. ACM. https://doi.org/10.1145/3742420")
    assert report_export.citation_text(claims, "APA").startswith(
        "Generating Scientific Claims for Zero-Shot Scientific Fact Checking. (2022). arXiv.")


def test_reference_links_are_live_in_the_docx():
    sources = [{"ordinal": 1, "title": "Survey", "year": "2024", "publisher": "ACM",
                "doi": "10.1145/3742420", "url": "https://dl.acm.org/doi/abs/10.1145/3742420"}]
    sections = report_export.with_references(
        [{"heading": "동향", "content": "사실성 연구가 늘었다 [1]."}], sources, "APA")
    docx = zipfile.ZipFile(io.BytesIO(report_export.to_docx("계획서", sections)))
    body = docx.read("word/document.xml").decode()
    rels = docx.read("word/_rels/document.xml.rels").decode()
    assert "<w:hyperlink" in body
    assert 'Target="https://doi.org/10.1145/3742420"' in rels and 'TargetMode="External"' in rels
