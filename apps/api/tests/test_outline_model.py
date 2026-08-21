"""The planner model: one call per document, chosen separately from the writer."""

from __future__ import annotations

import pytest
from conftest import both_passes

from app.services import deck, page, report
from app.services import design_templates as dt


class _Client:
    """Records which model each call named, and answers with a usable plan."""

    def __init__(self, replies, seen, **_):
        self._replies = list(replies)
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, _url, json):
        self._seen.append(json["model"])
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


@pytest.fixture
def gateway(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    for module in (deck, page, report):
        monkeypatch.setattr(module.settings_store, "litellm_config", litellm_config)
    return monkeypatch


_DECK_PLAN = (
    '{"title": "제목", "slides": ['
    '{"title": "제목", "layout": "title"},'
    '{"title": "가", "layout": "bullets"},'
    '{"title": "나", "layout": "two-column"},'
    '{"title": "다", "layout": "quote"}]}'
)


@pytest.mark.asyncio
async def test_the_planner_writes_the_outline_and_the_writer_writes_the_rest(gateway):
    seen: list[str] = []
    replies = [_DECK_PLAN, *["<ul><li>내용</li></ul>"] * 6]
    gateway.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    await both_passes(deck, request="발표", model="writer", api_key="k", outline_model="planner")

    assert seen[0] == "planner"
    assert set(seen[1:]) == {"writer"}


@pytest.mark.asyncio
async def test_without_one_the_writer_plans_as_before(gateway):
    seen: list[str] = []
    replies = [_DECK_PLAN, *["<ul><li>내용</li></ul>"] * 6]
    gateway.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    await both_passes(deck, request="발표", model="writer", api_key="k")

    assert set(seen) == {"writer"}


@pytest.mark.asyncio
async def test_a_flat_plan_is_asked_once_more_and_the_planner_is_asked_again(gateway):
    """The retry is part of planning, so it is charged to the planner too."""
    seen: list[str] = []
    flat = (
        '{"title": "제목", "slides": ['
        '{"title": "제목", "layout": "title"},'
        '{"title": "가", "layout": "bullets"},'
        '{"title": "나", "layout": "bullets"},'
        '{"title": "다", "layout": "bullets"},'
        '{"title": "라", "layout": "bullets"}]}'
    )
    replies = [flat, _DECK_PLAN, *["<ul><li>내용</li></ul>"] * 6]
    gateway.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    await both_passes(deck, request="발표", model="writer", api_key="k", outline_model="planner")

    assert seen[:2] == ["planner", "planner"]
    assert set(seen[2:]) == {"writer"}


@pytest.mark.asyncio
async def test_a_page_plans_with_it_too(gateway):
    seen: list[str] = []
    plan = (
        '{"title": "제목", "blocks": ['
        '{"title": "제목", "layout": "cover"},'
        '{"title": "가", "layout": "bullets"},'
        '{"title": "나", "layout": "table"},'
        '{"title": "다", "layout": "quote"}]}'
    )
    replies = [plan, *["<p>내용</p>"] * 6]
    gateway.setattr(page.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    await both_passes(
        page,
        request="발표",
        model="writer",
        api_key="k",
        template=dt.get("deck-editorial"),
        outline_model="planner",
    )

    assert seen[0] == "planner"
    assert set(seen[1:]) == {"writer"}


@pytest.mark.asyncio
async def test_a_report_plans_with_it_too(gateway):
    seen: list[str] = []
    replies = ['{"title": "제목", "sections": ["가", "나", "다", "라"]}', *["본문"] * 6]
    gateway.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    await both_passes(
        report, request="보고서", model="writer", api_key="k", outline_model="planner"
    )

    assert seen[0] == "planner"
    assert set(seen[1:]) == {"writer"}


@pytest.mark.asyncio
async def test_the_planner_tokens_are_counted_apart_from_the_writer_tokens(gateway):
    """A planner can be a different model, and its price is its own.

    Counted together, an outline on a frontier model would be billed at the
    local writer's rate — which is a ledger that says the wrong thing about
    where the money went.
    """
    seen: list[str] = []
    replies = [_DECK_PLAN, *["<ul><li>내용</li></ul>"] * 6]
    gateway.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    events = await both_passes(
        deck, request="발표", model="writer", api_key="k", outline_model="planner"
    )
    # The planning pass and the writing pass each end with their own `usage`.
    # The first carries the planner's tokens and nothing else; the second
    # carries the writer's.
    planned, wrote = [e for e in events if e["type"] == "usage"]

    assert planned["outlineInputTokens"] > 0
    assert planned["outlineOutputTokens"] > 0
    # The writer's own half counts every block call and none of the plan.
    assert wrote["inputTokens"] > 0
    assert wrote["outlineInputTokens"] == 0


@pytest.mark.asyncio
async def test_without_a_planner_the_outline_is_the_writer_s_own_cost(gateway):
    seen: list[str] = []
    replies = [_DECK_PLAN, *["<ul><li>내용</li></ul>"] * 6]
    gateway.setattr(deck.httpx, "AsyncClient", lambda **kw: _Client(replies, seen, **kw))

    events = await both_passes(deck, request="발표", model="writer", api_key="k")
    totals = [e for e in events if e["type"] == "usage"]

    assert all(e["outlineInputTokens"] == 0 for e in totals)
    assert all(e["outlineOutputTokens"] == 0 for e in totals)
    # One per call, from the fake gateway, across both passes.
    assert sum(e["inputTokens"] for e in totals) == len(seen)
