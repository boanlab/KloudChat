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


_NAVER_WEB = {
    "items": [
        {
            "title": "<b>국가장학금</b> 2차 신청 안내",
            "link": "https://www.kosaf.go.kr/ko/notice/1",
            "description": "2026년 2학기 <b>국가장학금</b> 2차 신청 기간은 9월 15일부터",
        }
    ]
}
_NAVER_NEWS = {
    "items": [
        {
            "title": "&quot;등록금 덜 내려면&quot; <b>국가장학금</b> 2차 신청하세요",
            "originallink": "https://www.yna.co.kr/view/AKR20260911000100530",
            "link": "https://n.news.naver.com/mnews/article/001/0001",
            "description": "한국장학재단은 11일",
            "pubDate": "Thu, 11 Sep 2026 09:00:00 +0900",
        }
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


def _is_naver(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).netloc == "openapi.naver.com"


class _Client:
    calls: list[dict] = []

    def __init__(self, *_a, **_k) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None

    async def get(self, url: str, *, params: dict, headers: dict | None = None):
        _Client.calls.append({**params, "_url": url, **({"_headers": headers} if headers else {})})
        if _is_naver(url):
            return _Response(_NAVER_NEWS if "/news" in url else _NAVER_WEB)
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


def test_hits_without_the_querys_proper_nouns_are_dropped() -> None:
    """A FastAPI question once returned a Unity asset, a drama page and an
    adult site: many shared common words, none of them the subject."""
    rows = [
        {
            "title": "PostProcessing Controller | Unity",
            "url": "https://assetstore.unity.com/x",
            "snippet": "버전 릴리스 날짜",
            "published": "",
        },
        {
            "title": "신병4 다시보기",
            "url": "https://tvwiki48.net/drama/4136",
            "snippet": "최신 릴리스",
            "published": "",
        },
        {
            "title": "태그: 유디 야동",
            "url": "https://ydparty06.tv/tag",
            "snippet": "fastapi",
            "published": "",
        },
        {
            "title": "#버전 | TikTok",
            "url": "https://www.tiktok.com/tag/x",
            "snippet": "fastapi",
            "published": "",
        },
        {
            "title": "FastAPI 0.120 release notes",
            "url": "https://fastapi.tiangolo.com/release-notes/",
            "snippet": "",
            "published": "",
        },
    ]
    kept = builtin._select(rows, "FastAPI 최신 버전 번호랑 릴리스 날짜", 5)
    assert [r["url"] for r in kept] == ["https://fastapi.tiangolo.com/release-notes/"]
    # A version number must appear; three-letter words like LTS do not anchor.
    assert builtin._anchors("Ubuntu 24.04 지원 종료일") == (["ubuntu"], ["24.04"])
    assert builtin._anchors("Node.js 22 LTS 지원 종료일") == (["node.js"], ["22"])
    assert builtin._latin_only("Attention Is All You Need 논문 저자랑 arXiv 번호") == (
        "Attention Is All You Need"
    )
    assert builtin._foreign_script("Переключение языка ввода в Ubuntu 24.04")
    assert not builtin._foreign_script("Ubuntu 24.04.5 (Noble Numbat)")
    plesk = {
        "title": "Plesk on Ubuntu",
        "url": "https://x/plesk",
        "snippet": "ubuntu 22.04",
        "published": "",
    }
    assert not builtin._anchored(plesk, builtin._anchors("Ubuntu 24.04 지원 종료일"))
    # Korean-only queries have no anchors and keep the old behaviour.
    assert builtin._anchors("2026년 최저임금 시급") == ([], [])


def test_a_fitting_lane_hit_outranks_a_wordier_blog_but_a_stray_one_sinks() -> None:
    query = "Attention Is All You Need 논문 저자 arXiv 번호"
    blog = {
        "title": "Attention Is All You Need 논문 저자 arXiv 번호 정리",
        "url": "https://b.test/p",
        "snippet": "",
        "published": "",
    }
    paper = {
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/abs/1706.03762",
        "snippet": "arxiv",
        "published": "",
        "lane": "kind",
        "lane_query": "Attention Is All You Need",
    }
    stray = {
        "title": "Superconductivity as a consequence of ordering",
        "url": "https://arxiv.org/abs/1005.0280",
        "snippet": "attention",
        "published": "",
        "lane": "kind",
        "lane_query": "Attention Is All You Need",
    }
    ranked = builtin._rank([blog, stray, paper], query)
    # The paper fits over half the question: head start, and it leads.
    assert ranked[0]["url"] == "https://arxiv.org/abs/1706.03762"
    # One shared word earns no head start: the stray paper sinks below the blog.
    assert ranked[-1]["url"] == "https://arxiv.org/abs/1005.0280"


@pytest.mark.asyncio
async def test_a_paper_question_takes_the_science_lane(monkeypatch) -> None:
    class _Backends:
        search = "http://searx"
        fetch = ""
        exec = ""

    async def tools_config():
        return _Backends()

    monkeypatch.setattr(builtin.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()
    await builtin.web_search({"query": "Attention Is All You Need 논문 arXiv 번호"})
    assert [c.get("categories") for c in _Client.calls] == [None, "science"]
    # The science lane is asked in English for the title alone.
    assert _Client.calls[1]["q"] == "Attention Is All You Need"
    assert _Client.calls[1]["language"] == "en"


@pytest.mark.asyncio
async def test_a_latin_named_subject_gets_an_english_lane(monkeypatch) -> None:
    """「Ubuntu 24.04 지원 종료일」: the official page is English and a Korean
    locale hides it, so the Latin-alphabet words are searched in English too."""
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()
    await builtin._searxng("http://searx", "Ubuntu 24.04 지원 종료일", 5)
    assert [(c["q"], c["language"]) for c in _Client.calls] == [
        ("Ubuntu 24.04 지원 종료일", "ko-KR"),
        ("Ubuntu 24.04 end of life", "en"),
    ]
    assert builtin._english_query("2026년 NeurIPS 논문 제출 마감일") == "2026 NeurIPS deadline"
    assert builtin._english_query("FastAPI 최신 버전 번호랑 릴리스 날짜") == (
        "FastAPI latest version release"
    )
    # Nothing Latin, or a bare number: no English lane.
    assert builtin._english_query("2026년 최저임금") == "2026년 최저임금"
    assert builtin._places("이번 주말 분당 근처 행사나 축제") == ["분당"]
    assert builtin._places("성남시 분당구 정자동 소아과") == ["성남", "분당", "정자"]
    seoul = {
        "title": "이번 주말엔 불꽃축제, 차 없는 잠수교",
        "url": "https://n.test/1",
        "snippet": "서울",
        "published": "",
    }
    assert not builtin._anchored(seoul, builtin._anchors("분당 축제"), builtin._places("분당 축제"))
    _Client.calls.clear()
    # A Korean-only subject runs the general lane alone.
    await builtin._searxng("http://searx", "2026년 최저임금 시급", 5)
    assert len(_Client.calls) == 1


@pytest.mark.asyncio
async def test_hints_shape_the_search(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls.clear()
    await builtin._searxng(
        "http://searx",
        "국가장학금 2차 신청 기간",
        5,
        hints={"site": "kosaf.go.kr", "time_range": "month", "language": "en"},
    )
    assert _Client.calls[0]["q"] == "국가장학금 2차 신청 기간 site:kosaf.go.kr"
    assert _Client.calls[0]["time_range"] == "month" and _Client.calls[0]["language"] == "en"
    _Client.calls.clear()
    await builtin._searxng(
        "http://searx", "2026년 청년도약계좌 가입 조건", 5, hints={"official": True}
    )
    # The official lane drops the year: the notice carries it in its body, not its title.
    assert [c["q"] for c in _Client.calls] == [
        "2026년 청년도약계좌 가입 조건",
        "청년도약계좌 가입 조건 site:go.kr",
    ]
    assert all(c["safesearch"] == 2 for c in _Client.calls)


def test_foreign_boards_and_feeds_are_dropped() -> None:
    """TikTok, Blind, HiNative, Reddit: not where a Korean user looks."""
    rows = [
        {
            "title": "오늘 기분이 꾸리한게",
            "url": "https://www.teamblind.com/kr/post/x",
            "snippet": "",
            "published": "",
        },
        {
            "title": "how do you answer 오늘 기분이 어때?",
            "url": "https://hinative.com/questions/1",
            "snippet": "",
            "published": "",
        },
        {
            "title": "#기분 | TikTok",
            "url": "https://www.tiktok.com/tag/x",
            "snippet": "",
            "published": "",
        },
        {
            "title": "r/korea",
            "url": "https://www.reddit.com/r/korea/",
            "snippet": "",
            "published": "",
        },
        {
            "title": "기분 전환 방법 10가지",
            "url": "https://health.example.kr/mood",
            "snippet": "",
            "published": "",
        },
    ]
    assert [r["url"] for r in builtin._select(rows, "기분 전환 방법", 5)] == [
        "https://health.example.kr/mood"
    ]


def test_a_community_thread_ranks_below_a_page_with_the_same_words() -> None:
    query = "예금 금리 높은 은행"
    board = {
        "title": "예금 금리 높은 은행 어디?",
        "url": "https://www.a-ha.io/questions/1",
        "snippet": "",
        "published": "",
    }
    page = {
        "title": "예금 금리 높은 은행 비교",
        "url": "https://finance.example.com/rates",
        "snippet": "",
        "published": "",
    }
    assert [r["url"] for r in builtin._rank([board, page], query)][
        0
    ] == "https://finance.example.com/rates"


@pytest.mark.asyncio
async def test_naver_answers_a_korean_search_when_credentials_are_set(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(builtin.settings, "naver_client_id", "id")
    monkeypatch.setattr(builtin.settings, "naver_client_secret", "secret")
    _Client.calls.clear()
    hits = await builtin._searxng("http://searx", "2026년 국가장학금 2차 신청 기간", 5, fresh=True)
    naver_calls = [c for c in _Client.calls if _is_naver(c["_url"])]
    # News first for a fresh question, then web documents, with the credentials.
    assert [c["_url"].rsplit("/", 1)[-1] for c in naver_calls] == ["news.json", "webkr.json"]
    assert naver_calls[0]["_headers"]["X-Naver-Client-Id"] == "id"
    assert naver_calls[0]["sort"] == "date" and naver_calls[1]["sort"] == "sim"
    urls = [h["url"] for h in hits]
    # Naver's hits lead (order among them by fit), tags stripped, the article's
    # original address kept, dated.
    assert set(urls[:2]) == {
        "https://www.yna.co.kr/view/AKR20260911000100530",
        "https://www.kosaf.go.kr/ko/notice/1",
    }
    news = next(h for h in hits if "yna.co.kr" in h["url"])
    assert news["title"] == '"등록금 덜 내려면" 국가장학금 2차 신청하세요'
    assert news["published"] == "2026-09-11"


@pytest.mark.asyncio
async def test_no_naver_call_without_credentials_or_for_english(monkeypatch) -> None:
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(builtin.settings, "naver_client_id", "")
    _Client.calls.clear()
    await builtin._searxng("http://searx", "2026년 국가장학금 2차 신청 기간", 5, fresh=True)
    assert not [c for c in _Client.calls if _is_naver(c["_url"])]
    monkeypatch.setattr(builtin.settings, "naver_client_id", "id")
    monkeypatch.setattr(builtin.settings, "naver_client_secret", "secret")
    _Client.calls.clear()
    await builtin._searxng("http://searx", "Python 3.14 what's new", 5)
    assert not [c for c in _Client.calls if _is_naver(c["_url"])]
