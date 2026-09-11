"""Web search: a time-sensitive query also runs the news lane, whose hits come
first; front pages and a host's third hit are benched; a published date reaches
the model."""

from __future__ import annotations

import pytest

from app.services.tools import builtin

_GENERAL = {
    "results": [
        {"title": "Home \\ Anthropic", "url": "https://www.anthropic.com/", "content": "home"},
        {
            "title": "앤트로픽 - 위키백과",
            "url": "https://ko.wikipedia.org/wiki/앤트로픽",
            "content": "회사",
        },
        {
            "title": "앤트로픽 총정리 2026",
            "url": "https://lemontia.tistory.com/1372",
            "content": "최신 모델 소개",
        },
        {
            "title": "앤트로픽 관련주",
            "url": "https://lemontia.tistory.com/1374",
            "content": "최신 모델 관련주",
        },
        {
            "title": "앤트로픽 IPO",
            "url": "https://lemontia.tistory.com/1375",
            "content": "최신 모델 IPO",
        },
        {
            "title": "분당구청",
            "url": "https://www.bundang-gu.go.kr:10009/main/index.asp",
            "content": "구청",
        },
    ]
}
_NEWS = {
    "results": [
        {
            "title": "앤트로픽 최신 모델 '페이블5' 공개",
            "url": "https://www.msn.com/ko-kr/news/a1",
            "content": "새 모델",
            "publishedDate": "2026-09-06T08:00:00",
        },
        {
            "title": "앤트로픽 최신 모델 '페이블5' 공개",
            "url": "https://www.msn.com/ko-kr/news/a1",
            "content": "duplicate",
        },
    ]
}


class _Response:
    status_code = 200

    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _Client:
    calls: list[dict] = []

    def __init__(self, *_a, **_k) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None

    async def get(self, _url: str, *, params: dict):
        _Client.calls.append(params)
        return _Response(_NEWS if params.get("categories") == "news" else _GENERAL)


@pytest.mark.asyncio
async def test_a_fresh_query_runs_both_lanes_news_first(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()

    hits = await builtin._searxng("http://searx", "앤트로픽 최신 모델", 5, fresh=True)

    assert [c.get("categories") for c in _Client.calls] == [None, "news"]
    assert _Client.calls[1]["time_range"] == "month"
    urls = [h["url"] for h in hits]
    # News first, the duplicate folded, the third tistory hit and both front
    # pages benched; four remain, so one benched front page pads the list.
    assert urls[0] == "https://www.msn.com/ko-kr/news/a1"
    assert hits[0]["published"] == "2026-09-06"
    assert urls.count("https://www.msn.com/ko-kr/news/a1") == 1
    assert sum(1 for u in urls if "lemontia" in u) == 2
    assert "https://www.bundang-gu.go.kr:10009/main/index.asp" not in urls
    assert urls[-1] == "https://www.anthropic.com/"
    assert len(hits) == 5


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_a_papers_search_runs_the_science_lane_first(monkeypatch) -> None:
    _Client.calls = []
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    hits = await builtin._searxng("http://searx", "교육과정 재구성 교사 인식", 5, kind="papers")
    assert [c.get("categories") for c in _Client.calls] == [None, "science"]
    assert "time_range" not in _Client.calls[1]
    assert hits


async def test_a_timeless_query_runs_the_general_lane_only(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()
    await builtin._searxng("http://searx", "앤트로픽 회사 소개", 5)
    assert len(_Client.calls) == 1 and "categories" not in _Client.calls[0]


@pytest.mark.asyncio
async def test_the_model_sees_the_published_date(monkeypatch) -> None:
    class _Backends:
        search = "http://searx"
        fetch = ""
        exec = ""

    async def tools_config():
        return _Backends()

    monkeypatch.setattr(builtin.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)

    result = await builtin.web_search({"query": "앤트로픽 최신 모델"})

    assert not result.failed
    assert (
        "[1] 앤트로픽 최신 모델 '페이블5' 공개\nhttps://www.msn.com/ko-kr/news/a1\n"
        "게시일 2026-09-06 · 새 모델"
    ) in result.content


def test_a_thin_list_is_padded_with_benched_hits() -> None:
    rows = [
        {"title": "홈", "url": "https://a.test/", "snippet": "", "published": ""},
        {"title": "글", "url": "https://a.test/post", "snippet": "", "published": ""},
    ]
    assert [r["url"] for r in builtin._select(rows, "질문", 5)] == [
        "https://a.test/post",
        "https://a.test/",
    ]
