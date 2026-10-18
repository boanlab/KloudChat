"""Literature searches keep the named subject, read the papers, and read the links given."""

import pytest
from test_freshness_followup import _setup
from test_freshness_runtime import _capture_normal_route, _events, _RouteDb
from test_privacy import _request

from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import query_terms, research
from app.services.tools import builtin
from app.services.tools.base import Tool

#: The request that searched prompt injection and single characters.
REQUEST = (
    "오픈라우터 LLM 모델을 이용해서 실험을 한 top-tier conference 또는 journal에 발표된 논문 찾아줘"
)
#: What the planner answered for it: a topic the request never names.
INVENTED = (
    '["survey prompt injection attacks LLM agents", '
    '"safety guardrails prompt injection open LLM models", '
    '"indirect prompt injection mitigation multi-agent systems", '
    '"robustness evaluation prompt injection adversarial attacks", '
    '"prompt injection defense mechanisms large language models", '
    '"security benchmark prompt injection LLM applications"]'
)
#: The model's own call: a JSON list sent as one string.
STRINGIFIED = '["OpenRouter LLM experiments", "OpenRouter ACL EMNLP", "openrouter.ai"]'


def test_a_hangul_product_name_is_kept_in_latin_in_every_query():
    queries, entities = research.scholar_queries(INVENTED, REQUEST)
    assert entities == ["OpenRouter"]
    assert queries and all("OpenRouter" in q for q in queries)
    assert not any("prompt injection" in q for q in queries)
    # a conference/journal request gets venue names
    assert any("ACL" in q for q in queries) and any("NeurIPS" in q for q in queries)


def test_planner_entities_are_trusted_only_when_the_request_names_them():
    plan = (
        '{"entities": [{"name": "OpenRouter", "as_written": "오픈라우터"}, '
        '{"name": "LangChain", "as_written": "랭체인"}], '
        '"queries": ["OpenRouter LLM evaluation", "prompt injection survey", '
        '"OpenRouter API experiments ACL EMNLP", "LangChain agents"]}'
    )
    queries, entities = research.scholar_queries(plan, REQUEST)
    assert entities == ["OpenRouter"]
    assert queries[:2] == ["OpenRouter LLM evaluation", "OpenRouter API experiments ACL EMNLP"]
    assert "prompt injection survey" not in queries and "LangChain agents" not in queries


def test_a_request_without_names_keeps_the_planners_queries():
    request = "LLM 환각 관련 연구 동향 정리해줘"
    plan = '["survey hallucination large language models", "hallucination detection benchmark"]'
    assert research.scholar_queries(plan, request) == (
        ["survey hallucination large language models", "hallucination detection benchmark"], [])


def test_a_list_sent_as_a_string_is_never_split_into_characters():
    assert query_terms.query_list(STRINGIFIED) == [
        "OpenRouter LLM experiments", "OpenRouter ACL EMNLP", "openrouter.ai"]
    # what the bad call searched: one character per query
    assert query_terms.query_list(list(STRINGIFIED[:6])) == []
    assert query_terms.query_list("p") == [] and query_terms.query_list('["Open') == []
    assert research._parse_queries(INVENTED, "원래 요청")[0].startswith("survey prompt")


def _backends(fetch=""):
    class Backends:
        search = "http://search"

    Backends.fetch = fetch
    return Backends()


@pytest.mark.asyncio
async def test_queries_sent_as_a_string_search_whole_queries(monkeypatch):
    calls = []

    async def fake(_base, query, _count, **_kwargs):
        calls.append(query)
        return [{"url": f"https://arxiv.org/abs/2501.0000{len(calls)}",
                 "title": f"{query} paper", "snippet": "LLM routing"}]

    async def config():
        return _backends()

    async def nothing(*_a, **_k):
        return ""

    monkeypatch.setattr(builtin, "_searxng", fake)
    monkeypatch.setattr(builtin.settings_store, "tools_config", config)
    monkeypatch.setattr(builtin, "_scrape", nothing)
    # the errored step: no query, only the stringified list
    result = await builtin.web_search({"queries": STRINGIFIED, "kind": "papers"})
    assert not result.failed
    assert sorted(calls) == sorted(query_terms.query_list(STRINGIFIED))


@pytest.mark.asyncio
async def test_paper_hits_are_read_in_full_and_checked_for_the_name(monkeypatch):
    hits = [
        {"url": "https://example.com/blog/llm-routers", "title": "Blog on LLM routers",
         "snippet": "routers"},
        {"url": "https://aclanthology.org/2025.acl-long.7/", "title": "Cheap judges",
         "snippet": "LLM judges at scale"},
        {"url": "https://arxiv.org/abs/2502.01234", "title": "Agent costs", "snippet": "agents"},
    ]
    read = []

    async def fake(*_a, **_k):
        return hits

    async def config():
        return _backends("http://fetch")

    async def scrape(_base, url):
        read.append(url)
        if url == "https://aclanthology.org/2025.acl-long.7.pdf":
            return "We query all models through OpenRouter (openrouter.ai) in our experiments."
        return "A paper body without the name."

    monkeypatch.setattr(builtin, "_searxng", fake)
    monkeypatch.setattr(builtin.settings_store, "tools_config", config)
    monkeypatch.setattr(builtin, "_scrape", scrape)
    monkeypatch.setattr(builtin.credibility, "tier", lambda _url: 1)
    result = await builtin.web_search({
        "query": "OpenRouter LLM", "queries": ["OpenRouter LLM", "OpenRouter ACL EMNLP"],
        "kind": "papers", "verify": ["OpenRouter"],
    })
    # full texts, not landing pages
    assert "https://aclanthology.org/2025.acl-long.7.pdf" in read
    assert "https://arxiv.org/html/2502.01234" in read
    entries = [line for line in result.content.splitlines() if line.startswith("[")]
    assert entries[0] == "[1] Cheap judges"
    assert "본문 확인: 'OpenRouter' 언급 있음" in result.content
    assert "읽은 본문에 'OpenRouter' 언급 없음" in result.content


@pytest.mark.asyncio
async def test_several_given_links_are_read_in_one_call(monkeypatch):
    async def config():
        return _backends("http://fetch")

    async def scrape(_base, url):
        return "" if url.endswith("9.pdf") else f"text of {url} using OpenRouter"

    async def allowed(_url):
        return None

    monkeypatch.setattr(builtin.settings_store, "tools_config", config)
    monkeypatch.setattr(builtin, "_scrape", scrape)
    monkeypatch.setattr(builtin.netguard, "refusal", allowed)
    links = ["https://aclanthology.org/2025.findings-acl.1.pdf",
             "https://aclanthology.org/2025.acl-long.9.pdf"]
    result = await builtin.fetch_url({"urls": links})
    assert not result.failed and result.detail == "링크 1/2개 읽음"
    assert f"[1] 사용자가 준 링크\n{links[0]}" in result.content
    assert "(이 링크는 읽지 못했습니다.)" in result.content


def test_links_are_taken_from_the_message_in_order():
    text = (
        "이런 것들이 있다고 클로드는 찾아주는데?\n"
        "1. https://aclanthology.org/2025.findings-acl.1.pdf\n"
        "2. (https://aclanthology.org/2025.acl-long.9.pdf).\n"
        "3. https://aclanthology.org/2025.findings-acl.1.pdf"
    )
    assert sessions.given_links_in(text) == [
        "https://aclanthology.org/2025.findings-acl.1.pdf",
        "https://aclanthology.org/2025.acl-long.9.pdf",
    ]


async def _chat_tools(*_args, **kwargs):
    async def unused(_arguments):
        pytest.fail("the mocked route must not execute a tool")

    tools = [Tool(name="fetch_url", description="read", parameters={"type": "object"},
                  run=unused, label="read", read_only=True)]
    if kwargs.get("web_search"):
        tools.insert(0, Tool(name="web_search", description="search",
                             parameters={"type": "object"}, run=unused, label="search",
                             read_only=True))
    return tools


@pytest.mark.asyncio
async def test_links_in_the_message_are_read_before_answering(monkeypatch):
    user, session, _ = await _setup(monkeypatch, lambda _session: [])
    monkeypatch.setattr(sessions, "build_tools", _chat_tools)
    captured = _capture_normal_route(monkeypatch)
    text = ("이런 것들이 있다고 클로드는 찾아주는데? 논문 찾아줘\n"
            "- https://aclanthology.org/2025.findings-acl.1.pdf\n"
            "- https://aclanthology.org/2025.acl-long.9.pdf")
    response = await sessions.send_message(
        session.id, SendMessage(content=text, web_search="auto"), _request(), user, _RouteDb(),
    )
    await _events(response)
    assert captured["preset_call"] == ("fetch_url", {"urls": [
        "https://aclanthology.org/2025.findings-acl.1.pdf",
        "https://aclanthology.org/2025.acl-long.9.pdf",
    ]})
    assert "never say a link the person gave did not appear" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_a_literature_request_presets_entity_queries_and_the_name_check(monkeypatch):
    user, session, _ = await _setup(monkeypatch, lambda _session: [])
    monkeypatch.setattr(sessions, "build_tools", _chat_tools)
    captured = _capture_normal_route(monkeypatch)

    async def plan(request, _model, _key):
        assert request == REQUEST
        return research.scholar_queries(INVENTED, request)

    monkeypatch.setattr(sessions.research_service, "literature_plan", plan)
    response = await sessions.send_message(
        session.id, SendMessage(content=REQUEST, web_search="auto"), _request(), user, _RouteDb(),
    )
    await _events(response)
    name, arguments = captured["preset_call"]
    assert name == "web_search" and arguments["verify"] == ["OpenRouter"]
    assert all("OpenRouter" in q for q in arguments["queries"])


def test_paper_landing_pages_map_to_their_full_text():
    assert builtin.full_text_url("https://aclanthology.org/2025.acl-long.7/") == (
        "https://aclanthology.org/2025.acl-long.7.pdf")
    assert builtin.full_text_url("https://aclanthology.org/P19-1001") == (
        "https://aclanthology.org/P19-1001.pdf")
    assert builtin.full_text_url("https://openreview.net/forum?id=abC-12") == (
        "https://openreview.net/pdf?id=abC-12")
    assert builtin.full_text_url("https://arxiv.org/abs/2502.01234") == (
        "https://arxiv.org/html/2502.01234")
    # already a PDF, or not a single paper
    assert builtin.full_text_url("https://aclanthology.org/2025.acl-long.7.pdf") is None
    assert builtin.full_text_url("https://aclanthology.org/volumes/2025.acl-long/") is None
