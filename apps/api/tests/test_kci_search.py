"""D4: Korean scholarly search through KCI, its papers cited with authors, year, journal, DOI.

Recorded answers only: nothing here reaches open.kci.go.kr."""

import logging
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.services import kci, report_export, research, settings_store
from app.services.tools import builtin

_XML = (Path(__file__).parent / "fixtures" / "kci" / "article_search.xml").read_bytes()
_ERROR = ('<?xml version="1.0" encoding="UTF-8"?><MetaData><inputData><key>BAD</key>'
          "<apiCode>articleSearch</apiCode></inputData>"
          "<error>등록되지 않은 key 입니다.</error></MetaData>").encode()
_KEY = "test-kci-key-0000"
_CLIENT = httpx.AsyncClient


def _answering(monkeypatch, body: bytes, status: int = 200) -> list[httpx.Request]:
    """httpx clients that answer every request with `body`; the requests are recorded."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=body, headers={"content-type": "text/xml"})

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return _CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return seen


def test_a_record_carries_what_the_reference_list_needs():
    first, second = kci.parse(_XML)
    assert first["title"] == "거대언어모델 기반 학술 문헌 요약의 사실 일관성 평가"
    assert first["authors"] == ["김민지", "이준호", "박서연", "최하늘"]
    assert (first["published"], first["journal"]) == ("2024", "정보과학회논문지")
    assert first["doi"] == "10.5626/JOK.2024.51.5.401"
    assert first["url"].startswith("https://www.kci.go.kr/")
    meta = research.bibliography(first)
    assert meta == {"author": "김민지, 이준호, 박서연 외", "year": "2024",
                    "publisher": "정보과학회논문지", "doi": "10.5626/JOK.2024.51.5.401"}
    assert report_export.citation_text({"title": first["title"], **meta}, "APA") == (
        "김민지, 이준호, 박서연 외. (2024). 거대언어모델 기반 학술 문헌 요약의 사실 일관성 평가. "
        "정보과학회논문지. https://doi.org/10.5626/JOK.2024.51.5.401")
    assert second["doi"] == "" and second["authors"] == ["정우성"]


def test_a_korean_research_request_is_told_and_its_subject_read():
    assert kci.wanted("거대언어모델 요약의 국내외 연구 동향을 정리한 연구계획서")
    assert kci.wanted("KCI 논문으로 찾아 줘")
    assert not kci.wanted("NeurIPS 2026 마감일 알려 줘")
    assert kci.terms("거대언어모델 요약의 국내 연구 동향을 보고서로 정리해 주세요") == [
        "거대언어모델", "요약"]
    assert kci.terms("학습 평가와 교육과정") == ["학습", "평가", "교육과정"]


@pytest.mark.asyncio
async def test_with_a_key_the_open_api_is_asked_and_the_key_is_not_logged(monkeypatch, caplog):
    monkeypatch.setattr(settings, "kci_api_key", _KEY)
    seen = _answering(monkeypatch, _XML)
    caplog.set_level(logging.INFO)
    hits = await kci.search("거대언어모델 요약", 6)
    assert [h["lane"] for h in hits] == ["kci", "kci"]
    params = seen[0].url.params
    assert seen[0].url.host == "open.kci.go.kr"
    assert (params["apiCode"], params["title"], params["key"]) == (
        "articleSearch", "거대언어모델 요약", _KEY)
    assert _KEY not in caplog.text


@pytest.mark.asyncio
async def test_a_refused_key_or_a_failure_finds_nothing_and_says_no_key(monkeypatch, caplog):
    monkeypatch.setattr(settings, "kci_api_key", _KEY)
    _answering(monkeypatch, _ERROR)
    caplog.set_level(logging.INFO)
    assert await kci.search("거대언어모델 요약", 6) == []
    _answering(monkeypatch, b"busy", status=503)
    assert await kci.search("거대언어모델 요약", 6) == []
    assert "503" in caplog.text and _KEY not in caplog.text


@pytest.mark.asyncio
async def test_without_a_key_the_search_backend_is_asked_within_kci(monkeypatch):
    monkeypatch.setattr(settings, "kci_api_key", "")
    asked = {}

    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def searxng(base, query, count, **kwargs):
        asked.update(query=query, **kwargs)
        return [{"title": "한국어 문서 요약 모델의 환각 유형 분석",
                 "url": "https://www.kci.go.kr/kciportal/landing/article.kci?arti_id=ART1",
                 "snippet": "", "published": ""}]

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(builtin, "searxng", searxng)
    hits = await kci.search("문서 요약 환각", 6)
    assert asked["hints"] == {"site": "kci.go.kr"} and asked["query"] == "문서 요약 환각"
    assert hits[0]["lane"] == "kci"
    assert research.bibliography(hits[0]) == {"publisher": "KCI"}


@pytest.mark.asyncio
async def test_a_report_on_korean_research_cites_kci_papers(monkeypatch):
    asked = []

    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def plan(*_args, **_kwargs):
        return ["LLM summarization factual consistency"], {"inputTokens": 0, "outputTokens": 0}

    async def gather(*_args, **_kwargs):
        return [{"title": "Factual consistency of LLM summarization",
                 "url": "https://arxiv.org/abs/2401.00001",
                 "snippet": "LLM summarization factual consistency"}]

    async def search(query, count=6):
        asked.append(query)
        return kci.parse(_XML)

    async def scrape(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(research, "_plan", plan)
    monkeypatch.setattr(research, "_gather", gather)
    monkeypatch.setattr(research, "scrape", scrape)
    monkeypatch.setattr(kci, "search", search)
    found = await research.run("거대언어모델 요약의 국내외 연구 동향 보고서", scholarly=True)
    assert asked == ["거대언어모델 요약"]
    kci_sources = [s for s in found.sources if "kci.go.kr" in s["url"]]
    assert len(kci_sources) == 2
    assert kci_sources[0]["author"] == "김민지, 이준호, 박서연 외"
    assert kci_sources[0]["publisher"] == "정보과학회논문지"
    assert "거대언어모델 기반 학술 문헌 요약" in found.context

    # Not asked when the request is not about Korean research.
    asked.clear()
    await research.run("LLM summarization factual consistency survey", scholarly=True)
    assert asked == []


@pytest.mark.asyncio
async def test_the_search_tool_offers_kci(monkeypatch):
    async def search(query, count=6):
        return kci.parse(_XML)

    monkeypatch.setattr(kci, "search", search)
    monkeypatch.setattr(settings, "kci_api_key", _KEY)
    result = await builtin.web_search({"query": "거대언어모델 요약", "kind": "kci"})
    assert not result.failed
    meta = "김민지, 이준호, 박서연, 최하늘 · 2024 · 정보과학회논문지 · DOI 10.5626"
    assert meta in result.content
    assert "kci" in builtin.WEB_SEARCH.parameters["properties"]["kind"]["enum"]
