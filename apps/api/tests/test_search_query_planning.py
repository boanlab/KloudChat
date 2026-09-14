"""Long requests get query planning without changing search consent or source scope."""

import pytest
from test_freshness_runtime import _capture_normal_route, _events, _RouteDb
from test_privacy import _external_model, _patch_guard_dependencies, _request

from app.models.chat import ChatSession
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import context
from app.services.tools import builtin
from app.services.tools.base import Tool

LONG_REQUEST = (
    "지금 확인 가능한 원/달러 환율을 찾아줘. 1달러가 몇 원인지, 매매기준율인지 다른 "
    "종류인지, 기준 시각과 출처를 함께 알려줘. 실시간 값이 아니면 가장 최근 확인값이라고 "
    "구분해줘. 요청한 수치와 관계없는 예시는 추가하지 말고 확인된 범위만 설명해줘."
)


@pytest.mark.parametrize("words", ["한국장학재단 공식 공지", "공식 공고에서 일정 확인"])
def test_official_notice_is_a_structured_search_hint(words):
    assert context.search_hints(words).get("official") is True


async def _capture_route(monkeypatch, question, toggle, *, strict=False, supports_tools=True):
    user = User(email="synthetic@example.test", password_hash="hash", name="Synthetic")
    model = {**_external_model("synthetic/model"), "supportsTools": supports_tools}
    if strict:
        model.update(strictLocal=True, dataBoundary="self_hosted", creditCost=0)
    session = ChatSession(user_id=user.id, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    captured = _capture_normal_route(monkeypatch)
    builds = []

    async def search(_args):
        pytest.fail("Capturing the route must not execute a search")

    tool = Tool(
        name="web_search", description="synthetic", parameters={"type": "object"},
        run=search, label="search", read_only=True,
    )

    async def build_tools(*_args, **kwargs):
        builds.append(kwargs)
        return [tool] if kwargs["web_search"] else []

    monkeypatch.setattr(sessions, "build_tools", build_tools)
    response = await sessions.send_message(
        session.id, SendMessage(content=question, web_search=toggle),
        _request(), user, _RouteDb(),
    )
    await _events(response)
    captured["build_calls"] = builds
    return captured


@pytest.mark.asyncio
async def test_long_request_plans_search_in_the_forced_model_hop(monkeypatch):
    captured = await _capture_route(monkeypatch, LONG_REQUEST, True)
    assert captured["force_tool"] == "web_search"
    assert captured["preset_call"] is None
    assert any(message.get("content") == LONG_REQUEST for message in captured["messages"])


@pytest.mark.asyncio
async def test_short_preset_preserves_supplied_period_and_domain(monkeypatch):
    words = "최근 한 달 2026년 1학기 공지 site:catalog.example.test"
    captured = await _capture_route(monkeypatch, words, True)
    name, arguments = captured["preset_call"]
    assert name == "web_search"
    assert "2026년 1학기" in arguments["query"]
    assert arguments["site"] == "catalog.example.test"
    assert arguments["time_range"] == "month"


@pytest.mark.asyncio
async def test_long_request_does_not_turn_an_off_toggle_on(monkeypatch):
    captured = await _capture_route(monkeypatch, LONG_REQUEST, False)
    assert captured["force_tool"] is None
    assert captured["preset_call"] is None
    assert captured["tools"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["strict", "no_tools"])
async def test_long_planning_does_not_bypass_model_tool_boundaries(monkeypatch, unavailable):
    captured = await _capture_route(
        monkeypatch, LONG_REQUEST, True,
        strict=unavailable == "strict", supports_tools=unavailable != "no_tools",
    )
    assert captured["force_tool"] is None
    assert captured["preset_call"] is None
    assert captured["tools"] == []
    if unavailable == "strict":
        assert captured["model"]["strictLocal"] is True
        assert all(not args["web_search"] for args in captured["build_calls"])
    else:
        assert captured["build_calls"] == []


class _Response:
    def raise_for_status(self):
        return None

    status_code = 200

    def json(self):
        return {"results": [
            {"title": "Project release", "url": f"https://{host}/releases", "content": "release"}
            for host in [
                "docs.example.test", "sub.docs.example.test", "docs.example.test.evil.test",
            ]
        ]}


class _Client:
    calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url, *, params):
        self.calls.append(params)
        return _Response()


@pytest.mark.asyncio
async def test_inline_site_stays_structured_on_every_search_lane(monkeypatch):
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls = []
    hits = await builtin._searxng(
        "https://search.example.test", "Project latest release site:docs.example.test", 5,
        fresh=True, hints={"time_range": "week"},
    )
    assert len(_Client.calls) == 2
    assert all(call["q"].count("site:docs.example.test") == 1 for call in _Client.calls)
    assert all(call["time_range"] == "week" for call in _Client.calls)
    assert {hit["url"] for hit in hits} == {
        "https://docs.example.test/releases", "https://sub.docs.example.test/releases",
    }


@pytest.mark.asyncio
async def test_conflicting_inline_and_structured_sites_do_not_broaden(monkeypatch):
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls = []
    with pytest.raises(ValueError, match="site"):
        await builtin._searxng(
            "https://search.example.test", "release site:docs.example.test", 5,
            hints={"site": "other.example.test"},
        )
    assert _Client.calls == []


@pytest.mark.asyncio
async def test_multiple_inline_sites_are_not_silently_reduced_to_one(monkeypatch):
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls = []
    with pytest.raises(ValueError, match="site"):
        await builtin._searxng(
            "https://search.example.test", "release site:a.example.test OR site:b.example.test", 5,
        )
    assert _Client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/2026/releases", ":8443"])
async def test_unsupported_site_prefix_is_not_broadened_to_a_domain(monkeypatch, suffix):
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls = []
    query = f"release site:docs.example.test{suffix}"
    assert context.search_query(query) == query
    with pytest.raises(ValueError, match="site"):
        await builtin._searxng("https://search.example.test", query, 5)
    assert _Client.calls == []


@pytest.mark.parametrize("query", [
    "Project release -site:docs.example.test",
    '"release site:docs.example.test notes"',
])
def test_excluded_or_quoted_site_is_not_a_positive_scope(query):
    assert context.search_site_scope(query) == (query, "")


def test_structured_site_prefix_is_not_a_character_strip():
    assert context.search_site_scope("release", "tesla.example.test") == (
        "release", "tesla.example.test",
    )


def test_only_the_unquoted_positive_operator_becomes_scope():
    assert context.search_site_scope(
        '"site:literal.example.test in a manual" release site:docs.example.test'
    ) == ('"site:literal.example.test in a manual" release', "docs.example.test")


@pytest.mark.parametrize("query", [
    "Project release -site:docs.example.test",
    '"release site:docs.example.test notes"',
])
def test_short_query_and_hints_do_not_invert_excluded_or_quoted_sites(query):
    assert "site" not in context.search_hints(query)
    assert context.search_query(query) == query


def test_short_conflicting_sites_reach_the_tool_without_silent_reduction():
    query = "release site:a.example.test OR site:b.example.test"
    assert "site" not in context.search_hints(query)
    assert context.search_query(query) == query
    with pytest.raises(ValueError, match="site"):
        context.search_site_scope(
            context.search_query(query), context.search_hints(query).get("site")
        )


@pytest.mark.asyncio
async def test_papers_lane_keeps_the_requested_site_and_period(monkeypatch):
    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    _Client.calls = []
    await builtin._searxng(
        "https://search.example.test", "Transformer attention site:docs.example.test", 5,
        kind="papers", hints={"time_range": "year"},
    )
    assert len(_Client.calls) == 2
    assert all(call["q"].count("site:docs.example.test") == 1 for call in _Client.calls)
    assert all(call["time_range"] == "year" for call in _Client.calls)
