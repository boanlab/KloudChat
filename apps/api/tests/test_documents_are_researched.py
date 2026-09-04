"""Writers research before outlining, pass pages as user-role data, and say when they could not."""

from __future__ import annotations

import pytest

from app.models.chat import SessionKind
from app.services import context, deck, report, research


class _Client:
    """Answers with canned replies and records what was asked."""

    def __init__(self, replies, posts, **_):
        self._replies = replies
        self._posts = posts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, _url, json):
        self._posts.append(json)
        text = self._replies.pop(0) if self._replies else "{}"

        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            @staticmethod
            def json():
                return {
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        return R()


_PLAN = (
    '{"title": "제목", "slides": ['
    '{"title": "제목", "layout": "title"},'
    '{"title": "가", "layout": "bullets"},'
    '{"title": "나", "layout": "two-column"},'
    '{"title": "다", "layout": "quote"}]}'
)

#: Findings for a successful search; "96GB" in a prompt proves the page body reached the writer.
_FINDINGS = research.Findings(
    sources=[
        {
            "id": "src0_aaaaaa",
            "ordinal": 1,
            "title": "제품 사양",
            "publisher": "example.com",
            "url": "https://example.com/spec",
            "origin": "web",
            "originLabel": "웹 검색",
            "quote": "96GB",
        }
    ],
    context="[1] 제품 사양\n출처: https://example.com/spec\n본문 발췌:\nVRAM 은 96GB 다.",
    queries=["제품 사양 96GB"],
    searched=True,
)


@pytest.fixture
def gateway(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    for module in (deck, report, research):
        monkeypatch.setattr(module.settings_store, "litellm_config", litellm_config)
    return monkeypatch


@pytest.fixture
def searched(gateway):
    """A search backend that is up, returning `_FINDINGS` for any request."""

    async def available():
        return True

    async def run(_request, **_kwargs):
        return _FINDINGS

    for module in (deck, report):
        gateway.setattr(module.research, "available", available)
        gateway.setattr(module.research, "run", run)
    return gateway


@pytest.fixture
def unsearchable(gateway):
    """No search backend configured."""

    async def available():
        return False

    for module in (deck, report):
        gateway.setattr(module.research, "available", available)
    return gateway


def _system(post: dict) -> str:
    return next(m["content"] for m in post["messages"] if m["role"] == "system")


def _user_blocks(post: dict) -> str:
    return "\n".join(m["content"] for m in post["messages"] if m["role"] == "user")


# ── the pass runs, and it runs first ───────────────────────────────────


async def test_a_report_researches_before_it_plans(searched):
    posts: list[dict] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다"]}']
    searched.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    events = [e async for e in report.write(request="보고서", model="m", api_key="k")]

    steps = [e["id"] for e in events if e["type"] == "step"]
    assert steps.index("sources") < steps.index("outline")
    assert "96GB" in _user_blocks(posts[0])


async def test_a_deck_researches_before_it_plans(searched):
    posts: list[dict] = []
    searched.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client([_PLAN], posts, **kw))

    events = [e async for e in deck.write(request="발표", model="m", api_key="k")]

    steps = [e["id"] for e in events if e["type"] == "step"]
    assert steps.index("sources") < steps.index("outline")
    assert "96GB" in _user_blocks(posts[0])


async def test_the_pages_reach_every_section_as_data_not_instructions(searched):
    posts: list[dict] = []
    replies = ["본문"] * 3
    searched.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    approved = {"title": "제목", "sections": ["가", "나", "다"]}
    events = [
        e
        async for e in report.write(
            request="보고서", model="m", api_key="k", approved_plan=approved
        )
    ]

    assert any(e["type"] == "report" for e in events)
    assert posts, "no section was written"
    for post in posts:
        # Fetched pages are data, never instructions.
        assert "96GB" in _user_blocks(post)
        assert "96GB" not in _system(post)


async def test_the_shelf_the_prose_cites_is_the_one_that_was_read(searched):
    posts: list[dict] = []
    replies = ["본문"] * 3
    searched.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    approved = {"title": "제목", "sections": ["가", "나", "다"]}
    events = [
        e
        async for e in report.write(
            request="보고서", model="m", api_key="k", approved_plan=approved
        )
    ]

    shelf = next(e["sources"] for e in events if e["type"] == "sources")
    assert [s["url"] for s in shelf] == ["https://example.com/spec"]

    audit = next(e["research"] for e in events if e["type"] == "research")
    assert audit == {
        "enabled": True,
        "searched": True,
        "queries": ["제품 사양 96GB"],
        "selected": 1,
        "excluded": 0,
        "webSelected": 1,
        "projectSelected": 0,
        "projectExcluded": 0,
    }


async def test_project_knowledge_becomes_numbered_report_evidence(searched):
    posts: list[dict] = []
    replies = ["본문 [2]"] * 3
    searched.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    events = [
        e
        async for e in report.write(
            request="보고서",
            model="m",
            api_key="k",
            approved_plan={"title": "제목", "sections": ["가", "나", "다"]},
            project_sources=[
                {
                    "id": "project-file-1",
                    "name": "내부 조사.md",
                    "state": "included",
                    "sourceUrl": "",
                },
                {
                    "id": "project-file-2",
                    "name": "분량 밖 자료.pdf",
                    "state": "omitted",
                    "sourceUrl": "",
                },
            ],
        )
    ]

    shelf = next(e["sources"] for e in events if e["type"] == "sources")
    assert [(s["ordinal"], s["title"], s["origin"]) for s in shelf] == [
        (1, "제품 사양", "web"),
        (2, "내부 조사.md", "file"),
    ]
    audit = next(e["research"] for e in events if e["type"] == "research")
    assert audit["webSelected"] == 1
    assert audit["projectSelected"] == 1
    assert audit["projectExcluded"] == 1
    assert all("[2] 내부 조사.md" in _system(post) for post in posts)


# ── and when it cannot run, it says so ─────────────────────────────────


async def test_an_unresearchable_report_is_told_it_is_unresearched(unsearchable):
    posts: list[dict] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다"]}']
    unsearchable.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    events = [e async for e in report.write(request="보고서", model="m", api_key="k")]

    assert "sources" not in [e["id"] for e in events if e["type"] == "step"]
    assert research.UNRESEARCHED_RULE in _system(posts[0])
    audit = next(e["research"] for e in events if e["type"] == "research")
    assert audit["enabled"] is True
    assert audit["searched"] is False


async def test_a_toggle_switched_off_is_a_choice_and_gets_no_disclaimer(searched):
    posts: list[dict] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다"]}']
    searched.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    events = [
        e async for e in report.write(request="보고서", model="m", api_key="k", web_search=False)
    ]

    assert "sources" not in [e["id"] for e in events if e["type"] == "step"]
    assert research.UNRESEARCHED_RULE not in _system(posts[0])
    assert "96GB" not in _user_blocks(posts[0])


# ── the planner ────────────────────────────────────────────────────────


def test_a_planner_that_will_not_answer_still_searches():
    """An unparseable planner reply falls back to the raw request as the query."""
    assert research._parse_queries("설명만 하고 JSON 은 없다", "원래 요청") == ["원래 요청"]


def test_planned_queries_are_capped():
    text = '["가", "나", "다", "라", "마", "바"]'
    assert len(research._parse_queries(text, "원래 요청")) == research.MAX_QUERIES


# ── the rule lands where instructions land ─────────────────────────────


def test_the_research_rule_is_a_system_instruction():
    messages = context.build_document_messages(
        SessionKind.report,
        "본문을 써라",
        untrusted_context=["첨부된 자료"],
        research_rule=research.UNRESEARCHED_RULE,
    )
    system = next(m["content"] for m in messages if m["role"] == "system")
    assert research.UNRESEARCHED_RULE in system
    assert "첨부된 자료" not in system


def test_a_compound_or_particled_word_still_counts_as_relevant() -> None:
    from app.services.research import relevance

    hit = {"title": "전고체 배터리, 기업들이 2025년 양산 경쟁", "snippet": ""}
    assert relevance("고체 배터리 기업 양산 2025", hit) == 1.0
    assert relevance("고체 배터리 기업 양산 2025", {"title": "UPS Tracking", "snippet": ""}) == 0.0
