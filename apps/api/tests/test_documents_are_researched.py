"""A document is written from what was looked up, not from what was recalled.

The failure this closes was reported with two screenshots side by side. Asked
which open-source models fit on one 96GB card, the assistant answered from
training data: it named a GPU generation two years old, insisted a current
product did not exist, and listed models that had been superseded twice. The
person happened to know better and typed 인터넷 검색해봐 — the next turn
searched, found the right spec, and corrected itself.

That recovery is the problem, not the fix. It only works for the facts somebody
already doubted, and it does not exist at all on the surfaces that write a
document: a report and a deck were handed no tools, so there was no search to
ask for. The document simply came out wrong, got exported, and went out.

So the writers research before they write, and these are the properties that
have to hold:

* The pass runs **before the outline**, because an outline chosen from memory
  commits every section under it to that memory's shape.
* The queries are **planned off the request**, not the request typed verbatim
  into a search box.
* Page bodies reach the writer as **user-role data**, never as instructions —
  a fetched page is exactly the text an injection would arrive in.
* When research could not run, the writer is **told so**, because silence is
  what lets a remembered fact pass for a checked one.
"""

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

#: What the writers are handed when the search actually ran. The body carries a
#: fact no model could have remembered, so a prompt that contains it proves the
#: page reached the writer rather than just the citation list.
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
    """No search backend configured, which is the honesty path."""

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

    # The step lands before the outline one, and it is not the outline call
    # that produced it — the shelf exists before a heading is chosen.
    steps = [e["id"] for e in events if e["type"] == "step"]
    assert steps.index("sources") < steps.index("outline")
    # The outline call already had the page under it.
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
        # In the data block, never the instruction one. A fetched page is
        # exactly the text an injection arrives in.
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


# ── and when it cannot run, it says so ─────────────────────────────────


async def test_an_unresearchable_report_is_told_it_is_unresearched(unsearchable):
    posts: list[dict] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다"]}']
    unsearchable.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, posts, **kw))

    events = [e async for e in report.write(request="보고서", model="m", api_key="k")]

    # No step claiming a search that never happened.
    assert "sources" not in [e["id"] for e in events if e["type"] == "step"]
    assert research.UNRESEARCHED_RULE in _system(posts[0])


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
    """Falling back to the raw request is what every report used to do."""
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
    # The attachment stays where attachments go.
    assert "첨부된 자료" not in system
