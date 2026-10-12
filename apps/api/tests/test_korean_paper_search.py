"""Korean scholarly papers found the way a Korean researcher finds them: the ordinary search
backend asked with 「학회 논문」·「학술대회 발표」·「학회지」 and inside KCI·DBpia·RISS, the
hits merged, and each paper's authors, year, venue and DOI read from its landing page's meta
tags. There is no KCI API key.

Recorded answers only (tests/fixtures/korean_papers): nothing here reaches the network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.services import korean_papers, netguard, report_export, research, settings_store
from app.services.tools import builtin

_DIR = Path(__file__).parent / "fixtures" / "korean_papers"
_SEARCH = json.loads((_DIR / "search_results.json").read_text(encoding="utf-8"))
_PAGES = {
    "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11743721": "dbpia_NODE11743721",
    "https://journal.kci.go.kr/KJS/archive/articleView?artiId=ART002656945":
        "kci_journal_ART002656945",
    "https://dspace.kci.go.kr/handle/kci/2171498": "kci_dspace_2171498",
    "https://www.earticle.net/Article/A454990": "earticle_A454990",
    "https://koreascience.kr/article/CFKO202404272003320.page":
        "koreascience_CFKO202404272003320",
    "https://scienceon.kisti.re.kr/srch/selectPORSrchArticle.do?cn=JAKO202302557635729":
        "scienceon_JAKO202302557635729",
    "https://kiss.kstudy.com/Detail/Ar?key=4059314": "kiss_4059314",
}
_RISS = ("https://www.riss.kr/search/detail/DetailView.do?p_mat_type=1a0202e37d52c72d"
         "&control_no=5241a7c031159fb86aae8a9")


def _page(name: str) -> str:
    return (_DIR / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture
def recorded(monkeypatch):
    """The search backend and page reader answering from the recordings; what was asked
    and read is recorded."""
    asked: list[tuple[str, str]] = []
    read: list[str] = []

    async def backends():
        return settings_store.ToolBackends(search="http://search.test", fetch="http://fetch.test")

    async def searxng(base, query, count, **kwargs):
        site = (kwargs.get("hints") or {}).get("site") or ""
        asked.append((query, site))
        return [dict(hit) for hit in _SEARCH.get(f"{query}|{site}", [])]

    async def scrape_html(base, url):
        read.append(url)
        if url in _PAGES:
            return _page(_PAGES[url])
        return _page("riss_no_meta") if "riss.kr" in url else ""

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(builtin, "searxng", searxng)
    monkeypatch.setattr(builtin, "scrape_html", scrape_html)
    return asked, read


def test_no_kci_key_setting_is_left():
    assert not hasattr(settings, "kci_api_key")
    root = Path(__file__).resolve().parents[3]
    for name in (".env.example", "docker-compose.yml", "docs/configuration.md"):
        if (root / name).exists():
            assert "KCI_API_KEY" not in (root / name).read_text(encoding="utf-8"), name


def test_a_korean_research_request_is_told_and_its_subject_read():
    assert korean_papers.wanted("거대언어모델 요약의 국내외 연구 동향을 정리한 연구계획서")
    assert korean_papers.wanted("KCI 논문으로 찾아 줘")
    assert not korean_papers.wanted("NeurIPS 2026 마감일 알려 줘")
    assert korean_papers.terms("거대언어모델 요약의 국내 연구 동향을 보고서로 정리해 주세요") == [
        "거대언어모델", "요약"]
    assert korean_papers.terms("학습 평가와 교육과정") == ["학습", "평가", "교육과정"]
    assert korean_papers.query_for("거대언어모델 요약 학회 논문 찾아줘") == "거대언어모델 요약"


def test_the_queries_are_the_ones_a_korean_researcher_types():
    assert korean_papers.variants("거대언어모델 요약") == [
        ("거대언어모델 요약 학회 논문", ""),
        ("거대언어모델 요약 학술대회 발표", ""),
        ("거대언어모델 요약 학회지", ""),
        ("거대언어모델 요약", "kci.go.kr"),
        ("거대언어모델 요약", "dbpia.co.kr"),
        ("거대언어모델 요약", "riss.kr"),
        ("거대언어모델 요약", "scienceon.kisti.re.kr"),
        ("거대언어모델 요약", "earticle.net"),
    ]
    assert korean_papers.variants("  ") == []


def test_database_titles_are_read_as_the_papers_titles():
    clean = korean_papers.clean_title
    dbpia = "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE1"
    assert clean("초거대 언어모델 연구 동향 - 논문", dbpia) == "초거대 언어모델 연구 동향"
    assert clean("거대 언어 모델의 발전 및 상용화 과제 - 한국통신학회지(정보와통신) : 논문",
                 dbpia) == "거대 언어 모델의 발전 및 상용화 과제"
    assert clean("GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구 | DBpia") == (
        "GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구")
    assert clean("거대언어모델의 차별문제 비교 연구 = A Comparative Study on Discrimination") == (
        "거대언어모델의 차별문제 비교 연구")
    assert clean("[논문]ChatGPT 및 거대언어모델의 추론 능력 향상을 위한 프롬프트 방....") == (
        "ChatGPT 및 거대언어모델의 추론 능력 향상을 위한 프롬프트 방")
    # Off the databases a 「 - 」 is the paper's own subtitle.
    assert clean("생성형 AI 교육 연구 - 초등 사례 연구", "https://www.example.ac.kr/paper/1") == (
        "생성형 AI 교육 연구 - 초등 사례 연구")


def test_the_variants_are_merged_into_papers_once_each():
    batches = [v for k, v in _SEARCH.items() if not k.startswith("_")]
    hits = korean_papers.merge(batches, "거대언어모델 요약")
    urls = [h["url"] for h in hits]
    # Blogs, videos, an encyclopedia, a society's file, search and list pages, a PDF link
    # titled by its address, and a journal's policy page are not papers.
    for junk in ("aithor.com", "musely.ai", "youtube.com", "wikipedia.org", "kiise.or.kr",
                 "conf.kics.or.kr", "Search.do", "articlePdf", "editorialPolicy",
                 "scienceon.kisti.re.kr/\""):
        assert not any(junk in u for u in urls), junk
    assert "https://scienceon.kisti.re.kr/" not in urls
    # One work on KCI and on RISS is one paper, kept on KCI.
    survey = [h for h in hits if h["title"].startswith("거대언어모델을 활용한 설문조사")]
    assert [h["url"] for h in survey] == ["https://dspace.kci.go.kr/handle/kci/2176140"]
    # Found by two variants, once; its venue read from DBpia's result title.
    dev = [h for h in hits if h["title"] == "거대 언어 모델의 발전 및 상용화 과제"]
    assert len(dev) == 1 and dev[0]["journal"] == "한국통신학회지(정보와통신)"
    assert {h["lane"] for h in hits} == {korean_papers.LANE}
    # Papers on both subject words before papers on one.
    both = [i for i, h in enumerate(hits)
            if "요약" in h["title"] + h.get("snippet", "") and "언어" in h["title"]]
    assert both and both[0] == 0


@pytest.mark.parametrize(("name", "expected"), [
    ("dbpia_NODE11743721", {
        "title": "GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구",
        "authors": ["이용준", "강장묵", "김지효"], "published": "2024",
        "journal": "한국산학기술학회 논문지", "volume": "25", "issue": "3",
        "doi": "10.5762/KAIS.2024.25.3.10"}),
    ("kci_journal_ART002656945", {
        "title": "한국 학계의 고유성은 존재하는가? 한국 사회학과 국제 사회학의 "
                 "지식 담론 구조 비교를 중심으로",
        "authors": ["김란우", "송수연"], "published": "2020", "journal": "한국사회학",
        "volume": "54", "issue": "4", "doi": "10.21562/kjs.2020.11.54.4.1"}),
    ("kci_dspace_2171498", {
        "title": "Research on the Application of Large Language Models (LLMs) in the "
                 "Defense Domain",
        "authors": ["Kyongsoo Moon", "Minjung Kang", "Ho Young Yu", "Yongtae Shin"],
        "published": "2024", "journal": "Journal of Korea Academia-Industrial cooperation Society",
        "volume": "25", "issue": "8", "doi": "10.5762/KAIS.2024.25.8.484"}),
    ("koreascience_CFKO202404272003320", {
        "title": "Evaluation of Large Language Models' Korean Descriptive Writing Using University "
                 "Essay Questions",
        "authors": ["Seong-Hun Noh", "Ju-ae Kim", "Han-Seo Jo"], "published": "2024",
        "journal": "Annual Conference on Human and Language Technology"}),
    ("scienceon_JAKO202302557635729", {
        "title": "ChatGPT 및 거대언어모델의 추론 능력 향상을 위한 프롬프트 엔지니어링 "
                 "방법론 및 연구 현황 분석",
        "authors": ["박상언", "강주영"], "published": "2023", "journal": "지능정보연구",
        "volume": "29", "issue": "4", "doi": "10.13088/jiis.2023.29.4.287"}),
    ("kiss_4059314", {
        "title": "한국어 헬스케어 개체명 인식을 위한 거대 언어 모델에서의 형태소 기반 "
                 "Few-Shot 학습 기법",
        "authors": ["강수연", "김건우"], "published": "2023",
        "journal": "한국정보처리학회 학술대회논문집", "volume": "30", "issue": "2"}),
    ("earticle_A454990", {
        "title": "한국어 학습자 오류에 대한 거대언어모델과 모어 화자의 교정 양상 비교 연구",
        "authors": ["남신혜"], "published": "2024", "journal": "한국언어문화학",
        "volume": "21", "issue": "3"}),
    ("riss_no_meta", {}),
])
def test_a_landing_page_gives_its_bibliographic_fields(name, expected):
    found = korean_papers.parse_meta(_page(name))
    found.pop("snippet", None)
    assert found == expected


def test_dbpia_og_description_stands_in_for_missing_citation_tags():
    html = ('<html><head><meta property="og:description" '
            'content="이용준; 강장묵; 김지효 | 한국산학기술학회 논문지 | 2024.3"></head></html>')
    assert korean_papers.parse_meta(html) == {
        "authors": ["이용준", "강장묵", "김지효"], "published": "2024",
        "journal": "한국산학기술학회 논문지"}


def test_names_in_one_tag_are_told_apart():
    html = ('<html><head><meta name="citation_title" content="디지털트윈 어플리케이션">'
            '<meta name="citation_author" content="박규태 이현승 임성주"></head></html>')
    assert korean_papers.parse_meta(html)["authors"] == ["박규태", "이현승", "임성주"]


def test_off_the_databases_only_what_reads_as_a_paper_is_kept():
    hits = [{"title": "자이로소프트 주식회사 | 고려대학교 산학협력 기술사업화 플랫폼",
             "url": "https://tlo.korea.ac.kr/partnership/1278", "snippet": "디지털 트윈"},
            {"title": "디지털 트윈 기반 공정 시뮬레이션 연구",
             "url": "https://repository.example.ac.kr/handle/1/2", "snippet": ""}]
    assert [h["url"] for h in korean_papers.merge([hits], "디지털 트윈")] == [
        "https://repository.example.ac.kr/handle/1/2"]


def test_journal_notices_books_and_database_pages_are_not_papers():
    riss = "https://www.riss.kr/search/detail/DetailView.do?p_mat_type="
    hits = [
        {"title": "「한국초등수학교육학회지」 논문 투고 규정 외",
         "url": riss + "1a0202e37d52c72d&control_no=9a73"},
        {"title": "초등수학교육의 이해", "url": riss + "d7345961987b50bf&control_no=9185"},
        {"title": "RISS 검색 - 국내학술지논문 상세보기",
         "url": riss + "1a0202e37d52c72d&control_no=292e"},
        {"title": "초등수학교육에서 메타버스 활용 연구",
         "url": riss + "be54d9b8bc7cdb09&control_no=1"},
    ]
    assert [h["title"] for h in korean_papers.merge([hits], "초등 수학")] == [
        "초등수학교육에서 메타버스 활용 연구"]
    assert korean_papers.query_for("초등 수학 교육 메타버스 KCI 논문") == "초등 수학 교육 메타버스"


def test_dublin_core_tags_are_read_too():
    html = ('<html><head><meta name="DC.title" content="학교 현장의 생성형 AI 활용 연구">'
            '<meta name="DC.creator" content="홍길동"><meta name="DC.creator" content="김철수">'
            '<meta name="DC.date.issued" content="2025-03-01">'
            '<meta name="DC.source" content="교육정보미디어연구">'
            '<meta name="DC.identifier" content="https://doi.org/10.15833/KAFEIAM.31.1.001">'
            '</head></html>')
    assert korean_papers.parse_meta(html) == {
        "title": "학교 현장의 생성형 AI 활용 연구", "authors": ["홍길동", "김철수"],
        "published": "2025", "journal": "교육정보미디어연구", "doi": "10.15833/KAFEIAM.31.1.001"}


@pytest.mark.asyncio
async def test_a_search_asks_every_variant_and_reads_the_papers_pages(recorded):
    asked, read = recorded
    hits = await korean_papers.search("거대언어모델 요약의 국내 연구 동향", 8)
    assert asked == korean_papers.variants("거대언어모델 요약")
    assert len(read) == 6 and read == [h["url"] for h in hits[:6]]
    by_url = {h["url"]: h for h in hits}

    gpt = by_url["https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11743721"]
    assert gpt["title"] == "GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구"
    assert gpt["authors"] == ["이용준", "강장묵", "김지효"]
    meta = research.bibliography(gpt)
    assert meta == {"author": "이용준, 강장묵, 김지효", "year": "2024",
                    "publisher": "한국산학기술학회 논문지, 25(3)",
                    "doi": "10.5762/KAIS.2024.25.3.10"}
    assert report_export.citation_text({"title": gpt["title"], **meta}, "APA") == (
        "이용준, 강장묵, 김지효. (2024). GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구. "
        "한국산학기술학회 논문지, 25(3). https://doi.org/10.5762/KAIS.2024.25.3.10")

    # A Korean paper whose page has only English tags keeps its Korean title.
    defense = by_url["https://dspace.kci.go.kr/handle/kci/2171498"]
    assert defense["title"] == "거대언어모델(LLM)의 국방분야 적용모델 연구"
    assert defense["doi"] == "10.5762/KAIS.2024.25.8.484"


@pytest.mark.asyncio
async def test_without_meta_tags_a_paper_is_its_title_venue_and_link(recorded, monkeypatch):
    async def nothing(base, url):
        return _page("riss_no_meta") if "riss.kr" in url else ""

    monkeypatch.setattr(builtin, "scrape_html", nothing)
    hits = await korean_papers.search("거대언어모델 요약", 20)
    riss = next(h for h in hits if h["url"] == _RISS)
    assert riss["title"] == "거대언어모델의 차별문제 비교 연구"
    assert research.bibliography(riss) == {"publisher": "RISS"}
    assert report_export.citation_text(
        {"title": riss["title"], "url": riss["url"], **research.bibliography(riss)},
        "APA") == (
        f"거대언어모델의 차별문제 비교 연구. RISS. {_RISS}")
    dev = next(h for h in hits if h["title"] == "거대 언어 모델의 발전 및 상용화 과제")
    assert research.bibliography(dev) == {"publisher": "한국통신학회지(정보와통신)"}


@pytest.mark.asyncio
async def test_a_failing_backend_finds_nothing_or_what_the_rest_found(recorded, monkeypatch):
    async def broken(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(builtin, "searxng", broken)
    assert await korean_papers.search("거대언어모델 요약") == []

    async def no_backend():
        return settings_store.ToolBackends()

    monkeypatch.setattr(settings_store, "tools_config", no_backend)
    assert await korean_papers.search("거대언어모델 요약") == []


@pytest.mark.asyncio
async def test_landing_pages_are_read_as_raw_html_through_the_reader(monkeypatch):
    sent: list[httpx.Request] = []

    async def handle(self, request):
        await request.aread()
        sent.append(request)
        return httpx.Response(200, json={"success": True, "data": {
            "rawHtml": _page("dbpia_NODE11743721")}})

    async def public(_url):
        return None

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setattr(netguard, "refusal", public)
    html = await builtin.scrape_html("http://fetch.test", "https://www.dbpia.co.kr/x")
    assert "citation_doi" in html
    body = json.loads(sent[0].content)
    assert body == {"url": "https://www.dbpia.co.kr/x", "formats": ["rawHtml"],
                    "onlyMainContent": False}


@pytest.mark.asyncio
async def test_a_report_on_korean_research_cites_korean_papers(monkeypatch):
    asked = []

    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def plan(*_args, **_kwargs):
        return ["LLM summarization factual consistency"], {"inputTokens": 0, "outputTokens": 0}

    async def gather(*_args, **_kwargs):
        return [{"title": "Factual consistency of LLM summarization",
                 "url": "https://arxiv.org/abs/2401.00001",
                 "snippet": "LLM summarization factual consistency"}]

    papers = [
        {"title": "GPT 4.0의 우회 공격과 거대언어모델 요약 침입 차단 모델에 대한 연구",
         "url": "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11743721",
         "snippet": "거대언어모델 요약", "authors": ["이용준", "강장묵", "김지효", "홍길동"],
         "published": "2024", "journal": "한국산학기술학회 논문지", "volume": "25",
         "issue": "3", "doi": "10.5762/KAIS.2024.25.3.10", "lane": korean_papers.LANE},
        {"title": "거대언어모델 요약의 국방분야 적용모델 연구",
         "url": "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE2",
         "snippet": "거대언어모델 요약", "lane": korean_papers.LANE},
    ]

    async def search(query, count=6):
        asked.append(query)
        return [dict(p) for p in papers]

    async def scrape(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(research, "_plan", plan)
    monkeypatch.setattr(research, "_gather", gather)
    monkeypatch.setattr(research, "scrape", scrape)
    monkeypatch.setattr(korean_papers, "search", search)
    found = await research.run("거대언어모델 요약의 국내외 연구 동향 보고서", scholarly=True)
    assert asked == ["거대언어모델 요약"]
    korean = [s for s in found.sources if "dbpia.co.kr" in s["url"]]
    assert len(korean) == 2  # several from one database
    assert korean[0]["author"] == "이용준, 강장묵, 김지효 외"
    assert korean[0]["publisher"] == "한국산학기술학회 논문지, 25(3)"
    assert korean[1]["publisher"] == "DBpia"
    assert "국내 논문 검색 결과 없음" not in found.context

    # Nothing Korean found: the sources say so, and the survey above is not domestic.
    papers.clear()
    found = await research.run("거대언어모델 요약의 국내외 연구 동향 보고서", scholarly=True)
    assert "[국내 논문 검색 결과 없음] 「거대언어모델 요약」" in found.context

    asked.clear()
    await research.run("LLM summarization factual consistency survey", scholarly=True)
    assert asked == []


@pytest.mark.asyncio
async def test_the_search_tool_offers_korean_papers(monkeypatch):
    seen = []

    async def search(query, count=6):
        seen.append(query)
        return [{"title": "GPT 4.0의 우회 공격과 LLM 침입 차단 모델에 대한 연구",
                 "url": "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11743721",
                 "snippet": "", "authors": ["이용준", "강장묵", "김지효"], "published": "2024",
                 "journal": "한국산학기술학회 논문지", "volume": "25", "issue": "3",
                 "doi": "10.5762/KAIS.2024.25.3.10", "lane": korean_papers.LANE}]

    monkeypatch.setattr(korean_papers, "search", search)
    for kind in ("korean_papers", "kci"):  # the earlier name still works
        result = await builtin.web_search({"query": "거대언어모델 보안", "kind": kind})
        assert not result.failed
        assert ("이용준, 강장묵, 김지효 · 2024 · 한국산학기술학회 논문지 25권 3호 · "
                "DOI 10.5762/KAIS.2024.25.3.10") in result.content
        assert result.search_evidence is not None
    assert seen == ["거대언어모델 보안", "거대언어모델 보안"]
    kinds = builtin.WEB_SEARCH.parameters["properties"]["kind"]["enum"]
    assert "korean_papers" in kinds and "kci" not in kinds


def test_the_subject_is_the_quoted_topic_not_the_document_s_words():
    # Not 「한국연구재단 개인기초연구 중견연구 연구계획서」, which finds nothing.
    request = ("한국연구재단 개인기초연구(중견연구) 연구계획서 초안을 써 줘. "
               "주제는 \"대형 언어모델 기반 학술 문헌 요약의 사실 일관성 평가 방법 연구\"이고, "
               "연구기간은 3년, 국내외 연구 동향을 포함해 줘.")
    planned = ["한국연구재단 개인기초연구 중견연구 연구계획서"]
    query = korean_papers.query_for(request, planned)
    assert query.startswith("대형 언어모델") and "연구재단" not in query
    assert korean_papers.terms("한국연구재단 개인기초연구 중견연구 연구계획서 초안") == []


@pytest.mark.asyncio
async def test_korean_papers_are_judged_and_kept_among_many_english_hits(monkeypatch):
    """Eight English queries fill the judge's 30 candidates; the Korean lane still gets in."""
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    queries = [f"generative AI university teaching {w}" for w in words]

    async def backends():
        return settings_store.ToolBackends(search="http://search.test")

    async def plan(*_args, **_kwargs):
        return list(queries), {"inputTokens": 0, "outputTokens": 0}

    async def gather(_base, query, **_kwargs):
        tag = query.split()[-1]
        return [{"title": f"{query} study {tag}{n} {n * 7}",
                 "url": f"https://arxiv.org/abs/{tag}{n}", "snippet": query}
                for n in range(6)]

    papers = [
        {"title": "국내 대학 교육에서의 생성형 AI 활용 연구 동향 분석",
         "url": "https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE1",
         "snippet": "생성형 AI 대학 교육", "lane": korean_papers.LANE},
        {"title": "대학생의 생성형 AI의 교육적 사용경험 및 인식 탐색",
         "url": "https://dspace.kci.go.kr/handle/kci/2",
         "snippet": "생성형 AI 대학 교육", "lane": korean_papers.LANE},
    ]

    async def search(query, count=6):
        return [dict(p) for p in papers]

    async def scrape(*_args, **_kwargs):
        return ""

    async def litellm():
        return "http://llm.test", ""

    shown: list[str] = []

    def judge(request: httpx.Request) -> httpx.Response:
        listing = json.loads(request.content)["messages"][0]["content"]
        shown.append(listing)
        numbers = [int(n) for n in __import__("re").findall(r"^(\d+)\. ", listing, flags=8)]
        return httpx.Response(200, json={"choices": [{"message": {"content": str(numbers)}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(research.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(judge), **kw))
    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(settings_store, "litellm_config", litellm)
    monkeypatch.setattr(research, "_plan", plan)
    monkeypatch.setattr(research, "_gather", gather)
    monkeypatch.setattr(research, "scrape", scrape)
    monkeypatch.setattr(korean_papers, "search", search)
    ask = "생성형 AI의 대학 교육 활용 국내외 연구 동향 보고서, 국내는 국내 학회 중심"
    found = await research.run(ask, model="m", api_key="k", scholarly=True)
    assert shown and "국내 대학 교육에서의 생성형 AI" in shown[0]
    korean = [s for s in found.sources if "dbpia" in s["url"] or "kci.go.kr" in s["url"]]
    assert len(korean) == 2
    assert "국내 논문 검색 결과 없음" not in found.context
